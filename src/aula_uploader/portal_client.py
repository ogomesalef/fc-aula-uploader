"""Cliente HTTP mínimo do admin: auth, conteúdo de vídeo e upload."""

from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

SESSION_COOKIE_NAMES = frozenset({"PHPSESSID", "REMEMBERME"})
AUTH_CACHE_SECONDS = 300
CAPITULO_PREFIX = "son_cursosbundle_capitulo"
CONTEUDO_PREFIX = "son_cursosbundle_conteudotype"
VIDEO_TIPO_NIVO = "12"
CURSO_EDIT_HREF = re.compile(r"/admin/curso/(\d+)/edit")
CURSO_SEARCH_MAX_PAGES = 15


def _fold_text(text: str) -> str:
    """Caixa-baixa sem acento: comunicação → comunicacao."""
    decomposto = unicodedata.normalize("NFD", text)
    sem_acento = "".join(
        ch for ch in decomposto if unicodedata.category(ch) != "Mn"
    )
    return sem_acento.casefold()


def search_query_variants(query: str) -> list[str]:
    """Texto original e versão sem acento, se forem diferentes."""
    query = query.strip()
    if not query:
        return []
    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFD", query)
        if unicodedata.category(ch) != "Mn"
    )
    variants: list[str] = []
    seen: set[str] = set()
    for value in (query, folded):
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            variants.append(value)
    return variants


def _text_matches(text: str, query: str) -> bool:
    query = query.strip()
    if not query:
        return True
    hay = _fold_text(text)
    return all(part in hay for part in _fold_text(query).split())


def parse_curso_search_page(html: str) -> tuple[list[CursoInfo], set[int]]:
    """Extrai cursos da listagem /admin/curso/ e os números de página do pager."""
    soup = BeautifulSoup(html, "html.parser")
    cursos: list[CursoInfo] = []
    seen: set[int] = set()
    for row in soup.select("table tbody tr"):
        edit = row.select_one('a[href*="/admin/curso/"][href*="/edit"]')
        if not edit:
            continue
        match = CURSO_EDIT_HREF.search(str(edit.get("href", "")))
        if not match:
            continue
        curso_id = int(match.group(1))
        if curso_id in seen:
            continue
        nome = ""
        delete = row.select_one("[data-curso-nome]")
        if delete:
            nome = str(delete.get("data-curso-nome") or "").strip()
        if not nome:
            cells = row.find_all("td")
            if len(cells) > 2:
                br = cells[2].find("br")
                if br is not None:
                    prev = br.previous_sibling
                    nome = str(prev).strip() if prev else ""
                if not nome:
                    nome = cells[2].get_text(" ", strip=True)
        seen.add(curso_id)
        cursos.append(CursoInfo(id=curso_id, nome=nome or f"Curso {curso_id}"))
    pages: set[int] = set()
    for link in soup.find_all("a", href=True):
        page_match = re.search(r"[?&]page=(\d+)", str(link["href"]))
        if page_match:
            pages.add(int(page_match.group(1)))
    return cursos, pages


@dataclass
class CursoInfo:
    id: int
    nome: str


@dataclass
class CapituloInfo:
    id: int
    nome: str
    ordem: int = 0
    curso_id: int = 0


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
    MAX_REDIRECTS = 5

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
        # Redirects são seguidos manualmente para os cookies de sessão nunca
        # saírem do host do portal (ver _follow).
        self.client = httpx.Client(
            follow_redirects=False,
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

    def _is_portal_url(self, url: httpx.URL) -> bool:
        expected = urlparse(self.base_url).hostname or ""
        return url.scheme == "https" and (url.host or "") == expected

    def _follow(self, response: httpx.Response) -> httpx.Response:
        """Segue redirects só dentro do host do portal."""
        for _ in range(self.MAX_REDIRECTS):
            if response.next_request is None:
                return response
            if not self._is_portal_url(response.next_request.url):
                raise RuntimeError(
                    "Redirect para fora do portal foi bloqueado "
                    f"({response.next_request.url.host})"
                )
            response = self.client.send(response.next_request)
        raise RuntimeError("Excesso de redirects do portal")

    def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._follow(self.client.get(url, **kwargs))

    def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._follow(self.client.post(url, **kwargs))

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
        """Grava só os cookies de sessão do portal (nunca o jar inteiro)."""
        if not self.session_path:
            return
        host = urlparse(self.base_url).hostname or ""
        cookies: list[dict[str, str]] = []
        for cookie in self.client.cookies.jar:
            if cookie.name not in SESSION_COOKIE_NAMES:
                continue
            domain = (cookie.domain or "").lstrip(".")
            if domain and not (host == domain or host.endswith(f".{domain}")):
                continue
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value or "",
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
        # Sem _get: o redirect aqui é o próprio sinal de sessão expirada.
        response = self.client.get(f"{self.base_url}/admin/curso/")
        if response.status_code == 200:
            ok = True
        elif response.status_code in (301, 302, 303, 307, 308):
            # Só conta como autenticado se continuar dentro do admin do portal:
            # sessão expirada redireciona para /login, e um Location externo
            # nunca deve valer como prova de sessão.
            target = response.url.join(response.headers.get("location", ""))
            path = (target.path or "").lower()
            ok = (
                self._is_portal_url(target)
                and path.startswith("/admin")
                and "login" not in path
            )
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
        login_page = self._get(f"{self.base_url}/login")
        login_page.raise_for_status()
        soup = BeautifulSoup(login_page.text, "html.parser")
        csrf_input = soup.find("input", {"name": "_csrf_token"})
        if not csrf_input or not csrf_input.get("value"):
            raise RuntimeError("Token CSRF de login não encontrado")
        response = self._post(
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

    def _get_capitulo_token(self, form_url: str) -> str:
        response = self._get(form_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        token_input = soup.find("input", {"name": f"{CAPITULO_PREFIX}[_token]"})
        if not token_input or not token_input.get("value"):
            raise RuntimeError("Token do formulário de capítulo não encontrado")
        return str(token_input["value"])

    def inspect_curso(self, curso_id: int) -> CursoInfo:
        """Consulta e confirma o curso de destino antes de criar capítulo."""
        response = self._get(f"{self.base_url}/admin/curso/{curso_id}/edit")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title_input = soup.find("input", {"name": re.compile(r"\[nome\]$")})
        nome = ""
        if title_input:
            nome = str(title_input.get("value", "")).strip()
        if not nome:
            heading = soup.find(["h1", "h2", "h3"])
            nome = heading.get_text(" ", strip=True) if heading else ""
        if not nome:
            raise RuntimeError(f"Não foi possível identificar o nome do curso {curso_id}")
        return CursoInfo(id=curso_id, nome=nome)

    def buscar_cursos(self, query: str) -> list[CursoInfo]:
        """Lista cursos em /admin/curso/?string=… (trecho do nome; tenta com e sem acento)."""
        query = query.strip()
        if not query:
            return []
        merged: dict[int, CursoInfo] = {}
        for variant in search_query_variants(query):
            pending = {1}
            seen_pages: set[int] = set()
            while pending:
                page = min(pending)
                pending.remove(page)
                if page in seen_pages or page > CURSO_SEARCH_MAX_PAGES:
                    continue
                seen_pages.add(page)
                params: dict[str, str | int] = {"string": variant}
                if page > 1:
                    params["page"] = page
                response = self._get(f"{self.base_url}/admin/curso/", params=params)
                response.raise_for_status()
                cursos, pages = parse_curso_search_page(response.text)
                for curso in cursos:
                    merged[curso.id] = curso
                for other in pages:
                    if other not in seen_pages and other <= CURSO_SEARCH_MAX_PAGES:
                        pending.add(other)
        results = list(merged.values())
        filtered = [curso for curso in results if _text_matches(f"{curso.nome} {curso.id}", query)]
        return filtered

    def list_capitulos(self, curso_id: int) -> list[CapituloInfo]:
        response = self._get(
            f"{self.base_url}/admin/curso/capitulo/{curso_id}/curso"
        )
        response.raise_for_status()
        capitulos: list[CapituloInfo] = []
        soup = BeautifulSoup(response.text, "html.parser")
        for row in soup.select("table tbody tr"):
            edit_link = row.select_one(
                f'a[href*="/admin/curso/capitulo/"][href*="/edit/{curso_id}/curso"]'
            )
            if not edit_link:
                continue
            href = str(edit_link.get("href", ""))
            parts = href.strip("/").split("/")
            try:
                capitulo_id = int(parts[parts.index("capitulo") + 1])
            except (ValueError, IndexError):
                continue
            cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
            nome = cells[1] if len(cells) > 1 else ""
            ordem = int(cells[2]) if len(cells) > 2 and cells[2].isdigit() else 0
            capitulos.append(
                CapituloInfo(
                    id=capitulo_id,
                    nome=nome,
                    ordem=ordem,
                    curso_id=curso_id,
                )
            )
        return capitulos

    def create_capitulo(
        self,
        curso_id: int,
        nome: str,
        ordem: int,
        *,
        bunny_folder_id: str,
    ) -> CapituloInfo:
        """Cria capítulo vinculado a uma pasta Bunny já existente."""
        before_ids = {chapter.id for chapter in self.list_capitulos(curso_id)}
        form_url = f"{self.base_url}/admin/curso/capitulo/new/{curso_id}/curso"
        token = self._get_capitulo_token(form_url)
        response = self.client.post(
            f"{self.base_url}/admin/curso/capitulo/{curso_id}/curso",
            data={
                f"{CAPITULO_PREFIX}[nome]": nome,
                f"{CAPITULO_PREFIX}[ordem]": str(ordem),
                f"{CAPITULO_PREFIX}[finalizado]": "1",
                f"{CAPITULO_PREFIX}[ativo]": "1",
                f"{CAPITULO_PREFIX}[gerenciamentoProgresso]": "1",
                f"{CAPITULO_PREFIX}[bunnyFolderId]": bunny_folder_id,
                f"{CAPITULO_PREFIX}[_token]": token,
            },
            follow_redirects=False,
        )
        if response.status_code not in (302, 303):
            raise RuntimeError(
                f"Criação do capítulo falhou (HTTP {response.status_code})"
            )

        created = [
            chapter
            for chapter in self.list_capitulos(curso_id)
            if chapter.id not in before_ids
            and chapter.nome.strip().casefold() == nome.strip().casefold()
        ]
        if not created:
            raise RuntimeError(
                f"Capítulo '{nome}' não foi encontrado após a criação."
            )
        return max(created, key=lambda chapter: chapter.id)

    def _get_conteudo_token(self, form_url: str) -> str:
        response = self._get(form_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        token_input = soup.find("input", {"name": f"{CONTEUDO_PREFIX}[_token]"})
        if not token_input or not token_input.get("value"):
            raise RuntimeError("Token do formulário de conteúdo não encontrado")
        return token_input["value"]

    def listar_conteudos_tabela(
        self, capitulo_id: int, *, incluir_deletados: bool = False
    ) -> list[ConteudoLinha]:
        response = self._get(
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
        """Consulta o capítulo e resolve nome real + curso (nome e ID)."""
        url = f"{self.base_url}/admin/curso/conteudo/{capitulo_id}/capitulo"
        response = self._get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        curso_id = self._extract_curso_id_from_conteudo_page(soup, capitulo_id)
        nome = ""
        curso_nome = ""

        if curso_id is not None:
            nome = self._get_capitulo_nome(capitulo_id, curso_id) or ""
            try:
                curso_nome = self.inspect_curso(curso_id).nome
            except Exception:  # noqa: BLE001 - fallback abaixo
                curso_nome = ""

        if not nome or nome.strip().casefold() in {"conteúdo", "conteudo"}:
            heading = soup.find(["h1", "h2", "h3"])
            heading_text = heading.get_text(" ", strip=True) if heading else ""
            if heading_text and heading_text.casefold() not in {"conteúdo", "conteudo"}:
                nome = heading_text
            else:
                nome = f"Capítulo {capitulo_id}"

        return CapituloResumo(
            id=capitulo_id,
            nome=nome,
            curso_id=curso_id,
            curso_nome=curso_nome,
        )

    def _extract_curso_id_from_conteudo_page(
        self, soup: BeautifulSoup, capitulo_id: int
    ) -> int | None:
        for link in soup.find_all("a", href=True):
            href = str(link.get("href", ""))
            match = re.search(
                rf"/admin/curso/capitulo/{capitulo_id}/edit/(\d+)/curso",
                href,
            )
            if match:
                return int(match.group(1))

        for link in soup.find_all("a", href=True):
            href = str(link.get("href", ""))
            if "/edit/" in href:
                continue
            match = re.search(r"/admin/curso/capitulo/(\d+)/curso(?:\?|$|/)", href)
            if match:
                return int(match.group(1))

        for link in soup.find_all("a", href=True):
            href = str(link.get("href", ""))
            match = re.search(r"/admin/curso/(\d+)/edit(?:\?|$|/)", href)
            if match:
                return int(match.group(1))
        return None

    def _get_capitulo_nome(self, capitulo_id: int, curso_id: int) -> str:
        response = self._get(
            f"{self.base_url}/admin/curso/capitulo/{capitulo_id}/edit/{curso_id}/curso"
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        return self._field_value(soup, f"{CAPITULO_PREFIX}[nome]").strip()

    def get_conteudo(self, conteudo_id: int, *, retries: int = 3) -> ConteudoData:
        url = f"{self.base_url}/admin/curso/conteudo/{conteudo_id}/edit"
        last_status = None
        response = None
        for attempt in range(retries):
            response = self._get(url)
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
        response = self._get(
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
        response = self._get(form_url)
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
        response = self._get(form_url)
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
            # Sem corpo da resposta: o HTML do admin pode conter token/CSRF.
            raise RuntimeError(
                f"Salvar conteúdo falhou (HTTP {response.status_code})"
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
                f"Criar conteúdo falhou (HTTP {response.status_code})"
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
        # O portal espera de volta exatamente a URL que ele devolveu no upload.
        conteudo.url_s3_nivo = s3_url
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
                response = self._post(
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
            log("Upload concluído.")
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
