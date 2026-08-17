"""Testes de estado retomável, matching de títulos e execução do plano."""

import unicodedata
from pathlib import Path

import pytest

from aula_uploader.naming import AulaArquivo
from aula_uploader.plan import (
    Acao,
    PlanoItem,
    index_existentes,
    match_key,
    montar_plano,
    titulos_duplicados,
)
from aula_uploader.portal_client import ConteudoLinha
from aula_uploader.runner import build_state, executar_plano
from aula_uploader.state import UploadState


@pytest.fixture(autouse=True)
def state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_match_key_iguala_nfd_e_nfc():
    nfc = unicodedata.normalize("NFC", "Introdução ao Docker")
    nfd = unicodedata.normalize("NFD", "Introdução ao Docker")
    assert nfc != nfd
    assert match_key(nfc) == match_key(nfd)


def test_match_key_ignora_caixa_e_espacos_extras():
    assert match_key("  Aula   UM ") == match_key("aula um")


def test_plano_nao_duplica_aula_com_acento_em_nfd():
    aulas = [
        AulaArquivo(
            path=Path("1.mp4"),
            ordem=1,
            titulo=unicodedata.normalize("NFD", "Introdução"),
            tamanho_bytes=10,
        )
    ]
    existentes = [
        ConteudoLinha(
            id=7,
            titulo=unicodedata.normalize("NFC", "Introdução"),
            tem_video=True,
        )
    ]
    plano = montar_plano(aulas, existentes)
    assert plano[0].acao == Acao.PULAR
    assert plano[0].existente_id == 7


def test_titulos_duplicados_aponta_os_arquivos():
    aulas = [
        AulaArquivo(path=Path("a.mp4"), ordem=1, titulo="Introdução"),
        AulaArquivo(path=Path("b.mp4"), ordem=2, titulo="  introducao  "),
        AulaArquivo(path=Path("c.mp4"), ordem=3, titulo="Outra"),
    ]
    duplicados = titulos_duplicados(aulas)
    # "introducao" sem acento é outro título; só o mesmo texto conta.
    assert duplicados == {}

    aulas[1].titulo = unicodedata.normalize("NFD", "INTRODUÇÃO")
    duplicados = titulos_duplicados(aulas)
    assert list(duplicados.values()) == [["a.mp4", "b.mp4"]]


def test_titulos_duplicados_vazio_quando_todos_unicos():
    aulas = [
        AulaArquivo(path=Path("a.mp4"), ordem=1, titulo="Um"),
        AulaArquivo(path=Path("b.mp4"), ordem=2, titulo="Dois"),
    ]
    assert titulos_duplicados(aulas) == {}


def test_index_existentes_prefere_o_que_ja_tem_video():
    linhas = [
        ConteudoLinha(id=1, titulo="Aula", tem_video=False),
        ConteudoLinha(id=2, titulo="Aula", tem_video=True),
    ]
    assert index_existentes(linhas)["aula"].id == 2


def test_state_guarda_a_fonte_original(tmp_path):
    plano = [
        PlanoItem(
            aula=AulaArquivo(path=tmp_path / "1.mp4", ordem=1, titulo="Um"),
            acao=Acao.CRIAR,
        )
    ]
    state = build_state(
        portal="fullcycle",
        capitulo_id=55,
        pasta=tmp_path / "extraido",
        fonte=str(tmp_path / "lote.zip"),
        plano=plano,
        status_criacao="0",
        force=False,
    )
    recarregado = UploadState.load("fullcycle", 55)
    assert recarregado is not None
    assert recarregado.fonte == str(tmp_path / "lote.zip")
    assert recarregado.pasta == str(tmp_path / "extraido")
    assert state.path.exists()


def test_state_usa_a_pasta_como_fonte_quando_nao_informada(tmp_path):
    state = build_state(
        portal="fullcycle",
        capitulo_id=56,
        pasta=tmp_path / "videos",
        plano=[],
        status_criacao="0",
        force=False,
    )
    assert state.fonte == str(tmp_path / "videos")


class _PortalFake:
    def __init__(self):
        self.criados = []

    def save_session(self):
        pass

    def upload_aula_video(self, conteudo_id, path, **kwargs):
        self.criados.append(("upload", conteudo_id))

    def criar_aula_com_video(self, capitulo_id, titulo, ordem, path, **kwargs):
        self.criados.append(("criar", titulo))
        return 500 + ordem


def test_executar_plano_falha_sem_id_em_vez_de_assert(tmp_path):
    aula = AulaArquivo(path=tmp_path / "1.mp4", ordem=1, titulo="Um")
    plano = [PlanoItem(aula=aula, acao=Acao.ENVIAR, existente_id=None)]
    state = build_state(
        portal="fullcycle",
        capitulo_id=57,
        pasta=tmp_path,
        plano=plano,
        status_criacao="0",
        force=False,
    )
    ok, pulados, falhas = executar_plano(
        _PortalFake(), capitulo_id=57, plano=plano, state=state
    )
    assert ok == 0
    assert len(falhas) == 1
    assert "ID da aula existente" in falhas[0][1]
    assert UploadState.load("fullcycle", 57).items[0].status == "failed"


def test_executar_plano_marca_pulado_e_criado(tmp_path):
    aulas = [
        AulaArquivo(path=tmp_path / "1.mp4", ordem=1, titulo="Um"),
        AulaArquivo(path=tmp_path / "2.mp4", ordem=2, titulo="Dois"),
    ]
    plano = [
        PlanoItem(aula=aulas[0], acao=Acao.PULAR, existente_id=10),
        PlanoItem(aula=aulas[1], acao=Acao.CRIAR),
    ]
    state = build_state(
        portal="fullcycle",
        capitulo_id=58,
        pasta=tmp_path,
        plano=plano,
        status_criacao="0",
        force=False,
    )
    portal = _PortalFake()
    ok, pulados, falhas = executar_plano(
        portal, capitulo_id=58, plano=plano, state=state
    )
    assert (ok, pulados, falhas) == (1, 1, [])
    assert portal.criados == [("criar", "Dois")]


def test_only_pending_nao_reenvia_o_que_ja_terminou(tmp_path):
    aula = AulaArquivo(path=tmp_path / "1.mp4", ordem=1, titulo="Um")
    plano = [PlanoItem(aula=aula, acao=Acao.CRIAR)]
    state = build_state(
        portal="fullcycle",
        capitulo_id=59,
        pasta=tmp_path,
        plano=plano,
        status_criacao="0",
        force=False,
    )
    state.mark("1.mp4", "done", conteudo_id=123)

    portal = _PortalFake()
    ok, _pulados, falhas = executar_plano(
        portal, capitulo_id=59, plano=plano, state=state, only_pending=True
    )
    assert ok == 1
    assert falhas == []
    assert portal.criados == []
