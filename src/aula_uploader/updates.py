"""Aviso discreto quando o GitHub está à frente do clone local."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from aula_uploader.session import config_dir, project_root

GITHUB_REPO = "ogomesalef/fc-aula-uploader"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"
UPDATE_COMMAND = "git pull && pip install -e ."
CHECK_EVERY_SECONDS = 6 * 60 * 60
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass(frozen=True)
class UpdateStatus:
    available: bool
    local_sha: str = ""
    remote_sha: str = ""
    command: str = UPDATE_COMMAND


def _git_bin() -> str | None:
    return shutil.which("git")


def _run_git(*args: str) -> str | None:
    git = _git_bin()
    root = project_root()
    if git is None or not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(  # noqa: S603 - argv fixo, git do PATH
            [git, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def local_sha() -> str | None:
    sha = _run_git("rev-parse", "HEAD")
    if sha and _SHA_RE.match(sha):
        return sha
    return None


def _is_behind(remote_sha: str) -> bool:
    """True se o GitHub tem commits que este clone ainda não tem."""
    # remote é ancestral de HEAD → estamos iguais ou à frente.
    git = _git_bin()
    root = project_root()
    if git is None or not (root / ".git").exists():
        return True
    try:
        result = subprocess.run(  # noqa: S603
            [git, "-C", str(root), "merge-base", "--is-ancestor", remote_sha, "HEAD"],
            capture_output=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return result.returncode != 0


def fetch_remote_sha(timeout: float = 1.5) -> str | None:
    """SHA de main no GitHub. Falha silenciosa: update check nunca deve bloquear."""
    req = urllib.request.Request(  # noqa: S310 - URL https fixa do GitHub
        GITHUB_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "aula-uploader",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    sha = str(payload.get("sha") or "")
    return sha if _SHA_RE.match(sha) else None


def _cache_path() -> Path:
    return config_dir() / "update-check.json"


def _read_cache(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(path: Path, payload: dict[str, object]) -> None:
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass


def check_for_update(
    *,
    now: float | None = None,
    fetch: Callable[[], str | None] | None = None,
    cache_path: Path | None = None,
    current_sha: str | None = None,
    behind: Callable[[str], bool] | None = None,
) -> UpdateStatus | None:
    """None = não deu para checar ou já está em dia. Status com available=True = avisar."""
    if os.environ.get("AULA_UPLOADER_SKIP_UPDATE_CHECK"):
        return None
    local = current_sha if current_sha is not None else local_sha()
    if not local:
        return None

    path = cache_path or _cache_path()
    cache = _read_cache(path)
    stamp = now if now is not None else time.time()
    cached_remote = str(cache.get("remote_sha") or "")
    checked_at = float(cache.get("checked_at") or 0)
    fresh = cached_remote and (stamp - checked_at) < CHECK_EVERY_SECONDS

    if fresh:
        remote = cached_remote
    else:
        getter = fetch if fetch is not None else fetch_remote_sha
        remote = getter()
        if remote:
            _write_cache(
                path,
                {"checked_at": stamp, "remote_sha": remote, "local_sha": local},
            )
        elif cached_remote:
            remote = cached_remote
        else:
            return None

    if not _SHA_RE.match(str(remote)):
        return None
    remote = str(remote)
    is_behind = behind(remote) if behind is not None else _is_behind(remote)
    if not is_behind:
        return UpdateStatus(available=False, local_sha=local, remote_sha=remote)
    return UpdateStatus(available=True, local_sha=local, remote_sha=remote)
