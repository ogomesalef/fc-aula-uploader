"""Catálogo local de cursos e capítulos já consultados no portal."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aula_uploader.portal_client import CapituloInfo, CapituloResumo, CursoInfo, PortalClient
from aula_uploader.session import config_dir


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


class CatalogStore:
    """Armazena somente metadados de navegação no computador do usuário."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "catalog.json")
        self._lock = threading.RLock()
        self._courses = self._load()

    def _load(self) -> dict[int, CatalogCourse]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            courses: dict[int, CatalogCourse] = {}
            for raw in payload.get("courses", []):
                chapters = [
                    CatalogChapter(**chapter) for chapter in raw.get("chapters", [])
                ]
                course = CatalogCourse(
                    id=int(raw["id"]),
                    nome=str(raw["nome"]),
                    updated_at=float(raw.get("updated_at", 0)),
                    chapters=chapters,
                )
                courses[course.id] = course
            return courses
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"courses": [asdict(course) for course in self.courses()]}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def courses(self) -> list[CatalogCourse]:
        with self._lock:
            return sorted(
                self._courses.values(),
                key=lambda course: course.nome.casefold(),
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
                    key=lambda chapter: (chapter.ordem, chapter.nome.casefold()),
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
                    key=lambda chapter: (chapter.ordem, chapter.nome.casefold()),
                ),
            )
            self._save()

    def sync_course(self, portal: PortalClient, course_id: int) -> CatalogCourse:
        """Busca o curso no portal e substitui o cache local daquele curso."""
        curso = portal.inspect_curso(course_id)
        chapters = portal.list_capitulos(course_id)
        self.upsert_course(curso, chapters)
        course = self.get_course(course_id)
        assert course is not None
        return course

    def sync_course_in_background(self, portal: PortalClient, course_id: int) -> None:
        """Atualiza o curso numa thread daemon; a TUI não espera a rede."""

        base_url = portal.base_url
        username = portal.username
        password = portal.password
        cookies = [
            (cookie.name, cookie.value, cookie.domain, cookie.path)
            for cookie in portal.client.cookies.jar
        ]

        def sync() -> None:
            background = PortalClient(base_url, username, password)
            try:
                for name, value, domain, path in cookies:
                    background.client.cookies.set(
                        name, value, domain=domain, path=path or "/"
                    )
                self.sync_course(background, course_id)
            except Exception:
                # Catálogo é conveniência local: nunca deve interromper upload.
                pass
            finally:
                background.close()

        threading.Thread(target=sync, daemon=True, name="catalog-sync").start()
