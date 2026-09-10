"""Envio de aulas em processo separado, independente da interface web.

O worker grava o progresso em disco. Se o servidor cair, o upload continua.
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

from aula_uploader.media import mask_text
from aula_uploader.naming import AulaArquivo
from aula_uploader.plan import Acao, PlanoItem
from aula_uploader.runner import build_state, executar_plano
from aula_uploader.session import ensure_authenticated, has_saved_session
from aula_uploader.state import UploadState
from aula_uploader.videopack import cache_dir


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
    return "upload-worker" in out.casefold()

JOB_NAME = "upload-job.json"
PID_NAME = "upload-worker.pid"
CANCEL_NAME = "upload-cancel"
LOG_NAME = "upload-worker.log"


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
    job["updated_at"] = time.time()
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        from aula_uploader.upload_jobs import upsert_job

        upsert_job(job)
    except Exception:  # noqa: BLE001, S110
        pass


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


def novo_job(
    *,
    portal: str,
    capitulo_id: int,
    pasta: Path,
    fonte: str,
    status_criacao: str,
    force: bool,
    plano: list[Any],
    capitulo_nome: str = "",
    curso_nome: str = "",
    curso_id: int | None = None,
    url: str = "",
) -> dict[str, Any]:
    from aula_uploader.upload_jobs import novo_job_id, stamp_now

    items = []
    for item in plano:
        items.append(
            {
                "arquivo": item.aula.path.name,
                "path": str(item.aula.path),
                "titulo": item.aula.titulo,
                "ordem": item.aula.ordem,
                "acao": item.acao.value,
                "status": "pular" if item.acao == Acao.PULAR else "pendente",
                "pct": 0,
                "erro": "",
                "conteudo_id": item.existente_id,
            }
        )
    return {
        "id": novo_job_id(),
        "status": "running",
        "pid": 0,
        "portal": portal,
        "capitulo_id": int(capitulo_id),
        "capitulo_nome": capitulo_nome or f"capítulo {capitulo_id}",
        "curso_id": int(curso_id) if curso_id else None,
        "curso_nome": curso_nome or "",
        "url": url or "",
        "pasta": str(pasta),
        "fonte": fonte or str(pasta),
        "status_criacao": status_criacao,
        "force": bool(force),
        "erro": "",
        "ok": 0,
        "pulados": 0,
        "falhas": [],
        "items": items,
        "detached": True,
        "logs": [],
        "created_at": stamp_now(),
        "updated_at": stamp_now(),
    }


def append_log(job: dict[str, Any], message: str) -> None:
    logs = job.setdefault("logs", [])
    logs.append({"ts": time.strftime("%H:%M:%S"), "message": message})
    if len(logs) > 400:
        del logs[:-400]


def marcar_interrupcao(job: dict[str, Any]) -> dict[str, Any]:
    for item in job.get("items") or []:
        if item.get("status") in {"enviando", "salvando"}:
            item["status"] = "falhou"
            item["erro"] = item.get("erro") or "parou no meio; o que já subiu no portal continua"
        elif item.get("status") == "pendente":
            item["erro"] = "ainda não começou — dá para continuar"
    prontos = sum(1 for i in job.get("items") or [] if i.get("status") in {"ok", "pulada"})
    falhas = [i for i in job.get("items") or [] if i.get("status") == "falhou"]
    if prontos and falhas:
        job["status"] = "done_with_errors"
        job["erro"] = f"{prontos} certo(s), {len(falhas)} para continuar."
    elif falhas:
        job["status"] = "error"
        job["erro"] = falhas[0].get("erro") or "o envio parou"
    elif prontos:
        job["status"] = "done"
        job["erro"] = ""
    else:
        job["status"] = "error"
        job["erro"] = "o envio parou"
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
    cancel_path().unlink(missing_ok=True)
    job["status"] = "running"
    job["erro"] = ""
    job["pid"] = 0
    save_job(job)
    log = log_path().open("ab")
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "aula_uploader", "upload-worker"],
        stdout=log,
        stderr=log,
        start_new_session=True,
        close_fds=True,
    )
    job["pid"] = proc.pid
    pid_path().write_text(str(proc.pid), encoding="utf-8")
    save_job(job)
    return job


def _montar_plano_do_job(job: dict[str, Any]) -> list[PlanoItem]:
    plano: list[PlanoItem] = []
    for item in job.get("items") or []:
        if item.get("status") in {"ok", "pulada"}:
            continue
        path = Path(item.get("path") or "")
        if not path.is_file():
            pasta = Path(job.get("pasta") or "")
            candidato = pasta / (item.get("arquivo") or "")
            path = candidato if candidato.is_file() else path
        if not path.is_file():
            item["status"] = "falhou"
            item["erro"] = f"Arquivo ausente: {item.get('arquivo')}"
            continue
        aula = AulaArquivo(
            path=path,
            ordem=int(item.get("ordem") or 1),
            titulo=item.get("titulo") or path.stem,
            tamanho_bytes=path.stat().st_size,
        )
        acao_raw = item.get("acao") or "criar"
        try:
            acao = Acao(acao_raw)
        except ValueError:
            acao = Acao.CRIAR
        # Se já criou o conteúdo e falhou no vídeo, só reenvia o arquivo.
        if item.get("conteudo_id") and acao == Acao.CRIAR:
            acao = Acao.ENVIAR
        plano.append(
            PlanoItem(
                aula=aula,
                acao=acao,
                existente_id=item.get("conteudo_id"),
            )
        )
    return plano


def run_worker() -> int:
    job = load_job()
    if not job:
        return 2
    job["pid"] = os.getpid()
    job["status"] = "running"
    save_job(job)
    pid_path().write_text(str(os.getpid()), encoding="utf-8")

    cancelado = threading.Event()

    def vigia() -> None:
        while not cancelado.wait(0.4):
            if cancel_path().is_file():
                cancelado.set()
                return

    threading.Thread(target=vigia, daemon=True, name="upload-cancel-watch").start()

    def handle(_sig: int, _frame: object) -> None:
        cancelado.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    portal = None
    try:
        def _log(msg: str) -> None:
            append_log(job, msg)
            save_job(job)

        portal_key = str(job.get("portal") or "")
        if not portal_key or not has_saved_session(portal_key):
            raise RuntimeError("Sem sessão salva do portal. Faça login de novo na interface.")
        portal = ensure_authenticated(
            portal_key,
            use_saved_session=True,
            persist_session=True,
            allow_env=False,
            log=_log,
        )
        if cancelado.is_set():
            job["status"] = "cancelado"
            save_job(job)
            return 0

        plano = _montar_plano_do_job(job)
        save_job(job)
        if not plano:
            job["status"] = "done"
            save_job(job)
            return 0

        pasta = Path(job.get("pasta") or Path(plano[0].aula.path).parent)
        upload_state = UploadState.load(portal_key, int(job["capitulo_id"]))
        if upload_state is None:
            upload_state = build_state(
                portal=portal_key,
                capitulo_id=int(job["capitulo_id"]),
                pasta=pasta,
                fonte=job.get("fonte") or pasta,
                plano=plano,
                status_criacao=str(job.get("status_criacao") or "0"),
                force=bool(job.get("force")),
            )
        else:
            # Capítulo já tinha aulas: acrescenta as novas do plano (não perde as antigas).
            upload_state.pasta = str(pasta)
            if job.get("fonte"):
                upload_state.fonte = str(job["fonte"])
            upload_state.ensure_plano(plano)
        # Alinha IDs já criados no job com o estado retomável (NFC-safe).
        for row in job.get("items") or []:
            st = upload_state.find_item(str(row.get("arquivo") or ""))
            if st and row.get("conteudo_id") and not st.conteudo_id:
                st.conteudo_id = int(row["conteudo_id"])
        upload_state.save()

        def on_progress(item, phase: str, error: str | None = None) -> None:
            row = item_por_nome(job, item.aula.path.name)
            if row is None:
                return
            if phase == "id_ready":
                if item.existente_id:
                    row["conteudo_id"] = int(item.existente_id)
                save_job(job)
                return
            row["status"] = {
                "start": "enviando",
                "ok": "ok",
                "skip": "pulada",
                "fail": "falhou",
                "salvando": "salvando",
                "processando": "processando",
            }.get(phase, phase)
            if phase == "ok":
                row["pct"] = 100
                row["erro"] = ""
                if item.existente_id:
                    row["conteudo_id"] = int(item.existente_id)
            elif phase == "salvando":
                row["pct"] = 100
                row["erro"] = ""
                if item.existente_id:
                    row["conteudo_id"] = int(item.existente_id)
            elif phase == "processando":
                row["pct"] = 100
                row["erro"] = ""
                if item.existente_id:
                    row["conteudo_id"] = int(item.existente_id)
                # Já no Nivo: libera o slot de upload de arquivo.
                if job.get("status") == "running":
                    job["status"] = "processando"
                upload_state.mark(
                    item.aula.path.name,
                    "processing",
                    conteudo_id=item.existente_id or row.get("conteudo_id"),
                )
            elif phase == "fail":
                row["erro"] = error or ""
            save_job(job)

        def on_chunk(item, atual: int, total: int) -> None:
            row = item_por_nome(job, item.aula.path.name)
            if row is None:
                return
            # Só "enviando" enquanto sobe o arquivo. "salvando" vem do on_status.
            row["status"] = "enviando"
            row["pct"] = round(100 * atual / max(total, 1))
            save_job(job)

        if cancelado.is_set():
            job["status"] = "cancelado"
            save_job(job)
            return 0

        ok, pulados, falhas = executar_plano(
            portal,
            capitulo_id=int(job["capitulo_id"]),
            plano=plano,
            state=upload_state,
            status_criacao=str(job.get("status_criacao") or "0"),
            log=lambda m: _log(m),
            only_pending=True,
            on_progress=on_progress,
            on_chunk=on_chunk,
            wait_nivo=False,
        )
        # Espelha IDs do UploadState de volta no job (NFC-safe).
        for row in job.get("items") or []:
            st = upload_state.find_item(str(row.get("arquivo") or ""))
            if st and st.conteudo_id:
                row["conteudo_id"] = st.conteudo_id
                if st.status == "processing":
                    row["status"] = "processando"
                    row["pct"] = 100
                elif st.status == "done":
                    row["status"] = "ok"
                    row["pct"] = 100
                elif st.status == "failed":
                    row["status"] = "falhou"
        job["ok"] = ok
        job["pulados"] = pulados
        job["falhas"] = [{"titulo": t, "erro": e} for t, e in falhas]
        if cancelado.is_set():
            job["status"] = "cancelado"
            save_job(job)
            return 0
        if falhas and not any(i.status == "processing" for i in upload_state.items):
            job["status"] = "done_with_errors"
            job["erro"] = falhas[0][1]
            save_job(job)
            return 1
        # Libera o slot de upload de arquivo: Nivo pode seguir enquanto outro capítulo sobe.
        if any(i.status == "processing" for i in upload_state.items):
            job["status"] = "processando"
            job["erro"] = ""
            save_job(job)
            try:
                from aula_uploader.upload_jobs import upsert_job

                upsert_job(dict(job))
            except Exception:  # noqa: BLE001, S110
                pass
            from aula_uploader.runner import _aguardar_processamentos

            prontos = _aguardar_processamentos(
                portal,
                capitulo_id=int(job["capitulo_id"]),
                state=upload_state,
                plano_por_arquivo={p.aula.path.name: p for p in plano},
                log=lambda m: _log(m),
                on_progress=on_progress,
            )
            job["ok"] = int(job.get("ok") or 0) + int(prontos)
            for row in job.get("items") or []:
                st = upload_state.find_item(str(row.get("arquivo") or ""))
                if not st:
                    continue
                if st.conteudo_id:
                    row["conteudo_id"] = st.conteudo_id
                if st.status == "done":
                    row["status"] = "ok"
                    row["pct"] = 100
                elif st.status == "processing":
                    row["status"] = "processando"
                    row["pct"] = 100
        ainda_nivo = any(
            (i.status == "processing") for i in upload_state.items
        ) or any(
            (r.get("status") == "processando") for r in (job.get("items") or [])
        )
        if falhas:
            job["status"] = "done_with_errors"
            job["erro"] = falhas[0][1]
        elif ainda_nivo:
            job["status"] = "processando"
            job["erro"] = ""
        else:
            job["status"] = "done"
            job["erro"] = ""
        save_job(job)
        try:
            from aula_uploader.upload_jobs import upsert_job

            upsert_job(dict(job))
        except Exception:  # noqa: BLE001, S110
            pass
        return 0 if not falhas else 1
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["erro"] = mask_text(str(exc))
        append_log(job, f"Envio interrompido: {job['erro']}")
        for item in job.get("items") or []:
            if item.get("status") in {"pendente", "enviando", "salvando"}:
                item["status"] = "falhou"
                if not item.get("erro"):
                    item["erro"] = job["erro"]
        save_job(job)
        return 1
    finally:
        cancelado.set()
        cancel_path().unlink(missing_ok=True)
        job["pid"] = 0
        save_job(job)
        if portal is not None:
            try:
                portal.close()
            except Exception:  # noqa: BLE001, S110
                pass


def web_job_view(job: dict[str, Any] | None) -> dict[str, Any]:
    """Formato que a interface já espera em session.job."""
    if not job:
        return {}
    from aula_uploader.session import DEFAULT_URLS
    from aula_uploader.tui import conteudo_admin_url
    from aula_uploader.upload_jobs import job_bucket, job_fase

    portal_key = str(job.get("portal") or "")
    base = DEFAULT_URLS.get(portal_key, "")
    items = []
    for i in job.get("items") or []:
        cid = i.get("conteudo_id")
        url = ""
        if cid and base:
            url = conteudo_admin_url(base, int(cid))
        items.append(
            {
                "arquivo": i.get("arquivo"),
                "titulo": i.get("titulo"),
                "ordem": i.get("ordem"),
                "acao": i.get("acao"),
                "status": i.get("status"),
                "pct": i.get("pct") or 0,
                "erro": i.get("erro") or "",
                "conteudo_id": cid,
                "url": i.get("url") or url,
                "path": i.get("path") or "",
            }
        )

    return {
        "id": job.get("id") or "",
        "status": job.get("status") or "",
        "fase": job_fase(job),
        "bucket": job_bucket(job),
        "archived": bool(job.get("archived")),
        "ok": int(job.get("ok") or 0),
        "pulados": int(job.get("pulados") or 0),
        "falhas": list(job.get("falhas") or []),
        "items": items,
        "erro": job.get("erro") or "",
        "detached": True,
        "portal": job.get("portal") or "",
        "capitulo_id": job.get("capitulo_id"),
        "capitulo_nome": job.get("capitulo_nome") or "",
        "curso_id": job.get("curso_id"),
        "curso_nome": job.get("curso_nome") or "",
        "url": job.get("url") or "",
        "pasta": job.get("pasta") or "",
        "fonte": job.get("fonte") or "",
    }
