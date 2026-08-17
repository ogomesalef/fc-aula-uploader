"""Detecção opcional do Ollama para sugerir nomes (somente local)."""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

# Preferência para tarefas curtas de normalização de texto. Qwen 2.5 7B é
# pequeno, multilíngue e segue JSON bem; modelos Whisper não servem para esta
# tarefa e modelos VL são desnecessários porque enviamos somente texto.
RECOMMENDED_MODELS = (
    "qwen2.5:7b",
    "qwen2.5",
    "llama3.2",
    "llama3.1",
    "llama3",
    "mistral",
    "gemma2",
    "phi3",
)


@dataclass
class OllamaStatus:
    installed: bool
    reachable: bool
    models: list[str]
    recommended: str | None
    host: str = "http://127.0.0.1:11434"


def ollama_binary() -> str | None:
    return shutil.which("ollama")


def _api_url(host: str, endpoint: str) -> str:
    """Monta a URL da API local, recusando esquemas fora de http(s).

    O host vem de config/ambiente; sem essa checagem um `file://` ou `ftp://`
    seria aberto por urllib.
    """
    parsed = urlparse(host)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"Host do Ollama inválido: {host}")
    return f"{host.rstrip('/')}{endpoint}"


def detect_ollama(host: str = "http://127.0.0.1:11434", timeout: float = 1.5) -> OllamaStatus:
    installed = ollama_binary() is not None
    models: list[str] = []
    reachable = False
    try:
        req = urllib.request.Request(  # noqa: S310 - http(s) validado em _api_url
            _api_url(host, "/api/tags"), method="GET"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - http(s) validado
            payload = json.loads(resp.read().decode("utf-8"))
        reachable = True
        for item in payload.get("models", []):
            name = item.get("name") or item.get("model")
            if name:
                models.append(str(name))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        reachable = False

    recommended = None
    for cand in RECOMMENDED_MODELS:
        for full in models:
            if full.lower().startswith(cand):
                recommended = full
                break
        if recommended:
            break
    if not recommended and models:
        recommended = models[0]

    return OllamaStatus(
        installed=installed,
        reachable=reachable,
        models=models,
        recommended=recommended,
        host=host,
    )


def suggest_titles(
    filenames: list[str],
    *,
    model: str,
    host: str = "http://127.0.0.1:11434",
    timeout: float = 60.0,
) -> list[dict[str, str | int]]:
    """Pede ao Ollama sugestões de ordem/título.

    Envia apenas nomes de arquivo (sem caminho, sem credenciais, sem URLs).
    Retorna lista de dicts com keys: arquivo, ordem, titulo.
    """
    prompt = (
        "Normalize nomes de arquivos de aulas de vídeo.\n"
        "Para cada arquivo, retorne ordem inteira e título em português.\n"
        "Remova TODA numeração inicial do título, inclusive 9.1, 9.2 etc.\n"
        "Remova sufixos de pós-produção como ed/final/rev.\n"
        "Preserve siglas como IA, API, AWS e K8s.\n"
        "Responda somente no formato JSON solicitado.\n\n"
        "Arquivos:\n" + "\n".join(f"- {name}" for name in filenames)
    )
    schema = {
        "type": "object",
        "properties": {
            "aulas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "arquivo": {"type": "string"},
                        "ordem": {"type": "integer"},
                        "titulo": {"type": "string"},
                    },
                    "required": ["arquivo", "ordem", "titulo"],
                },
            }
        },
        "required": ["aulas"],
    }
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0.1},
        }
    ).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - http(s) validado em _api_url
        _api_url(host, "/api/generate"),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - http(s) validado
        payload = json.loads(resp.read().decode("utf-8"))
    text = payload.get("response", "")
    data = json.loads(text) if isinstance(text, str) else text
    data = _extract_suggestions(data)
    if not isinstance(data, list):
        raise RuntimeError("O modelo não retornou a lista de aulas esperada")
    results: list[dict[str, str | int]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "arquivo": str(item.get("arquivo", "")),
                "ordem": int(item.get("ordem", 0) or 0),
                "titulo": str(item.get("titulo", "")).strip(),
            }
        )
    return results


def _extract_suggestions(data: object) -> object:
    """Aceita formatos comuns gerados por modelos em JSON mode."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return data

    for key in ("aulas", "items", "arquivos", "resultados", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return value

    # Alguns modelos embrulham a resposta uma camada além.
    for value in data.values():
        if isinstance(value, dict):
            nested = _extract_suggestions(value)
            if isinstance(nested, list):
                return nested
    return data
