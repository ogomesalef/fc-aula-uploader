from aula_uploader.updates import CHECK_EVERY_SECONDS, check_for_update


def test_check_for_update_avisa_quando_github_esta_na_frente(tmp_path, monkeypatch):
    monkeypatch.delenv("AULA_UPLOADER_SKIP_UPDATE_CHECK", raising=False)
    status = check_for_update(
        now=1000,
        current_sha="a" * 40,
        fetch=lambda: "b" * 40,
        behind=lambda _sha: True,
        cache_path=tmp_path / "update-check.json",
    )
    assert status is not None
    assert status.available is True
    assert "git pull" in status.command


def test_check_for_update_silencia_quando_esta_em_dia(tmp_path, monkeypatch):
    monkeypatch.delenv("AULA_UPLOADER_SKIP_UPDATE_CHECK", raising=False)
    status = check_for_update(
        now=1000,
        current_sha="a" * 40,
        fetch=lambda: "a" * 40,
        behind=lambda _sha: False,
        cache_path=tmp_path / "update-check.json",
    )
    assert status is not None
    assert status.available is False


def test_check_for_update_usa_cache_e_nao_refaz_a_rede(tmp_path, monkeypatch):
    monkeypatch.delenv("AULA_UPLOADER_SKIP_UPDATE_CHECK", raising=False)
    hits = {"n": 0}

    def fetch() -> str:
        hits["n"] += 1
        return "c" * 40

    cache = tmp_path / "update-check.json"
    kwargs = {
        "current_sha": "a" * 40,
        "fetch": fetch,
        "behind": lambda _sha: True,
        "cache_path": cache,
    }
    check_for_update(now=10, **kwargs)
    check_for_update(now=10 + CHECK_EVERY_SECONDS - 1, **kwargs)
    assert hits["n"] == 1


def test_check_for_update_offline_reusa_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("AULA_UPLOADER_SKIP_UPDATE_CHECK", raising=False)
    cache = tmp_path / "update-check.json"
    check_for_update(
        now=10,
        current_sha="a" * 40,
        fetch=lambda: "d" * 40,
        behind=lambda _sha: True,
        cache_path=cache,
    )
    status = check_for_update(
        now=10 + CHECK_EVERY_SECONDS + 1,
        current_sha="a" * 40,
        fetch=lambda: None,
        behind=lambda _sha: True,
        cache_path=cache,
    )
    assert status is not None
    assert status.available is True
    assert status.remote_sha == "d" * 40


def test_check_for_update_respeita_skip(monkeypatch, tmp_path):
    monkeypatch.setenv("AULA_UPLOADER_SKIP_UPDATE_CHECK", "1")
    status = check_for_update(
        now=1,
        current_sha="a" * 40,
        fetch=lambda: "b" * 40,
        behind=lambda _sha: True,
        cache_path=tmp_path / "c.json",
    )
    assert status is None
