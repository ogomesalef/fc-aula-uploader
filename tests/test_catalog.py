from aula_uploader.catalog import CatalogStore
from aula_uploader.portal_client import CapituloInfo, CapituloResumo, CursoInfo


def test_catalog_persists_course_and_chapters(tmp_path):
    path = tmp_path / "catalog.json"
    catalog = CatalogStore(path)
    catalog.upsert_course(
        CursoInfo(id=291, nome="Arquitetura na Era da IA"),
        [
            CapituloInfo(id=2276, nome="Teste", ordem=10, curso_id=291),
            CapituloInfo(id=2200, nome="Introdução", ordem=1, curso_id=291),
        ],
    )

    restored = CatalogStore(path).get_course(291)
    assert restored is not None
    assert restored.nome == "Arquitetura na Era da IA"
    assert [chapter.id for chapter in restored.chapters] == [2200, 2276]


def test_catalog_maps_a_single_chapter_when_course_is_known(tmp_path):
    catalog = CatalogStore(tmp_path / "catalog.json")
    catalog.upsert_chapter(
        CapituloResumo(
            id=2276,
            nome="Teste",
            curso_id=291,
            curso_nome="Arquitetura na Era da IA",
        )
    )

    course = catalog.get_course(291)
    assert course is not None
    assert course.chapters[0].nome == "Teste"


def test_catalog_updates_existing_chapter_name(tmp_path):
    catalog = CatalogStore(tmp_path / "catalog.json")
    catalog.upsert_chapter(
        CapituloResumo(
            id=2276,
            nome="Nome antigo",
            curso_id=291,
            curso_nome="Arquitetura na Era da IA",
        )
    )
    catalog.upsert_chapter(
        CapituloResumo(
            id=2276,
            nome="Nome novo",
            curso_id=291,
            curso_nome="Arquitetura na Era da IA",
        )
    )

    course = catalog.get_course(291)
    assert course is not None
    assert len(course.chapters) == 1
    assert course.chapters[0].nome == "Nome novo"


def test_sync_course_replaces_stale_chapters(tmp_path):
    catalog = CatalogStore(tmp_path / "catalog.json")
    catalog.upsert_course(
        CursoInfo(id=291, nome="Curso velho"),
        [CapituloInfo(id=1, nome="Capítulo velho", ordem=1, curso_id=291)],
    )

    class FakePortal:
        def inspect_curso(self, course_id: int) -> CursoInfo:
            assert course_id == 291
            return CursoInfo(id=291, nome="Curso atualizado")

        def list_capitulos(self, course_id: int) -> list[CapituloInfo]:
            assert course_id == 291
            return [
                CapituloInfo(id=1, nome="Capítulo renomeado", ordem=2, curso_id=291),
                CapituloInfo(id=2, nome="Capítulo novo", ordem=3, curso_id=291),
            ]

    course = catalog.sync_course(FakePortal(), 291)
    assert course.nome == "Curso atualizado"
    assert [(c.id, c.nome, c.ordem) for c in course.chapters] == [
        (1, "Capítulo renomeado", 2),
        (2, "Capítulo novo", 3),
    ]
    assert CatalogStore(tmp_path / "catalog.json").get_course(291).nome == "Curso atualizado"
