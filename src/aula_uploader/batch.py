"""Planejamento e validação de criação/upload em lote (vários capítulos)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from aula_uploader.naming import AulaArquivo
from aula_uploader.plan import Acao, PlanoItem
from aula_uploader.portal_client import CapituloInfo


@dataclass
class BatchChapterDraft:
    """Capítulo planejado antes/depois da criação no portal."""

    nome: str
    ordem: int
    bunny_folder_id: str
    bunny_url: str = ""
    capitulo_id: int | None = None
    already_existed: bool = False
    pasta: Path | None = None
    aulas: list[AulaArquivo] = field(default_factory=list)
    plano: list[PlanoItem] = field(default_factory=list)
    skip_videos: bool = False


@dataclass
class BatchPlan:
    curso_id: int
    curso_nome: str
    chapters: list[BatchChapterDraft] = field(default_factory=list)
    status_criacao: str = "0"  # 1 publicado · 0 rascunho (para todos)


def normalize_chapter_name(nome: str) -> str:
    return " ".join(nome.strip().split()).casefold()


def find_existing_chapter(
    nome: str,
    existentes: list[CapituloInfo],
) -> CapituloInfo | None:
    """Casa capítulo pelo nome (ignora maiúsculas/espaços extras)."""
    chave = normalize_chapter_name(nome)
    if not chave:
        return None
    for chapter in existentes:
        if normalize_chapter_name(chapter.nome) == chave:
            return chapter
    return None


def validate_batch_chapters(
    chapters: list[BatchChapterDraft],
    *,
    existing: list[CapituloInfo] | None = None,
) -> list[str]:
    """Retorna lista de erros de validação (vazia = ok)."""
    errors: list[str] = []
    if not chapters:
        errors.append("Inclua pelo menos um capítulo.")
        return errors

    names: dict[str, int] = {}
    orders: dict[int, int] = {}
    bunnies: dict[str, int] = {}

    for index, chapter in enumerate(chapters, start=1):
        nome = chapter.nome.strip()
        if not nome:
            errors.append(f"Capítulo #{index}: nome vazio.")
        else:
            key = normalize_chapter_name(nome)
            if key in names:
                errors.append(
                    f"Capítulo #{index}: nome repetido com #{names[key]} "
                    f"('{nome}')."
                )
            else:
                names[key] = index

        if chapter.ordem <= 0:
            errors.append(f"Capítulo #{index}: ordem inválida ({chapter.ordem}).")
        elif chapter.ordem in orders:
            errors.append(
                f"Capítulo #{index}: ordem {chapter.ordem} repetida "
                f"com #{orders[chapter.ordem]}."
            )
        else:
            orders[chapter.ordem] = index

        bunny = chapter.bunny_folder_id.strip()
        if not bunny:
            errors.append(f"Capítulo #{index}: pasta Bunny vazia.")
        elif bunny in bunnies:
            errors.append(
                f"Capítulo #{index}: mesma pasta Bunny que #{bunnies[bunny]} "
                f"({bunny})."
            )
        else:
            bunnies[bunny] = index

    return errors


def validate_batch_folders(chapters: list[BatchChapterDraft]) -> list[str]:
    """Valida vínculos capítulo ↔ pasta de vídeos."""
    errors: list[str] = []
    folders: dict[str, int] = {}
    linked = 0
    for index, chapter in enumerate(chapters, start=1):
        if chapter.skip_videos or chapter.pasta is None:
            continue
        linked += 1
        key = str(chapter.pasta.resolve())
        if key in folders:
            errors.append(
                f"Capítulo #{index} ({chapter.nome}): pasta já usada no "
                f"#{folders[key]}."
            )
        else:
            folders[key] = index
        if not chapter.aulas and not chapter.skip_videos:
            errors.append(
                f"Capítulo #{index} ({chapter.nome}): nenhum vídeo na pasta."
            )
    if linked == 0 and not any(c.skip_videos for c in chapters):
        errors.append("Vincule pelo menos uma pasta de vídeos a um capítulo.")
    return errors


def batch_summary_rows(chapters: list[BatchChapterDraft]) -> list[dict[str, str]]:
    """Linhas para a tabela final de conferência."""
    rows: list[dict[str, str]] = []
    for chapter in chapters:
        if chapter.skip_videos or not chapter.plano:
            acao = "sem vídeos" if chapter.skip_videos or not chapter.aulas else "—"
            aulas_txt = "—"
        else:
            criar = sum(1 for p in chapter.plano if p.acao == Acao.CRIAR)
            enviar = sum(
                1 for p in chapter.plano if p.acao in {Acao.ENVIAR, Acao.FORCAR}
            )
            pular = sum(1 for p in chapter.plano if p.acao == Acao.PULAR)
            acao = f"{criar} criar · {enviar} enviar · {pular} pular"
            aulas_txt = ", ".join(
                f"{p.aula.ordem}. {p.aula.titulo}" for p in chapter.plano[:4]
            )
            if len(chapter.plano) > 4:
                aulas_txt += f" … (+{len(chapter.plano) - 4})"

        destino = "já existia" if chapter.already_existed else "criar"
        if chapter.capitulo_id:
            destino += f" · ID {chapter.capitulo_id}"

        rows.append(
            {
                "ordem": str(chapter.ordem),
                "capitulo": chapter.nome,
                "bunny": chapter.bunny_folder_id,
                "destino": destino,
                "pasta": (
                    "—"
                    if chapter.skip_videos or chapter.pasta is None
                    else chapter.pasta.name
                ),
                "aulas": aulas_txt,
                "acoes": acao,
            }
        )
    return rows
