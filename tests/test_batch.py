from pathlib import Path

from aula_uploader.batch import (
    BatchChapterDraft,
    batch_summary_rows,
    find_existing_chapter,
    normalize_chapter_name,
    validate_batch_chapters,
    validate_batch_folders,
)
from aula_uploader.naming import AulaArquivo
from aula_uploader.plan import Acao, PlanoItem
from aula_uploader.portal_client import CapituloInfo


def test_normalize_chapter_name_collapses_spaces_and_case():
    assert normalize_chapter_name("  Threat  Model ") == "threat model"


def test_find_existing_chapter_by_name():
    existentes = [
        CapituloInfo(id=10, nome="Introdução", ordem=1, curso_id=1),
        CapituloInfo(id=20, nome="Threat Model", ordem=2, curso_id=1),
    ]
    found = find_existing_chapter("threat  model", existentes)
    assert found is not None
    assert found.id == 20
    assert find_existing_chapter("Outro", existentes) is None


def test_validate_batch_rejects_duplicate_bunny_and_order():
    chapters = [
        BatchChapterDraft(nome="A", ordem=1, bunny_folder_id="bunny-1"),
        BatchChapterDraft(nome="B", ordem=1, bunny_folder_id="bunny-1"),
    ]
    errors = validate_batch_chapters(chapters)
    assert any("ordem" in e.lower() for e in errors)
    assert any("bunny" in e.lower() for e in errors)


def test_validate_batch_rejects_duplicate_names():
    chapters = [
        BatchChapterDraft(nome="Mesmo", ordem=1, bunny_folder_id="a"),
        BatchChapterDraft(nome="mesmo", ordem=2, bunny_folder_id="b"),
    ]
    errors = validate_batch_chapters(chapters)
    assert any("nome repetido" in e.lower() for e in errors)


def test_validate_batch_folders_rejects_shared_path(tmp_path):
    pasta = tmp_path / "videos"
    pasta.mkdir()
    chapters = [
        BatchChapterDraft(
            nome="A",
            ordem=1,
            bunny_folder_id="a",
            pasta=pasta,
            aulas=[
                AulaArquivo(path=pasta / "1.mp4", ordem=1, titulo="Um"),
            ],
        ),
        BatchChapterDraft(
            nome="B",
            ordem=2,
            bunny_folder_id="b",
            pasta=pasta,
            aulas=[
                AulaArquivo(path=pasta / "2.mp4", ordem=1, titulo="Dois"),
            ],
        ),
    ]
    errors = validate_batch_folders(chapters)
    assert any("pasta já usada" in e.lower() for e in errors)


def test_batch_summary_marks_existing_chapter():
    chapter = BatchChapterDraft(
        nome="Já tem",
        ordem=3,
        bunny_folder_id="b1",
        capitulo_id=99,
        already_existed=True,
        pasta=Path("/tmp/x"),
        plano=[
            PlanoItem(
                aula=AulaArquivo(path=Path("a.mp4"), ordem=1, titulo="Aula"),
                acao=Acao.CRIAR,
            )
        ],
    )
    rows = batch_summary_rows([chapter])
    assert "já existia" in rows[0]["destino"]
    assert "99" in rows[0]["destino"]
    assert "criar" in rows[0]["acoes"]
