"""Utilitários de mídia: ZIP, ffprobe e formatação."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from aula_uploader.naming import VIDEO_EXTENSIONS


def mask_url(url: str) -> str:
    """Remove query string e mascara possíveis credenciais em URLs."""
    if not url:
        return ""
    base = url.split("?", 1)[0]
    # Esconde Access Key IDs se aparecerem no path (improvável, mas seguro).
    return re.sub(r"AKIA[0-9A-Z]{16}", "AKIA****************", base)


def format_bytes(n: int) -> str:
    mb = n / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.1f} MB"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def probe_duration_seconds(path: Path) -> float | None:
    if not ffprobe_available():
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout or "{}")
        duration = data.get("format", {}).get("duration")
        return float(duration) if duration is not None else None
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


def enrich_durations(aulas: list) -> None:
    for aula in aulas:
        if getattr(aula, "duracao_segundos", None) is None:
            aula.duracao_segundos = probe_duration_seconds(aula.path)


def normalize_user_path(raw: str) -> Path:
    """Normaliza caminho colado/arrastado no terminal.

    Aceita aspas e escapes de shell (ex.: ``Meu\\ Curso`` → ``Meu Curso``).
    """
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()

    # Escapes comuns ao arrastar pasta no macOS/Linux.
    for escaped, plain in (
        (r"\ ", " "),
        (r"\(", "("),
        (r"\)", ")"),
        (r"\[", "["),
        (r"\]", "]"),
        (r"\&", "&"),
        (r"\'", "'"),
        (r'\"', '"'),
    ):
        text = text.replace(escaped, plain)

    path = Path(text).expanduser()
    if path.exists():
        return path

    # Fallback: se ainda restar barra invertida literal, tenta sem ela.
    alt = Path(text.replace("\\", "")).expanduser()
    if alt.exists():
        return alt
    return path


def is_zip(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".zip"


def resolve_source(path: Path) -> tuple[Path, Path | None]:
    """Resolve pasta ou ZIP.

    Returns:
        (pasta_com_videos, temp_dir_ou_None). Se temp_dir não for None,
        o chamador deve limpar com ``cleanup_temp``.
    """
    path = path.expanduser().resolve()
    if path.is_dir():
        return path, None
    if is_zip(path):
        temp_dir = Path(tempfile.mkdtemp(prefix="aula-uploader-"))
        with zipfile.ZipFile(path, "r") as zf:
            _safe_extract(zf, temp_dir)
        # Se o ZIP tiver uma única pasta raiz, usa ela.
        children = [p for p in temp_dir.iterdir() if not p.name.startswith(".")]
        if len(children) == 1 and children[0].is_dir():
            return children[0], temp_dir
        return temp_dir, temp_dir
    raise FileNotFoundError(f"Informe uma pasta ou um arquivo .zip: {path}")


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extrai evitando zip slip."""
    dest = dest.resolve()
    for info in zf.infolist():
        target = (dest / info.filename).resolve()
        if not str(target).startswith(str(dest)):
            raise RuntimeError(f"Entrada ZIP inválida (zip slip): {info.filename}")
        zf.extract(info, dest)


def cleanup_temp(temp_dir: Path | None) -> None:
    if temp_dir and temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)


def count_videos(pasta: Path, *, recursivo: bool = False) -> int:
    aceitas = VIDEO_EXTENSIONS
    it = pasta.rglob("*") if recursivo else pasta.iterdir()
    return sum(1 for p in it if p.is_file() and p.suffix.lower() in aceitas)
