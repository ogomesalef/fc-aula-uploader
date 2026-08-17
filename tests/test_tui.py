from pathlib import Path

from aula_uploader.naming import AulaArquivo
from aula_uploader.plan import Acao
from aula_uploader.tui import apply_suggestions, format_acao


def test_ai_cannot_restore_hierarchical_number_to_title_or_order():
    aula = AulaArquivo(
        path=Path("9.1–O problema de segurança.mp4"),
        ordem=1,
        titulo="O Problema de Segurança",
        ordem_inferida=False,
    )
    apply_suggestions(
        [aula],
        [
            {
                "arquivo": aula.path.name,
                "ordem": 9,
                "titulo": "9.1 - O problema de segurança",
            }
        ],
    )
    assert aula.ordem == 1
    assert aula.titulo == "O problema de segurança"


def test_ai_can_supply_order_when_filename_has_no_number():
    aula = AulaArquivo(
        path=Path("introducao.mp4"),
        ordem=1,
        titulo="Introducao",
        ordem_inferida=True,
    )
    apply_suggestions(
        [aula],
        [{"arquivo": aula.path.name, "ordem": 4, "titulo": "Introdução"}],
    )
    assert aula.ordem == 4
    assert aula.titulo == "Introdução"
    assert aula.ordem_inferida is False


def test_format_acao_uses_distinct_colors():
    assert "green" in format_acao(Acao.CRIAR)
    assert "magenta" in format_acao(Acao.PULAR)
    assert "cyan" in format_acao(Acao.ENVIAR)
