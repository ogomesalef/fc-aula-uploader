"""API e página única da interface local."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from aula_uploader import __version__
from aula_uploader.catalog import CatalogCourse, CatalogProduct, CatalogStore, course_matches
from aula_uploader.media import (
    cleanup_temp,
    format_bytes,
    format_duration,
    is_zip,
    mask_text,
    normalize_user_path,
    resolve_source,
)
from aula_uploader.naming import VIDEO_EXTENSIONS, AulaArquivo, aulas_de_caminhos, listar_videos
from aula_uploader.plan import (
    Acao,
    montar_plano,
    parse_bunny_folder_id,
    parse_capitulo_id,
    parse_curso_id,
    resolve_curso_query,
    titulos_duplicados,
)
from aula_uploader.portal_client import CapituloResumo, CursoInfo, PortalClient
from aula_uploader.session import (
    ALLOWED_HOSTS,
    DEFAULT_URLS,
    PORTAL_LABELS,
    clear_session,
    enable_session_persistence,
    ensure_authenticated,
    ensure_secure_file,
    has_saved_session,
    read_last_portal,
    remember_last_portal,
    resolve_portal_key,
    session_path,
    session_username,
)
from aula_uploader.convert_worker import (
    load_job as load_convert_job,
    marcar_interrupcao,
    novo_job,
    request_cancel as cancel_convert_worker,
    save_job as save_convert_job,
    start_detached as start_convert_worker,
    worker_vivo,
)
from aula_uploader.state import UploadState
from aula_uploader.upload_jobs import (
    archive_job as archive_upload_job,
    delete_job as delete_upload_job,
    get_job as get_upload_job_by_id,
    job_fase,
    list_jobs as list_upload_jobs,
    pode_iniciar_upload,
    unarchive_job as unarchive_upload_job,
    upsert_job,
)
from aula_uploader.upload_worker import (
    load_job as load_upload_job,
    marcar_interrupcao as marcar_upload_interrupcao,
    novo_job as novo_upload_job,
    request_cancel as cancel_upload_worker,
    save_job as save_upload_job,
    start_detached as start_upload_worker,
    web_job_view,
    worker_vivo as upload_worker_vivo,
)
from aula_uploader.tui import capitulo_admin_url, conteudo_admin_url, curso_admin_url
from aula_uploader.videopack import (
    ALVO_BYTES,
    AVISO_BYTES,
    SUFIXO_PADRAO,
    TETO_BYTES,
    VideoInfo,
    probe_muitos,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
COOKIE_NAME = "aula_uploader_web"
ALLOWED_LOOPBACK = frozenset({"127.0.0.1", "localhost"})
LOG_LIMIT = 400
INBOX_PREFIX = "aula-uploader-web-"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


def _safe_relpath(rel: str, inbox: Path) -> Path:
    cleaned = (rel or "").replace("\\", "/").strip().lstrip("/")
    if not cleaned or cleaned.startswith("/"):
        raise ValueError("Caminho de arquivo inválido.")
    parts = Path(cleaned).parts
    if not parts or any(part in {"..", ""} for part in parts):
        raise ValueError("Caminho de arquivo inválido.")
    first = parts[0]
    if len(first) == 2 and first[1] == ":" and first[0].isalpha():
        raise ValueError("Caminho de arquivo inválido.")
    # ":" é ilegal em alguns sistemas; yt-dlp usa o dois-pontos largo.
    safe_parts = [part.replace(":", "：") for part in parts]
    dest = inbox.joinpath(*safe_parts).resolve()
    if not dest.is_relative_to(inbox.resolve()):
        raise ValueError("Caminho de arquivo inválido.")
    return dest


@dataclass
class LogEntry:
    id: int
    ts: str
    level: str
    message: str
    url: str | None = None
    url_label: str | None = None


@dataclass
class PortalDestino:
    curso_id: int | None = None
    curso_nome: str = ""
    capitulo: CapituloResumo | None = None


@dataclass
class WebState:
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    portal_key: str | None = None
    clients: dict[str, PortalClient] = field(default_factory=dict)
    destinos: dict[str, PortalDestino] = field(default_factory=dict)
    catalog: CatalogStore = field(default_factory=CatalogStore)
    inbox: Path | None = None
    extract_temp: Path | None = None
    source_label: str = ""
    aulas: list[AulaArquivo] = field(default_factory=list)
    uploading: bool = False
    job: dict[str, Any] = field(default_factory=dict)
    # Metadados do ffprobe por caminho absoluto, preenchidos em segundo plano.
    video_infos: dict[str, VideoInfo] = field(default_factory=dict)
    converting: bool = False
    convert_job: dict[str, Any] = field(default_factory=dict)
    conversao: Any = None
    convert_watch_stop: bool = False
    upload_watch_stop: bool = False
    nivo_watch_stop: bool = False
    nivo_watch_thread: threading.Thread | None = None
    # Arquivo convertido esperando aprovação: nome original -> caminho novo.
    convertidos: dict[str, str] = field(default_factory=dict)
    logs: deque[LogEntry] = field(default_factory=lambda: deque(maxlen=LOG_LIMIT))
    _log_id: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _subs: list[queue.Queue] = field(default_factory=list)

    @property
    def portal(self) -> PortalClient | None:
        if not self.portal_key:
            return None
        return self.clients.get(self.portal_key)

    def _slot(self) -> PortalDestino:
        key = self.portal_key
        if not key:
            return PortalDestino()
        slot = self.destinos.get(key)
        if slot is None:
            slot = PortalDestino()
            self.destinos[key] = slot
        return slot

    @property
    def curso_id(self) -> int | None:
        return self._slot().curso_id if self.portal_key else None

    @curso_id.setter
    def curso_id(self, value: int | None) -> None:
        if self.portal_key:
            self._slot().curso_id = value

    @property
    def curso_nome(self) -> str:
        return self._slot().curso_nome if self.portal_key else ""

    @curso_nome.setter
    def curso_nome(self, value: str) -> None:
        if self.portal_key:
            self._slot().curso_nome = value or ""

    @property
    def capitulo(self) -> CapituloResumo | None:
        return self._slot().capitulo if self.portal_key else None

    @capitulo.setter
    def capitulo(self, value: CapituloResumo | None) -> None:
        if self.portal_key:
            self._slot().capitulo = value

    def emit(
        self,
        message: str,
        *,
        level: str = "info",
        url: str | None = None,
        url_label: str | None = None,
    ) -> LogEntry:
        with self._lock:
            self._log_id += 1
            entry = LogEntry(
                id=self._log_id,
                ts=_now(),
                level=level,
                message=mask_text(message),
                url=url,
                url_label=url_label,
            )
            self.logs.append(entry)
            payload = {"type": "log", **_log_dict(entry)}
            for sub in list(self._subs):
                try:
                    sub.put_nowait(payload)
                except queue.Full:
                    pass
            return entry

    def publish(self, payload: dict[str, Any]) -> None:
        with self._lock:
            for sub in list(self._subs):
                try:
                    sub.put_nowait(payload)
                except queue.Full:
                    pass

    def subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: queue.Queue) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    def reset_inbox(self) -> Path:
        self.clear_inbox()
        self.inbox = _make_inbox_dir()
        return self.inbox

    def clear_inbox(self) -> None:
        if self.extract_temp is not None:
            cleanup_temp(self.extract_temp)
            self.extract_temp = None
        if self.inbox is not None:
            shutil.rmtree(self.inbox, ignore_errors=True)
            self.inbox = None
        self.aulas = []
        self.source_label = ""
        self.video_infos = {}
        self.convertidos = {}
        # Trocar a lista de vídeos zera o resultado da conversão anterior.
        if not self.converting:
            self.convert_job = {}
        try:
            from aula_uploader.videopack import cache_dir

            (cache_dir() / "workspace.json").unlink(missing_ok=True)
        except OSError:
            pass

    def close_portal(self, key: str | None = None) -> None:
        keys = [key] if key else list(self.clients)
        for portal_key in keys:
            client = self.clients.pop(portal_key, None)
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001, S110
                    pass
            self.destinos.pop(portal_key, None)
        if self.portal_key not in self.clients:
            self.portal_key = next(iter(self.clients), None)

    def set_client(self, key: str, client: PortalClient) -> None:
        old = self.clients.get(key)
        if old is not None and old is not client:
            try:
                old.close()
            except Exception:  # noqa: BLE001, S110
                pass
        self.clients[key] = client
        self.portal_key = key
        self.destinos[key] = PortalDestino()


def _log_dict(entry: LogEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "ts": entry.ts,
        "level": entry.level,
        "message": entry.message,
        "url": entry.url,
        "url_label": entry.url_label,
    }


def _aula_dict(aula: AulaArquivo, info: VideoInfo | None = None) -> dict[str, Any]:
    ext = aula.path.suffix.lstrip(".").upper()
    tamanho = aula.tamanho_bytes
    resolucao = ""
    if info is not None and info.resolucao:
        resolucao = info.resolucao
    origem = "convertido" if SUFIXO_PADRAO in aula.path.stem else "original"
    dados: dict[str, Any] = {
        "arquivo": aula.path.name,
        "rel": str(aula.path.name),
        "ordem": aula.ordem,
        "titulo": aula.titulo,
        "formato": ext,
        "tamanho": tamanho,
        "tamanho_fmt": format_bytes(tamanho),
        "ordem_inferida": aula.ordem_inferida,
        "resolucao": resolucao,
        "resolucao_fmt": resolucao,
        "largura": 0,
        "altura": 0,
        "duracao_fmt": "",
        "origem": origem,
        # O portal recusa arquivos grandes, então a interface avisa antes do envio.
        "grande": tamanho > AVISO_BYTES,
        "acima_do_limite": tamanho >= TETO_BYTES,
        "acima_do_teto": tamanho >= TETO_BYTES,
    }
    if info is not None:
        dados["resolucao"] = info.resolucao
        dados["resolucao_fmt"] = info.resolucao
        dados["largura"] = info.largura
        dados["altura"] = info.altura
        dados["duracao_fmt"] = format_duration(info.duracao) if info.duracao else ""
    return dados


def _aulas_payload(state: WebState) -> list[dict[str, Any]]:
    return [_aula_dict(a, state.video_infos.get(str(a.path))) for a in state.aulas]


def _acao_label(acao: Acao) -> str:
    return {
        Acao.CRIAR: "criar",
        Acao.ENVIAR: "enviar vídeo",
        Acao.PULAR: "já tem vídeo",
        Acao.FORCAR: "reenviar",
    }[acao]


def _product_payload(product: CatalogProduct) -> dict[str, str]:
    return {
        "id": product.id,
        "nome": product.nome,
        "nome_curto": product.nome_curto or product.nome,
        "portal": product.portal,
    }


def _course_payload(course: CatalogCourse) -> dict[str, Any]:
    return {
        "id": course.id,
        "nome": course.nome,
        "produto_ids": list(course.produto_ids),
        "capitulos": [
            {"id": chapter.id, "nome": chapter.nome, "ordem": chapter.ordem}
            for chapter in course.chapters
        ],
    }


def _catalog_payload(catalog: CatalogStore) -> dict[str, Any]:
    return {
        "produtos": [_product_payload(product) for product in catalog.products()],
        "cursos": [_course_payload(course) for course in catalog.courses()],
    }


class LoginBody(BaseModel):
    portal: str
    username: str = ""
    password: str = ""
    persist: bool = False


class PortalPickBody(BaseModel):
    portal: str


class SearchBody(BaseModel):
    q: str = ""


class CursoBody(BaseModel):
    curso: str
    nome: str = ""


class ProductBody(BaseModel):
    nome: str


class AssignProductBody(BaseModel):
    curso: str
    produto: str
    nome: str = ""


class CapituloBody(BaseModel):
    capitulo: str


class CreateCapituloBody(BaseModel):
    nome: str
    ordem: int = Field(ge=1)
    bunny: str


class PathBody(BaseModel):
    path: str


class PickFolderBody(BaseModel):
    append: bool = False


class LocalFileSpec(BaseModel):
    name: str
    size: int = 0


class MatchLocalBody(BaseModel):
    files: list[LocalFileSpec] = Field(default_factory=list)
    hint: str = ""
    reset: bool = True


class RemoveAulaBody(BaseModel):
    arquivo: str


class ConvertBody(BaseModel):
    arquivos: list[str] = Field(default_factory=list)
    engine: str = "hw"


class JobOpenBody(BaseModel):
    id: str = ""


class JobIdBody(BaseModel):
    id: str = ""


class ConvertApplyBody(BaseModel):
    arquivos: list[str] = Field(default_factory=list)


class AulaEdit(BaseModel):
    arquivo: str
    ordem: int = Field(ge=1)
    titulo: str


class PlanBody(BaseModel):
    force: bool = False
    aulas: list[AulaEdit] | None = None


class EditsBody(BaseModel):
    aulas: list[AulaEdit] = Field(default_factory=list)


class UploadBody(BaseModel):
    publicar: bool = False
    force: bool = False
    aulas: list[AulaEdit] | None = None


def create_app(
    state: WebState | None = None,
    *,
    allow_test_host: bool = False,
) -> FastAPI:
    state = state or WebState()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _restore_saved_sessions(state)
        _restore_workspace(state)
        _reattach_convert(state)
        _reattach_upload(state)
        _ensure_nivo_watcher(state)
        yield
        state.convert_watch_stop = True
        state.upload_watch_stop = True
        state.nivo_watch_stop = True

    app = FastAPI(
        title="aula-uploader",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.web = state
    allowed_hosts = set(ALLOWED_LOOPBACK)
    if allow_test_host:
        allowed_hosts.add("testserver")

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable):
        host = (request.headers.get("host") or "").split(":")[0].strip().lower()
        if host not in allowed_hosts:
            return JSONResponse({"detail": "Só aceito 127.0.0.1."}, status_code=403)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if (parsed.hostname or "").lower() not in ALLOWED_LOOPBACK:
                return JSONResponse({"detail": "Origin recusada."}, status_code=403)
        path = request.url.path
        if path.startswith("/static/"):
            if not _authorized(request, state):
                return JSONResponse({"detail": "Sem sessão."}, status_code=403)
            return await call_next(request)
        if path == "/":
            return await call_next(request)
        if path.startswith("/api/") and not _authorized(request, state):
            return JSONResponse({"detail": "Abra o link que o terminal mostrou."}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, k: str | None = None) -> HTMLResponse:
        if not _authorized(request, state, k):
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>aula-uploader</title>"
                "<body style='font-family:system-ui;padding:2rem;max-width:40rem'>"
                "<h1>Interface local</h1>"
                "<p>Esta página só abre a partir do comando "
                "<code>aula-uploader web</code> neste computador.</p>",
                status_code=403,
            )
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        response = HTMLResponse(
            html,
            headers={"Cache-Control": "no-store, max-age=0"},
        )
        if k == state.token:
            response.set_cookie(
                COOKIE_NAME,
                state.token,
                httponly=True,
                samesite="strict",
                secure=False,
                path="/",
            )
        return response

    @app.get("/api/bootstrap")
    async def bootstrap() -> dict[str, Any]:
        portals = [
            {
                "key": key,
                "label": label,
                "url": DEFAULT_URLS[key],
                "has_session": has_saved_session(key),
            }
            for key, label in PORTAL_LABELS.items()
        ]
        return {
            "version": __version__,
            "platform": _client_platform(),
            "portals": portals,
            "session": _session_payload(state),
            "logs": [_log_dict(entry) for entry in state.logs],
        }

    @app.post("/api/login")
    async def login(body: LoginBody) -> dict[str, Any]:
        try:
            portal_key = resolve_portal_key(body.portal)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if portal_key not in ALLOWED_HOSTS:
            raise HTTPException(400, "Portal inválido.")

        username = body.username.strip() or session_username(portal_key)
        password = body.password
        use_saved = has_saved_session(portal_key)
        if not use_saved and (not username or not password):
            raise HTTPException(400, "Informe e-mail e senha deste portal.")

        saved_path = session_path(portal_key) if use_saved else None
        if saved_path:
            ensure_secure_file(saved_path)

        portal = PortalClient(
            DEFAULT_URLS[portal_key],
            username,
            password,
            session_path=saved_path,
        )
        if use_saved and not body.persist:
            # Lê cookies já gravados; só regrava se persist=True.
            portal.session_path = None
        try:
            portal.ensure_authenticated(
                log=lambda msg: state.emit(msg, level="info"),
                force=not use_saved,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                portal.close()
            except Exception:  # noqa: BLE001, S110
                pass
            if use_saved:
                clear_session(portal_key)
            raise HTTPException(401, mask_text(str(exc))) from exc

        if body.persist:
            path = enable_session_persistence(portal, portal_key)
            state.emit(f"Sessão salva em {path}", level="ok")
        remember_last_portal(portal_key)
        state.set_client(portal_key, portal)
        label = PORTAL_LABELS[portal_key]
        state.emit(
            f"Login OK em {label}",
            level="ok",
            url=DEFAULT_URLS[portal_key],
            url_label="Abrir portal",
        )
        return _session_payload(state)

    @app.post("/api/logout")
    async def logout() -> dict[str, Any]:
        current = state.portal_key
        label = PORTAL_LABELS.get(current or "", "portal")
        if current:
            clear_session(current)
        state.close_portal(current)
        state.emit(f"Saiu de {label}.", level="warn")
        return _session_payload(state)

    @app.post("/api/portal/select")
    async def select_portal(body: PortalPickBody) -> dict[str, Any]:
        try:
            portal_key = resolve_portal_key(body.portal)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if portal_key not in state.clients:
            raise HTTPException(401, "Faça login neste portal primeiro.")
        state.portal_key = portal_key
        return _session_payload(state)

    @app.get("/api/catalog")
    async def catalog() -> dict[str, Any]:
        _require_portal(state)
        return _catalog_payload(state.catalog)

    @app.post("/api/produtos")
    async def create_produto(body: ProductBody) -> dict[str, Any]:
        _require_portal(state)
        try:
            product = state.catalog.ensure_product(body.nome)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        state.emit(f"Produto local: {product.nome}.", level="ok")
        return {"produto": _product_payload(product), **_catalog_payload(state.catalog)}

    @app.post("/api/produtos/assign")
    async def assign_produto(body: AssignProductBody) -> dict[str, Any]:
        _require_portal(state)
        try:
            curso_id = parse_curso_id(body.curso)
            course = state.catalog.assign_product(
                curso_id,
                body.produto.strip(),
                nome=body.nome,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        state.emit(
            f"Vinculou {course.nome} (ID {course.id}) a um produto. Só neste computador.",
            level="ok",
        )
        return {"curso": _course_payload(course), **_catalog_payload(state.catalog)}

    @app.post("/api/produtos/unassign")
    async def unassign_produto(body: AssignProductBody) -> dict[str, Any]:
        _require_portal(state)
        try:
            curso_id = parse_curso_id(body.curso)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        course = state.catalog.unassign_product(curso_id, body.produto.strip())
        if course is None:
            raise HTTPException(404, "Curso ainda não está no catálogo local.")
        return {"curso": _course_payload(course), **_catalog_payload(state.catalog)}

    @app.post("/api/cursos/search")
    async def search_cursos(body: SearchBody) -> dict[str, Any]:
        portal = _require_portal(state)
        query = body.q.strip()
        products = state.catalog.products_map()
        mapped = [
            {
                "id": course.id,
                "nome": course.nome,
                "fonte": "mapeado",
                "produto_ids": list(course.produto_ids),
            }
            for course in state.catalog.courses()
            if course_matches(course, query, products)
        ]
        remote: list[dict[str, Any]] = []
        if query:
            if query.isdigit():
                curso_id = int(query)
                state.emit(f"Consultando curso ID {curso_id} no portal…")
                try:
                    curso = portal.inspect_curso(curso_id)
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(502, mask_text(str(exc))) from exc
                remote = [
                    {
                        "id": curso.id,
                        "nome": curso.nome,
                        "fonte": "portal",
                        "produto_ids": [],
                    }
                ]
                state.emit(
                    f"Curso encontrado: {curso.nome} (ID {curso.id}).",
                    level="ok",
                    url=curso_admin_url(portal.base_url, curso.id),
                    url_label="Abrir curso no admin",
                )
            else:
                try:
                    resolved = resolve_curso_query(query)
                except ValueError:
                    resolved = query
                if isinstance(resolved, str):
                    state.emit(f"Buscando curso “{resolved}” no portal…")
                    try:
                        encontrados = portal.buscar_cursos(resolved)
                    except Exception as exc:  # noqa: BLE001
                        raise HTTPException(502, mask_text(str(exc))) from exc
                    remote = [
                        {
                            "id": c.id,
                            "nome": c.nome,
                            "fonte": "portal",
                            "produto_ids": [],
                        }
                        for c in encontrados
                    ]
                    state.emit(
                        f"{len(remote)} curso(s) encontrado(s) para “{resolved}”.",
                        level="ok" if remote else "warn",
                        url=f"{portal.base_url}/admin/curso/?string={quote(resolved)}",
                        url_label="Ver busca no admin",
                    )
        return {"cursos": _merge_courses(mapped, remote)}

    @app.post("/api/cursos/select")
    async def select_curso(body: CursoBody) -> dict[str, Any]:
        portal = _require_portal(state)
        try:
            curso_id = parse_curso_id(body.curso)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            if body.nome.strip():
                curso = CursoInfo(id=curso_id, nome=body.nome.strip())
            else:
                curso = portal.inspect_curso(curso_id)
            capitulos = portal.list_capitulos(curso_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, mask_text(str(exc))) from exc
        state.curso_id = curso.id
        state.curso_nome = curso.nome
        state.capitulo = None
        url = curso_admin_url(portal.base_url, curso.id)
        state.emit(
            f"Consultou o curso {curso.nome} (ID {curso.id}) · {len(capitulos)} capítulo(s).",
            level="ok",
            url=url,
            url_label="Abrir curso no admin",
        )
        return {
            "curso": {"id": curso.id, "nome": curso.nome, "url": url},
            "capitulos": [
                {"id": ch.id, "nome": ch.nome, "ordem": ch.ordem} for ch in capitulos
            ],
        }

    @app.post("/api/capitulos/select")
    async def select_capitulo(body: CapituloBody) -> dict[str, Any]:
        portal = _require_portal(state)
        try:
            capitulo_id = parse_capitulo_id(body.capitulo)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        anterior_id = state.capitulo.id if state.capitulo is not None else None
        try:
            capitulo = portal.inspect_capitulo(capitulo_id)
            existentes = portal.listar_conteudos_tabela(capitulo_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, mask_text(str(exc))) from exc
        job_cap = (state.job or {}).get("capitulo_id")
        aulas_de_outro = bool(
            state.aulas
            and job_cap is not None
            and int(job_cap) != int(capitulo_id)
        )
        state.capitulo = capitulo
        if capitulo.curso_id:
            state.curso_id = capitulo.curso_id
            state.curso_nome = capitulo.curso_nome or state.curso_nome
        # Workspace do capítulo novo não herda vídeos do envio anterior.
        if (anterior_id is not None and anterior_id != capitulo.id) or aulas_de_outro:
            state.clear_inbox()
            state.emit(
                f"Lista limpa para o capítulo {capitulo.nome}. "
                "Adicione a pasta deste capítulo — o outro envio segue na barra.",
                level="info",
            )
        _hydrate_job_from_disk(state)
        # Job de outro capítulo (ex.: Nivo) continua visível via /api/jobs.
        outros = [
            web_job_view(j)
            for j in list_upload_jobs()
            if int(j.get("capitulo_id") or 0) != capitulo.id
        ]
        if outros and not (state.job and state.job.get("items")):
            # Prefere mostrar na barra o envio ainda processando, se houver.
            em_andamento = next(
                (
                    j
                    for j in outros
                    if (j.get("fase") or "") in {"uploading", "processando", "queued"}
                ),
                outros[0],
            )
            state.job = em_andamento
        url = capitulo_admin_url(portal.base_url, capitulo.id)
        state.emit(
            f"Consultou o capítulo {capitulo.nome} (ID {capitulo.id}) · "
            f"{len(existentes)} aula(s) já no portal.",
            level="ok",
            url=url,
            url_label="Abrir capítulo no admin",
        )
        payload = _capitulo_payload(state, existentes)
        payload["job"] = state.job
        payload["uploading"] = state.uploading
        payload["aulas"] = _aulas_payload(state)
        payload["fonte"] = state.source_label
        payload["jobs"] = [web_job_view(j) for j in list_upload_jobs()]
        return payload

    @app.post("/api/capitulos/create")
    async def create_capitulo(body: CreateCapituloBody) -> dict[str, Any]:
        portal = _require_portal(state)
        if state.curso_id is None:
            raise HTTPException(400, "Escolha o curso antes de criar o capítulo.")
        try:
            bunny_id = parse_bunny_folder_id(body.bunny)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        nome = body.nome.strip()
        if not nome:
            raise HTTPException(400, "Informe o nome do capítulo.")
        try:
            created = portal.create_capitulo(
                state.curso_id,
                nome,
                body.ordem,
                bunny_folder_id=bunny_id,
            )
            capitulo = portal.inspect_capitulo(created.id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, mask_text(str(exc))) from exc
        state.capitulo = capitulo
        url = capitulo_admin_url(portal.base_url, capitulo.id)
        state.emit(
            f"Criou o capítulo {capitulo.nome} (ID {capitulo.id}).",
            level="ok",
            url=url,
            url_label="Abrir capítulo no admin",
        )
        existentes = portal.listar_conteudos_tabela(capitulo.id)
        return _capitulo_payload(state, existentes)

    @app.post("/api/videos/clear")
    async def clear_videos() -> dict[str, Any]:
        state.clear_inbox()
        state.emit("Lista de vídeos limpa.", level="warn")
        return {"aulas": [], "fonte": ""}

    @app.post("/api/workspace/new")
    async def new_workspace() -> dict[str, Any]:
        """Limpa a lista para um novo envio; o job anterior continua na barra."""
        if _arquivo_subindo():
            job = state.job or {}
            cap = state.capitulo.id if state.capitulo else None
            job_cap = job.get("capitulo_id")
            if cap is not None and job_cap is not None and int(job_cap) == int(cap):
                raise HTTPException(
                    409,
                    "Ainda está subindo arquivo deste capítulo. "
                    "Espere o upload terminar (Nivo pode continuar).",
                )
        state.clear_inbox()
        state.emit(
            "Novo envio: lista limpa. Adicione a pasta deste capítulo.",
            level="ok",
        )
        return {
            "aulas": [],
            "fonte": "",
            "job": state.job,
            "jobs": [web_job_view(j) for j in list_upload_jobs()],
            "uploading": _arquivo_subindo(),
            "capitulo": (
                {
                    "id": state.capitulo.id,
                    "nome": state.capitulo.nome,
                    "curso_id": state.capitulo.curso_id,
                    "curso_nome": state.capitulo.curso_nome,
                }
                if state.capitulo
                else None
            ),
        }

    @app.post("/api/videos/remove")
    async def remove_video(body: RemoveAulaBody) -> dict[str, Any]:
        if _arquivo_subindo():
            raise HTTPException(409, "Não dá para tirar aula no meio do envio de arquivo.")
        nome = Path(body.arquivo).name
        if nome != body.arquivo.strip() or nome in {"", ".", ".."}:
            raise HTTPException(400, "Arquivo inválido.")
        if not any(aula.path.name == nome for aula in state.aulas):
            raise HTTPException(404, "Essa aula não está na lista.")
        if state.inbox is not None:
            inbox = state.inbox.resolve()
            for path in list(state.inbox.rglob("*")):
                if not path.is_file() or path.name != nome:
                    continue
                resolved = path.resolve()
                if resolved.is_relative_to(inbox):
                    resolved.unlink(missing_ok=True)
            _scan_inbox(state)
        else:
            state.aulas = [aula for aula in state.aulas if aula.path.name != nome]
        state.emit(f"Tirou {nome} da lista.", level="warn")
        return {"aulas": _aulas_payload(state), "fonte": state.source_label}

    @app.post("/api/videos/pick-folder")
    async def pick_folder(body: PickFolderBody) -> dict[str, Any]:
        if _arquivo_subindo():
            raise HTTPException(
                409,
                "Espere terminar o upload de arquivo em andamento. "
                "Processar no Nivo não trava escolher outra pasta.",
            )
        try:
            path = await asyncio.to_thread(_pick_folder_native)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        if path is None:
            return {
                "cancelled": True,
                "aulas": _aulas_payload(state),
                "fonte": state.source_label,
            }
        return _videos_from_path_response(state, path, append=body.append)

    @app.post("/api/videos/pick-files")
    async def pick_files(body: PickFolderBody) -> dict[str, Any]:
        if _arquivo_subindo():
            raise HTTPException(409, "Não dá para mudar a lista no meio do envio de arquivo.")
        try:
            paths = await asyncio.to_thread(_pick_files_native)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        if paths is None:
            return {
                "cancelled": True,
                "aulas": _aulas_payload(state),
                "fonte": state.source_label,
            }
        return _videos_from_files_response(
            state,
            paths,
            append=body.append and bool(state.aulas or state.inbox),
        )

    @app.post("/api/videos/path")
    async def videos_from_path(body: PathBody) -> dict[str, Any]:
        path = normalize_user_path(body.path)
        return _videos_from_path_response(state, path, append=False)

    @app.post("/api/videos/match-local")
    async def match_local_videos(body: MatchLocalBody) -> dict[str, Any]:
        if _arquivo_subindo():
            raise HTTPException(409, "Não dá para mudar a lista no meio do envio de arquivo.")
        hint = normalize_user_path(body.hint) if body.hint.strip() else None
        found, missing = _match_local_files(state, body.files, hint)
        if not found:
            return {
                "matched": [],
                "missing": [spec.name for spec in body.files],
                "aulas": _aulas_payload(state),
                "fonte": state.source_label,
            }
        if missing:
            return {
                "matched": [p.name for p in found],
                "missing": missing,
                "aulas": _aulas_payload(state),
                "fonte": state.source_label,
            }
        return _videos_from_files_response(
            state,
            found,
            append=not body.reset and bool(state.aulas or state.inbox),
        )

    @app.post("/api/videos/upload")
    async def videos_upload(
        files: list[UploadFile] = File(default_factory=list),
        rels: list[str] = Form(default_factory=list),
        reset: str = Form("0"),
    ) -> dict[str, Any]:
        inbox = _ensure_inbox(state, append=reset != "1")
        if len(rels) not in {0, len(files)}:
            raise HTTPException(400, "Lista de caminhos não bate com os arquivos.")
        saved = 0
        for index, upload in enumerate(files):
            rel = rels[index] if rels else (upload.filename or f"video-{index}")
            try:
                dest = _safe_relpath(rel, inbox)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as out:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            saved += 1
        aulas = _scan_inbox(state)
        state.emit(
            f"Recebeu {saved} arquivo(s). {len(aulas)} vídeo(s) prontos.",
            level="ok" if aulas else "info",
        )
        return {"aulas": _aulas_payload(state), "fonte": state.source_label}

    @app.post("/api/videos/edits")
    async def save_video_edits(body: EditsBody) -> dict[str, Any]:
        if _arquivo_subindo():
            raise HTTPException(409, "Espere o upload de arquivo terminar para editar a lista.")
        _apply_edits(state, body.aulas)
        return {"aulas": _aulas_payload(state)}

    @app.post("/api/videos/convert")
    async def convert_videos(body: ConvertBody) -> dict[str, Any]:
        if _arquivo_subindo():
            raise HTTPException(409, "Espere o upload de arquivo terminar para converter.")
        if state.converting:
            raise HTTPException(409, "Já tem uma conversão rolando.")
        alvos = _aulas_por_nome(state, body.arquivos)
        if not alvos:
            raise HTTPException(400, "Nenhum vídeo para converter.")
        engine = "x264" if body.engine == "x264" else "hw"
        job = novo_job(
            alvos=alvos,
            engine=engine,
            fonte=state.source_label,
            lista=list(state.aulas),
        )
        job["convertidos"] = dict(state.convertidos)
        start_convert_worker(job)
        _apply_convert_job(state, job)
        state.converting = True
        state.convert_watch_stop = False
        state.emit(
            f"Comprimindo {len(alvos)} vídeo(s) em segundo plano "
            f"para caber em {format_bytes(ALVO_BYTES)}.",
            level="info",
        )
        state.publish({"type": "convert", "convert": state.convert_job, "converting": True})
        threading.Thread(
            target=_watch_convert_job,
            args=(state,),
            daemon=True,
            name="web-convert-watch",
        ).start()
        return {"ok": True, "convert": state.convert_job}

    @app.post("/api/videos/convert/cancel")
    async def cancel_convert() -> dict[str, Any]:
        if not state.converting and not worker_vivo(state.convert_job):
            return {"ok": True, "convert": state.convert_job}
        cancel_convert_worker(state.convert_job)
        state.emit("Cancelando a conversão em segundo plano…", level="warn")
        return {"ok": True, "convert": state.convert_job}

    @app.post("/api/videos/convert/apply")
    async def apply_convert(body: ConvertApplyBody) -> dict[str, Any]:
        if _arquivo_subindo():
            raise HTTPException(409, "Não dá para trocar arquivos no meio do envio.")
        nomes = body.arquivos or list(state.convertidos)
        trocados = 0
        for nome in nomes:
            destino = state.convertidos.get(nome)
            if not destino:
                continue
            novo = Path(destino)
            if not novo.is_file():
                continue
            for indice, aula in enumerate(state.aulas):
                if aula.path.name != nome:
                    continue
                state.aulas[indice] = AulaArquivo(
                    path=novo,
                    ordem=aula.ordem,
                    titulo=aula.titulo,
                    ordem_inferida=aula.ordem_inferida,
                    tamanho_bytes=novo.stat().st_size,
                )
                trocados += 1
            state.convertidos.pop(nome, None)
            item = _convert_item(state, nome)
            if item is not None:
                item["status"] = "aplicado"
            if state.convert_job:
                save_convert_job({**state.convert_job, "convertidos": dict(state.convertidos)})
        if trocados:
            state.emit(f"Usando a versão comprimida de {trocados} vídeo(s).", level="ok")
            _start_probe(state)
        return {"aulas": _aulas_payload(state), "fonte": state.source_label, "trocados": trocados}

    @app.get("/api/videos/preview")
    async def preview_video(request: Request, arquivo: str) -> Response:
        path = _resolver_video(state, arquivo)
        if path is None:
            raise HTTPException(404, "Vídeo não encontrado.")
        return _range_response(path, request.headers.get("range"))

    @app.post("/api/plan")
    async def make_plan(body: PlanBody) -> dict[str, Any]:
        portal = _require_portal(state)
        if state.capitulo is None:
            raise HTTPException(400, "Escolha o capítulo antes de montar o plano.")
        _apply_edits(state, body.aulas)
        if not state.aulas:
            raise HTTPException(400, "Solte uma pasta de vídeos (ou um .zip) primeiro.")
        try:
            existentes = portal.listar_conteudos_tabela(state.capitulo.id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, mask_text(str(exc))) from exc
        plano = montar_plano(state.aulas, existentes, force=body.force)
        duplicados = titulos_duplicados(state.aulas)
        url = capitulo_admin_url(portal.base_url, state.capitulo.id)
        state.emit(
            f"Montou o plano: {len(plano)} aula(s) contra o capítulo {state.capitulo.nome}.",
            level="ok",
            url=url,
            url_label="Conferir capítulo",
        )
        return {
            "plano": _plano_payload(plano),
            "duplicados": duplicados,
            "capitulo": _capitulo_payload(state, existentes)["capitulo"],
        }

    @app.post("/api/upload")
    async def start_upload(body: UploadBody) -> dict[str, Any]:
        portal = _require_portal(state)
        if state.capitulo is None:
            raise HTTPException(400, "Escolha o capítulo antes de enviar.")
        # Upload de arquivo: só um por vez. Nivo processando não bloqueia.
        if _arquivo_subindo():
            raise HTTPException(
                409,
                "Ainda tem aula subindo arquivo pro portal. "
                "Quando terminar o upload (mesmo que o Nivo continue), dá pra enfileirar outro.",
            )
        if not pode_iniciar_upload():
            raise HTTPException(
                409,
                "Outro envio ainda está subindo arquivo. Espere terminar o upload.",
            )
        _apply_edits(state, body.aulas)
        if not state.aulas:
            raise HTTPException(400, "Não há vídeos para enviar.")
        # O portal recusa arquivos deste tamanho, e a recusa só apareceria depois
        # de uma subida longa. Melhor barrar aqui e mandar converter.
        gigantes = [a.path.name for a in state.aulas if a.tamanho_bytes >= TETO_BYTES]
        if gigantes:
            raise HTTPException(
                400,
                f"{len(gigantes)} vídeo(s) passam de {format_bytes(TETO_BYTES)} e seriam "
                f"recusados pelo portal. Converta antes: {', '.join(gigantes[:3])}"
                + ("…" if len(gigantes) > 3 else ""),
            )
        try:
            existentes = portal.listar_conteudos_tabela(state.capitulo.id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, mask_text(str(exc))) from exc
        plano = montar_plano(state.aulas, existentes, force=body.force)
        pasta = state.aulas[0].path.parent
        status = "1" if body.publicar else "0"
        if not state.portal_key:
            raise HTTPException(401, "Faça login no portal primeiro.")
        job = novo_upload_job(
            portal=state.portal_key,
            capitulo_id=state.capitulo.id,
            pasta=pasta,
            fonte=state.source_label or str(pasta),
            status_criacao=status,
            force=body.force,
            plano=plano,
            capitulo_nome=state.capitulo.nome,
            curso_nome=state.capitulo.curso_nome or state.curso_nome,
            curso_id=state.capitulo.curso_id or state.curso_id,
            url=capitulo_admin_url(portal.base_url, state.capitulo.id),
        )
        start_upload_worker(job)
        upsert_job(job)
        state.job = web_job_view(job)
        state.uploading = True
        state.upload_watch_stop = False
        state.emit(
            f"Começou o envio de {len(plano)} aula(s) · {state.capitulo.nome}",
            level="info",
            url=job.get("url"),
            url_label="Abrir capítulo",
        )
        state.publish(
            {
                "type": "job",
                "job": state.job,
                "uploading": True,
                "jobs": [web_job_view(j) for j in list_upload_jobs()],
            }
        )
        threading.Thread(
            target=_watch_upload_job,
            args=(state,),
            daemon=True,
            name="web-upload-watch",
        ).start()
        return {
            "ok": True,
            "job": state.job,
            "jobs": [web_job_view(j) for j in list_upload_jobs()],
        }

    @app.get("/api/job")
    async def job() -> dict[str, Any]:
        _hydrate_job_from_disk(state)
        _ensure_nivo_watcher(state)
        return {
            "uploading": state.uploading,
            "job": state.job,
            "jobs": [web_job_view(j) for j in list_upload_jobs()],
        }

    @app.post("/api/nivo/refresh")
    async def nivo_refresh() -> dict[str, Any]:
        """Consulta o portal e marca aulas prontas no Nivo."""
        mudou = _refresh_nivo_status(state)
        _ensure_nivo_watcher(state)
        return {
            "ok": True,
            "updated": mudou,
            "job": state.job,
            "jobs": [web_job_view(j) for j in list_upload_jobs()],
        }

    @app.post("/api/jobs/open")
    async def open_job(body: JobOpenBody) -> dict[str, Any]:
        """Abre o detalhe de um envio: lista as aulas e o capítulo."""
        job_id = (body.id or "").strip()
        job = get_upload_job_by_id(job_id) if job_id else None
        if job is None and state.job and (
            not job_id or str(state.job.get("id") or "") == job_id
        ):
            job = dict(state.job)
        if job is None:
            if state.portal_key and state.capitulo is not None:
                st = UploadState.load(state.portal_key, state.capitulo.id)
                if st:
                    job = _job_from_upload_state(st, state=state)
        if not job:
            raise HTTPException(404, "Envio não encontrado.")
        view = web_job_view(job) if "fase" not in job else job
        if not view.get("fase"):
            view = {**view, "fase": job_fase(view)}
        state.job = view
        job_cap = job.get("capitulo_id")
        mesmo_capitulo = (
            state.capitulo is not None
            and job_cap is not None
            and int(job_cap) == int(state.capitulo.id)
        )
        # Só troca a lista de vídeos se o envio for do capítulo atual.
        # Assim dá para olhar o Nivo antigo sem perder o workspace do Tech Week.
        if mesmo_capitulo or state.capitulo is None:
            aulas = _aulas_from_job(job if job.get("items") else view)
            if aulas:
                state.aulas = aulas
                state.source_label = str(job.get("fonte") or job.get("pasta") or "")
            if cap_id := job_cap:
                if state.portal is not None and (
                    state.capitulo is None or state.capitulo.id != int(cap_id)
                ):
                    try:
                        state.capitulo = state.portal.inspect_capitulo(int(cap_id))
                        if state.capitulo.curso_id:
                            state.curso_id = state.capitulo.curso_id
                            state.curso_nome = (
                                state.capitulo.curso_nome or state.curso_nome
                            )
                    except Exception:  # noqa: BLE001, S110
                        pass
        return {
            "job": state.job,
            "aulas": _aulas_payload(state),
            "fonte": state.source_label,
            "session": _session_payload(state),
            "switched": mesmo_capitulo or state.capitulo is None,
        }

    @app.post("/api/jobs/archive")
    async def archive_job_api(body: JobIdBody) -> dict[str, Any]:
        """Arquiva o envio: sai da fila ativa e vai para o histórico (só no app)."""
        job_id = (body.id or "").strip()
        if not job_id or job_id.startswith("workspace-"):
            raise HTTPException(400, "Este item ainda não é um envio arquivável.")
        job = get_upload_job_by_id(job_id)
        if not job:
            raise HTTPException(404, "Envio não encontrado.")
        if job_fase(job) == "uploading":
            raise HTTPException(400, "Espere o upload de arquivo terminar para arquivar.")
        archived = archive_upload_job(job_id)
        if not archived:
            raise HTTPException(404, "Envio não encontrado.")
        if state.job and str(state.job.get("id") or "") == job_id:
            state.job = web_job_view(archived)
        return {
            "ok": True,
            "job": web_job_view(archived),
            "jobs": [web_job_view(j) for j in list_upload_jobs()],
        }

    @app.post("/api/jobs/unarchive")
    async def unarchive_job_api(body: JobIdBody) -> dict[str, Any]:
        """Volta um envio do histórico para concluídos / andamento."""
        job_id = (body.id or "").strip()
        restored = unarchive_upload_job(job_id)
        if not restored:
            raise HTTPException(404, "Envio não encontrado.")
        if state.job and str(state.job.get("id") or "") == job_id:
            state.job = web_job_view(restored)
        return {
            "ok": True,
            "job": web_job_view(restored),
            "jobs": [web_job_view(j) for j in list_upload_jobs()],
        }

    @app.post("/api/jobs/delete")
    async def delete_job_api(body: JobIdBody) -> dict[str, Any]:
        """Remove o envio só deste app (índice + estado local). Não mexe no portal."""
        job_id = (body.id or "").strip()
        if not job_id or job_id.startswith("workspace-"):
            raise HTTPException(400, "Nada para excluir neste item.")
        job = get_upload_job_by_id(job_id)
        if not job:
            raise HTTPException(404, "Envio não encontrado.")
        if job_fase(job) == "uploading":
            raise HTTPException(400, "Não dá para excluir enquanto o arquivo sobe.")
        portal = str(job.get("portal") or "")
        cap_id = job.get("capitulo_id")
        if portal and cap_id is not None:
            from aula_uploader.session import state_dir

            path = state_dir() / f"upload-{portal}-{int(cap_id)}.json"
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if not delete_upload_job(job_id):
            raise HTTPException(404, "Envio não encontrado.")
        if state.job and str(state.job.get("id") or "") == job_id:
            state.job = None
            state.uploading = False
            state.aulas = []
            state.source_label = ""
            _persist_workspace(state)
        return {
            "ok": True,
            "jobs": [web_job_view(j) for j in list_upload_jobs()],
            "session": _session_payload(state),
            "aulas": _aulas_payload(state),
        }

    @app.get("/api/logs")
    async def logs() -> dict[str, Any]:
        return {"logs": [_log_dict(entry) for entry in state.logs]}

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        sub = state.subscribe()

        async def generate() -> AsyncIterator[str]:
            try:
                snapshot = json.dumps(
                    {
                        "type": "hello",
                        "job": state.job,
                        "jobs": [web_job_view(j) for j in list_upload_jobs()],
                        "uploading": state.uploading,
                        "convert": state.convert_job,
                        "converting": state.converting,
                    },
                    ensure_ascii=False,
                )
                yield f"data: {snapshot}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.to_thread(sub.get, True, 1.0)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            finally:
                state.unsubscribe(sub)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _client_platform() -> dict[str, Any]:
    """Metadados do SO onde o servidor roda (a interface é sempre local)."""
    if sys.platform == "darwin":
        return {"os": "mac", "folder_picker": True}
    if sys.platform == "win32":
        return {"os": "windows", "folder_picker": False}
    return {"os": "linux", "folder_picker": False}


def _authorized(request: Request, state: WebState, k: str | None = None) -> bool:
    # A interface é local (só 127.0.0.1 / localhost). O token `k` era só uma
    # camada extra, mas agora a exigência foi removida.
    return True


def _require_portal(state: WebState) -> PortalClient:
    if state.portal is None or state.portal_key is None:
        raise HTTPException(401, "Faça login no portal primeiro.")
    return state.portal


def _arquivo_subindo() -> bool:
    """True só enquanto sobe arquivo; Nivo processando não conta."""
    job = load_upload_job()
    return bool(upload_worker_vivo(job) and job_fase(job or {}) == "uploading")


def _session_payload(state: WebState) -> dict[str, Any]:
    _hydrate_job_from_disk(state)
    portal = state.portal
    capitulo = state.capitulo
    payload: dict[str, Any] = {
        "autenticado": portal is not None,
        "autenticados": list(state.clients),
        "portal": state.portal_key,
        "portal_label": PORTAL_LABELS.get(state.portal_key or "", ""),
        "portal_url": portal.base_url if portal else "",
        "curso_id": state.curso_id,
        "curso_nome": state.curso_nome,
        "curso_url": (
            curso_admin_url(portal.base_url, state.curso_id)
            if portal and state.curso_id
            else ""
        ),
        "capitulo": None,
        "fonte": state.source_label,
        "aulas": _aulas_payload(state),
        "uploading": _arquivo_subindo(),
        "job": state.job,
        "jobs": [web_job_view(j) for j in list_upload_jobs()],
        "converting": state.converting,
        "convert": state.convert_job,
        "convertidos": dict(state.convertidos),
        "limites": {
            "alvo": ALVO_BYTES,
            "alvo_fmt": format_bytes(ALVO_BYTES),
            "aviso": AVISO_BYTES,
            "teto": TETO_BYTES,
            "teto_fmt": format_bytes(TETO_BYTES),
        },
    }
    if portal and capitulo is not None:
        payload["capitulo"] = {
            "id": capitulo.id,
            "nome": capitulo.nome,
            "curso_id": capitulo.curso_id,
            "curso_nome": capitulo.curso_nome,
            "url": capitulo_admin_url(portal.base_url, capitulo.id),
        }
    return payload


def _capitulo_payload(state: WebState, existentes: list) -> dict[str, Any]:
    portal = _require_portal(state)
    assert state.capitulo is not None
    return {
        "capitulo": {
            "id": state.capitulo.id,
            "nome": state.capitulo.nome,
            "curso_id": state.capitulo.curso_id,
            "curso_nome": state.capitulo.curso_nome,
            "url": capitulo_admin_url(portal.base_url, state.capitulo.id),
        },
        "existentes": [
            {
                "id": linha.id,
                "titulo": linha.titulo,
                "tem_video": linha.tem_video,
                "status": linha.status,
            }
            for linha in existentes
        ],
    }


def _merge_courses(
    mapped: list[dict[str, Any]], remote: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}
    for item in mapped + remote:
        current = by_id.get(item["id"])
        if current is None:
            by_id[item["id"]] = item
        elif item.get("fonte") == "mapeado":
            by_id[item["id"]] = item
    return list(by_id.values())


def _make_inbox_dir() -> Path:
    path = Path(tempfile.mkdtemp(prefix=INBOX_PREFIX))
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def _adopt_aulas_into_new_inbox(state: WebState) -> Path:
    aulas = list(state.aulas)
    new_inbox = _make_inbox_dir()
    for aula in aulas:
        if not aula.path.is_file():
            continue
        try:
            _link_or_copy(aula.path, new_inbox / aula.path.name)
        except OSError:
            continue
    old_extract = state.extract_temp
    state.inbox = new_inbox
    state.extract_temp = None
    state.source_label = state.source_label or "pasta arrastada"
    if old_extract is not None:
        cleanup_temp(old_extract)
    return new_inbox


def _ensure_inbox(state: WebState, *, append: bool) -> Path:
    if append and state.inbox is not None:
        return state.inbox
    if append and state.aulas:
        return _adopt_aulas_into_new_inbox(state)
    inbox = state.reset_inbox()
    state.source_label = "pasta arrastada"
    return inbox


def _pick_folder_native() -> Path | None:
    if sys.platform == "darwin":
        pass
    elif sys.platform == "win32":
        raise RuntimeError(
            "No Windows, cole o caminho da pasta abaixo (ex.: C:\\Users\\…\\aulas)."
        )
    else:
        raise RuntimeError("Neste sistema, cole o caminho absoluto da pasta.")
    osascript = shutil.which("osascript")
    if osascript is None:
        raise RuntimeError("Não achei o seletor nativo de pastas.")
    try:
        proc = subprocess.run(  # noqa: S603 - argv fixo, sem shell
            [
                osascript,
                "-e",
                'POSIX path of (choose folder with prompt "Escolha a pasta das aulas")',
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Não achei o seletor nativo de pastas.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("O seletor de pasta demorou demais.") from exc
    if proc.returncode != 0:
        err = (proc.stderr or "").casefold()
        if "user canceled" in err or "-128" in err or proc.returncode == 1:
            return None
        raise RuntimeError((proc.stderr or "Não deu para escolher a pasta.").strip())
    raw = proc.stdout.strip().rstrip("/")
    if not raw:
        return None
    return Path(raw)


def _pick_files_native() -> list[Path] | None:
    if sys.platform != "darwin":
        raise RuntimeError("Neste sistema, cole o caminho da pasta abaixo.")
    osascript = shutil.which("osascript")
    if osascript is None:
        raise RuntimeError("Não achei o seletor nativo de arquivos.")
    script = (
        'set theFiles to choose file with prompt "Escolha os vídeos" '
        "with multiple selections allowed\n"
        'set out to ""\n'
        "repeat with f in theFiles\n"
        "set out to out & POSIX path of f & linefeed\n"
        "end repeat\n"
        "return out"
    )
    try:
        proc = subprocess.run(  # noqa: S603 - argv fixo, sem shell
            [osascript, "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Não achei o seletor nativo de arquivos.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("O seletor de arquivos demorou demais.") from exc
    if proc.returncode != 0:
        err = (proc.stderr or "").casefold()
        if "user canceled" in err or "-128" in err or proc.returncode == 1:
            return None
        raise RuntimeError((proc.stderr or "Não deu para escolher os arquivos.").strip())
    paths = [Path(line.strip()) for line in proc.stdout.splitlines() if line.strip()]
    existing = [p for p in paths if p.is_file()]
    return existing or None


def _macos_finder_selection() -> list[Path]:
    if sys.platform != "darwin":
        return []
    osascript = shutil.which("osascript")
    if osascript is None:
        return []
    script = (
        'tell application "Finder"\n'
        'if (count of selection) is 0 then return ""\n'
        'set out to ""\n'
        "repeat with i in selection\n"
        "try\n"
        "set out to out & POSIX path of (i as alias) & linefeed\n"
        "end try\n"
        "end repeat\n"
        "return out\n"
        "end tell"
    )
    try:
        proc = subprocess.run(  # noqa: S603
            [osascript, "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    paths: list[Path] = []
    for line in proc.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        path = Path(raw)
        if path.exists():
            paths.append(path)
    return paths


def _norm_nome(nome: str) -> str:
    return unicodedata.normalize("NFC", Path(nome).name)


def _iter_video_files(pasta: Path, *, profundidade: int = 2) -> list[Path]:
    if not pasta.is_dir():
        return []
    arquivos: list[Path] = []
    for path in pasta.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        try:
            rel = path.relative_to(pasta)
        except ValueError:
            continue
        if len(rel.parts) > profundidade:
            continue
        arquivos.append(path)
    return arquivos


def _match_local_files(
    state: WebState,
    specs: list[LocalFileSpec],
    hint: Path | None,
) -> tuple[list[Path], list[str]]:
    """Acha no disco os arquivos arrastados, sem copiar o conteúdo."""
    finder = _macos_finder_selection()
    wanted: dict[str, int] = {}
    for spec in specs:
        nome = _norm_nome(spec.name)
        if nome:
            wanted[nome] = int(spec.size or 0)

    finder_videos = [
        p for p in finder
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    finder_dirs = [p for p in finder if p.is_dir()]
    if finder_videos:
        nomes_finder = {_norm_nome(p.name) for p in finder_videos}
        if not wanted or set(wanted).issubset(nomes_finder):
            return finder_videos, []
    if finder_dirs and (not wanted or len(wanted) > 1):
        aulas = listar_videos(finder_dirs[0], recursivo=True)
        caminhos = [aula.path for aula in aulas]
        if caminhos:
            if not wanted:
                return caminhos, []
            nomes = {_norm_nome(p.name) for p in caminhos}
            if set(wanted).issubset(nomes):
                return caminhos, []

    found: dict[str, Path] = {}

    def considerar(path: Path) -> None:
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        nome = _norm_nome(path.name)
        if wanted and nome not in wanted:
            return
        if wanted and wanted[nome] and path.stat().st_size != wanted[nome]:
            return
        found[nome] = path

    pastas: list[Path] = []
    for path in finder:
        if path.is_dir():
            pastas.append(path)
        else:
            considerar(path)
    if hint is not None:
        if hint.is_dir():
            pastas.append(hint)
        elif hint.is_file():
            considerar(hint)
            pastas.append(hint.parent)
    for aula in state.aulas:
        pastas.append(aula.path.parent)

    vistos: set[Path] = set()
    for pasta in pastas:
        resolved = pasta.expanduser().resolve() if pasta.exists() else pasta
        if resolved in vistos:
            continue
        vistos.add(resolved)
        for path in _iter_video_files(resolved):
            considerar(path)

    if not wanted:
        # Soltou a pasta: usa o que o Finder ainda tem selecionado.
        if found:
            return list(found.values()), []
        if pastas:
            aulas = listar_videos(pastas[0], recursivo=True)
            return [aula.path for aula in aulas], []
        return [], []

    missing = [nome for nome in wanted if nome not in found]
    return [found[nome] for nome in wanted if nome in found], missing


def _videos_from_files_response(
    state: WebState,
    paths: list[Path],
    *,
    append: bool,
) -> dict[str, Any]:
    novas = aulas_de_caminhos(paths)
    if not novas:
        raise HTTPException(400, "Não achei vídeo nesses arquivos.")
    if append and state.aulas:
        por_nome = {aula.path.name: aula for aula in state.aulas}
        for aula in novas:
            por_nome[aula.path.name] = aula
        state.aulas = sorted(por_nome.values(), key=lambda aula: (aula.ordem, aula.path.name))
        extra = f" + {len(novas)} arquivo(s)"
        state.source_label = (state.source_label or "arquivos locais") + extra
    else:
        state.clear_inbox()
        state.aulas = novas
        parents = {aula.path.parent for aula in novas}
        if len(parents) == 1:
            state.source_label = str(next(iter(parents)))
        else:
            state.source_label = f"{len(novas)} arquivo(s) local(is)"
    _start_probe(state)
    state.emit(
        f"Leu {len(state.aulas)} vídeo(s) no disco, sem copiar.",
        level="ok",
    )
    _persist_workspace(state)
    return {
        "cancelled": False,
        "matched": [aula.path.name for aula in novas],
        "missing": [],
        "aulas": _aulas_payload(state),
        "fonte": state.source_label,
    }


def _videos_from_user_path(state: WebState, path: Path, *, append: bool) -> list[AulaArquivo]:
    if not path.exists():
        raise FileNotFoundError(f"Não achei essa pasta ou zip: {path}")
    pasta, temp = resolve_source(path)
    novas = listar_videos(pasta, recursivo=True)
    if append and (state.aulas or state.inbox is not None):
        inbox = _ensure_inbox(state, append=True)
        for aula in novas:
            if not aula.path.is_file():
                continue
            try:
                _link_or_copy(aula.path, inbox / aula.path.name)
            except OSError:
                continue
        if temp is not None:
            cleanup_temp(temp)
        if state.source_label:
            state.source_label = f"{state.source_label} + {path.name}"
        else:
            state.source_label = str(path)
        return _scan_inbox(state)
    state.clear_inbox()
    state.extract_temp = temp
    state.source_label = str(path)
    state.aulas = novas
    _start_probe(state)
    _persist_workspace(state)
    return novas


def _videos_from_path_response(state: WebState, path: Path, *, append: bool) -> dict[str, Any]:
    try:
        aulas = _videos_from_user_path(
            state,
            path,
            append=append and bool(state.aulas or state.inbox),
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (RuntimeError, OSError, NotADirectoryError) as exc:
        raise HTTPException(400, mask_text(str(exc))) from exc
    state.emit(
        f"Leu {len(aulas)} vídeo(s) em {path.name}.",
        level="ok" if aulas else "warn",
    )
    _persist_workspace(state)
    return {"cancelled": False, "aulas": _aulas_payload(state), "fonte": state.source_label}


def _scan_inbox(state: WebState) -> list[AulaArquivo]:
    inbox = state.inbox
    if inbox is None:
        state.aulas = []
        return []
    zips = [p for p in inbox.rglob("*") if p.is_file() and is_zip(p)]
    videos = [
        p
        for p in inbox.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    pasta = inbox
    if len(zips) == 1 and not videos:
        if state.extract_temp is not None:
            cleanup_temp(state.extract_temp)
            state.extract_temp = None
        pasta, temp = resolve_source(zips[0])
        state.extract_temp = temp
        state.source_label = zips[0].name
    aulas = listar_videos(pasta, recursivo=True)
    state.aulas = aulas
    _start_probe(state)
    _persist_workspace(state)
    return aulas


def _aulas_por_nome(state: WebState, nomes: list[str]) -> list[AulaArquivo]:
    """Filtra as aulas pedidas; sem lista, pega todas que passam do limite."""
    if not nomes:
        return [aula for aula in state.aulas if aula.tamanho_bytes > AVISO_BYTES]
    pedidos = {Path(nome).name for nome in nomes}
    return [aula for aula in state.aulas if aula.path.name in pedidos]


def _convert_item(state: WebState, nome: str) -> dict[str, Any] | None:
    alvo = unicodedata.normalize("NFC", Path(nome).name)
    for item in state.convert_job.get("items", []):
        if unicodedata.normalize("NFC", item["arquivo"]) == alvo:
            return item
    return None


def _apply_convert_job(state: WebState, job: dict[str, Any]) -> None:
    state.convert_job = job
    state.convertidos = dict(job.get("convertidos") or {})
    if job.get("fonte") and not state.source_label:
        state.source_label = str(job["fonte"])
    _restore_aulas_from_job(state, job)


def _workspace_path() -> Path:
    from aula_uploader.videopack import cache_dir

    return cache_dir() / "workspace.json"


def _persist_workspace(state: WebState) -> None:
    """Grava a lista atual pra sobreviver a refresh / restart."""
    if not state.aulas:
        return
    payload = {
        "fonte": state.source_label,
        "portal": state.portal_key,
        "capitulo_id": state.capitulo.id if state.capitulo else None,
        "aulas": [
            {
                "arquivo": a.path.name,
                "path": str(a.path),
                "titulo": a.titulo,
                "ordem": a.ordem,
            }
            for a in state.aulas
        ],
    }
    path = _workspace_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)


def _restore_workspace(state: WebState) -> None:
    if state.aulas:
        return
    path = _workspace_path()
    if not path.is_file():
        return
    try:
        dados = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(dados, dict):
        return
    fonte = str(dados.get("fonte") or "")
    rows = dados.get("aulas") or []
    caminhos: list[Path] = []
    meta: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        p = Path(row.get("path") or "")
        if p.is_file():
            caminhos.append(p)
            meta[p.name] = row
    if not caminhos and fonte:
        pasta = _pasta_da_fonte(fonte)
        if pasta is not None:
            state.aulas = listar_videos(pasta, recursivo=True)
            state.source_label = fonte or str(pasta)
            _start_probe(state)
            return
    if not caminhos:
        return
    aulas = aulas_de_caminhos(caminhos)
    for aula in aulas:
        extra = meta.get(aula.path.name) or {}
        if extra.get("titulo"):
            aula.titulo = str(extra["titulo"])
        if extra.get("ordem"):
            aula.ordem = int(extra["ordem"])
    state.aulas = aulas
    if fonte:
        state.source_label = fonte
    _start_probe(state)


def _pasta_da_fonte(fonte: str) -> Path | None:
    raw = (fonte or "").strip()
    if not raw:
        return None
    # "…/pasta + 1 arquivo(s)" → tenta a parte antes do " +"
    candidato = raw.split(" + ", 1)[0].strip()
    path = Path(candidato).expanduser()
    if path.is_dir():
        return path
    if path.is_file():
        return path.parent
    return None


def _restore_aulas_from_job(state: WebState, job: dict[str, Any]) -> None:
    if state.aulas:
        return
    meta = {item.get("arquivo"): item for item in (job.get("aulas") or [])}
    caminhos: list[Path] = []
    # Preferência: lista completa da tela salva no job.
    for item in job.get("aulas") or []:
        path = Path(item.get("path") or "")
        if path.is_file():
            caminhos.append(path)
    if not caminhos:
        for item in job.get("items") or []:
            path = Path(item.get("path") or "")
            if path.is_file():
                caminhos.append(path)
    if not caminhos:
        pasta = _pasta_da_fonte(str(job.get("fonte") or state.source_label or ""))
        if pasta is not None:
            state.aulas = listar_videos(pasta, recursivo=True)
            if job.get("fonte"):
                state.source_label = str(job["fonte"])
            _start_probe(state)
            _persist_workspace(state)
        return
    aulas = aulas_de_caminhos(caminhos)
    for aula in aulas:
        extra = meta.get(aula.path.name) or {}
        if extra.get("titulo"):
            aula.titulo = extra["titulo"]
        if extra.get("ordem"):
            aula.ordem = int(extra["ordem"])
    state.aulas = aulas
    _start_probe(state)
    _persist_workspace(state)


def _reattach_convert(state: WebState) -> None:
    job = load_convert_job()
    if not job:
        return
    if worker_vivo(job):
        _apply_convert_job(state, job)
        state.converting = True
        state.convert_watch_stop = False
        state.emit("A compressão em segundo plano ainda está rodando.", level="ok")
        threading.Thread(
            target=_watch_convert_job,
            args=(state,),
            daemon=True,
            name="web-convert-watch",
        ).start()
        state.publish({"type": "convert", "convert": state.convert_job, "converting": True})
        return
    if job.get("status") == "running":
        job = marcar_interrupcao(job)
        save_convert_job(job)
        _apply_convert_job(state, job)
        state.converting = False
        prontos = sum(1 for i in job.get("items") or [] if i.get("status") in {"pronto", "aplicado"})
        state.emit(
            f"A interface caiu, mas a compressão não se perdeu. {prontos} arquivo(s) pronto(s). "
            "O restante dá para continuar.",
            level="warn",
        )
        state.publish({"type": "convert", "convert": state.convert_job, "converting": False})
        return
    _apply_convert_job(state, job)
    state.converting = False


def _watch_convert_job(state: WebState) -> None:
    ultimo = ""
    while not getattr(state, "convert_watch_stop", False):
        job = load_convert_job()
        if not job:
            break
        dump = json.dumps(job, sort_keys=True, ensure_ascii=False)
        if dump != ultimo:
            _apply_convert_job(state, job)
            vivo = worker_vivo(job) or job.get("status") == "running"
            state.converting = vivo
            state.publish(
                {"type": "convert", "convert": state.convert_job, "converting": state.converting}
            )
            ultimo = dump
        if job.get("status") not in {"running"} and not worker_vivo(job):
            state.converting = False
            state.publish({"type": "convert", "convert": state.convert_job, "converting": False})
            break
        time.sleep(0.5)


def _resolver_video(state: WebState, arquivo: str) -> Path | None:
    """Aceita só arquivos que já estão na lista ou saíram da conversão."""
    nome = Path(arquivo).name
    if nome != arquivo.strip() or nome in {"", ".", ".."}:
        return None
    for aula in state.aulas:
        if aula.path.name == nome and aula.path.is_file():
            return aula.path
    for destino in state.convertidos.values():
        candidato = Path(destino)
        if candidato.name == nome and candidato.is_file():
            return candidato
    return None


def _range_response(path: Path, range_header: str | None) -> Response:
    """Serve o vídeo com suporte a Range, que é o que o player usa para buscar."""
    tamanho = path.stat().st_size
    tipo = "video/mp4" if path.suffix.lower() in {".mp4", ".m4v"} else "application/octet-stream"
    base = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}

    inicio, fim = 0, tamanho - 1
    parcial = False
    if range_header and range_header.strip().lower().startswith("bytes="):
        trecho = range_header.split("=", 1)[1].split(",")[0].strip()
        comeco, _, termino = trecho.partition("-")
        try:
            if comeco:
                inicio = int(comeco)
                if termino:
                    fim = min(int(termino), tamanho - 1)
            elif termino:
                inicio = max(tamanho - int(termino), 0)
            parcial = True
        except ValueError:
            inicio, fim, parcial = 0, tamanho - 1, False
        if inicio > fim or inicio >= tamanho:
            return Response(
                status_code=416,
                headers={**base, "Content-Range": f"bytes */{tamanho}"},
            )

    def corpo() -> Any:
        restante = fim - inicio + 1
        with path.open("rb") as fh:
            fh.seek(inicio)
            while restante > 0:
                pedaco = fh.read(min(1024 * 1024, restante))
                if not pedaco:
                    break
                restante -= len(pedaco)
                yield pedaco

    headers = {**base, "Content-Length": str(fim - inicio + 1)}
    if parcial:
        headers["Content-Range"] = f"bytes {inicio}-{fim}/{tamanho}"
    return StreamingResponse(
        corpo(),
        status_code=206 if parcial else 200,
        media_type=tipo,
        headers=headers,
    )


def _start_probe(state: WebState) -> None:
    """Lê resolução e duração em segundo plano.

    A lista precisa aparecer na hora; o ffprobe de dezenas de arquivos leva
    alguns segundos, então roda numa thread e avisa a interface pelo SSE.
    """
    paths = [aula.path for aula in state.aulas if str(aula.path) not in state.video_infos]
    if not paths:
        return

    def worker() -> None:
        try:
            infos = probe_muitos(paths)
        except Exception:  # noqa: BLE001 - metadado é enfeite, não pode derrubar a lista
            return
        state.video_infos.update(infos)
        state.publish({"type": "probe", "aulas": _aulas_payload(state)})

    threading.Thread(target=worker, daemon=True, name="web-probe").start()


def _apply_edits(state: WebState, edits: list[AulaEdit] | None) -> None:
    if not edits:
        return
    by_name = {aula.path.name: aula for aula in state.aulas}
    for edit in edits:
        aula = by_name.get(edit.arquivo)
        if aula is None:
            continue
        aula.ordem = edit.ordem
        aula.titulo = edit.titulo.strip() or aula.titulo
        aula.ordem_inferida = False
    state.aulas.sort(key=lambda aula: (aula.ordem, aula.path.name))


def _plano_payload(plano: list) -> list[dict[str, Any]]:
    return [
        {
            "arquivo": item.aula.path.name,
            "ordem": item.aula.ordem,
            "titulo": item.aula.titulo,
            "acao": item.acao.value,
            "acao_label": _acao_label(item.acao),
            "tamanho_fmt": format_bytes(item.aula.tamanho_bytes),
        }
        for item in plano
    ]


def _restore_saved_sessions(state: WebState) -> None:
    """Recarrega cookies salvos quando a interface sobe de novo."""
    restaurados: list[str] = []
    for key in ALLOWED_HOSTS:
        if not has_saved_session(key):
            continue
        username = session_username(key)
        try:
            portal = ensure_authenticated(
                key,
                username=username,
                use_saved_session=True,
                persist_session=True,
                allow_env=True,
                force=False,
                log=lambda msg, _k=key: state.emit(msg, level="info"),
            )
        except Exception as exc:  # noqa: BLE001
            clear_session(key)
            state.emit(
                f"Sessão salva de {PORTAL_LABELS[key]} expirou — faça login de novo.",
                level="warn",
            )
            state.emit(mask_text(str(exc)), level="warn")
            continue
        state.set_client(key, portal)
        restaurados.append(key)
        state.emit(f"Sessão restaurada: {PORTAL_LABELS[key]}", level="ok")

    if not restaurados:
        return
    preferido = read_last_portal()
    if preferido in state.clients:
        state.portal_key = preferido
    elif state.portal_key not in state.clients:
        state.portal_key = restaurados[0]


def _aulas_from_job(job: dict[str, Any]) -> list[AulaArquivo]:
    """Remonta aulas a partir dos itens/pasta do job (se os arquivos ainda existem)."""
    pasta = Path(job.get("pasta") or "")
    aulas: list[AulaArquivo] = []
    for item in job.get("items") or []:
        path = Path(item.get("path") or "")
        if not path.is_file():
            candidato = pasta / (item.get("arquivo") or "")
            path = candidato if candidato.is_file() else path
        if not path.is_file():
            # Sem arquivo local: ainda mostra a linha com tamanho 0.
            aulas.append(
                AulaArquivo(
                    path=pasta / (item.get("arquivo") or "ausente.mp4"),
                    ordem=int(item.get("ordem") or 1),
                    titulo=item.get("titulo") or item.get("arquivo") or "aula",
                    tamanho_bytes=0,
                )
            )
            continue
        aulas.append(
            AulaArquivo(
                path=path,
                ordem=int(item.get("ordem") or 1),
                titulo=item.get("titulo") or path.stem,
                tamanho_bytes=path.stat().st_size,
            )
        )
    aulas.sort(key=lambda a: (a.ordem, a.path.name))
    return aulas


def _job_from_upload_state(
    st: UploadState, *, state: WebState | None = None
) -> dict[str, Any]:
    """Espelha o estado em disco no formato da barra de envio."""
    mapa = {
        "pending": "pendente",
        "done": "ok",
        "skipped": "pulada",
        "failed": "falhou",
        "processing": "processando",
    }
    items = []
    base = ""
    if state and state.portal:
        base = state.portal.base_url
    elif st.portal:
        from aula_uploader.session import DEFAULT_URLS

        base = DEFAULT_URLS.get(st.portal, "")
    for i in st.items:
        cid = i.conteudo_id
        url = conteudo_admin_url(base, int(cid)) if cid and base else ""
        items.append(
            {
                "arquivo": i.arquivo,
                "titulo": i.titulo,
                "ordem": i.ordem,
                "acao": "criar",
                "status": mapa.get(i.status, i.status),
                "pct": 100 if i.status in {"done", "processing"} else 0,
                "erro": i.erro or "",
                "conteudo_id": cid,
                "url": url,
                "path": str(Path(st.pasta) / i.arquivo) if st.pasta else "",
            }
        )
    falhas = [{"titulo": i.titulo, "erro": i.erro} for i in st.items if i.status == "failed"]
    processando = sum(1 for i in st.items if i.status == "processing")
    ok = sum(1 for i in st.items if i.status == "done")
    if processando:
        # Upload de arquivo já acabou; só falta o encode no Nivo.
        status = "processando"
    elif falhas and ok:
        status = "done_with_errors"
    elif falhas:
        status = "error"
    elif ok:
        status = "done"
    else:
        status = "idle"
    cap_nome = ""
    curso_nome = ""
    url = ""
    if state and state.capitulo and state.capitulo.id == st.capitulo_id:
        cap_nome = state.capitulo.nome
        curso_nome = state.capitulo.curso_nome or state.curso_nome
        if state.portal:
            url = capitulo_admin_url(state.portal.base_url, st.capitulo_id)
    job = {
        "id": f"state-{st.portal}-{st.capitulo_id}",
        "status": status,
        "ok": ok,
        "pulados": sum(1 for i in st.items if i.status == "skipped"),
        "falhas": falhas,
        "items": items,
        "erro": "",
        "detached": False,
        "portal": st.portal,
        "capitulo_id": st.capitulo_id,
        "capitulo_nome": cap_nome or f"capítulo {st.capitulo_id}",
        "curso_nome": curso_nome,
        "url": url,
        "pasta": st.pasta,
        "fonte": st.fonte or st.pasta,
    }
    job["fase"] = job_fase(job)
    return job


def _hydrate_job_from_disk(state: WebState) -> None:
    """Espelha progresso em disco na barra, sem misturar capítulos."""
    vivo = load_upload_job()
    if upload_worker_vivo(vivo):
        state.job = web_job_view(vivo)
        # uploading só enquanto sobe arquivo; Nivo não trava a workspace.
        state.uploading = job_fase(vivo) == "uploading"
        return
    # Worker já terminou: ainda usa o job em disco deste capítulo (evita UI
    # presa em "salvando" quando o SSE caiu no meio do envio).
    if (
        vivo
        and state.capitulo is not None
        and vivo.get("capitulo_id") is not None
        and int(vivo["capitulo_id"]) == int(state.capitulo.id)
        and (vivo.get("items") or [])
    ):
        state.job = web_job_view(vivo)
        state.uploading = False
        try:
            upsert_job(dict(vivo))
        except Exception:  # noqa: BLE001
            pass
    if not state.portal_key:
        return
    if state.capitulo is not None:
        st = UploadState.load(state.portal_key, state.capitulo.id)
        if not st or not st.items:
            return
    else:
        st = _latest_upload_state(state.portal_key)
        if not st or not st.items:
            return
    # Preferir UploadState quando ele já avançou (ex.: Nivo marcado done).
    from_state = _job_from_upload_state(st, state=state)
    if not state.job or _job_mais_avancado(from_state, state.job):
        state.job = from_state
    try:
        upsert_job(dict(state.job))
    except Exception:  # noqa: BLE001
        pass
    # Remonta vídeos só quando a lista está vazia e o job é deste capítulo.
    if state.aulas:
        return
    job_cap = state.job.get("capitulo_id")
    if state.capitulo is not None and job_cap is not None:
        if int(job_cap) != int(state.capitulo.id):
            return
    aulas = _aulas_from_job(state.job)
    if aulas:
        state.aulas = aulas
        state.source_label = st.fonte or st.pasta


def _job_rank(job: dict[str, Any] | None) -> int:
    if not job:
        return -1
    ranks = {
        "ok": 5,
        "pulada": 5,
        "falhou": 4,
        "processando": 3,
        "salvando": 2,
        "enviando": 1,
        "pendente": 0,
    }
    items = job.get("items") or []
    if not items:
        return 0
    return max(ranks.get(str(i.get("status") or ""), 0) for i in items)


def _job_mais_avancado(candidato: dict[str, Any], atual: dict[str, Any]) -> bool:
    return _job_rank(candidato) >= _job_rank(atual)


def _latest_upload_state(portal_key: str) -> UploadState | None:
    from aula_uploader.session import state_dir

    prefix = f"upload-{portal_key}-"
    melhores: list[tuple[float, UploadState]] = []
    for path in state_dir().glob(f"{prefix}*.json"):
        try:
            cap_id = int(path.stem.split("-")[-1])
        except ValueError:
            continue
        st = UploadState.load(portal_key, cap_id)
        if st and st.items:
            melhores.append((st.updated_at, st))
    if not melhores:
        return None
    melhores.sort(key=lambda x: x[0], reverse=True)
    return melhores[0][1]


def _item_nivo_pronto(portal: Any, capitulo_id: int, conteudo_id: int) -> bool:
    try:
        conteudo = portal.get_conteudo(conteudo_id)
    except Exception:  # noqa: BLE001
        conteudo = None
    play_ok = bool(conteudo is not None and portal.conteudo_pronto_para_play(conteudo))
    if play_ok:
        return True
    try:
        tabela_ok = portal._conteudo_tem_video_na_tabela(capitulo_id, conteudo_id)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        tabela_ok = False
    tempo_ok = bool(
        conteudo is not None
        and conteudo.tempo not in ("", "00:00", "---")
        and tabela_ok
    )
    return tempo_ok


def _refresh_nivo_status(state: WebState) -> bool:
    """Marca no disco/UI as aulas que o Nivo já liberou. Retorna se mudou algo."""
    if not state.portal_key or state.portal is None:
        return False
    st: UploadState | None = None
    if state.capitulo is not None:
        st = UploadState.load(state.portal_key, state.capitulo.id)
    if not st:
        st = _latest_upload_state(state.portal_key)
    if not st:
        return False

    # Sempre prefere o upload-job.json deste capítulo se estiver mais avançado.
    vivo = load_upload_job()
    if (
        vivo
        and state.capitulo is not None
        and vivo.get("capitulo_id") is not None
        and int(vivo["capitulo_id"]) == int(state.capitulo.id)
        and (vivo.get("items") or [])
    ):
        view = web_job_view(vivo)
        if not state.job or _job_mais_avancado(view, state.job):
            antes = json.dumps(state.job or {}, sort_keys=True, ensure_ascii=False)
            state.job = view
            state.uploading = False
            try:
                upsert_job(dict(vivo))
            except Exception:  # noqa: BLE001
                pass
            depois = json.dumps(state.job or {}, sort_keys=True, ensure_ascii=False)
            if antes != depois:
                state.publish(
                    {
                        "type": "job",
                        "job": state.job,
                        "uploading": False,
                        "jobs": [web_job_view(j) for j in list_upload_jobs()],
                    }
                )
                return True

    pendentes = [i for i in st.items if i.status == "processing" and i.conteudo_id]
    if not pendentes:
        # Espelha estado concluído na UI e AVISA o browser (antes não publicava).
        if any(i.status == "done" for i in st.items):
            novo = _job_from_upload_state(st, state=state)
            antes = json.dumps(state.job or {}, sort_keys=True, ensure_ascii=False)
            if not state.job or _job_mais_avancado(novo, state.job):
                state.job = novo
            try:
                upsert_job(dict(state.job))
            except Exception:  # noqa: BLE001
                pass
            depois = json.dumps(state.job or {}, sort_keys=True, ensure_ascii=False)
            if antes != depois:
                state.publish(
                    {
                        "type": "job",
                        "job": state.job,
                        "uploading": False,
                        "jobs": [web_job_view(j) for j in list_upload_jobs()],
                    }
                )
                return True
        return False
    mudou = False
    portal = state.portal
    capitulo_id = st.capitulo_id
    for item in pendentes:
        assert item.conteudo_id is not None
        if not _item_nivo_pronto(portal, capitulo_id, item.conteudo_id):
            continue
        st.mark(item.arquivo, "done", conteudo_id=item.conteudo_id, erro="")
        mudou = True
        state.emit(f"Nivo pronto: {item.titulo}", level="ok")
    if not mudou:
        return False
    st = UploadState.load(state.portal_key, capitulo_id) or st
    state.job = _job_from_upload_state(st, state=state)
    try:
        upsert_job(dict(state.job))
    except Exception:  # noqa: BLE001
        pass
    by_name = {i.arquivo: i for i in st.items}
    for row in (state.job.get("items") or []):
        src = by_name.get(row.get("arquivo") or "")
        if src and src.status == "done":
            row["status"] = "ok"
            row["pct"] = 100
            row["erro"] = ""
    state.publish(
        {
            "type": "job",
            "job": state.job,
            "uploading": state.uploading,
            "jobs": [web_job_view(j) for j in list_upload_jobs()],
        }
    )
    return True


def _ensure_nivo_watcher(state: WebState) -> None:
    """Poll leve enquanto houver aula em processing (worker de upload já pode ter morrido)."""
    if getattr(state, "nivo_watch_thread", None) and state.nivo_watch_thread.is_alive():
        return
    state.nivo_watch_stop = False

    def _loop() -> None:
        while not getattr(state, "nivo_watch_stop", False):
            try:
                _hydrate_job_from_disk(state)
                job = state.job or {}
                items = job.get("items") or []
                ainda = any(i.get("status") == "processando" for i in items)
                if ainda and state.portal is not None:
                    _refresh_nivo_status(state)
                elif not ainda:
                    # Sem pendência: dorme mais e só reavalia hydrate.
                    time.sleep(20.0)
                    continue
            except Exception:  # noqa: BLE001
                pass
            time.sleep(15.0)

    t = threading.Thread(target=_loop, daemon=True, name="web-nivo-watch")
    state.nivo_watch_thread = t
    t.start()


def _reattach_upload(state: WebState) -> None:
    job = load_upload_job()
    if not job:
        return
    if upload_worker_vivo(job):
        state.job = web_job_view(job)
        state.uploading = True
        state.upload_watch_stop = False
        state.emit("O envio em segundo plano ainda está rodando.", level="ok")
        threading.Thread(
            target=_watch_upload_job,
            args=(state,),
            daemon=True,
            name="web-upload-watch",
        ).start()
        state.publish({"type": "job", "job": state.job, "uploading": True})
        return
    if job.get("status") == "running":
        job = marcar_upload_interrupcao(job)
        save_upload_job(job)
        state.job = web_job_view(job)
        state.uploading = False
        state.emit(
            "A interface caiu, mas o progresso do envio não se perdeu. "
            "O que já subiu ficou no portal; o restante dá para continuar.",
            level="warn",
        )
        state.publish({"type": "job", "job": state.job, "uploading": False})
        return
    state.job = web_job_view(job)
    state.uploading = False


def _watch_upload_job(state: WebState) -> None:
    ultimo = ""
    while not getattr(state, "upload_watch_stop", False):
        job = load_upload_job()
        if not job:
            break
        dump = json.dumps(job, sort_keys=True, ensure_ascii=False)
        if dump != ultimo:
            state.job = web_job_view(job)
            vivo = upload_worker_vivo(job)
            fase = job_fase(job)
            state.uploading = vivo and fase == "uploading"
            # Espelha logs novos do worker no painel da interface.
            for entry in job.get("logs") or []:
                msg = entry.get("message") or ""
                if msg and not any(l.message == msg for l in list(state.logs)[-20:]):
                    state.emit(msg, level="info")
            state.publish(
                {"type": "job", "job": state.job, "uploading": state.uploading}
            )
            ultimo = dump
        # Continua enquanto sobe arquivo OU ainda há item no Nivo — mesmo se o
        # status geral já mudou. Só para quando tudo terminal no disco.
        items = job.get("items") or []
        ainda = any(
            i.get("status") in {"pendente", "enviando", "salvando", "processando"}
            for i in items
        ) or job.get("status") in {"running", "processando", "queued"}
        if not ainda and not upload_worker_vivo(job):
            state.uploading = False
            if job.get("status") in {"done", "done_with_errors"}:
                state.emit(
                    f"Envio concluído: {job.get('ok') or 0} ok · "
                    f"{job.get('pulados') or 0} pulada(s) · "
                    f"{len(job.get('falhas') or [])} falha(s).",
                    level="ok" if not job.get("falhas") else "error",
                )
                if state.portal and state.capitulo and int(job.get("ok") or 0) > 0:
                    state.catalog.remember_after_upload(
                        state.portal, state.capitulo, uploaded=int(job["ok"])
                    )
            state.publish({"type": "job", "job": state.job, "uploading": False})
            break
        time.sleep(0.5)
