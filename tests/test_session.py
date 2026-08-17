"""Testes de resolução de portal, URL base e origem das credenciais."""

import stat

import pytest

from aula_uploader import session as session_mod
from aula_uploader.session import (
    PORTAL_LABELS,
    config_dir,
    get_credentials,
    prompt_credentials_if_needed,
    resolve_portal_key,
    validate_base_url,
)


@pytest.fixture(autouse=True)
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # Sem .env de verdade interferindo no teste.
    monkeypatch.setattr(session_mod, "load_env", lambda: None)
    for var in (
        "PORTAL_USERNAME",
        "PORTAL_PASSWORD",
        "PORTAL_2_USERNAME",
        "PORTAL_2_PASSWORD",
        "DEVOPS_PORTAL_USERNAME",
        "DEVOPS_PORTAL_PASSWORD",
        "PORTAL_1_URL",
        "PORTAL_2_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_portal_labels_usam_o_nome_real():
    assert PORTAL_LABELS["fullcycle"] == "Full Cycle"
    assert PORTAL_LABELS["devops"] == "DevOps Pro"


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("1", "fullcycle"),
        ("2", "devops"),
        ("fullcycle", "fullcycle"),
        ("DevOps", "devops"),
        (" 1 ", "fullcycle"),
    ],
)
def test_resolve_portal_key_aceita_numero_e_slug(entrada, esperado):
    assert resolve_portal_key(entrada) == esperado


def test_resolve_portal_key_recusa_desconhecido():
    with pytest.raises(ValueError, match="Portal inválido"):
        resolve_portal_key("3")


def test_validate_base_url_recusa_http():
    with pytest.raises(ValueError):
        validate_base_url("http://portal.fullcycle.com.br", "fullcycle")


def test_validate_base_url_recusa_subdominio_parecido():
    with pytest.raises(ValueError):
        validate_base_url(
            "https://portal.fullcycle.com.br.evil.example", "fullcycle"
        )


def test_config_dir_tem_permissao_restrita():
    caminho = config_dir()
    assert not caminho.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)


def test_credenciais_vem_do_ambiente(monkeypatch):
    monkeypatch.setenv("PORTAL_USERNAME", "alguem@example.com")
    monkeypatch.setenv("PORTAL_PASSWORD", "senha-de-teste")
    base, user, pwd = get_credentials("fullcycle")
    assert base == "https://portal.fullcycle.com.br"
    assert (user, pwd) == ("alguem@example.com", "senha-de-teste")


def test_portal_2_herda_credenciais_quando_nao_tem_proprias(monkeypatch):
    monkeypatch.setenv("PORTAL_USERNAME", "alguem@example.com")
    monkeypatch.setenv("PORTAL_PASSWORD", "senha-de-teste")
    _base, user, pwd = get_credentials("devops")
    assert (user, pwd) == ("alguem@example.com", "senha-de-teste")


def test_allow_env_false_ignora_o_env_e_pergunta(monkeypatch):
    monkeypatch.setenv("PORTAL_USERNAME", "doenv@example.com")
    monkeypatch.setenv("PORTAL_PASSWORD", "senha-do-env")
    monkeypatch.setattr("builtins.input", lambda *_: "digitado@example.com")
    monkeypatch.setattr(
        session_mod.getpass, "getpass", lambda *_: "senha-digitada"
    )

    _base, user, pwd = prompt_credentials_if_needed("fullcycle", allow_env=False)
    assert (user, pwd) == ("digitado@example.com", "senha-digitada")


def test_allow_env_true_nao_pergunta_nada(monkeypatch):
    monkeypatch.setenv("PORTAL_USERNAME", "doenv@example.com")
    monkeypatch.setenv("PORTAL_PASSWORD", "senha-do-env")

    def _explode(*_args, **_kwargs):
        raise AssertionError("não deveria perguntar nada")

    monkeypatch.setattr("builtins.input", _explode)
    monkeypatch.setattr(session_mod.getpass, "getpass", _explode)

    _base, user, pwd = prompt_credentials_if_needed("fullcycle", allow_env=True)
    assert (user, pwd) == ("doenv@example.com", "senha-do-env")
