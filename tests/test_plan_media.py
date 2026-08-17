import zipfile
from pathlib import Path

import pytest

from aula_uploader.media import mask_url, resolve_source, cleanup_temp
from aula_uploader.plan import parse_capitulo_id, montar_plano, Acao
from aula_uploader.naming import AulaArquivo
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


def test_mask_url_redacts_access_key_pattern():
    # Padrão artificial (não é chave real) para validar a máscara.
    token = "AKIA" + ("Z" * 16)
    masked = mask_url(f"https://example.invalid/path/{token}/file")
    assert token not in masked
    assert "****" in masked


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
