import pytest

from aula_uploader.portal_client import PortalClient, ConteudoData


LOGIN_HTML = """
<html><body>
<form>
  <input name="_csrf_token" value="csrf-test" />
</form>
</body></html>
"""

CAPITULO_HTML = """
<html><body>
  <h1>Capítulo Segurança</h1>
  <nav class="breadcrumb">
    <a href="/admin/curso/10/edit">Curso Demo</a>
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
    info = portal.inspect_capitulo(55)
    assert info.id == 55
    assert "Segurança" in info.nome
    assert info.curso_id == 10


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
