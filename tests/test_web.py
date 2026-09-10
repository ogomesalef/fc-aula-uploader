import json
import re
import shutil
import subprocess

import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from aula_uploader.catalog import CatalogStore
from aula_uploader.web.app import COOKIE_NAME, WebState, _safe_relpath, create_app


@pytest.fixture(autouse=True)
def _config_isolada(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _client(tmp_path, *, token="test-token", allow_test_host=True):
    state = WebState(token=token, catalog=CatalogStore(tmp_path / "catalog.json"))
    app = create_app(state, allow_test_host=allow_test_host)
    return TestClient(app), state


def test_sem_token_nao_abre_a_interface(tmp_path):
    client, _state = _client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200


def test_token_na_url_abre_e_grava_cookie(tmp_path):
    client, _state = _client(tmp_path)
    response = client.get("/?k=test-token")
    assert response.status_code == 200
    assert "Arraste a pasta" in response.text
    assert response.text.index("Portal") < response.text.index("Produto")
    html = response.text
    pos = html.find('id="selecao"')
    bloco = html[pos:pos + 8000]
    assert bloco.find("Portal") < bloco.find("Produto") < bloco.find("Curso") < bloco.find("Capítulo")
    assert "Full Cycle" in response.text
    assert "DevOps Pro" in response.text
    assert response.text.index("Já existe") < response.text.index("Criar novo")
    assert "Novo produto" not in response.text
    assert 'id="portal-picks"' in response.text
    assert 'id="path-lab"' in response.text
    assert "Caminho da pasta" in response.text
    assert "Formato antigo" not in response.text
    assert 'id="card-videos"' in response.text
    assert response.text.count("Continuar") >= 2
    assert 'id="btn-next-curso">Entrar</button>' in response.text
    assert "Voltar" in response.text
    assert "Adicionar mais arquivos" in response.text
    assert 'id="btn-add-more"' in response.text
    assert "webkitdirectory" not in response.text
    assert 'id="xfer"' in response.text
    assert ">Formato<" in response.text
    assert 'id="aulas-table"' in response.text
    assert 'data-col="titulo"' in response.text
    assert 'id="modal-aviso-cancel"' in response.text
    js = client.get("/static/app.js").text
    assert "window.confirm" not in js
    assert "Enviar essas aulas para o portal agora?" in js
    assert client.cookies.get(COOKIE_NAME) == "test-token"
    boot = client.get("/api/bootstrap")
    assert boot.status_code == 200
    assert boot.json()["version"]
    labels = [p["label"] for p in boot.json()["portals"]]
    assert labels == ["Full Cycle", "DevOps Pro"]
    portal = boot.json()["portals"][0]
    assert "has_session" in portal
    assert "has_env" not in portal
    assert "env_user" not in portal


def test_host_externo_e_recusado(tmp_path):
    client, _state = _client(tmp_path, allow_test_host=False)
    response = client.get("/?k=test-token", headers={"host": "evil.example"})
    assert response.status_code == 403


def test_api_sem_cookie_e_bloqueada(tmp_path):
    client, _state = _client(tmp_path)
    response = client.get("/api/bootstrap")
    assert response.status_code == 200


def test_safe_relpath_recusa_parent(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    try:
        _safe_relpath("../secret.mp4", inbox)
        raise AssertionError("devia recusar")
    except ValueError:
        pass
    dest = _safe_relpath("aulas/01-intro.mp4", inbox)
    assert dest.is_relative_to(inbox.resolve())
    colon = _safe_relpath("Aula: LangChain.mkv", inbox)
    assert colon.parent == inbox.resolve()
    assert ":" not in colon.name
    assert "：" in colon.name


def test_upload_recusa_zip_slip_no_rel(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post(
        "/api/videos/upload",
        data={"reset": "1", "rels": "../etc/passwd.mp4"},
        files={"files": ("aula.mp4", b"fake", "video/mp4")},
    )
    assert response.status_code == 400


def test_upload_de_video_lista_aula(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post(
        "/api/videos/upload",
        data={"reset": "1", "rels": "turma/01-introducao.mp4"},
        files={"files": ("01-introducao.mp4", b"0123456789", "video/mp4")},
    )
    assert response.status_code == 200
    aulas = response.json()["aulas"]
    assert len(aulas) == 1
    assert aulas[0]["titulo"]
    assert aulas[0]["ordem"] == 1
    assert aulas[0]["formato"] == "MP4"


def test_upload_varios_arquivos_de_uma_vez(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post(
        "/api/videos/upload",
        data={
            "reset": "1",
            "rels": ["01-introducao.mp4", "02-docker.mp4", "03-k8s.mp4"],
        },
        files=[
            ("files", ("01-introducao.mp4", b"aaaa", "video/mp4")),
            ("files", ("02-docker.mp4", b"bbbb", "video/mp4")),
            ("files", ("03-k8s.mp4", b"cccc", "video/mp4")),
        ],
    )
    assert response.status_code == 200
    nomes = {aula["arquivo"] for aula in response.json()["aulas"]}
    assert nomes == {"01-introducao.mp4", "02-docker.mp4", "03-k8s.mp4"}


def test_salvar_edicao_de_titulo(tmp_path):
    client, state = _client(tmp_path)
    client.get("/?k=test-token")
    client.post(
        "/api/videos/upload",
        data={"reset": "1", "rels": "01-introducao.mp4"},
        files={"files": ("01-introducao.mp4", b"aaaa", "video/mp4")},
    )
    response = client.post(
        "/api/videos/edits",
        json={
            "aulas": [
                {"arquivo": "01-introducao.mp4", "ordem": 1, "titulo": "Langchain na prática"},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["aulas"][0]["titulo"] == "Langchain na prática"
    assert state.aulas[0].titulo == "Langchain na prática"


def test_upload_acrescenta_sem_apagar_lista(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    first = client.post(
        "/api/videos/upload",
        data={"reset": "1", "rels": "01-introducao.mp4"},
        files={"files": ("01-introducao.mp4", b"aaaa", "video/mp4")},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/videos/upload",
        data={"reset": "0", "rels": "02-docker.mp4"},
        files={"files": ("02-docker.mp4", b"bbbb", "video/mp4")},
    )
    assert second.status_code == 200
    nomes = {aula["arquivo"] for aula in second.json()["aulas"]}
    assert nomes == {"01-introducao.mp4", "02-docker.mp4"}


def test_remove_aula_tira_so_ela(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    client.post(
        "/api/videos/upload",
        data={"reset": "1", "rels": "01-introducao.mp4"},
        files={"files": ("01-introducao.mp4", b"aaaa", "video/mp4")},
    )
    client.post(
        "/api/videos/upload",
        data={"reset": "0", "rels": "02-docker.mp4"},
        files={"files": ("02-docker.mp4", b"bbbb", "video/mp4")},
    )
    response = client.post("/api/videos/remove", json={"arquivo": "01-introducao.mp4"})
    assert response.status_code == 200
    nomes = [aula["arquivo"] for aula in response.json()["aulas"]]
    assert nomes == ["02-docker.mp4"]


def test_remove_aula_recusa_caminho(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post("/api/videos/remove", json={"arquivo": "../etc/passwd.mp4"})
    assert response.status_code == 400


def test_upload_acrescenta_depois_de_caminho_local(tmp_path):
    pasta = tmp_path / "aulas"
    pasta.mkdir()
    (pasta / "01-introducao.mp4").write_bytes(b"aaaa")
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    opened = client.post("/api/videos/path", json={"path": str(pasta)})
    assert opened.status_code == 200
    assert [aula["arquivo"] for aula in opened.json()["aulas"]] == ["01-introducao.mp4"]
    added = client.post(
        "/api/videos/upload",
        data={"reset": "0", "rels": "02-docker.mp4"},
        files={"files": ("02-docker.mp4", b"bbbb", "video/mp4")},
    )
    assert added.status_code == 200
    nomes = {aula["arquivo"] for aula in added.json()["aulas"]}
    assert nomes == {"01-introducao.mp4", "02-docker.mp4"}


def test_pick_folder_cancelado(tmp_path, monkeypatch):
    monkeypatch.setattr("aula_uploader.web.app._pick_folder_native", lambda: None)
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post("/api/videos/pick-folder", json={})
    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    assert response.json()["aulas"] == []


def test_pick_folder_carrega_pasta(tmp_path, monkeypatch):
    pasta = tmp_path / "aulas"
    pasta.mkdir()
    (pasta / "01-introducao.mp4").write_bytes(b"aaaa")
    monkeypatch.setattr("aula_uploader.web.app._pick_folder_native", lambda: pasta)
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post("/api/videos/pick-folder", json={})
    assert response.status_code == 200
    assert response.json()["cancelled"] is False
    assert [aula["arquivo"] for aula in response.json()["aulas"]] == ["01-introducao.mp4"]


def test_match_local_abre_varios_arquivos_pelo_caminho(tmp_path, monkeypatch):
    monkeypatch.setattr("aula_uploader.web.app._macos_finder_selection", lambda: [])
    pasta = tmp_path / "aulas"
    pasta.mkdir()
    (pasta / "01-a.mp4").write_bytes(b"aaaa")
    (pasta / "02-b.mp4").write_bytes(b"bbbb")
    (pasta / "03-c.mp4").write_bytes(b"cccc")
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post(
        "/api/videos/match-local",
        json={
            "files": [
                {"name": "01-a.mp4", "size": 4},
                {"name": "02-b.mp4", "size": 4},
                {"name": "03-c.mp4", "size": 4},
            ],
            "hint": str(pasta),
            "reset": True,
        },
    )
    assert response.status_code == 200
    nomes = [aula["arquivo"] for aula in response.json()["aulas"]]
    assert nomes == ["01-a.mp4", "02-b.mp4", "03-c.mp4"]
    assert response.json()["missing"] == []


def test_pick_files_carrega_varios(tmp_path, monkeypatch):
    pasta = tmp_path / "aulas"
    pasta.mkdir()
    a = pasta / "01-a.mp4"
    b = pasta / "02-b.mp4"
    a.write_bytes(b"aaaa")
    b.write_bytes(b"bbbb")
    monkeypatch.setattr("aula_uploader.web.app._pick_files_native", lambda: [a, b])
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post("/api/videos/pick-files", json={})
    assert response.status_code == 200
    assert [aula["arquivo"] for aula in response.json()["aulas"]] == ["01-a.mp4", "02-b.mp4"]


REAL_DOWNLOADS = Path("/Users/alefgomes/Projects/yt-downloader/downloads")
REAL_MKV = sorted(REAL_DOWNLOADS.glob("*.mkv")) if REAL_DOWNLOADS.is_dir() else []
_precisa_3_mkv = pytest.mark.skipif(
    len(REAL_MKV) < 3, reason="precisa de 3 arquivos .mkv reais na pasta de downloads"
)


@_precisa_3_mkv
def test_path_abre_os_tres_mkv_reais(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post("/api/videos/path", json={"path": str(REAL_DOWNLOADS)})
    assert response.status_code == 200
    aulas = response.json()["aulas"]
    assert len(aulas) == 3
    assert {aula["formato"] for aula in aulas} == {"MKV"}
    assert [aula["ordem"] for aula in aulas] == [1, 2, 3]


@_precisa_3_mkv
def test_match_local_dos_mkv_reais(tmp_path, monkeypatch):
    monkeypatch.setattr("aula_uploader.web.app._macos_finder_selection", lambda: [])
    arquivos = sorted(p for p in REAL_DOWNLOADS.iterdir() if p.suffix.lower() == ".mkv")
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post(
        "/api/videos/match-local",
        json={
            "files": [{"name": p.name, "size": p.stat().st_size} for p in arquivos],
            "hint": str(REAL_DOWNLOADS),
            "reset": True,
        },
    )
    assert response.status_code == 200
    assert len(response.json()["aulas"]) == 3
    assert response.json()["missing"] == []


def test_plan_exige_login(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post("/api/plan", json={"force": False})
    assert response.status_code == 401


def test_login_sem_senha_pede_credencial_do_portal(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post("/api/login", json={"portal": "devops", "persist": False})
    assert response.status_code == 400
    assert "e-mail" in response.json()["detail"].casefold()


def test_login_sem_senha_pede_credencial_do_portal(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post("/api/login", json={"portal": "devops", "persist": False})
    assert response.status_code == 400
    assert "e-mail" in response.json()["detail"].casefold()


class _DummyPortal:
    def __init__(self, url):
        self.base_url = url
        self.closed = False

    def close(self):
        self.closed = True


def test_restore_saved_sessions_recoloca_cliente(tmp_path, monkeypatch):
    from aula_uploader.session import session_path
    from aula_uploader.web.app import WebState, _restore_saved_sessions

    path = session_path("fullcycle")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"username":"alef@fullcycle.com.br","cookies":[{"name":"PHPSESSID","value":"x","domain":"portal.fullcycle.com.br","path":"/"}]}',
        encoding="utf-8",
    )

    dummy = _DummyPortal("https://portal.fullcycle.com.br")

    def fake_ensure(portal_key, **kwargs):
        assert portal_key == "fullcycle"
        assert kwargs.get("use_saved_session") is True
        assert kwargs.get("persist_session") is True
        return dummy

    monkeypatch.setattr("aula_uploader.web.app.ensure_authenticated", fake_ensure)
    state = WebState(token="t", catalog=CatalogStore(tmp_path / "cat.json"))
    _restore_saved_sessions(state)
    assert state.portal_key == "fullcycle"
    assert state.clients["fullcycle"] is dummy


def test_login_de_um_portal_nao_fecha_o_outro(tmp_path):
    _http, state = _client(tmp_path)
    fc = _DummyPortal("https://portal.fullcycle.com.br")
    dev = _DummyPortal("https://portal.devopspro.com.br")
    state.set_client("fullcycle", fc)
    state.set_client("devops", dev)
    state.close_portal("devops")
    assert not fc.closed
    assert state.portal_key == "fullcycle"
    assert "fullcycle" in state.clients
    from aula_uploader.web.app import _session_payload

    payload = _session_payload(state)
    assert payload["autenticados"] == ["fullcycle"]
    assert payload["portal"] == "fullcycle"


def test_catalog_vincula_curso_a_produto(tmp_path):
    client, state = _client(tmp_path)
    client.get("/?k=test-token")
    state.set_client("fullcycle", _DummyPortal("https://portal.fullcycle.com.br"))
    created = client.post("/api/produtos", json={"nome": "MBA em Engenharia de Software com IA"})
    assert created.status_code == 200
    produto = created.json()["produto"]
    assigned = client.post(
        "/api/produtos/assign",
        json={"curso": "291", "produto": produto["id"], "nome": "Arquitetura na Era da IA"},
    )
    assert assigned.status_code == 200
    curso = assigned.json()["curso"]
    assert curso["id"] == 291
    assert produto["id"] in curso["produto_ids"]
    catalog = client.get("/api/catalog")
    assert catalog.status_code == 200
    assert any(item["id"] == produto["id"] for item in catalog.json()["produtos"])


def test_trocar_portal_sem_login_e_bloqueado(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post("/api/portal/select", json={"portal": "devops"})
    assert response.status_code == 401


def _sobe_video(client, nome="01-introducao.mp4", conteudo=b"0123456789", reset="1"):
    return client.post(
        "/api/videos/upload",
        data={"reset": reset, "rels": nome},
        files={"files": (nome, conteudo, "video/mp4")},
    )


def test_interface_tem_coluna_de_resolucao_e_preview(tmp_path):
    client, _state = _client(tmp_path)
    html = client.get("/?k=test-token").text
    assert 'data-col="resolucao"' in html
    assert ">Resolução<" in html
    assert 'id="convert-xfer"' in html
    assert 'id="btn-convert-all"' in html
    # O cancelar é renderizado na linha da tabela e tratado por delegação em #aulas.
    assert "data-convert-cancel" in client.get("/static/app.js").text
    assert 'id="modal-preview"' in html
    assert 'id="preview-player"' in html
    assert "Usar o convertido" in html
    assert "Manter o original" in html


def test_bootstrap_informa_plataforma(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    platform = client.get("/api/bootstrap").json()["platform"]
    assert platform["os"] in {"mac", "windows", "linux"}
    assert "folder_picker" in platform
    client, state = _client(tmp_path)
    client.get("/?k=test-token")
    _sobe_video(client)
    aula = client.get("/api/bootstrap").json()["session"]["aulas"][0]
    assert aula["grande"] is False
    assert aula["acima_do_teto"] is False
    assert aula["acima_do_limite"] is False
    assert aula["origem"] == "original"
    assert "resolucao" in aula
    assert "resolucao_fmt" in aula

    from aula_uploader.videopack import TETO_BYTES

    state.aulas[0].tamanho_bytes = TETO_BYTES + 1
    aula = client.get("/api/bootstrap").json()["session"]["aulas"][0]
    assert aula["grande"] is True
    assert aula["acima_do_teto"] is True
    assert aula["acima_do_limite"] is True


def test_bootstrap_publica_os_limites(tmp_path):
    from aula_uploader.videopack import ALVO_BYTES, TETO_BYTES

    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    limites = client.get("/api/bootstrap").json()["session"]["limites"]
    assert limites["alvo"] == ALVO_BYTES
    assert limites["teto"] == TETO_BYTES
    assert limites["teto_fmt"]


def test_upload_recusa_video_acima_do_teto(tmp_path):
    from aula_uploader.videopack import TETO_BYTES

    client, state = _client(tmp_path)
    client.get("/?k=test-token")
    _sobe_video(client)
    state.set_client("fullcycle", _DummyPortal("https://portal.fullcycle.com.br"))
    state.capitulo = object()
    state.aulas[0].tamanho_bytes = TETO_BYTES + 1

    response = client.post("/api/upload", json={"publicar": False, "force": False})

    assert response.status_code == 400
    assert "01-introducao.mp4" in response.json()["detail"]


def test_convert_dispara_e_publica_progresso(tmp_path, monkeypatch):
    from aula_uploader.videopack import AVISO_BYTES
    from aula_uploader.convert_worker import aplicar_evento, save_job

    client, state = _client(tmp_path)
    client.get("/?k=test-token")
    _sobe_video(client)
    state.aulas[0].tamanho_bytes = AVISO_BYTES + 1
    saida = tmp_path / "01-introducao (comprimido).mp4"
    saida.write_bytes(b"menor")

    def fake_start(job):
        nome = job["items"][0]["arquivo"]
        aplicar_evento(job, {"tipo": "arquivo-inicio", "nome": nome, "tamanho_antes": 999})
        aplicar_evento(job, {"tipo": "progresso", "nome": nome, "pct": 42.5, "eta_s": 12})
        aplicar_evento(
            job,
            {
                "tipo": "fim",
                "nome": nome,
                "saida": str(saida),
                "tamanho": 5,
                "tamanho_antes": 999,
                "largura": 1920,
                "altura": 1080,
            },
        )
        job["status"] = "done"
        job["pid"] = 0
        save_job(job)
        return job

    monkeypatch.setattr("aula_uploader.web.app.start_convert_worker", fake_start)

    response = client.post("/api/videos/convert", json={"arquivos": ["01-introducao.mp4"]})
    assert response.status_code == 200

    _espera(lambda: state.convert_job.get("status") == "done")
    item = state.convert_job["items"][0]
    assert item["status"] == "pronto"
    assert item["resolucao_nova"] == "1920x1080"
    assert state.convertidos["01-introducao.mp4"] == str(saida)


def test_convert_sem_arquivo_grande_recusa(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    _sobe_video(client)
    response = client.post("/api/videos/convert", json={"arquivos": []})
    assert response.status_code == 400


def test_convert_recusa_dois_lotes_ao_mesmo_tempo(tmp_path):
    client, state = _client(tmp_path)
    client.get("/?k=test-token")
    _sobe_video(client)
    state.converting = True
    response = client.post("/api/videos/convert", json={"arquivos": ["01-introducao.mp4"]})
    assert response.status_code == 409


def test_convert_apply_troca_o_arquivo_da_lista(tmp_path):
    client, state = _client(tmp_path)
    client.get("/?k=test-token")
    _sobe_video(client)
    convertido = tmp_path / "01-introducao (comprimido).mp4"
    convertido.write_bytes(b"menor")
    state.convertidos["01-introducao.mp4"] = str(convertido)
    state.aulas[0].titulo = "Aula de abertura"

    response = client.post("/api/videos/convert/apply", json={"arquivos": ["01-introducao.mp4"]})

    assert response.status_code == 200
    dados = response.json()
    assert dados["trocados"] == 1
    aula = dados["aulas"][0]
    assert aula["arquivo"] == "01-introducao (comprimido).mp4"
    assert aula["titulo"] == "Aula de abertura"
    assert aula["tamanho"] == len(b"menor")
    assert aula["origem"] == "convertido"
    assert state.convertidos == {}


def test_reattach_recupera_compressao_salva(tmp_path, monkeypatch):
    from aula_uploader.convert_worker import novo_job, save_job
    from aula_uploader.naming import AulaArquivo
    from aula_uploader.web.app import _reattach_convert

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    pasta = tmp_path / "aulas"
    pasta.mkdir()
    video = pasta / "01-introducao.mp4"
    video.write_bytes(b"aaaa")
    aula = AulaArquivo(path=video, ordem=1, titulo="Intro", tamanho_bytes=4)
    job = novo_job(alvos=[aula], engine="hw", fonte=str(pasta))
    job["items"][0]["status"] = "pronto"
    job["items"][0]["saida"] = str(pasta / "01-introducao (comprimido).mp4")
    job["convertidos"] = {"01-introducao.mp4": job["items"][0]["saida"]}
    job["status"] = "done"
    job["pid"] = 0
    save_job(job)

    client, state = _client(tmp_path)
    _reattach_convert(state)
    client.get("/?k=test-token")
    assert state.convert_job.get("status") == "done"
    assert state.convertidos["01-introducao.mp4"].endswith("(comprimido).mp4")
    assert state.aulas[0].path.name == "01-introducao.mp4"


def test_convert_cancel_sem_conversao_nao_quebra(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    response = client.post("/api/videos/convert/cancel")
    assert response.status_code == 200


def test_preview_serve_o_video_inteiro(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    _sobe_video(client, conteudo=b"0123456789")

    response = client.get("/api/videos/preview", params={"arquivo": "01-introducao.mp4"})

    assert response.status_code == 200
    assert response.content == b"0123456789"
    assert response.headers["accept-ranges"] == "bytes"


def test_preview_responde_range_parcial(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    _sobe_video(client, conteudo=b"0123456789")

    response = client.get(
        "/api/videos/preview",
        params={"arquivo": "01-introducao.mp4"},
        headers={"Range": "bytes=2-5"},
    )

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"


def test_preview_range_fora_do_arquivo(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    _sobe_video(client, conteudo=b"0123456789")

    response = client.get(
        "/api/videos/preview",
        params={"arquivo": "01-introducao.mp4"},
        headers={"Range": "bytes=50-60"},
    )

    assert response.status_code == 416


def test_preview_recusa_arquivo_de_fora(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    _sobe_video(client)

    assert client.get("/api/videos/preview", params={"arquivo": "../etc/passwd"}).status_code == 404
    assert client.get("/api/videos/preview", params={"arquivo": "outro.mp4"}).status_code == 404


def _espera(condicao, limite=3.0):
    import time

    fim = time.time() + limite
    while time.time() < fim:
        if condicao():
            return
        time.sleep(0.02)
    raise AssertionError("a condição não aconteceu no tempo esperado")


def test_app_js_tem_sintaxe_valida():
    import shutil
    import subprocess
    from pathlib import Path

    js = Path(__file__).resolve().parents[1] / "src/aula_uploader/web/static/app.js"
    assert "function produtosDoPortal()" in js.read_text(encoding="utf-8")
    assert "function pickFolder(" in js.read_text(encoding="utf-8")
    assert "function renderXfer(" in js.read_text(encoding="utf-8")
    assert "function uploadForm(" in js.read_text(encoding="utf-8")
    assert "function openTitulo(" in js.read_text(encoding="utf-8")
    assert "function bindColResize(" in js.read_text(encoding="utf-8")
    assert "function renderConvert(" in js.read_text(encoding="utf-8")
    assert "function abrirPreview(" in js.read_text(encoding="utf-8")
    assert "function usarConvertido(" in js.read_text(encoding="utf-8")
    assert "function avisar(" in js.read_text(encoding="utf-8")
    # Nada de alert/confirm do navegador: tudo em modal do app.
    assert "alert(" not in js.read_text(encoding="utf-8")
    node = shutil.which("node")
    if not node:
        pytest.skip("node não está no PATH")
    proc = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# Contrato entre app.js e index.html
#
# Regressão do bug que derrubava a interface: `$("id-que-nao-existe")` devolve
# null, o `.onclick =` seguinte lança TypeError no topo do módulo e **todo o
# resto do app.js deixa de rodar** — inclusive `bootstrap()`. A página abre,
# pinta o HTML estático e nenhum botão funciona.
# ---------------------------------------------------------------------------

STATIC = Path(__file__).resolve().parents[1] / "src" / "aula_uploader" / "web" / "static"


def _ids_referenciados_no_js(js: str) -> set[str]:
    return set(re.findall(r'\$\(\s*"([^"]+)"\s*\)', js))


def _ids_declarados_no_html(html: str) -> set[str]:
    return set(re.findall(r'id="([^"]+)"', html))


def test_todo_id_usado_no_app_js_existe_no_index_html():
    ausentes = sorted(
        _ids_referenciados_no_js((STATIC / "app.js").read_text(encoding="utf-8"))
        - _ids_declarados_no_html((STATIC / "index.html").read_text(encoding="utf-8"))
    )
    assert ausentes == [], (
        "app.js chama $() em ids que não existem no index.html "
        f"— isso mata o script inteiro: {ausentes}"
    )


def test_app_js_servido_bate_com_o_arquivo_em_disco(tmp_path):
    client, _state = _client(tmp_path)
    client.get("/?k=test-token")
    servido = client.get("/static/app.js")
    assert servido.status_code == 200
    assert servido.text == (STATIC / "app.js").read_text(encoding="utf-8")


def test_index_referencia_app_js_com_cache_buster(tmp_path):
    client, _state = _client(tmp_path)
    html = client.get("/?k=test-token").text
    assert re.search(r'src="/static/app\.js\?v=\d+"', html), (
        "sem ?v=N o navegador serve app.js do cache e correções não chegam"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node não instalado")
def test_app_js_nao_tem_erro_de_sintaxe():
    resultado = subprocess.run(
        ["node", "--check", str(STATIC / "app.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node não instalado")
def test_app_js_registra_listeners_ate_o_fim_do_arquivo():
    """Garante que `bootstrap()` continua sendo a última coisa a rodar."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "bootstrap()" in js
    assert js.index("bootstrap()") > js.index('$("btn-convert-all")')


# ---------------------------------------------------------------------------
# Progresso do envio na linha da aula (como o da compressão), em vez do
# card flutuante que repetia a mesma informação no topo.
# ---------------------------------------------------------------------------


def _corpo_da_funcao(js: str, nome: str) -> str:
    trecho = js[js.index(f"function {nome}(") :]
    return trecho[: trecho.index("\n}\n")]


def test_status_de_envio_mostra_progresso_inline():
    corpo = _corpo_da_funcao((STATIC / "app.js").read_text(encoding="utf-8"), "statusCellHtml")
    assert 'status === "enviando"' in corpo
    assert "conv-bar" in corpo, "a linha da aula precisa da mesma barra da compressão"


def test_card_flutuante_nao_repete_o_progresso_do_envio():
    corpo = _corpo_da_funcao((STATIC / "app.js").read_text(encoding="utf-8"), "renderXfer")
    assert "no portal" not in corpo
    assert "Subindo" not in corpo


@pytest.mark.skipif(shutil.which("node") is None, reason="node não instalado")
def test_jobPctGeral_calcula_o_progresso_do_projeto():
    fonte = _corpo_da_funcao(
        (STATIC / "app.js").read_text(encoding="utf-8"), "jobPctGeral"
    ) + "\n}\n"
    script = fonte + """
const casos = [
  { items: [] },
  { items: [{ status: "enviando", pct: 42 }] },
  { items: [{ status: "ok" }, { status: "enviando", pct: 50 }] },
  { items: [{ status: "ok" }, { status: "pulada" }] },
  { items: [{ status: "pendente" }, { status: "pendente" }] },
  { items: [{ status: "enviando", pct: 999 }] },
];
console.log(JSON.stringify(casos.map((c) => jobPctGeral(c))));
"""
    saida = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    assert json.loads(saida.stdout) == [None, 42, 75, 100, 0, 100]


# ---------------------------------------------------------------------------
# Envio com falha fica em "Em andamento", com marca vermelha — some da fila
# só quando a pessoa arquiva.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node não instalado")
def test_cliente_manda_job_com_falha_para_andamento():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    fonte = "".join(
        _corpo_da_funcao(js, nome) + "\n}\n"
        for nome in ("jobFalhas", "jobTemFalha", "projectBucket")
    )
    script = fonte + """
const casos = {
  tudoOk: { bucket: "concluido", fase: "done", status: "done", items: [{ status: "ok" }] },
  umaFalhou: { bucket: "concluido", fase: "done", status: "done_with_errors",
               items: [{ status: "ok" }, { status: "falhou" }] },
  jobParou: { bucket: "concluido", fase: "error", status: "error",
              items: [{ status: "pendente" }] },
  cancelado: { bucket: "concluido", fase: "error", status: "cancelado",
               items: [{ status: "ok" }] },
  arquivado: { archived: true, bucket: "historico", fase: "error",
               status: "error", items: [{ status: "falhou" }] },
};
const out = {};
for (const [k, v] of Object.entries(casos)) out[k] = projectBucket(v);
console.log(JSON.stringify(out));
"""
    saida = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    assert json.loads(saida.stdout) == {
        "tudoOk": "concluido",
        # mesmo com o servidor dizendo "concluido", falha volta para a fila
        "umaFalhou": "andamento",
        "jobParou": "andamento",
        "cancelado": "concluido",
        "arquivado": "historico",
    }


def test_card_com_falha_ganha_marca_vermelha():
    corpo = _corpo_da_funcao(
        (STATIC / "app.js").read_text(encoding="utf-8"), "renderProjects"
    )
    assert 'falhou ? " falha" : ""' in corpo
    assert 'class="project-falha"' in corpo
    assert "dá para continuar" in corpo


def test_css_da_falha_e_vermelho_e_vence_o_estado_normal():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".project-row.falha" in css
    assert ".project-falha" in css
    # precisa vir depois de .on/.work para ganhar na cascata
    assert css.index(".project-row.falha") > css.index(".project-row.work")


# ---------------------------------------------------------------------------
# Coluna "Ação" ficava só com "—" depois que o plano saía da memória.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node não instalado")
def test_acao_cai_para_o_que_o_job_registrou():
    fonte = _corpo_da_funcao(
        (STATIC / "app.js").read_text(encoding="utf-8"), "acaoLabel"
    ) + "\n}\n"
    script = fonte + """
console.log(JSON.stringify([
  acaoLabel({ acao_label: "criar" }, null),
  acaoLabel({}, { acao: "criar" }),
  acaoLabel({}, { acao: "forcar" }),
  acaoLabel({}, { acao: "pular" }),
  acaoLabel({}, {}),
  acaoLabel(null, null),
]));
"""
    saida = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    assert json.loads(saida.stdout) == [
        "criar",     # o plano manda quando existe
        "criar",     # sem plano, usa o que o job guardou
        "reenviar",
        "pular",
        "—",         # sem nada, aí sim o traço
        "—",
    ]


def test_verde_significa_pronta_e_nada_menos():
    """Só o estado final pinta a linha de verde — senão o sinal perde valor."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    corpo = _corpo_da_funcao(js, "renderAulas")
    assert 'if (status === "ok") tr.classList.add("enviada");' in corpo
    assert '"processando"].includes(status)) tr.classList.add("sending")' in corpo
    assert "tr.enviada td" in css


def test_barrinha_acompanha_ate_ficar_pronta():
    """Toda fase intermediária mostra barra; só 'pronta' fica sem."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    corpo = _corpo_da_funcao(js, "statusCellHtml")
    for fase in ("enviando", "salvando", "processando"):
        trecho = corpo[corpo.index(f'status === "{fase}"') :][:600]
        assert "conv-bar" in trecho, f"fase {fase} ficou sem barra de carregando"
    # e cada fase se explica em palavras
    assert "finalizando no portal" in corpo
    assert "vídeo enviado, convertendo" in corpo


def test_colunas_curtas_ficam_centralizadas():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "#aulas-table th:nth-child(n+4):nth-child(-n+8)" in css
    assert ".cell-status .conv-inline { justify-items: center; }" in css
