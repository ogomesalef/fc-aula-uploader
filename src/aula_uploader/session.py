"""Sessão e credenciais locais (sem injeção de browser)."""

from __future__ import annotations

import getpass
import os
import stat
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from aula_uploader.portal_client import PortalClient

ALLOWED_HOSTS = {
    "fullcycle": "portal.fullcycle.com.br",
    "devops": "portal.devopspro.com.br",
}

PORTAL_LABELS = {
    "fullcycle": "Full Cycle",
    "devops": "DevOps Pro",
}

DEFAULT_URLS = {
    "fullcycle": "https://portal.fullcycle.com.br",
    "devops": "https://portal.devopspro.com.br",
}


PORTAL_ALIASES = {
    "1": "fullcycle",
    "2": "devops",
    "fullcycle": "fullcycle",
    "fc": "fullcycle",
    "devops": "devops",
    "devopspro": "devops",
}


def resolve_portal_key(value: str) -> str:
    """Aceita `1`/`2` e os slugs internos, que também nomeiam o arquivo de estado."""
    key = PORTAL_ALIASES.get(str(value).strip().casefold())
    if key is None:
        opcoes = " · ".join(
            f"{num} ou {slug} = {PORTAL_LABELS[slug]}"
            for num, slug in (("1", "fullcycle"), ("2", "devops"))
        )
        raise ValueError(f"Portal inválido: {value!r}. Use {opcoes}")
    return key


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    """Diretório de config do usuário (~/.config/aula-uploader)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".config"
    path = base / "aula-uploader"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def session_path(portal_key: str) -> Path:
    return config_dir() / f"{portal_key}.session.json"


def state_dir() -> Path:
    path = config_dir() / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_base_url(url: str, portal_key: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    expected = ALLOWED_HOSTS[portal_key]
    if parsed.scheme != "https" or host != expected:
        raise ValueError(
            f"URL inválida para {PORTAL_LABELS[portal_key]}. "
            f"Use apenas https://{expected}"
        )
    return f"https://{expected}"


def load_env() -> None:
    load_dotenv(project_root() / ".env")
    load_dotenv(config_dir() / ".env")


def ensure_secure_file(path: Path) -> None:
    if not path.exists():
        return
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        path.chmod(0o600)


def get_credentials(portal_key: str) -> tuple[str, str, str]:
    load_env()
    if portal_key not in ALLOWED_HOSTS:
        raise ValueError(f"Portal desconhecido: {portal_key}")

    if portal_key == "fullcycle":
        base = os.getenv("PORTAL_1_URL") or os.getenv(
            "PORTAL_FULLCYCLE_URL", DEFAULT_URLS["fullcycle"]
        )
        user = os.getenv("PORTAL_USERNAME", "")
        password = os.getenv("PORTAL_PASSWORD", "")
    else:
        base = (
            os.getenv("PORTAL_2_URL")
            or os.getenv("PORTAL_DEVOPS_URL")
            or DEFAULT_URLS["devops"]
        )
        user = (
            os.getenv("PORTAL_2_USERNAME")
            or os.getenv("DEVOPS_PORTAL_USERNAME")
            or os.getenv("PORTAL_USERNAME", "")
        )
        password = (
            os.getenv("PORTAL_2_PASSWORD")
            or os.getenv("DEVOPS_PORTAL_PASSWORD")
            or os.getenv("PORTAL_PASSWORD", "")
        )

    base = validate_base_url(base, portal_key)
    return base, user, password


def prompt_credentials_if_needed(
    portal_key: str,
    *,
    username: str = "",
    password: str = "",
    allow_empty_password: bool = False,
    allow_env: bool = True,
) -> tuple[str, str, str]:
    """Resolve base, usuário e senha.

    Com ``allow_env=False`` o ``.env`` só fornece a URL do portal; usuário e
    senha passam a ser sempre digitados, para nenhum comando logar em silêncio
    com credenciais que o operador não escolheu ali.
    """
    base, env_user, env_pass = get_credentials(portal_key)
    if not allow_env:
        env_user = ""
        env_pass = ""
    user = username or env_user
    pwd = password or env_pass
    if not user:
        user = input(
            f"Usuário do portal {PORTAL_LABELS[portal_key]} "
            "(mesmo do login administrativo): "
        ).strip()
    if not pwd and not allow_empty_password:
        pwd = getpass.getpass("Senha (não será exibida): ")
    if not user:
        raise RuntimeError("Usuário é obrigatório.")
    if not pwd and not allow_empty_password:
        raise RuntimeError("Senha é obrigatória.")
    return base, user, pwd


def has_saved_session(portal_key: str) -> bool:
    path = session_path(portal_key)
    return path.exists() and path.stat().st_size > 0


def build_client(
    portal_key: str,
    *,
    username: str = "",
    password: str = "",
    persist_session: bool = False,
    use_saved_session: bool = False,
    allow_env: bool = True,
) -> PortalClient:
    """Monta o client.

    Por padrão não grava cookies em disco (mais seguro).
    ``use_saved_session`` só lê um arquivo já existente; para gravar de novo
    use ``persist_session=True``.
    """
    base, user, pwd = prompt_credentials_if_needed(
        portal_key,
        username=username,
        password=password,
        allow_empty_password=use_saved_session and has_saved_session(portal_key),
        allow_env=allow_env,
    )
    path = None
    if persist_session or use_saved_session:
        path = session_path(portal_key)
        ensure_secure_file(path)
    if persist_session:
        return PortalClient(base, user, pwd, session_path=path)
    if use_saved_session and path and path.exists():
        # Lê cookies salvos, mas não sobrescreve o arquivo ao fechar.
        client = PortalClient(base, user, pwd, session_path=path)
        client.session_path = None
        return client
    return PortalClient(base, user, pwd, session_path=None)


def ensure_authenticated(
    portal_key: str,
    *,
    username: str = "",
    password: str = "",
    log: Callable[[str], None] | None = None,
    force: bool = False,
    persist_session: bool = False,
    use_saved_session: bool = False,
    allow_env: bool = True,
) -> PortalClient:
    portal = build_client(
        portal_key,
        username=username,
        password=password,
        persist_session=persist_session,
        use_saved_session=use_saved_session,
        allow_env=allow_env,
    )
    portal.ensure_authenticated(log=log, force=force)
    return portal


def enable_session_persistence(portal: PortalClient, portal_key: str) -> Path:
    """Passa a gravar cookies em disco (permissão 0600)."""
    path = session_path(portal_key)
    portal.session_path = path
    portal.save_session()
    ensure_secure_file(path)
    return path


def clear_session(portal_key: str) -> bool:
    path = session_path(portal_key)
    if path.exists():
        path.unlink()
        return True
    return False


def clear_all_sessions() -> list[str]:
    removed: list[str] = []
    for key in ALLOWED_HOSTS:
        if clear_session(key):
            removed.append(key)
    return removed
