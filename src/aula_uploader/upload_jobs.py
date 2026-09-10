"""Fila e histórico de envios (vários capítulos).

Regra: só um job faz upload de arquivo por vez. Quando o upload termina e as
aulas ficam só processando no Nivo, o próximo da fila pode começar.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from aula_uploader.videopack import cache_dir

INDEX_NAME = "upload-jobs.json"
MAX_HISTORY = 80


def index_path():
    return cache_dir() / INDEX_NAME


def load_index() -> dict[str, Any]:
    path = index_path()
    if not path.is_file():
        return {"jobs": [], "active_upload_id": None}
    try:
        dados = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"jobs": [], "active_upload_id": None}
    if not isinstance(dados, dict):
        return {"jobs": [], "active_upload_id": None}
    dados.setdefault("jobs", [])
    dados.setdefault("active_upload_id", None)
    return dados


def save_index(index: dict[str, Any]) -> None:
    path = index_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def job_fase(job: dict[str, Any]) -> str:
    """uploading | processando | queued | done | error | idle."""
    status = str(job.get("status") or "")
    if status == "queued":
        return "queued"
    items = job.get("items") or []
    if any(i.get("status") == "enviando" for i in items):
        return "uploading"
    if any(i.get("status") == "pendente" for i in items) and status == "running":
        return "uploading"
    # "salvando" = arquivo já no S3; falta só amarrar no portal/Nivo.
    if status == "processando" or any(
        i.get("status") in {"processando", "salvando"} for i in items
    ):
        return "processando"
    if status in {"done", "done_with_errors"}:
        return "done"
    if status in {"error", "cancelado"}:
        return "error"
    if status == "running":
        return "uploading"
    return "idle"


def job_tem_falha(job: dict[str, Any]) -> bool:
    """True quando o envio parou no meio ou alguma aula falhou."""
    if any((i.get("status") == "falhou") for i in job.get("items") or []):
        return True
    return str(job.get("status") or "") in {"error", "done_with_errors"}


def job_bucket(job: dict[str, Any]) -> str:
    """andamento | concluido | historico.

    Falha/cancelamento vão para concluído (fora da fila ativa), para não
    misturar com o envio que está rodando agora. Arquivado → histórico.
    """
    if job.get("archived"):
        return "historico"
    status = str(job.get("status") or "")
    # Cancelado / parado: não fica fingindo que ainda está na fila ativa.
    if status in {"cancelado", "error", "done_with_errors"}:
        return "concluido"
    fase = job_fase(job)
    if fase in {"uploading", "processando", "queued"}:
        return "andamento"
    if job_tem_falha(job):
        return "concluido"
    if fase in {"done", "error"}:
        return "concluido"
    if (job.get("items") or []) and status in {
        "done",
        "done_with_errors",
        "error",
        "cancelado",
    }:
        return "concluido"
    if job.get("items"):
        return "andamento"
    return "concluido"


def pode_iniciar_upload(index: dict[str, Any] | None = None) -> bool:
    """True se nenhum job está no meio do upload de arquivo."""
    index = index if index is not None else load_index()
    for job in index.get("jobs") or []:
        if job.get("archived"):
            continue
        if job_fase(job) == "uploading":
            return False
    return True


def upsert_job(job: dict[str, Any]) -> dict[str, Any]:
    index = load_index()
    jid = str(job.get("id") or "")
    jobs = list(index.get("jobs") or [])
    found = False
    for i, existing in enumerate(jobs):
        if str(existing.get("id")) == jid:
            incoming = dict(job)
            # Hydrate/worker não devem “desarquivar” sem querer.
            if "archived" not in incoming and existing.get("archived"):
                incoming["archived"] = True
                if existing.get("archived_at") is not None:
                    incoming["archived_at"] = existing["archived_at"]
            merged = {**existing, **incoming}
            jobs[i] = merged
            job = merged
            found = True
            break
    if not found:
        jobs.insert(0, job)

    # Um capítulo ativo = um card. Remove clones em andamento do mesmo cap.
    portal = str(job.get("portal") or "")
    cap = job.get("capitulo_id")
    if cap is not None and not job.get("archived"):
        limpos: list[dict[str, Any]] = []
        for existing in jobs:
            if str(existing.get("id") or "") == jid:
                limpos.append(existing)
                continue
            mesmo = (
                str(existing.get("portal") or "") == portal
                and existing.get("capitulo_id") is not None
                and int(existing["capitulo_id"]) == int(cap)
                and not existing.get("archived")
            )
            if mesmo and job_fase(existing) in {"uploading", "processando", "queued", "idle"}:
                continue
            # state-* do mesmo capítulo também cede ao job vivo.
            if mesmo and str(existing.get("id") or "").startswith("state-"):
                continue
            limpos.append(existing)
        jobs = limpos

    index["jobs"] = jobs[:MAX_HISTORY]
    if not job.get("archived") and job_fase(job) == "uploading":
        index["active_upload_id"] = jid
    elif index.get("active_upload_id") == jid and (
        job.get("archived") or job_fase(job) != "uploading"
    ):
        index["active_upload_id"] = None
    save_index(index)
    return index


def get_job(job_id: str) -> dict[str, Any] | None:
    for job in load_index().get("jobs") or []:
        if str(job.get("id")) == str(job_id):
            return job
    return None


def list_jobs(*, bucket: str | None = None) -> list[dict[str, Any]]:
    jobs = list(load_index().get("jobs") or [])
    if not bucket or bucket == "todos":
        return jobs
    return [j for j in jobs if job_bucket(j) == bucket]


def archive_job(job_id: str) -> dict[str, Any] | None:
    job = get_job(job_id)
    if not job:
        return None
    job = dict(job)
    job["archived"] = True
    job["archived_at"] = stamp_now()
    upsert_job(job)
    return job


def unarchive_job(job_id: str) -> dict[str, Any] | None:
    job = get_job(job_id)
    if not job:
        return None
    job = dict(job)
    job["archived"] = False
    job.pop("archived_at", None)
    upsert_job(job)
    return job


def delete_job(job_id: str) -> bool:
    """Remove o job só do índice local (não mexe no portal)."""
    index = load_index()
    jid = str(job_id)
    before = len(index.get("jobs") or [])
    index["jobs"] = [j for j in (index.get("jobs") or []) if str(j.get("id")) != jid]
    if index.get("active_upload_id") == jid:
        index["active_upload_id"] = None
    save_index(index)
    return len(index["jobs"]) < before


def novo_job_id() -> str:
    return uuid.uuid4().hex[:12]


def stamp_now() -> float:
    return time.time()
