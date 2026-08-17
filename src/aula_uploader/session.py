"""Sessão e credenciais locais (sem injeção de browser)."""

from __future__ import annotations

import getpass
import os
import stat
from pathlib import Path
from typing import Callable
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
        base = os.getenv("PORTAL_FULLCYCLE_URL", DEFAULT_URLS["fullcycle"])
        user = os.getenv("PORTAL_USERNAME", "")
        password = os.getenv("PORTAL_PASSWORD", "")
    else:
        base = os.getenv("PORTAL_DEVOPS_URL", DEFAULT_URLS["devops"])
        user = os.getenv("DEVOPS_PORTAL_USERNAME") or os.getenv("PORTAL_USERNAME", "")
        password = os.getenv("DEVOPS_PORTAL_PASSWORD") or os.getenv(
            "PORTAL_PASSWORD", ""
        )

    base = validate_base_url(base, portal_key)
    return base, user, password


def prompt_credentials_if_needed(
    portal_key: str,
    *,
    username: str = "",
    password: str = "",
) -> tuple[str, str, str]:
    base, env_user, env_pass = get_credentials(portal_key)
    user = username or env_user
    pwd = password or env_pass
    if not user:
        user = input(
            f"Usuário do portal {PORTAL_LABELS[portal_key]} "
            "(mesmo do login administrativo): "
        ).strip()
    if not pwd:
        pwd = getpass.getpass("Senha (não será exibida): ")
    if not user or not pwd:
        raise RuntimeError("Usuário e senha são obrigatórios.")
    return base, user, pwd


def build_client(
    portal_key: str,
    *,
    username: str = "",
    password: str = "",
    persist_session: bool = True,
) -> PortalClient:
    base, user, pwd = prompt_credentials_if_needed(
        portal_key, username=username, password=password
    )
    path = session_path(portal_key) if persist_session else None
    if path:
        ensure_secure_file(path)
    return PortalClient(base, user, pwd, session_path=path)


def ensure_authenticated(
    portal_key: str,
    *,
    log: Callable[[str], None] | None = None,
    force: bool = False,
    persist_session: bool = True,
) -> PortalClient:
    portal = build_client(portal_key, persist_session=persist_session)
    portal.ensure_authenticated(log=log, force=force)
    return portal


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
