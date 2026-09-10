"""Catálogo local de cursos, capítulos e produtos."""

from __future__ import annotations

import json
import os
import re
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
    """Trecho em qualquer parte do nome; acento e caixa não importam."""
    query = query.strip()
    if not query:
        return True
    hay = nome_sort_key(text)
    return all(part in hay for part in nome_sort_key(query).split())


def course_search_blob(course: CatalogCourse, products: dict[str, CatalogProduct]) -> str:
    parts = [course.nome, str(course.id)]
    for pid in course.produto_ids:
        product = products.get(pid)
        if product is not None:
            parts.extend([product.nome, product.nome_curto, product.id])
    return " ".join(parts)


def course_matches(
    course: CatalogCourse,
    query: str,
    products: dict[str, CatalogProduct],
) -> bool:
    return matches_search(course_search_blob(course, products), query)


def product_slug(nome: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", nome_sort_key(nome)).strip("-")
    return slug or "produto"


@dataclass
class CatalogChapter:
    id: int
    nome: str
    ordem: int = 0


@dataclass
class CatalogProduct:
    id: str
    nome: str
    nome_curto: str = ""
    portal: str = ""


PORTAL_RANK = {"fullcycle": 0, "devops": 1}
PRODUCT_ORDER = {
    "mba-eng-ia": 0,
    "mba-arq": 1,
    "pos-techlead": 2,
    "pos-go": 3,
    "pos-aiops": 4,
}


@dataclass
class CatalogCourse:
    id: int
    nome: str
    updated_at: float = field(default_factory=time.time)
    chapters: list[CatalogChapter] = field(default_factory=list)
    produto_ids: list[str] = field(default_factory=list)


def _parse_products(payload: object) -> dict[str, CatalogProduct]:
    if not isinstance(payload, dict):
        return {}
    products: dict[str, CatalogProduct] = {}
    for raw in payload.get("products", []):
        if not isinstance(raw, dict):
            continue
        pid = str(raw.get("id") or "").strip()
        nome = str(raw.get("nome") or "").strip()
        if not pid or not nome:
            continue
        products[pid] = CatalogProduct(
            id=pid,
            nome=nome,
            nome_curto=str(raw.get("nome_curto") or "").strip() or nome,
            portal=str(raw.get("portal") or "").strip(),
        )
    return products


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
        produto_ids = [
            str(item).strip()
            for item in raw.get("produto_ids", [])
            if str(item).strip()
        ]
        course = CatalogCourse(
            id=int(raw["id"]),
            nome=str(raw["nome"]),
            updated_at=float(raw.get("updated_at", 0)),
            chapters=sorted(
                chapters,
                key=lambda chapter: (chapter.ordem, nome_sort_key(chapter.nome)),
            ),
            produto_ids=list(dict.fromkeys(produto_ids)),
        )
        courses[course.id] = course
    return courses


def load_seed_payload() -> dict:
    try:
        return json.loads(SEED_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {}


def load_seed_courses() -> dict[int, CatalogCourse]:
    """Cursos que já vêm no repositório para a pessoa usar no primeiro clone."""
    return _parse_courses(load_seed_payload())


def _merge_ids(left: list[str], right: list[str]) -> list[str]:
    return list(dict.fromkeys([*left, *right]))


class CatalogStore:
    """Armazena somente metadados de navegação no computador do usuário."""

    def __init__(self, path: Path | None = None, *, seed: bool | None = None) -> None:
        self.path = path or (config_dir() / "catalog.json")
        self._use_seed = seed if seed is not None else path is None
        self._lock = threading.RLock()
        self._courses, self._products = self._load()

    def _load(self) -> tuple[dict[int, CatalogCourse], dict[str, CatalogProduct]]:
        payload: dict = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                payload = {}
        courses = _parse_courses(payload)
        products = _parse_products(payload)
        if self._use_seed:
            seed = load_seed_payload()
            for product in _parse_products(seed).values():
                products[product.id] = product
            for course_id, seeded in _parse_courses(seed).items():
                existing = courses.get(course_id)
                if existing is None:
                    courses[course_id] = seeded
                    continue
                courses[course_id] = CatalogCourse(
                    id=existing.id,
                    nome=existing.nome or seeded.nome,
                    updated_at=existing.updated_at or seeded.updated_at,
                    chapters=existing.chapters or seeded.chapters,
                    produto_ids=_merge_ids(existing.produto_ids, seeded.produto_ids),
                )

        # Cards extras para facilitar seleção no UI: eles "re-agrupam" cursos
        # já mapeados em produtos existentes.
        curso_full_cycle_id = "curso-full-cycle"
        curso_goexpert_id = "curso-goexpert"
        curso_devops_id = "curso-devops"

        fullcycle_ids = {"mba-eng-ia", "mba-arq", "pos-techlead", "pos-go"}
        if curso_full_cycle_id in products or curso_goexpert_id in products or curso_devops_id in products:
            for course in courses.values():
                pids = set(course.produto_ids)
                if curso_full_cycle_id in products and (pids & fullcycle_ids):
                    course.produto_ids = _merge_ids(course.produto_ids, [curso_full_cycle_id])
                if curso_goexpert_id in products and ("pos-go" in pids):
                    course.produto_ids = _merge_ids(course.produto_ids, [curso_goexpert_id])
                if curso_devops_id in products and ("pos-aiops" in pids):
                    course.produto_ids = _merge_ids(course.produto_ids, [curso_devops_id])

        return courses, products

    def _save(self) -> None:
        """Grava de forma atômica: a sync em background pode escrever junto."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "products": [asdict(product) for product in self.products()],
            "courses": [asdict(course) for course in self.courses()],
        }
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

    def products(self) -> list[CatalogProduct]:
        with self._lock:
            return sorted(
                self._products.values(),
                key=lambda product: (
                    PORTAL_RANK.get(product.portal, 9),
                    PRODUCT_ORDER.get(product.id, 99),
                    nome_sort_key(product.nome_curto or product.nome),
                ),
            )

    def products_map(self) -> dict[str, CatalogProduct]:
        with self._lock:
            return dict(self._products)

    def get_product(self, product_id: str) -> CatalogProduct | None:
        with self._lock:
            return self._products.get(product_id)

    def courses(self) -> list[CatalogCourse]:
        with self._lock:
            return sorted(
                self._courses.values(),
                key=lambda course: nome_sort_key(course.nome),
            )

    def get_course(self, course_id: int) -> CatalogCourse | None:
        with self._lock:
            return self._courses.get(course_id)

    def ensure_product(self, nome: str) -> CatalogProduct:
        nome = nome.strip()
        if not nome:
            raise ValueError("Informe o nome do produto.")
        with self._lock:
            for product in self._products.values():
                if nome_sort_key(product.nome) == nome_sort_key(nome):
                    return product
            slug = product_slug(nome)
            pid = slug
            suffix = 2
            while pid in self._products:
                pid = f"{slug}-{suffix}"
                suffix += 1
            product = CatalogProduct(id=pid, nome=nome, nome_curto=nome)
            self._products[pid] = product
            self._save()
            return product

    def assign_product(
        self,
        curso_id: int,
        produto_id: str,
        *,
        nome: str = "",
    ) -> CatalogCourse:
        with self._lock:
            product = self._products.get(produto_id)
            if product is None:
                raise ValueError("Produto desconhecido.")
            course = self._courses.get(curso_id)
            if course is None:
                course = CatalogCourse(
                    id=curso_id,
                    nome=nome.strip() or f"Curso {curso_id}",
                )
            elif nome.strip() and (not course.nome or course.nome.startswith("Curso ")):
                course = CatalogCourse(
                    id=course.id,
                    nome=nome.strip(),
                    updated_at=course.updated_at,
                    chapters=course.chapters,
                    produto_ids=course.produto_ids,
                )
            if produto_id not in course.produto_ids:
                course.produto_ids = _merge_ids(course.produto_ids, [produto_id])
            course.updated_at = time.time()
            self._courses[curso_id] = course
            self._save()
            return course

    def unassign_product(self, curso_id: int, produto_id: str) -> CatalogCourse | None:
        with self._lock:
            course = self._courses.get(curso_id)
            if course is None:
                return None
            course.produto_ids = [pid for pid in course.produto_ids if pid != produto_id]
            course.updated_at = time.time()
            self._courses[curso_id] = course
            self._save()
            return course

    def upsert_course(
        self,
        curso: CursoInfo,
        chapters: list[CapituloInfo],
    ) -> None:
        with self._lock:
            existing = self._courses.get(curso.id)
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
                produto_ids=list(existing.produto_ids) if existing else [],
            )
            self._save()

    def upsert_chapter(self, summary: CapituloResumo) -> None:
        if summary.curso_id is None or not summary.curso_nome:
            return
        with self._lock:
            course = self._courses.get(summary.curso_id)
            chapters = list(course.chapters) if course else []
            produto_ids = list(course.produto_ids) if course else []
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
                produto_ids=produto_ids,
            )
            self._save()

    def remember_after_upload(
        self,
        portal: PortalClient,
        capitulo: CapituloResumo,
        *,
        uploaded: int,
    ) -> None:
        """Só entra no catálogo mapeado depois de subir pelo menos uma aula."""
        if uploaded <= 0 or capitulo.curso_id is None:
            return
        self.upsert_chapter(capitulo)
        self.sync_course_in_background(portal, capitulo.curso_id)

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
            background = PortalClient(base_url, username, "")
            try:
                for name, value, domain, path in cookies:
                    background.client.cookies.set(
                        name, value, domain=domain, path=path or "/"
                    )
                self.sync_course(background, course_id)
            except Exception:  # noqa: BLE001, S110
                pass
            finally:
                background.close()

        threading.Thread(target=sync, daemon=True, name="catalog-sync").start()
