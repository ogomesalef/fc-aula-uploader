"""Normalização determinística de nomes de arquivo → (ordem, título)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"})

POST_SUFFIXES = frozenset({"ed", "final", "rev"})

KNOWN_ACRONYMS = {
    "k8s": "K8s",
    "sdd": "SDD",
    "api": "API",
    "apis": "APIs",
    "aws": "AWS",
    "gcp": "GCP",
    "cli": "CLI",
    "ci": "CI",
    "cd": "CD",
    "cicd": "CI/CD",
    "ia": "IA",
    "ai": "AI",
    "sql": "SQL",
    "http": "HTTP",
    "https": "HTTPS",
    "tls": "TLS",
    "iac": "IaC",
}

STOPWORDS = frozenset(
    {
        "de",
        "da",
        "do",
        "das",
        "dos",
        "e",
        "a",
        "o",
        "ao",
        "aos",
        "com",
        "em",
        "para",
        "por",
        "no",
        "na",
        "nos",
        "nas",
    }
)


@dataclass
class AulaArquivo:
    path: Path
    ordem: int
    titulo: str
    ordem_inferida: bool = False
    tamanho_bytes: int = 0
    duracao_segundos: float | None = None


def _title_case(tokens: list[str]) -> str:
    palavras: list[str] = []
    for idx, tok in enumerate(tokens):
        low = tok.lower()
        if low in KNOWN_ACRONYMS:
            palavras.append(KNOWN_ACRONYMS[low])
        elif idx > 0 and low in STOPWORDS:
            palavras.append(low)
        else:
            palavras.append(tok[:1].upper() + tok[1:].lower() if tok else tok)
    return " ".join(p for p in palavras if p)


def parse_nome_aula(filename: str) -> tuple[int | None, str]:
    """Extrai (ordem, titulo) do nome do arquivo.

    Exemplos:
      ``9-segurança.mp4`` → (9, ``Segurança``)
      ``02_basico_03_docker_k8s_ed.mp4`` → (3, ``Docker K8s``)
      ``01 - Introdução.mp4`` → (1, ``Introdução``)
    """
    stem = Path(filename).stem

    # Numeração hierárquica no começo do arquivo:
    #   9.1–O problema...  -> ordem 1, "O problema..."
    #   9.3-Mapeando...    -> ordem 3, "Mapeando..."
    # O portal usa uma ordem inteira dentro do capítulo; por isso usamos o
    # último componente da numeração e removemos o prefixo inteiro do título.
    hierarchical = re.match(
        r"^\s*(?P<number>\d+(?:\.\d+)+)\s*(?:[-–—_:]\s*)?(?P<title>.+?)\s*$",
        stem,
    )
    if hierarchical:
        number = hierarchical.group("number")
        title = hierarchical.group("title")
        title_tokens = [t for t in re.split(r"[_\-\s–—]+", title) if t]
        while title_tokens and title_tokens[-1].lower() in POST_SUFFIXES:
            title_tokens.pop()
        return int(number.rsplit(".", 1)[1]), _title_case(title_tokens)

    tokens = [t for t in re.split(r"[_\-\s]+", stem) if t]

    seq_idx = -1
    for i, tok in enumerate(tokens):
        if tok.isdigit():
            seq_idx = i

    if seq_idx >= 0:
        ordem: int | None = int(tokens[seq_idx])
        title_tokens = tokens[seq_idx + 1 :]
    else:
        ordem = None
        title_tokens = list(tokens)

    while title_tokens and title_tokens[-1].lower() in POST_SUFFIXES:
        title_tokens.pop()

    if not title_tokens:
        title_tokens = [
            t for t in tokens if t.lower() not in POST_SUFFIXES and not t.isdigit()
        ]

    titulo = _title_case(title_tokens) if title_tokens else stem
    return ordem, titulo


def listar_videos(
    pasta: str | Path,
    *,
    extensoes: frozenset[str] | set[str] | None = None,
    recursivo: bool = False,
) -> list[AulaArquivo]:
    """Lista vídeos normalizados e ordenados pela ordem da aula."""
    pasta = Path(pasta).expanduser().resolve()
    if not pasta.is_dir():
        raise NotADirectoryError(f"Pasta não encontrada: {pasta}")

    aceitas = {e.lower() for e in (extensoes or VIDEO_EXTENSIONS)}
    if recursivo:
        arquivos = sorted(
            p
            for p in pasta.rglob("*")
            if p.is_file() and p.suffix.lower() in aceitas
        )
    else:
        arquivos = sorted(
            p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in aceitas
        )

    com_numero: list[AulaArquivo] = []
    sem_numero: list[AulaArquivo] = []

    for path in arquivos:
        ordem, titulo = parse_nome_aula(path.name)
        tamanho = path.stat().st_size
        if ordem is None:
            sem_numero.append(
                AulaArquivo(
                    path=path,
                    ordem=0,
                    titulo=titulo,
                    ordem_inferida=True,
                    tamanho_bytes=tamanho,
                )
            )
        else:
            com_numero.append(
                AulaArquivo(
                    path=path,
                    ordem=ordem,
                    titulo=titulo,
                    tamanho_bytes=tamanho,
                )
            )

    com_numero.sort(key=lambda a: (a.ordem, a.path.name))
    proxima = (com_numero[-1].ordem + 1) if com_numero else 1
    for aula in sem_numero:
        aula.ordem = proxima
        proxima += 1

    return com_numero + sem_numero
