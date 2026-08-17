"""Testes das defesas: zip slip, redirects e persistência de cookies."""

import stat
import zipfile
from pathlib import Path

import httpx
import pytest

from aula_uploader.media import cleanup_temp, resolve_source
from aula_uploader.ollama_client import _api_url
from aula_uploader.portal_client import PortalClient

BASE = "https://portal.fullcycle.com.br"


@pytest.fixture
def portal(tmp_path):
    client = PortalClient(
        BASE,
        "user@example.com",
        "secret",
        session_path=tmp_path / "session.json",
    )
    yield client
    client.close()


def _zip_with_entry(zip_path: Path, arcname: str) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(arcname, b"conteudo")


def test_zip_slip_parent_traversal_is_rejected(tmp_path):
    zip_path = tmp_path / "malicioso.zip"
    _zip_with_entry(zip_path, "../fora.mp4")
    with pytest.raises(RuntimeError, match="zip slip"):
        resolve_source(zip_path)
    assert not (tmp_path / "fora.mp4").exists()


def test_zip_slip_absolute_path_is_rejected(tmp_path):
    zip_path = tmp_path / "absoluto.zip"
    _zip_with_entry(zip_path, "/tmp/aula-uploader-invasao.mp4")  # noqa: S108 - alvo do ataque
    # ZipFile normaliza caminhos absolutos, então o teste garante ao menos que
    # nada é escrito fora do destino.
    try:
        pasta, temp = resolve_source(zip_path)
    except RuntimeError:
        return
    try:
        for extraido in pasta.rglob("*"):
            assert temp is not None
            assert extraido.resolve().is_relative_to(temp.resolve())
    finally:
        cleanup_temp(temp)


def test_zip_symlink_entry_is_rejected(tmp_path):
    zip_path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        info = zipfile.ZipInfo("atalho.mp4")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(RuntimeError, match="symlink"):
        resolve_source(zip_path)


def test_redirect_para_outro_host_e_bloqueado(httpx_mock, portal):
    httpx_mock.add_response(
        url=f"{BASE}/admin/curso/291/edit",
        status_code=302,
        headers={"location": "https://evil.example/roubar"},
    )
    with pytest.raises(RuntimeError, match="fora do portal"):
        portal.inspect_curso(291)


def test_redirect_no_mesmo_host_e_seguido(httpx_mock, portal):
    httpx_mock.add_response(
        url=f"{BASE}/admin/curso/291/edit",
        status_code=302,
        headers={"location": "/admin/curso/291/edit/final"},
    )
    httpx_mock.add_response(
        url=f"{BASE}/admin/curso/291/edit/final",
        text='<input name="son_cursosbundle_cursotype[nome]" value="Curso X" />',
    )
    assert portal.inspect_curso(291).nome == "Curso X"


def test_save_session_grava_somente_cookies_de_sessao(portal):
    portal.client.cookies.set(
        "PHPSESSID", "abc123", domain="portal.fullcycle.com.br", path="/"
    )
    portal.client.cookies.set(
        "_ga", "analytics", domain="portal.fullcycle.com.br", path="/"
    )
    portal.client.cookies.set("tracker", "xyz", domain="evil.example", path="/")

    portal.save_session()
    gravados = portal.session_path.read_text(encoding="utf-8")
    assert "PHPSESSID" in gravados
    assert "_ga" not in gravados
    assert "evil.example" not in gravados


def test_session_file_tem_permissao_restrita(portal):
    portal.client.cookies.set(
        "PHPSESSID", "abc123", domain="portal.fullcycle.com.br", path="/"
    )
    portal.save_session()
    modo = portal.session_path.stat().st_mode
    assert not modo & (stat.S_IRWXG | stat.S_IRWXO)


def test_erro_de_conteudo_nao_vaza_html(httpx_mock, portal):
    httpx_mock.add_response(
        url=f"{BASE}/admin/curso/conteudo/55/capitulo",
        text="<html><body><table><tbody></tbody></table></body></html>",
    )
    httpx_mock.add_response(
        url=f"{BASE}/admin/curso/conteudo/new/55/capitulo",
        text='<input name="son_cursosbundle_conteudotype[_token]" value="tok" />',
    )
    httpx_mock.add_response(
        url=f"{BASE}/admin/curso/conteudo/",
        status_code=403,
        text="<html>token secreto csrf=abc</html>",
    )
    from aula_uploader.portal_client import ConteudoData

    with pytest.raises(RuntimeError) as exc:
        portal.create_conteudo(55, ConteudoData(titulo="X", ordem=1, tipo="12"))
    assert "csrf" not in str(exc.value)
    assert "403" in str(exc.value)


def test_is_authenticated_recusa_redirect_externo(httpx_mock, portal):
    portal.client.cookies.set(
        "PHPSESSID", "abc123", domain="portal.fullcycle.com.br", path="/"
    )
    httpx_mock.add_response(
        url=f"{BASE}/admin/curso/",
        status_code=302,
        headers={"location": "https://evil.example/admin"},
    )
    assert portal.is_authenticated() is False


@pytest.mark.parametrize(
    "host",
    ["file:///etc/passwd", "ftp://127.0.0.1", "127.0.0.1:11434", ""],
)
def test_api_url_do_ollama_recusa_esquemas_perigosos(host):
    with pytest.raises(ValueError):
        _api_url(host, "/api/tags")


def test_api_url_do_ollama_aceita_http_local():
    assert _api_url("http://127.0.0.1:11434/", "/api/tags") == (
        "http://127.0.0.1:11434/api/tags"
    )


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_follow_respeita_limite_de_redirects(httpx_mock, portal):
    httpx_mock.add_response(
        url=f"{BASE}/loop",
        status_code=302,
        headers={"location": "/loop"},
        is_reusable=True,
    )
    with pytest.raises(RuntimeError, match="Excesso de redirects"):
        portal._get(f"{BASE}/loop")


def test_client_nao_segue_redirects_sozinho(portal):
    assert portal.client.follow_redirects is False
    assert isinstance(portal.client, httpx.Client)
