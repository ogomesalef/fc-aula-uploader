from aula_uploader.naming import listar_videos, parse_nome_aula


def test_parse_seguranca():
    ordem, titulo = parse_nome_aula("9-segurança.mp4")
    assert ordem == 9
    assert titulo == "Segurança"


def test_parse_com_espacos():
    ordem, titulo = parse_nome_aula("01 - Introdução.mp4")
    assert ordem == 1
    assert titulo == "Introdução"


def test_parse_devops_style():
    ordem, titulo = parse_nome_aula("02_basico_agente_03_docker_k8s_ed.mp4")
    assert ordem == 3
    assert titulo == "Docker K8s"


def test_listar_videos(tmp_path):
    (tmp_path / "2-segunda.mp4").write_bytes(b"x")
    (tmp_path / "1-primeira.mp4").write_bytes(b"x")
    (tmp_path / "readme.txt").write_text("no")
    aulas = listar_videos(tmp_path)
    assert [a.ordem for a in aulas] == [1, 2]
    assert aulas[0].titulo == "Primeira"
    assert aulas[1].titulo == "Segunda"
