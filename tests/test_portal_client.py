import pytest

from aula_uploader.portal_client import ConteudoData, PortalClient

LOGIN_HTML = """
<html><body>
<form>
  <input name="_csrf_token" value="csrf-test" />
</form>
</body></html>
"""

CAPITULO_HTML = """
<html><body>
  <h1>Conteúdo</h1>
  <nav class="breadcrumb">
    <a href="/admin/curso/291/edit">Curso Demo</a>
    <a href="/admin/curso/capitulo/291/curso">Capítulos</a>
    <a href="/admin/curso/capitulo/55/edit/291/curso">Editar capítulo</a>
  </nav>
  <table><tbody>
    <tr>
      <td>1</td>
      <td>Intro</td>
      <td>Rascunho</td>
      <td>Vídeo</td>
      <td>---</td>
      <td>1</td>
      <td>00:00</td>
      <td>---</td>
      <td><a href="/admin/curso/conteudo/100/edit">Editar</a></td>
    </tr>
  </tbody></table>
</body></html>
"""

CAPITULO_EDIT_HTML = """
<html><body>
  <input name="son_cursosbundle_capitulo[nome]" value="Teste" />
  <input name="son_cursosbundle_capitulo[_token]" value="tok" />
</body></html>
"""

NEW_FORM_HTML = """
<html><body>
  <input name="son_cursosbundle_conteudotype[_token]" value="tok-new" />
</body></html>
"""

EDIT_FORM_HTML = """
<html><body>
  <input name="son_cursosbundle_conteudotype[_token]" value="tok-edit" />
  <input name="son_cursosbundle_conteudotype[titulo]" value="Intro" />
  <input name="son_cursosbundle_conteudotype[ordem]" value="1" />
  <input name="son_cursosbundle_conteudotype[tipo]" value="12" />
  <input name="son_cursosbundle_conteudotype[status]" value="0" />
  <input name="son_cursosbundle_conteudotype[urlS3Nivo]" value="" />
  <a href="/admin/curso/conteudo/55/capitulo">Voltar</a>
</body></html>
"""

CURSO_HTML = """
<html><body>
  <input name="son_cursosbundle_cursotype[nome]" value="Arquitetura na Era da IA" />
</body></html>
"""

CURSO_SEGURANCA_HTML = """
<html><body>
  <input name="son_cursosbundle_cursotype[nome]" value="Curso de Segurança" />
</body></html>
"""

CAPITULOS_BEFORE_HTML = """
<html><body><table><tbody>
  <tr>
    <td>10</td><td>Introdução</td><td>5</td>
    <td><a href="/admin/curso/capitulo/10/edit/99/curso">Editar</a></td>
  </tr>
</tbody></table></body></html>
"""

CAPITULOS_AFTER_HTML = """
<html><body><table><tbody>
  <tr>
    <td>10</td><td>Introdução</td><td>5</td>
    <td><a href="/admin/curso/capitulo/10/edit/99/curso">Editar</a></td>
  </tr>
  <tr>
    <td>11</td><td>Threat Modeling</td><td>6</td>
    <td><a href="/admin/curso/capitulo/11/edit/99/curso">Editar</a></td>
  </tr>
</tbody></table></body></html>
"""

CAPITULO_NEW_FORM_HTML = """
<html><body>
  <input name="son_cursosbundle_capitulo[_token]" value="chapter-token" />
</body></html>
"""


@pytest.fixture
def portal(tmp_path):
    client = PortalClient(
        "https://portal.fullcycle.com.br",
        "user@example.com",
        "secret",
        session_path=tmp_path / "session.json",
    )
    yield client
    client.close()


def test_login(httpx_mock, portal):
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/login",
        text=LOGIN_HTML,
    )
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/login_check",
        status_code=200,
        text="ok",
    )
    # After login, URL should not contain /login — httpx_mock keeps requested URL.
    # Simulate success by making response.url not include login via redirect mock.
    portal.login()
    assert portal.session_path.exists()


def test_listar_conteudos(httpx_mock, portal):
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/conteudo/55/capitulo",
        text=CAPITULO_HTML,
    )
    linhas = portal.listar_conteudos_tabela(55)
    assert len(linhas) == 1
    assert linhas[0].id == 100
    assert linhas[0].titulo == "Intro"
    assert linhas[0].tem_video is False


def test_inspect_capitulo(httpx_mock, portal):
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/conteudo/55/capitulo",
        text=CAPITULO_HTML,
    )
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/capitulo/55/edit/291/curso",
        text=CAPITULO_EDIT_HTML,
    )
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/291/edit",
        text=CURSO_HTML,
    )
    info = portal.inspect_capitulo(55)
    assert info.id == 55
    assert info.nome == "Teste"
    assert info.curso_id == 291
    assert info.curso_nome == "Arquitetura na Era da IA"


def test_inspect_curso_and_list_capitulos(httpx_mock, portal):
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/99/edit",
        text=CURSO_SEGURANCA_HTML,
    )
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/capitulo/99/curso",
        text=CAPITULOS_BEFORE_HTML,
    )
    curso = portal.inspect_curso(99)
    capitulos = portal.list_capitulos(99)
    assert curso.nome == "Curso de Segurança"
    assert [(chapter.id, chapter.ordem) for chapter in capitulos] == [(10, 5)]


def test_create_capitulo_with_bunny_folder(httpx_mock, portal):
    url = "https://portal.fullcycle.com.br/admin/curso/capitulo/99/curso"
    httpx_mock.add_response(url=url, text=CAPITULOS_BEFORE_HTML)
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/capitulo/new/99/curso",
        text=CAPITULO_NEW_FORM_HTML,
    )
    httpx_mock.add_response(url=url, status_code=302)
    httpx_mock.add_response(url=url, text=CAPITULOS_AFTER_HTML)

    created = portal.create_capitulo(
        99,
        "Threat Modeling",
        6,
        bunny_folder_id="bunny-folder-123",
    )
    assert created.id == 11
    assert created.ordem == 6


def test_upload_video_chunked(httpx_mock, portal, tmp_path):
    video = tmp_path / "aula.mp4"
    video.write_bytes(b"0123456789")
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/courses/upload/video-chunk",
        json={"status": "success", "url": "https://bucket.s3.amazonaws.com/v.mp4?sig=1"},
    )
    url = portal.upload_video_chunked(video)
    assert url.startswith("https://bucket.s3.amazonaws.com/v.mp4")


def test_create_conteudo(httpx_mock, portal):
    # ids before
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/conteudo/55/capitulo",
        text=CAPITULO_HTML,
    )
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/conteudo/new/55/capitulo",
        text=NEW_FORM_HTML,
    )
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/conteudo/",
        status_code=302,
        headers={"location": "/admin/curso/conteudo/55/capitulo"},
    )
    # after list with new id
    after = CAPITULO_HTML.replace("100", "101").replace("Intro", "Nova Aula")
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/conteudo/55/capitulo",
        text=after,
    )
    novo = portal.create_conteudo(
        55,
        ConteudoData(titulo="Nova Aula", ordem=2, tipo="12", status="0"),
    )
    assert novo == 101


CURSO_LIST_HTML = """
<html><body>
<table><tbody>
<tr>
  <td><a href="/admin/curso/296/edit">296</a></td>
  <td></td>
  <td>
    Protocolos de Comunicação
    <br/>
    <small>30h<br/>Escapar EV e abrir HTML5/AWS? <span>Não</span></small>
  </td>
  <td>MBA</td>
  <td>Wesley</td>
  <td></td>
  <td>
    <a class="js-curso-delete-trigger" data-curso-nome="Protocolos de Comunicação" href="#">Excluir</a>
  </td>
</tr>
<tr>
  <td><a href="/admin/curso/116/edit">116</a></td>
  <td></td>
  <td>
    Comunicação entre sistemas
    <br/>
    <small>18h</small>
  </td>
  <td>Dev</td>
  <td>Wesley</td>
  <td></td>
  <td>
    <a class="js-curso-delete-trigger" data-curso-nome="Comunicação entre sistemas" href="#">Excluir</a>
  </td>
</tr>
<tr>
  <td><a href="/admin/curso/95/edit">95</a></td>
  <td></td>
  <td>Arquitetura de software<br/><small>32h</small></td>
  <td>Dev</td>
  <td>Wesley</td>
  <td></td>
  <td>
    <a class="js-curso-delete-trigger" data-curso-nome="Arquitetura de software" href="#">Excluir</a>
  </td>
</tr>
</tbody></table>
<div class="pagination"><center></center></div>
</body></html>
"""

CURSO_LIST_PAGE1_HTML = """
<html><body>
<table><tbody>
<tr>
  <td><a href="/admin/curso/95/edit">95</a></td>
  <td></td>
  <td>Arquitetura de software<br/><small>32h</small></td>
  <td></td><td></td><td></td>
  <td><a class="js-curso-delete-trigger" data-curso-nome="Arquitetura de software" href="#">x</a></td>
</tr>
</tbody></table>
<div class="pagination">
  <a href="/admin/curso/?string=arquitetura&amp;page=2">2</a>
  <a href="/admin/curso/?string=arquitetura&amp;page=2">Next</a>
</div>
</body></html>
"""

CURSO_LIST_PAGE2_HTML = """
<html><body>
<table><tbody>
<tr>
  <td><a href="/admin/curso/291/edit">291</a></td>
  <td></td>
  <td>Arquitetura na Era da IA<br/><small>20h</small></td>
  <td></td><td></td><td></td>
  <td><a class="js-curso-delete-trigger" data-curso-nome="Arquitetura na Era da IA" href="#">x</a></td>
</tr>
</tbody></table>
</body></html>
"""


def test_parse_curso_search_page_usa_nome_limpo():
    from aula_uploader.portal_client import parse_curso_search_page

    cursos, pages = parse_curso_search_page(CURSO_LIST_HTML)
    by_id = {curso.id: curso.nome for curso in cursos}
    assert by_id[296] == "Protocolos de Comunicação"
    assert "30h" not in by_id[296]
    assert "Escapar" not in by_id[296]
    assert by_id[116] == "Comunicação entre sistemas"
    assert pages == set()


def test_search_query_variants_com_e_sem_acento():
    from aula_uploader.portal_client import search_query_variants

    assert search_query_variants("comunicação") == ["comunicação", "comunicacao"]
    assert search_query_variants("comunicacao") == ["comunicacao"]
    assert search_query_variants("  ") == []


def test_buscar_cursos_por_trecho_filtra_acento(httpx_mock, portal):
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/?string=comunicacao",
        text=CURSO_LIST_HTML,
    )
    encontrados = portal.buscar_cursos("comunicacao")
    assert [(curso.id, curso.nome) for curso in encontrados] == [
        (296, "Protocolos de Comunicação"),
        (116, "Comunicação entre sistemas"),
    ]


def test_buscar_cursos_tenta_variante_sem_acento(httpx_mock, portal):
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/?string=comunica%C3%A7%C3%A3o",
        text="<html><body><table><tbody></tbody></table></body></html>",
    )
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/?string=comunicacao",
        text=CURSO_LIST_HTML,
    )
    encontrados = portal.buscar_cursos("comunicação")
    assert {curso.id for curso in encontrados} == {296, 116}


def test_buscar_cursos_segue_paginacao(httpx_mock, portal):
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/?string=arquitetura",
        text=CURSO_LIST_PAGE1_HTML,
    )
    httpx_mock.add_response(
        url="https://portal.fullcycle.com.br/admin/curso/?string=arquitetura&page=2",
        text=CURSO_LIST_PAGE2_HTML,
    )
    encontrados = portal.buscar_cursos("arquitetura")
    assert [curso.id for curso in encontrados] == [95, 291]
