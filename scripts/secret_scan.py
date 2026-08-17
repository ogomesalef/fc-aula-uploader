#!/usr/bin/env python3
"""Scan básico de segredos no código publicado.

Roda no CI. Não substitui uma ferramenta dedicada; o objetivo é barrar o
acidente comum: uma senha real colada num arquivo versionado.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build"}
SKIP_SUFFIXES = {".pyc", ".mp4", ".mov", ".mkv", ".png", ".jpg", ".jpeg", ".gif", ".pdf"}

AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")
SESSION = re.compile(r"PHPSESSID=[A-Za-z0-9]+")
INTERNAL_EMAIL = re.compile(r"alef@fullcycle\.com\.br", re.I)

SECRET_NAME = r"(?:password|passwd|senha|secret|token|api[_-]?key)"  # noqa: S105 - regex, não valor
# O próprio scanner contém os padrões que procura.
SELF = Path(__file__).resolve()
# Atribuição literal em código: password="algumacoisa"
CODE_ASSIGN = re.compile(rf'(?i)\b{SECRET_NAME}\s*[=:]\s*["\']([^"\']{{6,}})["\']')
# Atribuição em .env / shell: PORTAL_PASSWORD=algumacoisa.
# Só maiúsculas: `password = x` em Python já é coberto por CODE_ASSIGN.
ENV_ASSIGN = re.compile(
    rf"(?m)^\s*[A-Z0-9_]*{SECRET_NAME.upper()}[A-Z0-9_]*\s*=\s*(\S{{6,}})\s*$"
)

# Valores que claramente são exemplo, fixture de teste ou referência a variável.
PLACEHOLDER = re.compile(
    r"(?i)^(?:"
    r"sua?-|seu-|your-|my-|"
    r".*(?:example|exemplo|placeholder|changeme|troque|dummy|fake|sample|"
    r"aqui|xxx|todo|none|null|test|teste|fixture|redacted|\*{3,}).*"
    r")$"
)
VARIABLE_REF = re.compile(r"^(?:\$|\{|<|self\.|os\.|process\.|args\.|.*\{\{)")


def _is_suspeito(value: str) -> bool:
    value = value.strip()
    if len(value) < 6:
        return False
    if PLACEHOLDER.match(value) or VARIABLE_REF.match(value):
        return False
    # Nomes de campo do formulário do portal (ex.: "_password") não são valores.
    return not value.startswith("_")


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        if ".egg-info" in path.parts or path.name.endswith(".egg-info"):
            continue
        files.append(path)
    return files


def scan_text(text: str, *, check_literals: bool = True) -> list[str]:
    achados: list[str] = []
    if AWS_KEY.search(text):
        achados.append("possível Access Key AWS")
    if SESSION.search(text):
        achados.append("possível cookie de sessão")
    if INTERNAL_EMAIL.search(text):
        achados.append("e-mail interno")
    if not check_literals:
        return achados
    for pattern, rotulo in ((CODE_ASSIGN, "código"), (ENV_ASSIGN, "env")):
        for match in pattern.finditer(text):
            if _is_suspeito(match.group(1)):
                achados.append(f"possível segredo literal ({rotulo})")
                break
    return achados


def main() -> int:
    failures: list[str] = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(ROOT)
        for achado in scan_text(text, check_literals=path.resolve() != SELF):
            failures.append(f"{rel}: {achado}")
    if failures:
        print("Secret scan FALHOU:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"Secret scan OK ({len(iter_files())} arquivos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
