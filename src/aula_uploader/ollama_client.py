"""Detecção opcional do Ollama para sugerir nomes (somente local)."""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass

# Preferência aproximada para tarefas curtas de normalização de texto.
RECOMMENDED_MODELS = (
    "llama3.2",
    "llama3.1",
    "llama3",
    "qwen2.5",
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


def detect_ollama(host: str = "http://127.0.0.1:11434", timeout: float = 1.5) -> OllamaStatus:
    installed = ollama_binary() is not None
    models: list[str] = []
    reachable = False
    try:
        req = urllib.request.Request(f"{host.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        reachable = True
        for item in payload.get("models", []):
            name = item.get("name") or item.get("model")
            if name:
                models.append(str(name))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        reachable = False

    recommended = None
    lower = [m.lower() for m in models]
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
        "Normalize nomes de arquivos de aulas de vídeo para JSON.\n"
        "Para cada arquivo, retorne ordem (número) e titulo (Title Case em português).\n"
        "Remova sufixos de pós-produção como ed/final/rev.\n"
        "Responda SOMENTE com um JSON array no formato:\n"
        '[{"arquivo":"...","ordem":1,"titulo":"..."}]\n\n'
        "Arquivos:\n" + "\n".join(f"- {name}" for name in filenames)
    )
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    text = payload.get("response", "")
    data = json.loads(text) if isinstance(text, str) else text
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise RuntimeError("Resposta do Ollama não é uma lista JSON")
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
