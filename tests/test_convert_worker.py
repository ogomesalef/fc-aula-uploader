from pathlib import Path

from aula_uploader.convert_worker import (
    aplicar_evento,
    caminhos_pendentes,
    load_job,
    marcar_interrupcao,
    novo_job,
    save_job,
)
from aula_uploader.naming import AulaArquivo


def _aula(tmp_path: Path, nome: str, tamanho: int = 10) -> AulaArquivo:
    path = tmp_path / nome
    path.write_bytes(b"x" * tamanho)
    return AulaArquivo(path=path, ordem=1, titulo=nome, tamanho_bytes=tamanho)


def test_novo_job_grava_caminho_absoluto(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    aula = _aula(tmp_path, "01-intro.mp4")
    job = novo_job(alvos=[aula], engine="hw", fonte=str(tmp_path))
    save_job(job)
    lido = load_job()
    assert lido is not None
    assert lido["items"][0]["path"] == str(aula.path)
    assert lido["status"] == "running"


def test_aplicar_evento_marca_pronto_e_saida(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    aula = _aula(tmp_path, "01-intro.mp4")
    job = novo_job(alvos=[aula], engine="hw")
    aplicar_evento(job, {"tipo": "arquivo-inicio", "nome": aula.path.name})
    aplicar_evento(
        job,
        {
            "tipo": "fim",
            "nome": aula.path.name,
            "saida": "/tmp/saida.mp4",
            "tamanho": 12,
            "largura": 1920,
            "altura": 1080,
        },
    )
    item = job["items"][0]
    assert item["status"] == "pronto"
    assert job["convertidos"][aula.path.name] == "/tmp/saida.mp4"
    assert not caminhos_pendentes(job)


def test_marcar_interrupcao_preserva_prontos(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    a = _aula(tmp_path, "01-a.mp4")
    b = _aula(tmp_path, "02-b.mp4")
    job = novo_job(alvos=[a, b], engine="hw")
    job["items"][0]["status"] = "pronto"
    job["items"][1]["status"] = "convertendo"
    job["pid"] = 999
    marcado = marcar_interrupcao(job)
    assert marcado["items"][0]["status"] == "pronto"
    assert marcado["items"][1]["status"] == "falhou"
    assert marcado["pid"] == 0
    assert len(caminhos_pendentes(marcado)) == 1
