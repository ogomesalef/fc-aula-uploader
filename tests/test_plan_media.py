import zipfile
from pathlib import Path

import pytest

from aula_uploader.media import cleanup_temp, mask_text, mask_url, resolve_source
from aula_uploader.naming import AulaArquivo
from aula_uploader.plan import (
    Acao,
    montar_plano,
    parse_bunny_folder_id,
    parse_capitulo_id,
    parse_curso_id,
)
from aula_uploader.portal_client import ConteudoLinha
from aula_uploader.session import validate_base_url


def test_parse_capitulo_id_from_url():
    url = "https://portal.fullcycle.com.br/admin/curso/conteudo/299/capitulo"
    assert parse_capitulo_id(url) == 299


def test_parse_capitulo_id_numeric():
    assert parse_capitulo_id(" 42 ") == 42


def test_parse_capitulo_id_invalid():
    with pytest.raises(ValueError):
        parse_capitulo_id("https://example.com/foo")


def test_parse_capitulo_id_rejects_curso_url():
    with pytest.raises(ValueError, match="lista de aulas"):
        parse_capitulo_id(
            "https://portal.fullcycle.com.br/admin/titulo/291/curso"
        )


def test_parse_curso_id_from_admin_url():
    assert (
        parse_curso_id(
            "https://portal.fullcycle.com.br/admin/curso/capitulo/291/curso"
        )
        == 291
    )
    assert (
        parse_curso_id("https://portal.fullcycle.com.br/admin/titulo/291/curso")
        == 291
    )


def test_parse_bunny_folder_id_from_url():
    assert (
        parse_bunny_folder_id(
            "https://dash.bunny.net/stream/library/123/folder/abc-123"
        )
        == "abc-123"
    )


def test_parse_bunny_folder_id_rejects_non_folder_url():
    with pytest.raises(ValueError):
        parse_bunny_folder_id("https://dash.bunny.net/stream/library/123")


def test_validate_base_url_ok():
    assert (
        validate_base_url("https://portal.fullcycle.com.br/", "fullcycle")
        == "https://portal.fullcycle.com.br"
    )


def test_validate_base_url_rejects_other_host():
    with pytest.raises(ValueError):
        validate_base_url("https://evil.example/admin", "fullcycle")


def test_mask_url_strips_query():
    url = "https://bucket.s3.amazonaws.com/videos/a.mp4?X-Amz-Signature=abc&token=xyz"
    masked = mask_url(url)
    assert "?" not in masked
    assert masked.endswith("/a.mp4")


def test_mask_text_mascara_url_dentro_da_mensagem():
    msg = (
        "Server error '500' for url "
        "'https://bucket.s3.amazonaws.com/v.mp4?X-Amz-Signature=abc123' "
        "durante o upload"
    )
    masked = mask_text(msg)
    assert "X-Amz-Signature" not in masked
    assert "https://bucket.s3.amazonaws.com/v.mp4" in masked
    assert masked.endswith("durante o upload")


def test_mask_text_preserva_texto_sem_url():
    assert mask_text("Falha ao salvar conteúdo (HTTP 500)") == (
        "Falha ao salvar conteúdo (HTTP 500)"
    )


def test_mask_url_redacts_access_key_pattern():
    # Padrão artificial (não é chave real) para validar a máscara.
    token = "AKIA" + ("Z" * 16)
    masked = mask_url(f"https://example.invalid/path/{token}/file")
    assert token not in masked
    assert "****" in masked


def test_normalize_user_path_unescapes_space(tmp_path):
    pasta = tmp_path / "Meu Curso"
    pasta.mkdir()
    alvo = pasta / "teste"
    alvo.mkdir()
    # Simula caminho arrastado no terminal: Full\ Cycle
    raw = str(alvo).replace(" ", r"\ ")
    assert r"\ " in raw
    from aula_uploader.media import normalize_user_path

    got = normalize_user_path(raw)
    assert got == alvo
    assert got.is_dir()


def test_normalize_user_path_quotes(tmp_path):
    from aula_uploader.media import normalize_user_path

    pasta = tmp_path / "videos"
    pasta.mkdir()
    assert normalize_user_path(f"'{pasta}'") == pasta
    assert normalize_user_path(f'"{pasta}"') == pasta


def test_resolve_zip(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "1-aula.mp4").write_bytes(b"abc")
    zip_path = tmp_path / "lote.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(video_dir / "1-aula.mp4", arcname="videos/1-aula.mp4")
    pasta, temp = resolve_source(zip_path)
    try:
        assert (pasta / "1-aula.mp4").exists() or list(pasta.rglob("*.mp4"))
    finally:
        cleanup_temp(temp)


def test_resolve_source_aceita_caminho_em_texto(tmp_path):
    # `resume` guarda a origem como string no estado.
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "1-aula.mp4").write_bytes(b"abc")
    pasta, temp = resolve_source(str(videos))
    assert temp is None
    assert pasta == videos.resolve()


def test_montar_plano_idempotente():
    aulas = [
        AulaArquivo(path=Path("1.mp4"), ordem=1, titulo="Alpha", tamanho_bytes=10),
        AulaArquivo(path=Path("2.mp4"), ordem=2, titulo="Beta", tamanho_bytes=10),
    ]
    existentes = [
        ConteudoLinha(id=10, titulo="Alpha", tem_video=True),
        ConteudoLinha(id=11, titulo="Beta", tem_video=False),
    ]
    plano = montar_plano(aulas, existentes)
    assert plano[0].acao == Acao.PULAR
    assert plano[1].acao == Acao.ENVIAR
