"""Cliente HTTP mínimo do admin: auth, conteúdo de vídeo e upload."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from aula_uploader.media import mask_url

SESSION_COOKIE_NAMES = frozenset({"PHPSESSID", "REMEMBERME"})
AUTH_CACHE_SECONDS = 300
CONTEUDO_PREFIX = "son_cursosbundle_conteudotype"
VIDEO_TIPO_NIVO = "12"


@dataclass
class CapituloResumo:
    id: int
    nome: str = ""
    curso_id: int | None = None
    curso_nome: str = ""


@dataclass
class ConteudoLinha:
    id: int
    titulo: str
    status: str = ""
    tipo: str = ""
    ordem: int = 0
    tempo: str = ""
    tem_video: bool = False
    deletado: bool = False


@dataclass
class ConteudoData:
    titulo: str
    ordem: int
    tipo: str
    status: str = "0"
    texto: str = ""
    link: str = ""
    video_url: str = ""
    video_url_bunny: str = ""
    tempo: str = "00:00"
    repositorio: str = ""
    caminho: str = ""
    transcription: str = ""
    forum_registro: str = ""
    projeto_fase: str = ""
    url_s3_nivo: str = ""
    video_transcription_nivo: str = ""
    extra: dict[str, str] = field(default_factory=dict)


class PortalClient:
    CHUNK_SIZE = 90 * 1024 * 1024

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        session_path: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session_path = session_path
        self._auth_cached = False
        self._auth_cache_until = 0.0
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=20.0,
            headers={
                "User-Agent": (
                    "aula-uploader/0.1 (+https://github.com/; local CLI)"
                ),
            },
        )
        if self.session_path and self.session_path.exists():
            self._load_session()

    def close(self) -> None:
        self.save_session()
        self.client.close()

    def __enter__(self) -> PortalClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _load_session(self) -> None:
        if not self.session_path or not self.session_path.exists():
            return
        try:
            payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for item in payload.get("cookies", []):
            self.client.cookies.set(
                item["name"],
                item["value"],
                domain=item.get("domain"),
                path=item.get("path", "/"),
            )

    def _has_session_cookie(self) -> bool:
        host = urlparse(self.base_url).hostname or ""
        for cookie in self.client.cookies.jar:
            if cookie.name not in SESSION_COOKIE_NAMES:
                continue
            domain = (cookie.domain or "").lstrip(".")
            if host == domain or host.endswith(f".{domain}"):
                return True
        return False

    def _clear_session_cookies(self) -> None:
        host = urlparse(self.base_url).hostname or ""
        for cookie in list(self.client.cookies.jar):
            domain = (cookie.domain or "").lstrip(".")
            if cookie.name in SESSION_COOKIE_NAMES and (
                host == domain or host.endswith(f".{domain}")
            ):
                self.client.cookies.delete(
                    cookie.name, domain=cookie.domain, path=cookie.path
                )

    def _mark_authenticated(self) -> None:
        self._auth_cached = True
        self._auth_cache_until = time.monotonic() + AUTH_CACHE_SECONDS

    def _invalidate_auth_cache(self) -> None:
        self._auth_cached = False
        self._auth_cache_until = 0.0

    def save_session(self) -> None:
        if not self.session_path:
            return
        cookies: list[dict[str, str]] = []
        for cookie in self.client.cookies.jar:
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain or "",
                    "path": cookie.path or "/",
                }
            )
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text(
            json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            self.session_path.chmod(0o600)
        except OSError:
            pass

    def is_authenticated(self) -> bool:
        if self._auth_cached and time.monotonic() < self._auth_cache_until:
            return True
        if not self._has_session_cookie():
            self._invalidate_auth_cache()
            return False
        response = self.client.get(
            f"{self.base_url}/admin/curso/",
            follow_redirects=False,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "").lower()
            ok = "login" not in location
        elif response.status_code == 200:
            ok = True
        else:
            ok = False
        if ok:
            self._mark_authenticated()
        else:
            self._invalidate_auth_cache()
        return ok

    def ensure_authenticated(
        self,
        *,
        log: Callable[[str], None] | None = None,
        force: bool = False,
    ) -> None:
        if not force and self.is_authenticated():
            if log:
                log("Sessão reutilizada.")
            return
        if log:
            log(f"Fazendo login em {self.base_url}...")
        self.login()
        if log:
            log("Login OK.")

    def login(self) -> None:
        self._clear_session_cookies()
        self._invalidate_auth_cache()
        login_page = self.client.get(f"{self.base_url}/login")
        login_page.raise_for_status()
        soup = BeautifulSoup(login_page.text, "html.parser")
        csrf_input = soup.find("input", {"name": "_csrf_token"})
        if not csrf_input or not csrf_input.get("value"):
            raise RuntimeError("Token CSRF de login não encontrado")
        response = self.client.post(
            f"{self.base_url}/login_check",
            data={
                "_csrf_token": csrf_input["value"],
                "_username": self.username,
                "_password": self.password,
                "_submit": "",
            },
        )
        response.raise_for_status()
        # Sucesso costuma redirecionar para /admin; falha permanece em /login
        # (não confundir com /login_check).
        path = str(response.url.path) if hasattr(response.url, "path") else str(response.url)
        if path.rstrip("/").endswith("/login") or path.endswith("/login/"):
            raise RuntimeError("Login falhou — verifique usuário e senha")
        self._mark_authenticated()
        self.save_session()

    def _field_value(self, soup: BeautifulSoup, name: str) -> str:
        element = soup.find(["input", "textarea", "select"], {"name": name})
        if not element:
            return ""
        if element.name == "textarea":
            return element.get_text()
        if element.name == "select":
            selected = element.find("option", selected=True)
            return selected["value"] if selected and selected.has_attr("value") else ""
        return element.get("value", "")

    def _get_conteudo_token(self, form_url: str) -> str:
        response = self.client.get(form_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        token_input = soup.find("input", {"name": f"{CONTEUDO_PREFIX}[_token]"})
        if not token_input or not token_input.get("value"):
            raise RuntimeError("Token do formulário de conteúdo não encontrado")
        return token_input["value"]

    def listar_conteudos_tabela(
        self, capitulo_id: int, *, incluir_deletados: bool = False
    ) -> list[ConteudoLinha]:
        response = self.client.get(
            f"{self.base_url}/admin/curso/conteudo/{capitulo_id}/capitulo"
        )
        response.raise_for_status()
        linhas: list[ConteudoLinha] = []
        soup = BeautifulSoup(response.text, "html.parser")
        for row in soup.select("table tbody tr"):
            edit_link = row.select_one(
                'a[href*="/admin/curso/conteudo/"][href$="/edit"]'
            )
            if not edit_link:
                continue
            href = edit_link.get("href", "")
            parts = href.strip("/").split("/")
            if len(parts) < 5 or not parts[-2].isdigit():
                continue
            conteudo_id = int(parts[-2])
            cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
            titulo = cells[1] if len(cells) > 1 else ""
            status = cells[2] if len(cells) > 2 else ""
            tipo = cells[3] if len(cells) > 3 else ""
            url_cell = cells[4] if len(cells) > 4 else ""
            ordem_txt = cells[5] if len(cells) > 5 else "0"
            tempo = cells[6] if len(cells) > 6 else ""
            deletado_em = cells[7] if len(cells) > 7 else "---"
            deletado = bool(
                (deletado_em and deletado_em != "---")
                or row.select_one('a[href$="/restore"], a[href$="/restaurar"]')
            )
            tem_url = bool(row.select_one("td a[href^='http']")) or (
                url_cell not in ("", "---")
            )
            tem_video = tem_url or (tempo not in ("", "00:00", "---"))
            if deletado and not incluir_deletados:
                continue
            linhas.append(
                ConteudoLinha(
                    id=conteudo_id,
                    titulo=titulo,
                    status=status,
                    tipo=tipo,
                    ordem=int(ordem_txt) if ordem_txt.isdigit() else 0,
                    tempo=tempo,
                    tem_video=tem_video,
                    deletado=deletado,
                )
            )
        return linhas

    def inspect_capitulo(self, capitulo_id: int) -> CapituloResumo:
        """Consulta a página do capítulo e tenta obter nome/curso."""
        url = f"{self.base_url}/admin/curso/conteudo/{capitulo_id}/capitulo"
        response = self.client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        nome = ""
        h1 = soup.find(["h1", "h2", "h3"])
        if h1:
            nome = h1.get_text(" ", strip=True)
        curso_id = None
        curso_nome = ""
        breadcrumb = soup.select_one(".breadcrumb, ol.breadcrumb, nav")
        if breadcrumb:
            links = breadcrumb.find_all("a")
            for link in links:
                href = link.get("href", "")
                if "/admin/curso/" in href and href.rstrip("/").endswith("/edit"):
                    parts = href.strip("/").split("/")
                    if len(parts) >= 2 and parts[-2].isdigit():
                        curso_id = int(parts[-2])
                        curso_nome = link.get_text(strip=True)
                        break
                if "/admin/curso/capitulo/" in href and "/curso" in href:
                    parts = href.strip("/").split("/")
                    try:
                        idx = parts.index("curso")
                        if idx > 0 and parts[idx - 1].isdigit() is False:
                            pass
                        # .../capitulo/{curso_id}/curso
                        if parts[-1] == "curso" and parts[-2].isdigit():
                            curso_id = int(parts[-2])
                            curso_nome = link.get_text(strip=True) or curso_nome
                    except ValueError:
                        pass
        if not nome:
            nome = f"Capítulo {capitulo_id}"
        return CapituloResumo(
            id=capitulo_id,
            nome=nome,
            curso_id=curso_id,
            curso_nome=curso_nome,
        )

    def get_conteudo(self, conteudo_id: int, *, retries: int = 3) -> ConteudoData:
        url = f"{self.base_url}/admin/curso/conteudo/{conteudo_id}/edit"
        last_status = None
        response = None
        for attempt in range(retries):
            response = self.client.get(url)
            if response.status_code == 200:
                break
            last_status = response.status_code
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
        else:
            raise RuntimeError(
                f"Não foi possível abrir o conteúdo {conteudo_id} "
                f"(último status: {last_status})"
            )
        assert response is not None
        soup = BeautifulSoup(response.text, "html.parser")
        prefix = CONTEUDO_PREFIX
        return ConteudoData(
            titulo=self._field_value(soup, f"{prefix}[titulo]"),
            ordem=int(self._field_value(soup, f"{prefix}[ordem]") or "0"),
            tipo=self._field_value(soup, f"{prefix}[tipo]"),
            status=self._field_value(soup, f"{prefix}[status]") or "0",
            texto=self._field_value(soup, f"{prefix}[texto]"),
            link=self._field_value(soup, f"{prefix}[link]"),
            video_url=self._field_value(soup, f"{prefix}[videoUrl]"),
            video_url_bunny=self._field_value(soup, f"{prefix}[videoUrlBunny]"),
            tempo=self._field_value(soup, f"{prefix}[tempo]") or "00:00",
            repositorio=self._field_value(soup, f"{prefix}[repositorio]"),
            caminho=self._field_value(soup, f"{prefix}[caminho]"),
            transcription=self._field_value(soup, f"{prefix}[transcription]"),
            forum_registro=self._field_value(soup, f"{prefix}[forumRegistro]"),
            projeto_fase=self._field_value(soup, f"{prefix}[projetoFase]"),
            url_s3_nivo=self._field_value(soup, f"{prefix}[urlS3Nivo]"),
            video_transcription_nivo=self._field_value(
                soup, f"{prefix}[videoTranscriptionNivo]"
            ),
        )

    def get_conteudo_capitulo_id(self, conteudo_id: int) -> int | None:
        response = self.client.get(
            f"{self.base_url}/admin/curso/conteudo/{conteudo_id}/edit"
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        back_link = soup.select_one(
            'a[href*="/admin/curso/conteudo/"][href$="/capitulo"]'
        )
        if not back_link:
            return None
        href = back_link.get("href", "")
        parts = href.strip("/").split("/")
        conteudo_idx = parts.index("conteudo") if "conteudo" in parts else -1
        if conteudo_idx < 0 or conteudo_idx + 1 >= len(parts):
            return None
        return int(parts[conteudo_idx + 1])

    def _read_new_conteudo_form(self, capitulo_id: int) -> tuple[str, dict[str, str]]:
        form_url = f"{self.base_url}/admin/curso/conteudo/new/{capitulo_id}/capitulo"
        response = self.client.get(form_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        prefix = CONTEUDO_PREFIX
        token_input = soup.find("input", {"name": f"{prefix}[_token]"})
        if not token_input or not token_input.get("value"):
            raise RuntimeError("Token do formulário de conteúdo não encontrado")
        token = token_input["value"]
        known_suffixes = {
            "titulo",
            "ordem",
            "status",
            "tipo",
            "videoUrl",
            "repositorio",
            "caminho",
            "link",
            "forumRegistro",
            "projetoFase",
            "texto",
            "transcription",
            "tempo",
            "videoUrlBunny",
            "videoTranscriptionNivo",
            "capitulo",
            "urlS3Nivo",
            "_token",
            "file",
        }
        extra: dict[str, str] = {}
        for inp in soup.find_all("input"):
            name = inp.get("name", "")
            if not name.startswith(f"{prefix}["):
                continue
            suffix = name[len(prefix) + 1 : -1]
            if suffix not in known_suffixes:
                extra[name] = inp.get("value", "")
        return token, extra

    def _read_edit_conteudo_extra_fields(self, conteudo_id: int) -> dict[str, str]:
        form_url = f"{self.base_url}/admin/curso/conteudo/{conteudo_id}/edit"
        response = self.client.get(form_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        prefix = CONTEUDO_PREFIX
        known_suffixes = {
            "titulo",
            "ordem",
            "status",
            "tipo",
            "videoUrl",
            "repositorio",
            "caminho",
            "link",
            "forumRegistro",
            "projetoFase",
            "texto",
            "transcription",
            "tempo",
            "videoUrlBunny",
            "videoTranscriptionNivo",
            "capitulo",
            "urlS3Nivo",
            "_token",
            "file",
        }
        extra: dict[str, str] = {}
        for inp in soup.find_all("input"):
            name = inp.get("name", "")
            if not name.startswith(f"{prefix}["):
                continue
            suffix = name[len(prefix) + 1 : -1]
            if suffix not in known_suffixes:
                extra[name] = inp.get("value", "")
        for sel in soup.find_all("select"):
            name = sel.get("name", "")
            if not name.startswith(f"{prefix}["):
                continue
            suffix = name[len(prefix) + 1 : -1]
            if suffix not in known_suffixes:
                selected = sel.find("option", selected=True)
                extra[name] = selected.get("value", "") if selected else ""
        return extra

    def _conteudo_form_data(
        self,
        conteudo: ConteudoData,
        *,
        capitulo_ref: int,
        token: str,
        extra_fields: dict[str, str] | None = None,
    ) -> dict[str, str]:
        prefix = CONTEUDO_PREFIX
        data = {
            f"{prefix}[titulo]": conteudo.titulo,
            f"{prefix}[ordem]": str(conteudo.ordem),
            f"{prefix}[status]": conteudo.status,
            f"{prefix}[tipo]": conteudo.tipo,
            f"{prefix}[videoUrl]": conteudo.video_url,
            f"{prefix}[repositorio]": conteudo.repositorio,
            f"{prefix}[caminho]": conteudo.caminho,
            f"{prefix}[link]": conteudo.link,
            f"{prefix}[forumRegistro]": conteudo.forum_registro,
            f"{prefix}[projetoFase]": conteudo.projeto_fase,
            f"{prefix}[texto]": conteudo.texto,
            f"{prefix}[transcription]": conteudo.transcription,
            f"{prefix}[tempo]": conteudo.tempo,
            f"{prefix}[videoUrlBunny]": conteudo.video_url_bunny,
            f"{prefix}[videoTranscriptionNivo]": conteudo.video_transcription_nivo,
            f"{prefix}[capitulo]": str(capitulo_ref),
            f"{prefix}[urlS3Nivo]": conteudo.url_s3_nivo,
            f"{prefix}[_token]": token,
        }
        if extra_fields:
            data.update(extra_fields)
        return data

    def _post_conteudo(
        self,
        post_url: str,
        form_data: dict[str, str],
        *,
        method_override: str | None = None,
        allow_500: bool = False,
    ) -> None:
        payload: dict[str, Any] = dict(form_data)
        if method_override:
            payload["_method"] = method_override
        response = self.client.post(
            post_url,
            data=payload,
            files={f"{CONTEUDO_PREFIX}[file]": ("", b"", "application/octet-stream")},
            follow_redirects=False,
        )
        ok_statuses = (302, 303, 500) if allow_500 else (302, 303)
        if response.status_code not in ok_statuses:
            raise RuntimeError(
                f"Salvar conteúdo falhou com status {response.status_code}: "
                f"{response.text[:200]}"
            )

    def create_conteudo(self, capitulo_id: int, conteudo: ConteudoData) -> int:
        ids_antes = {
            linha.id
            for linha in self.listar_conteudos_tabela(capitulo_id, incluir_deletados=True)
        }
        token, extra_fields = self._read_new_conteudo_form(capitulo_id)
        form_data = self._conteudo_form_data(
            conteudo,
            capitulo_ref=capitulo_id,
            token=token,
            extra_fields=extra_fields,
        )
        response = self.client.post(
            f"{self.base_url}/admin/curso/conteudo/",
            data=form_data,
            files={f"{CONTEUDO_PREFIX}[file]": ("", b"", "application/octet-stream")},
            follow_redirects=False,
        )
        if response.status_code not in (302, 303, 500):
            raise RuntimeError(
                f"Criar conteúdo falhou com status {response.status_code}: "
                f"{response.text[:200]}"
            )
        alvo = conteudo.titulo.strip().lower()
        novos = [
            linha
            for linha in self.listar_conteudos_tabela(capitulo_id, incluir_deletados=True)
            if linha.id not in ids_antes
            and not linha.deletado
            and linha.titulo.strip().lower() == alvo
        ]
        if not novos:
            raise RuntimeError(
                f"Conteúdo '{conteudo.titulo}' não foi criado no capítulo {capitulo_id}"
            )
        return max(linha.id for linha in novos)

    def update_conteudo(self, conteudo_id: int, conteudo: ConteudoData) -> None:
        form_url = f"{self.base_url}/admin/curso/conteudo/{conteudo_id}/edit"
        token = self._get_conteudo_token(form_url)
        extra_fields = self._read_edit_conteudo_extra_fields(conteudo_id)
        form_data = self._conteudo_form_data(
            conteudo,
            capitulo_ref=conteudo_id,
            token=token,
            extra_fields=extra_fields,
        )
        self._post_conteudo(
            f"{self.base_url}/admin/curso/conteudo/{conteudo_id}",
            form_data,
            method_override="PUT",
            allow_500=True,
        )

    def salvar_url_s3_nivo(self, conteudo_id: int, s3_url: str) -> None:
        conteudo = self.get_conteudo(conteudo_id)
        conteudo.url_s3_nivo = s3_url if "?" in s3_url else s3_url.split("?", 1)[0]
        self.update_conteudo(conteudo_id, conteudo)

    def _conteudo_tem_video_na_tabela(
        self, capitulo_id: int, conteudo_id: int
    ) -> bool:
        for linha in self.listar_conteudos_tabela(capitulo_id):
            if linha.id == conteudo_id:
                return linha.tem_video
        return False

    def upload_video_chunked(
        self,
        video_path: Path,
        *,
        log: Callable[[str], None] | None = None,
        chunk_timeout: float = 300.0,
    ) -> str:
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Arquivo não encontrado: {video_path}")
        file_size = video_path.stat().st_size
        total_chunks = math.ceil(file_size / self.CHUNK_SIZE) or 1
        filename = video_path.name
        if log:
            log(
                f"Upload: {filename} "
                f"({file_size / 1024 / 1024:.1f} MB, {total_chunks} chunks)"
            )
        upload_url = f"{self.base_url}/admin/courses/upload/video-chunk"
        s3_url = ""
        with video_path.open("rb") as fh:
            for index in range(1, total_chunks + 1):
                chunk_data = fh.read(self.CHUNK_SIZE)
                if not chunk_data:
                    break
                progress = round(100 * index / total_chunks)
                if log:
                    log(f"  Chunk {index}/{total_chunks} ({progress}%)...")
                response = self.client.post(
                    upload_url,
                    files={"file": (filename, chunk_data, "application/octet-stream")},
                    data={
                        "nameFile": filename,
                        "index": str(index),
                        "totalChunks": str(total_chunks),
                    },
                    timeout=chunk_timeout,
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("status") == "error":
                    raise RuntimeError(
                        f"Servidor rejeitou chunk {index}: "
                        f"{payload.get('message', 'erro')}"
                    )
                if payload.get("status") == "success":
                    s3_url = payload["url"]
        if not s3_url:
            raise RuntimeError("Upload concluído, mas a URL S3 não foi retornada")
        if log:
            log(f"Upload concluído. URL: {mask_url(s3_url)}")
        return s3_url

    def upload_aula_video(
        self,
        conteudo_id: int,
        video_path: Path,
        *,
        capitulo_id: int | None = None,
        log: Callable[[str], None] | None = None,
        chunk_timeout: float = 300.0,
    ) -> None:
        s3_url = self.upload_video_chunked(
            video_path, log=log, chunk_timeout=chunk_timeout
        )
        if log:
            log(f"Salvando conteúdo {conteudo_id}...")
        prev_timeout = self.client.timeout
        try:
            self.client.timeout = 120.0
            self.salvar_url_s3_nivo(conteudo_id, s3_url)
            if capitulo_id is None:
                capitulo_id = self.get_conteudo_capitulo_id(conteudo_id)
            if capitulo_id is None:
                raise RuntimeError(
                    f"Não foi possível determinar o capítulo do conteúdo {conteudo_id}"
                )
            for _ in range(36):
                if self._conteudo_tem_video_na_tabela(capitulo_id, conteudo_id):
                    break
                time.sleep(5.0)
            else:
                conteudo = self.get_conteudo(conteudo_id)
                if not conteudo.url_s3_nivo and not conteudo.video_url_bunny:
                    raise RuntimeError(
                        f"Upload concluído, mas o conteúdo {conteudo_id} "
                        "não ficou com vídeo após salvar."
                    )
        finally:
            self.client.timeout = prev_timeout
        if log:
            log(f"Aula atualizada (conteúdo {conteudo_id}).")

    def criar_aula_com_video(
        self,
        capitulo_id: int,
        titulo: str,
        ordem: int,
        video_path: Path,
        *,
        status: str = "0",
        tipo: str = VIDEO_TIPO_NIVO,
        log: Callable[[str], None] | None = None,
        chunk_timeout: float = 300.0,
    ) -> int:
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Arquivo não encontrado: {video_path}")
        if log:
            log(f"Criando aula '{titulo}' (ordem {ordem})...")
        conteudo = ConteudoData(
            titulo=titulo, ordem=ordem, tipo=tipo, status=status
        )
        novo_id = self.create_conteudo(capitulo_id, conteudo)
        if log:
            log(f"Aula criada com ID {novo_id}.")
        self.upload_aula_video(
            novo_id,
            video_path,
            capitulo_id=capitulo_id,
            log=log,
            chunk_timeout=chunk_timeout,
        )
        return novo_id
