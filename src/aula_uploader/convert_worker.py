"""Compressão em processo separado, independente da interface web.

O worker grava o progresso em disco. Se o servidor cair, o ffmpeg continua.
Quando a interface volta, ela só acompanha o arquivo de estado.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

from aula_uploader.media import format_bytes, mask_text
from aula_uploader.videopack import (
    ALVO_BYTES,
    JOBS_PADRAO,
    TETO_BYTES,
    Conversao,
    VideopackError,
    cache_dir,
)

JOB_NAME = "convert-job.json"
PID_NAME = "convert-worker.pid"
CANCEL_NAME = "convert-cancel"
LOG_NAME = "convert-worker.log"


def job_path() -> Path:
    return cache_dir() / JOB_NAME


def pid_path() -> Path:
    return cache_dir() / PID_NAME


def cancel_path() -> Path:
    return cache_dir() / CANCEL_NAME


def log_path() -> Path:
    return cache_dir() / LOG_NAME


def _norm(nome: str) -> str:
    return unicodedata.normalize("NFC", Path(nome).name)


def load_job() -> dict[str, Any] | None:
    path = job_path()
    if not path.is_file():
        return None
    try:
        dados = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return dados if isinstance(dados, dict) else None


def save_job(job: dict[str, Any]) -> None:
    path = job_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def pid_is_worker(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        ps = shutil.which("ps") or "/bin/ps"
        out = subprocess.check_output(  # noqa: S603
            [ps, "-p", str(pid), "-o", "command="],
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    cmd = out.casefold()
    return "convert-worker" in cmd


def worker_vivo(job: dict[str, Any] | None = None) -> bool:
    job = job if job is not None else load_job()
    if not job:
        return False
    return pid_is_worker(int(job.get("pid") or 0))


def item_por_nome(job: dict[str, Any], nome: str) -> dict[str, Any] | None:
    alvo = _norm(nome)
    for item in job.get("items") or []:
        if _norm(item.get("arquivo") or "") == alvo:
            return item
    return None


def aplicar_evento(job: dict[str, Any], evento: dict[str, Any]) -> None:
    tipo = evento.get("tipo")
    nome = evento.get("nome") or Path(evento.get("arquivo") or "").name
    item = item_por_nome(job, nome) if nome else None
    convertidos = job.setdefault("convertidos", {})

    if tipo == "arquivo-inicio" and item is not None:
        item["status"] = "convertendo"
        item["pct"] = 0
        item["erro"] = ""
    elif tipo == "progresso" and item is not None:
        item["status"] = "convertendo"
        item["pct"] = round(float(evento.get("pct") or 0), 1)
        item["eta_s"] = round(float(evento.get("eta_s") or 0))
    elif tipo == "aviso" and item is not None:
        item["aviso"] = evento.get("aviso") or ""
    elif tipo == "fim" and item is not None:
        tamanho = int(evento.get("tamanho") or 0)
        largura = int(evento.get("largura") or 0)
        altura = int(evento.get("altura") or 0)
        item.update(
            {
                "status": "pronto",
                "pct": 100,
                "eta_s": 0,
                "saida": evento.get("saida") or "",
                "tamanho_novo": tamanho,
                "tamanho_novo_fmt": format_bytes(tamanho),
                "resolucao_nova": f"{largura}x{altura}" if largura and altura else "",
                "erro": "",
            }
        )
        if evento.get("saida"):
            convertidos[item["arquivo"]] = evento["saida"]
    elif tipo == "erro" and item is not None:
        item["status"] = "falhou"
        item["erro"] = mask_text(str(evento.get("erro") or "falhou"))
    elif tipo == "cancelado":
        job["status"] = "cancelado"


def caminhos_pendentes(job: dict[str, Any]) -> list[Path]:
    """Arquivos que ainda precisam ser comprimidos (inclui o que parou no meio)."""
    saida: list[Path] = []
    for item in job.get("items") or []:
        if item.get("status") in {"pronto", "aplicado"}:
            continue
        bruto = item.get("path") or ""
        path = Path(bruto)
        if path.is_file():
            saida.append(path)
    return saida


def novo_job(
    *,
    alvos: list[Any],
    engine: str,
    fonte: str = "",
    lista: list[Any] | None = None,
) -> dict[str, Any]:
    items = []
    for aula in alvos:
        items.append(
            {
                "arquivo": aula.path.name,
                "path": str(aula.path),
                "status": "pendente",
                "pct": 0,
                "eta_s": 0,
                "tamanho_antes": aula.tamanho_bytes,
                "tamanho_antes_fmt": format_bytes(aula.tamanho_bytes),
                "tamanho_novo": 0,
                "tamanho_novo_fmt": "",
                "resolucao_nova": "",
                "saida": "",
                "erro": "",
                "aviso": "",
            }
        )
    # Guarda a lista inteira da tela, não só o que está comprimindo.
    base = list(lista) if lista is not None else list(alvos)
    aulas = []
    seen: set[str] = set()
    for aula in base:
        nome = aula.path.name
        if nome in seen:
            continue
        seen.add(nome)
        aulas.append(
            {
                "arquivo": nome,
                "path": str(aula.path),
                "titulo": aula.titulo,
                "ordem": aula.ordem,
            }
        )
    return {
        "status": "running",
        "pid": 0,
        "engine": engine,
        "fonte": fonte,
        "erro": "",
        "items": items,
        "aulas": aulas,
        "convertidos": {},
        "detached": True,
    }


def marcar_interrupcao(job: dict[str, Any]) -> dict[str, Any]:
    """Worker morreu: o que já ficou pronto continua; o resto fica para retomar."""
    for item in job.get("items") or []:
        if item.get("status") == "convertendo":
            item["status"] = "falhou"
            item["erro"] = item.get("erro") or "parou no meio; o que já ficou pronto continua"
        elif item.get("status") == "pendente":
            item["erro"] = "ainda não começou — dá para continuar"
    prontos = [i for i in job.get("items") or [] if i.get("status") in {"pronto", "aplicado"}]
    falhas = [i for i in job.get("items") or [] if i.get("status") == "falhou"]
    if prontos and falhas:
        job["status"] = "error"
        job["erro"] = f"{len(prontos)} pronto(s), {len(falhas)} para continuar."
    elif falhas:
        job["status"] = "error"
        job["erro"] = falhas[0].get("erro") or "a compressão parou"
    elif prontos:
        job["status"] = "done"
        job["erro"] = ""
    else:
        job["status"] = "error"
        job["erro"] = "a compressão parou"
    job["pid"] = 0
    return job


def request_cancel(job: dict[str, Any] | None = None) -> None:
    cancel_path().write_text("1", encoding="utf-8")
    job = job if job is not None else load_job()
    pid = int((job or {}).get("pid") or 0)
    if not pid_is_worker(pid):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def start_detached(job: dict[str, Any]) -> dict[str, Any]:
    """Sobe o worker fora da sessão do servidor web."""
    cancel_path().unlink(missing_ok=True)
    job["status"] = "running"
    job["erro"] = ""
    job["pid"] = 0
    save_job(job)
    log = log_path().open("ab")
    kwargs: dict[str, Any] = {
        "stdout": log,
        "stderr": log,
        "start_new_session": True,
        "close_fds": True,
    }
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "aula_uploader", "convert-worker"],
        **kwargs,
    )
    job["pid"] = proc.pid
    pid_path().write_text(str(proc.pid), encoding="utf-8")
    save_job(job)
    return job


def run_worker() -> int:
    job = load_job()
    if not job:
        return 2
    job["pid"] = os.getpid()
    job["status"] = "running"
    save_job(job)
    pid_path().write_text(str(os.getpid()), encoding="utf-8")

    caminhos = caminhos_pendentes(job)
    if not caminhos:
        job["status"] = "done"
        save_job(job)
        return 0

    conversao = Conversao(
        paths=caminhos,
        alvo_bytes=ALVO_BYTES,
        teto_bytes=TETO_BYTES,
        jobs=JOBS_PADRAO,
        engine=job.get("engine") or "hw",
    )
    parar = threading.Event()

    def vigia() -> None:
        while not parar.wait(0.4):
            if cancel_path().is_file():
                conversao.cancelar()
                return

    threading.Thread(target=vigia, daemon=True, name="convert-cancel-watch").start()

    def handle(_sig: int, _frame: object) -> None:
        conversao.cancelar()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    def on_event(evento: dict[str, Any]) -> None:
        aplicar_evento(job, evento)
        save_job(job)

    try:
        conversao.executar(on_event)
        if conversao.cancelado:
            job["status"] = "cancelado"
        elif any(i.get("status") == "falhou" for i in job.get("items") or []):
            job["status"] = "error"
        else:
            job["status"] = "done"
            job["erro"] = ""
    except VideopackError as exc:
        job["status"] = "error"
        job["erro"] = mask_text(str(exc))
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["erro"] = mask_text(str(exc))
    finally:
        parar.set()
        cancel_path().unlink(missing_ok=True)
        for item in job.get("items") or []:
            if item.get("status") in {"pendente", "convertendo"}:
                item["status"] = "cancelado" if conversao.cancelado else "falhou"
                if not item.get("erro"):
                    item["erro"] = job.get("erro") or "a conversão parou no meio"
        job["pid"] = 0
        save_job(job)
    return 0 if job.get("status") in {"done", "cancelado"} else 1


def aguardar_worker(timeout: float = 0.0) -> None:
    """Útil em testes; no app a interface só lê o JSON."""
    fim = time.time() + timeout if timeout else 0
    while worker_vivo():
        if timeout and time.time() >= fim:
            return
        time.sleep(0.2)
