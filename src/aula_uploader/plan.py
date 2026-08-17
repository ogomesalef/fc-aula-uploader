"""Parsing de URLs de capítulo e planejamento de ações."""

from __future__ import annotations

import re
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
    raise ValueError(
        "Não foi possível extrair o capítulo. "
        "Cole o ID numérico ou a URL .../admin/curso/conteudo/<ID>/capitulo"
    )


def montar_plano(
    aulas: list[AulaArquivo],
    existentes: list[ConteudoLinha],
    *,
    force: bool = False,
) -> list[PlanoItem]:
    mapa = {linha.titulo.strip().lower(): linha for linha in existentes}
    plano: list[PlanoItem] = []
    for aula in aulas:
        chave = aula.titulo.strip().lower()
        existente = mapa.get(chave)
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
