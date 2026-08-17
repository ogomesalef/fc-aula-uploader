"""Catálogo local de cursos e capítulos já consultados no portal."""

from __future__ import annotations

import json
import os
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aula_uploader.portal_client import CapituloInfo, CapituloResumo, CursoInfo, PortalClient
from aula_uploader.session import config_dir

SEED_CATALOG_PATH = Path(__file__).resolve().parent / "data" / "seed_catalog.json"


def nome_sort_key(nome: str) -> str:
    """Ordem alfabética ignorando acento e caixa (Arquitetura, Índice, Protocolos)."""
    decomposto = unicodedata.normalize("NFD", nome)
    sem_acento = "".join(
        ch for ch in decomposto if unicodedata.category(ch) != "Mn"
    )
    return sem_acento.casefold()


def matches_search(text: str, query: str) -> bool:
    """Trecho em qualquer parte do nome; acento e caixa não importam.

    ``comunicacao`` encontra ``Protocolos de Comunicação``.
    Várias palavras exigem que todas apareçam (em qualquer ordem).
    """
    query = query.strip()
    if not query:
        return True
    hay = nome_sort_key(text)
    return all(part in hay for part in nome_sort_key(query).split())


@dataclass
class CatalogChapter:
    id: int
    nome: str
    ordem: int = 0


@dataclass
class CatalogCourse:
    id: int
    nome: str
    updated_at: float = field(default_factory=time.time)
    chapters: list[CatalogChapter] = field(default_factory=list)


def _parse_courses(payload: object) -> dict[int, CatalogCourse]:
    if not isinstance(payload, dict):
        return {}
    courses: dict[int, CatalogCourse] = {}
    for raw in payload.get("courses", []):
        if not isinstance(raw, dict):
            continue
        chapters = [
            CatalogChapter(
                id=int(chapter["id"]),
                nome=str(chapter["nome"]),
                ordem=int(chapter.get("ordem", 0)),
            )
            for chapter in raw.get("chapters", [])
            if isinstance(chapter, dict)
        ]
        course = CatalogCourse(
            id=int(raw["id"]),
            nome=str(raw["nome"]),
            updated_at=float(raw.get("updated_at", 0)),
            chapters=sorted(
                chapters,
                key=lambda chapter: (chapter.ordem, nome_sort_key(chapter.nome)),
            ),
        )
        courses[course.id] = course
    return courses


def load_seed_courses() -> dict[int, CatalogCourse]:
    """Cursos que já vêm no repositório para a pessoa usar no primeiro clone."""
    try:
        payload = json.loads(SEED_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {}
    return _parse_courses(payload)


class CatalogStore:
    """Armazena somente metadados de navegação no computador do usuário."""

    def __init__(self, path: Path | None = None, *, seed: bool | None = None) -> None:
        self.path = path or (config_dir() / "catalog.json")
        # Seed só no catálogo padrão do usuário. Testes passam um path e ficam vazios.
        self._use_seed = seed if seed is not None else path is None
        self._lock = threading.RLock()
        self._courses = self._load()

    def _load(self) -> dict[int, CatalogCourse]:
        courses: dict[int, CatalogCourse] = {}
        if self.path.exists():
            try:
                courses = _parse_courses(
                    json.loads(self.path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                courses = {}
        if self._use_seed:
            for course_id, seeded in load_seed_courses().items():
                courses.setdefault(course_id, seeded)
        return courses

    def _save(self) -> None:
        """Grava de forma atômica: a sync em background pode escrever junto."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"courses": [asdict(course) for course in self.courses()]}
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            try:
                tmp.chmod(0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def courses(self) -> list[CatalogCourse]:
        with self._lock:
            return sorted(
                self._courses.values(),
                key=lambda course: nome_sort_key(course.nome),
            )

    def get_course(self, course_id: int) -> CatalogCourse | None:
        with self._lock:
            return self._courses.get(course_id)

    def upsert_course(
        self,
        curso: CursoInfo,
        chapters: list[CapituloInfo],
    ) -> None:
        with self._lock:
            self._courses[curso.id] = CatalogCourse(
                id=curso.id,
                nome=curso.nome,
                updated_at=time.time(),
                chapters=sorted(
                    [
                        CatalogChapter(
                            id=chapter.id,
                            nome=chapter.nome,
                            ordem=chapter.ordem,
                        )
                        for chapter in chapters
                    ],
                    key=lambda chapter: (chapter.ordem, nome_sort_key(chapter.nome)),
                ),
            )
            self._save()

    def upsert_chapter(self, summary: CapituloResumo) -> None:
        if summary.curso_id is None or not summary.curso_nome:
            return
        with self._lock:
            course = self._courses.get(summary.curso_id)
            chapters = list(course.chapters) if course else []
            updated = False
            for index, chapter in enumerate(chapters):
                if chapter.id == summary.id:
                    chapters[index] = CatalogChapter(
                        id=summary.id,
                        nome=summary.nome,
                        ordem=chapter.ordem,
                    )
                    updated = True
                    break
            if not updated:
                chapters.append(CatalogChapter(id=summary.id, nome=summary.nome))
            self._courses[summary.curso_id] = CatalogCourse(
                id=summary.curso_id,
                nome=summary.curso_nome,
                updated_at=time.time(),
                chapters=sorted(
                    chapters,
                    key=lambda chapter: (chapter.ordem, nome_sort_key(chapter.nome)),
                ),
            )
            self._save()

    def sync_course(self, portal: PortalClient, course_id: int) -> CatalogCourse:
        """Busca o curso no portal e substitui o cache local daquele curso."""
        curso = portal.inspect_curso(course_id)
        chapters = portal.list_capitulos(course_id)
        self.upsert_course(curso, chapters)
        course = self.get_course(course_id)
        if course is None:  # pragma: no cover - upsert_course sempre grava
            raise RuntimeError(f"Curso {course_id} não entrou no catálogo local")
        return course

    def sync_course_in_background(self, portal: PortalClient, course_id: int) -> None:
        """Atualiza o curso numa thread daemon; a TUI não espera a rede."""

        base_url = portal.base_url
        username = portal.username
        cookies = [
            (cookie.name, cookie.value, cookie.domain, cookie.path)
            for cookie in portal.client.cookies.jar
        ]

        def sync() -> None:
            # Sem senha: a thread só reusa cookies, nunca faz login sozinha.
            background = PortalClient(base_url, username, "")
            try:
                for name, value, domain, path in cookies:
                    background.client.cookies.set(
                        name, value, domain=domain, path=path or "/"
                    )
                self.sync_course(background, course_id)
            except Exception:  # noqa: BLE001, S110
                # Catálogo é conveniência local: nunca deve interromper upload.
                pass
            finally:
                background.close()

        threading.Thread(target=sync, daemon=True, name="catalog-sync").start()
