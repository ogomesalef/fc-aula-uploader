"""Parsing de URLs de capítulo e planejamento de ações."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from aula_uploader.naming import AulaArquivo
from aula_uploader.portal_client import ConteudoLinha


class Acao(str, Enum):
    CRIAR = "criar"
    ENVIAR = "enviar"  # aula existe sem vídeo
    PULAR = "pular"  # aula existe com vídeo
    FORCAR = "forcar"  # reenviar mesmo com vídeo


@dataclass
class PlanoItem:
    aula: AulaArquivo
    acao: Acao
    existente_id: int | None = None


def parse_capitulo_id(valor: str) -> int:
    valor = valor.strip().strip("'\"")
    if valor.isdigit():
        return int(valor)
    match = re.search(r"/conteudo/(\d+)/capitulo", valor)
    if match:
        return int(match.group(1))
    # Dicas para URLs comuns do admin que NÃO são o capítulo de conteúdos.
    if (
        re.search(r"/admin/curso/capitulo/\d+/curso", valor)
        or re.search(r"/admin/(titulo|curso)/\d+/(?:edit|curso)\b", valor)
        or ("/capitulo/" in valor and "/edit/" in valor)
    ):
        raise ValueError(
            "Esse link é do curso (lista de capítulos), não da lista de aulas.\n"
            "Abra o capítulo no admin e use a URL que termina em "
            ".../admin/curso/conteudo/<ID>/capitulo"
        )
    raise ValueError(
        "Não foi possível extrair o capítulo.\n"
        "Cole o ID numérico ou a URL .../admin/curso/conteudo/<ID>/capitulo"
    )


def parse_curso_id(valor: str) -> int:
    """Extrai o ID de um link de curso no admin."""
    valor = valor.strip().strip("'\"")
    if valor.isdigit():
        return int(valor)

    patterns = (
        # Lista de capítulos do curso (formato oficial no admin)
        r"/admin/curso/capitulo/(\d+)/curso",
        r"/admin/(?:curso|titulo)/(\d+)/(?:edit|curso)\b",
        r"/admin/curso/(\d+)/edit",
    )
    for pattern in patterns:
        match = re.search(pattern, valor)
        if match:
            return int(match.group(1))
    raise ValueError(
        "Não foi possível extrair o curso.\n"
        "Cole o ID numérico ou a URL "
        ".../admin/curso/capitulo/<ID>/curso"
    )


def resolve_curso_query(valor: str) -> int | str:
    """ID ou URL do admin → int; qualquer outro texto → busca por nome no portal."""
    valor = valor.strip().strip("'\"")
    if not valor:
        raise ValueError("Informe o nome, o ID ou o link do curso.")
    try:
        return parse_curso_id(valor)
    except ValueError:
        return valor


def parse_bunny_folder_id(valor: str) -> str:
    """Extrai o ID de pasta de uma URL Bunny ou aceita o ID diretamente."""
    valor = valor.strip().strip("'\"")
    if not valor:
        raise ValueError("Informe a URL ou o ID da pasta Bunny.")
    if "://" not in valor:
        return valor

    match = re.search(r"/folders?/([^/?#]+)", valor, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"[?&]folderId=([^&#]+)", valor, flags=re.IGNORECASE)
    if not match:
        raise ValueError(
            "Não encontrei o ID da pasta nessa URL Bunny. "
            "Cole a URL da pasta (com /folder/<ID>) ou só o ID dela."
        )
    return match.group(1)


def match_key(titulo: str) -> str:
    """Chave de comparação de títulos entre disco e portal.

    Normaliza Unicode (o macOS entrega NFD, o portal devolve NFC), colapsa
    espaços e ignora caixa — senão "Introdução" viraria uma aula duplicada.
    """
    normalizado = unicodedata.normalize("NFC", titulo)
    return " ".join(normalizado.split()).casefold()


def index_existentes(existentes: list[ConteudoLinha]) -> dict[str, ConteudoLinha]:
    """Indexa por título; em caso de duplicata, a que já tem vídeo vence."""
    mapa: dict[str, ConteudoLinha] = {}
    for linha in existentes:
        chave = match_key(linha.titulo)
        atual = mapa.get(chave)
        if atual is None or (linha.tem_video and not atual.tem_video):
            mapa[chave] = linha
    return mapa


def titulos_duplicados(aulas: list[AulaArquivo]) -> dict[str, list[str]]:
    """Títulos repetidos no mesmo lote, mapeados para os arquivos de origem.

    Dois arquivos com o mesmo título criariam uma aula e sobrescreveriam o
    vídeo da outra, então isso precisa aparecer antes do upload.
    """
    por_titulo: dict[str, list[str]] = {}
    rotulo: dict[str, str] = {}
    for aula in aulas:
        chave = match_key(aula.titulo)
        if not chave:
            continue
        rotulo.setdefault(chave, aula.titulo.strip())
        por_titulo.setdefault(chave, []).append(aula.path.name)
    return {
        rotulo[chave]: arquivos
        for chave, arquivos in por_titulo.items()
        if len(arquivos) > 1
    }


def montar_plano(
    aulas: list[AulaArquivo],
    existentes: list[ConteudoLinha],
    *,
    force: bool = False,
) -> list[PlanoItem]:
    mapa = index_existentes(existentes)
    plano: list[PlanoItem] = []
    for aula in aulas:
        existente = mapa.get(match_key(aula.titulo))
        if existente is None:
            plano.append(PlanoItem(aula=aula, acao=Acao.CRIAR))
        elif existente.tem_video and force:
            plano.append(
                PlanoItem(aula=aula, acao=Acao.FORCAR, existente_id=existente.id)
            )
        elif existente.tem_video:
            plano.append(
                PlanoItem(aula=aula, acao=Acao.PULAR, existente_id=existente.id)
            )
        else:
            plano.append(
                PlanoItem(aula=aula, acao=Acao.ENVIAR, existente_id=existente.id)
            )
    return plano
