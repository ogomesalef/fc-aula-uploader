from aula_uploader.ollama_client import _extract_suggestions, detect_ollama


def test_extract_suggestions_from_aulas_object():
    aulas = [{"arquivo": "9.1-a.mp4", "ordem": 1, "titulo": "A"}]
    assert _extract_suggestions({"aulas": aulas}) == aulas


def test_extract_suggestions_from_nested_object():
    aulas = [{"arquivo": "9.1-a.mp4", "ordem": 1, "titulo": "A"}]
    assert _extract_suggestions({"resultado": {"items": aulas}}) == aulas


def test_qwen_text_model_is_preferred_over_whisper_and_vl(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return (
                b'{"models":['
                b'{"name":"dimavz/whisper-tiny:latest"},'
                b'{"name":"qwen3-vl:8b"},'
                b'{"name":"qwen2.5:7b"}]}'
            )

    monkeypatch.setattr(
        "aula_uploader.ollama_client.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    status = detect_ollama()
    assert status.recommended == "qwen2.5:7b"
