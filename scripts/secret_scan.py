#!/usr/bin/env python3
"""Scan básico de segredos no código publicado (sem falsos positivos do próprio CI)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "tests", "dist", "build"}
SKIP_SUFFIXES = {".pyc", ".mp4", ".mov", ".mkv"}

AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")
SESSION = re.compile(r"PHPSESSID=[A-Za-z0-9]+")
INTERNAL_EMAIL = re.compile(r"alef@fullcycle\.com\.br", re.I)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        if path.name.endswith(".egg-info") or ".egg-info" in path.parts:
            continue
        files.append(path)
    return files


def main() -> int:
    failures: list[str] = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(ROOT)
        if AWS_KEY.search(text):
            failures.append(f"{rel}: possível Access Key AWS")
        if SESSION.search(text):
            failures.append(f"{rel}: possível cookie de sessão")
        if INTERNAL_EMAIL.search(text):
            failures.append(f"{rel}: e-mail interno")
    if failures:
        print("Secret scan FALHOU:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("Secret scan OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
