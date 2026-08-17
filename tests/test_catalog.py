import json
import stat
import threading

from aula_uploader.catalog import CatalogStore, matches_search
from aula_uploader.portal_client import CapituloInfo, CapituloResumo, CursoInfo


def test_matches_search_acha_trecho_no_meio_sem_acento():
    nome = "Protocolos de Comunicação"
    assert matches_search(nome, "comunicacao")
    assert matches_search(nome, "COMUNICAÇÃO")
    assert matches_search(nome, "protocolos comu")
    assert matches_search(f"{nome} 296", "296")
    assert not matches_search(nome, "arquitetura")
    assert matches_search(nome, "")
    assert matches_search(nome, "   ")


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


def test_courses_are_sorted_alphabetically(tmp_path):
    catalog = CatalogStore(tmp_path / "catalog.json")
    catalog.upsert_course(CursoInfo(id=2, nome="Protocolos de Comunicação"), [])
    catalog.upsert_course(CursoInfo(id=1, nome="Arquitetura na Era da IA"), [])
    catalog.upsert_course(CursoInfo(id=3, nome="índice"), [])
    assert [c.nome for c in catalog.courses()] == [
        "Arquitetura na Era da IA",
        "índice",
        "Protocolos de Comunicação",
    ]


def test_seed_preenche_catalogo_padrao_sem_arquivo(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    catalog = CatalogStore()
    nomes = [c.nome for c in catalog.courses()]
    assert nomes == [
        "Arquitetura na Era da IA",
        "Protocolos de Comunicação",
    ]
    assert catalog.get_course(291) is not None
    assert len(catalog.get_course(291).chapters) == 9
    assert catalog.get_course(296) is not None


def test_seed_nao_sobrescreve_curso_ja_mapeado(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "aula-uploader" / "catalog.json"
    path.parent.mkdir(parents=True)
    local = CatalogStore(path, seed=False)
    local.upsert_course(
        CursoInfo(id=291, nome="Nome local"),
        [CapituloInfo(id=1, nome="Só o meu", ordem=1, curso_id=291)],
    )
    catalog = CatalogStore()  # path padrão = config_dir/catalog.json
    assert catalog.get_course(291).nome == "Nome local"
    assert [c.nome for c in catalog.get_course(291).chapters] == ["Só o meu"]
    # O outro curso do seed continua disponível.
    assert catalog.get_course(296) is not None


def test_catalog_sem_path_de_teste_nao_injeta_seed(tmp_path):
    catalog = CatalogStore(tmp_path / "catalog.json")
    assert catalog.courses() == []


def test_catalog_escrita_e_atomica_com_upserts_concorrentes(tmp_path):
    # A sync em background escreve no mesmo arquivo que o fluxo principal.
    path = tmp_path / "catalog.json"
    catalog = CatalogStore(path)
    erros: list[Exception] = []

    def gravar(course_id: int) -> None:
        try:
            for rodada in range(20):
                catalog.upsert_course(
                    CursoInfo(id=course_id, nome=f"Curso {course_id}"),
                    [
                        CapituloInfo(
                            id=course_id * 100 + rodada,
                            nome=f"Cap {rodada}",
                            ordem=rodada,
                            curso_id=course_id,
                        )
                    ],
                )
        except Exception as exc:  # noqa: BLE001 - reportado no assert
            erros.append(exc)

    threads = [threading.Thread(target=gravar, args=(cid,)) for cid in (1, 2, 3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert erros == []
    # O arquivo final sempre é um JSON completo, nunca meio escrito.
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert {curso["id"] for curso in payload["courses"]} == {1, 2, 3}
    assert not list(tmp_path.glob("*.tmp"))


def test_catalog_tem_permissao_restrita(tmp_path):
    path = tmp_path / "catalog.json"
    CatalogStore(path).upsert_course(CursoInfo(id=1, nome="Curso"), [])
    assert not path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)
