import json
import stat
from pathlib import Path

import pytest

from aula_uploader import videopack


@pytest.fixture(autouse=True)
def _cache_isolado(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("AULA_UPLOADER_VIDEOPACK", raising=False)


def _binario_falso(tmp_path: Path, script: str) -> Path:
    caminho = tmp_path / "videopack-falso"
    caminho.write_text(f"#!/usr/bin/env python3\n{script}", encoding="utf-8")
    caminho.chmod(caminho.stat().st_mode | stat.S_IEXEC)
    return caminho


def test_conversao_padrao_e_um_arquivo_por_vez():
    assert videopack.JOBS_PADRAO == 1


def test_limites_seguem_o_que_o_portal_aceita():
    assert videopack.ALVO_BYTES == 1024**3
    # O teto tem que ficar abaixo de 1,2 GB, que é onde o portal recusa.
    assert videopack.TETO_BYTES < int(1.2 * 1024**3)
    assert videopack.AVISO_BYTES <= videopack.ALVO_BYTES
    assert videopack.precisa_converter(videopack.ALVO_BYTES + 1)
    assert not videopack.precisa_converter(videopack.ALVO_BYTES - 1)
    assert videopack.acima_do_teto(videopack.TETO_BYTES)


def test_bitrate_para_alvo_cabe_no_tamanho():
    bps = videopack.bitrate_para_alvo(videopack.ALVO_BYTES, 3600)
    previsto = (bps + videopack.AUDIO_BPS) * 3600 / 8
    assert previsto <= videopack.ALVO_BYTES


def test_bitrate_para_alvo_nao_passa_do_original():
    bps = videopack.bitrate_para_alvo(videopack.ALVO_BYTES, 60, origem_bps=1_500_000)
    assert bps <= 1_500_000


def test_caminho_de_saida_vira_mp4_com_sufixo(tmp_path):
    destino = videopack.caminho_de_saida(tmp_path / "aula-01.mov")
    assert destino.name == "aula-01 (comprimido).mp4"
    assert destino.parent == tmp_path


def test_probe_muitos_le_ndjson_do_binario(tmp_path, monkeypatch):
    binario = _binario_falso(
        tmp_path,
        "import json, sys\n"
        "for arq in sys.argv[2:]:\n"
        "    print(json.dumps({'arquivo': arq, 'nome': 'aula.mp4', 'duracao': 60.0,\n"
        "                      'largura': 3840, 'altura': 2160, 'tamanho': 123}))\n",
    )
    monkeypatch.setenv("AULA_UPLOADER_VIDEOPACK", str(binario))
    video = tmp_path / "aula.mp4"
    video.write_bytes(b"x")

    infos = videopack.probe_muitos([video])

    info = infos[str(video)]
    assert info.resolucao == "3840x2160"
    assert info.duracao == 60.0


def test_conversao_repassa_eventos_do_binario(tmp_path, monkeypatch):
    binario = _binario_falso(
        tmp_path,
        "import json\n"
        "print(json.dumps({'tipo': 'inicio', 'total': 1}), flush=True)\n"
        "print(json.dumps({'tipo': 'progresso', 'nome': 'aula.mp4', 'pct': 50}), flush=True)\n"
        "print(json.dumps({'tipo': 'fim', 'nome': 'aula.mp4', 'saida': '/tmp/x.mp4',\n"
        "                  'tamanho': 999}), flush=True)\n",
    )
    monkeypatch.setenv("AULA_UPLOADER_VIDEOPACK", str(binario))
    monkeypatch.setattr(videopack, "ffmpeg_disponivel", lambda: True)

    eventos = []
    videopack.Conversao(paths=[tmp_path / "aula.mp4"]).executar(eventos.append)

    tipos = [e["tipo"] for e in eventos]
    assert tipos == ["inicio", "progresso", "fim"]
    assert eventos[-1]["tamanho"] == 999


def test_conversao_avisa_quando_falta_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(videopack, "ffmpeg_disponivel", lambda: False)
    with pytest.raises(videopack.VideopackError) as exc:
        videopack.Conversao(paths=[tmp_path / "aula.mp4"]).executar(lambda _e: None)
    assert "ffmpeg" in str(exc.value)


def test_conversao_erro_do_binario_vira_mensagem(tmp_path, monkeypatch):
    binario = _binario_falso(
        tmp_path,
        "import sys\nsys.stderr.write('não achei o ffmpeg\\n')\nsys.exit(3)\n",
    )
    monkeypatch.setenv("AULA_UPLOADER_VIDEOPACK", str(binario))
    monkeypatch.setattr(videopack, "ffmpeg_disponivel", lambda: True)

    with pytest.raises(videopack.VideopackError) as exc:
        videopack.Conversao(paths=[tmp_path / "aula.mp4"]).executar(lambda _e: None)
    assert "ffmpeg" in str(exc.value)


def test_binario_do_ambiente_tem_prioridade(tmp_path, monkeypatch):
    binario = _binario_falso(tmp_path, "pass\n")
    monkeypatch.setenv("AULA_UPLOADER_VIDEOPACK", str(binario))
    assert videopack.binario_pronto() == binario


def test_probe_ignora_linha_invalida(tmp_path, monkeypatch):
    binario = _binario_falso(
        tmp_path,
        "import json, sys\n"
        "print('lixo que não é json')\n"
        "for arq in sys.argv[2:]:\n"
        "    print(json.dumps({'arquivo': arq, 'largura': 1920, 'altura': 1080}))\n",
    )
    monkeypatch.setenv("AULA_UPLOADER_VIDEOPACK", str(binario))
    video = tmp_path / "aula.mp4"
    video.write_bytes(b"x")

    infos = videopack.probe_muitos([video])
    assert infos[str(video)].resolucao == "1920x1080"


def test_probe_sem_arquivos_nao_chama_nada():
    assert videopack.probe_muitos([]) == {}


def test_go_build_gera_binario_no_cache(tmp_path):
    if not videopack.go_disponivel():
        pytest.skip("go não está instalado")
    if not videopack._repo_go_dir().is_dir():
        pytest.skip("fontes do videopack não estão neste checkout")

    binario = videopack.garantir_binario()

    assert binario is not None
    assert binario.is_file()
    assert binario.parent == videopack.cache_dir()
    # Segunda chamada reaproveita o que já está compilado.
    assert videopack.garantir_binario() == binario


def test_json_de_evento_sobrevive_a_acentos(tmp_path, monkeypatch):
    binario = _binario_falso(
        tmp_path,
        "import json\n"
        "print(json.dumps({'tipo': 'aviso', 'nome': 'aulaç.mp4',\n"
        "                  'aviso': 'ficou grande demais'}, ensure_ascii=False), flush=True)\n",
    )
    monkeypatch.setenv("AULA_UPLOADER_VIDEOPACK", str(binario))
    monkeypatch.setattr(videopack, "ffmpeg_disponivel", lambda: True)

    eventos = []
    videopack.Conversao(paths=[tmp_path / "aulaç.mp4"]).executar(eventos.append)

    assert eventos[0]["nome"] == "aulaç.mp4"
    assert json.dumps(eventos[0])
