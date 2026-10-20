import json
import urllib.error

import pytest

from app.extensions import db
from app.models import Profile, User
from app.services.ai import AIUnavailable, ask, get_embedder, llm_enabled, status
from app.services.ai import embeddings as emb
from app.services.ai import llm as llm_module
from app.services.ai.embeddings import LexicalEmbedder, RemoteEmbedder
from app.services.matching import _embedder, candidates, refresh_embeddings
from tests.conftest import make_user


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def calls(monkeypatch):
    """Перехватывает HTTP-запросы: ни одного сетевого вызова в тестах."""
    captured = []
    state = {"response": None, "error": None}

    def fake_urlopen(request, timeout=None):
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        captured.append({"url": request.full_url, "headers": {k.lower(): v for k, v in request.header_items()},
                         "body": body})
        if state["error"] is not None:
            raise state["error"]
        return FakeResponse(state["response"])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return {"captured": captured, "state": state}


def _config(app, monkeypatch, **values):
    for key, value in values.items():
        monkeypatch.setitem(app.config, key, value)


# --- LLM: один интерфейс, разные провайдеры -----------------------------------

def test_anthropic_request_and_reply(app, monkeypatch, calls):
    _config(app, monkeypatch, LLM_PROVIDER="anthropic", LLM_MODEL="claude-haiku-5-5", ANTHROPIC_API_KEY="k1")
    calls["state"]["response"] = {"content": [{"type": "text", "text": "Hallo"}]}
    with app.app_context():
        assert ask("sys", "user") == "Hallo"
    sent = calls["captured"][0]
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    assert sent["headers"]["x-api-key"] == "k1"
    assert sent["body"]["model"] == "claude-haiku-5-5"


def test_openai_uses_max_completion_tokens(app, monkeypatch, calls):
    _config(app, monkeypatch, LLM_PROVIDER="openai", LLM_MODEL="gpt-test", OPENAI_API_KEY="k2")
    calls["state"]["response"] = {"choices": [{"message": {"content": "Hi"}}]}
    with app.app_context():
        assert ask("sys", "user") == "Hi"
    sent = calls["captured"][0]
    assert sent["url"] == "https://api.openai.com/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Bearer k2"
    assert "max_completion_tokens" in sent["body"]


def test_mistral_reply(app, monkeypatch, calls):
    _config(app, monkeypatch, LLM_PROVIDER="mistral", LLM_MODEL="mistral-small-test", MISTRAL_API_KEY="k3")
    calls["state"]["response"] = {"choices": [{"message": {"content": "Bonjour"}}]}
    with app.app_context():
        assert ask("sys", "user") == "Bonjour"
    assert calls["captured"][0]["url"] == "https://api.mistral.ai/v1/chat/completions"


def test_google_reply_uses_header_key_not_url(app, monkeypatch, calls):
    _config(app, monkeypatch, LLM_PROVIDER="google", LLM_MODEL="gemini-test", GOOGLE_API_KEY="k4")
    calls["state"]["response"] = {"candidates": [{"content": {"parts": [{"text": "Guten Tag"}]}}]}
    with app.app_context():
        assert ask("sys", "user") == "Guten Tag"
    sent = calls["captured"][0]
    assert sent["url"].endswith("/models/gemini-test:generateContent")
    assert "k4" not in sent["url"]  # ключ не попадает в адрес, где его видно в логах
    assert sent["headers"]["x-goog-api-key"] == "k4"


def test_local_openai_compatible_server_needs_no_key(app, monkeypatch, calls):
    _config(app, monkeypatch, LLM_PROVIDER="openai_compatible", LLM_MODEL="llama-local",
            LLM_BASE_URL="http://ollama:11434/v1/", LLM_API_KEY="")
    calls["state"]["response"] = {"choices": [{"message": {"content": "lokal"}}]}
    with app.app_context():
        assert ask("sys", "user") == "lokal"
    sent = calls["captured"][0]
    assert sent["url"] == "http://ollama:11434/v1/chat/completions"
    assert "authorization" not in sent["headers"]


def test_switching_provider_is_only_a_setting(app, monkeypatch, calls):
    calls["state"]["response"] = {"choices": [{"message": {"content": "ok"}}]}
    _config(app, monkeypatch, LLM_PROVIDER="openai", LLM_MODEL="gpt-a", OPENAI_API_KEY="k")
    with app.app_context():
        ask("s", "u")
    _config(app, monkeypatch, LLM_PROVIDER="mistral", LLM_MODEL="mistral-b", MISTRAL_API_KEY="k")
    with app.app_context():
        ask("s", "u")
    assert [c["url"] for c in calls["captured"]] == [
        "https://api.openai.com/v1/chat/completions",
        "https://api.mistral.ai/v1/chat/completions",
    ]


def test_missing_key_and_disabled_provider_are_refused_without_network(app, monkeypatch, calls):
    _config(app, monkeypatch, LLM_PROVIDER="google", GOOGLE_API_KEY="")
    with app.app_context():
        with pytest.raises(AIUnavailable) as no_key:
            ask("s", "u")
        assert no_key.value.args[0] == "no_key"
        assert llm_enabled() is False
    _config(app, monkeypatch, LLM_PROVIDER="none")
    with app.app_context():
        with pytest.raises(AIUnavailable):
            ask("s", "u")
    assert calls["captured"] == []


def test_unknown_provider_is_refused(app, monkeypatch):
    _config(app, monkeypatch, LLM_PROVIDER="made-up")
    with app.app_context():
        with pytest.raises(AIUnavailable):
            ask("s", "u")


def test_http_error_does_not_leak_response_body(app, monkeypatch, calls):
    _config(app, monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="k")
    calls["state"]["error"] = urllib.error.HTTPError("u", 500, "boom", {}, None)
    with app.app_context():
        with pytest.raises(AIUnavailable) as failure:
            ask("s", "u")
    assert failure.value.args[0] == "http_500"


# --- эмбеддинги ----------------------------------------------------------------

def test_openai_embeddings_are_sorted_by_index(app, monkeypatch, calls):
    _config(app, monkeypatch, EMBEDDING_PROVIDER="openai", EMBEDDING_MODEL="emb-test", OPENAI_API_KEY="k")
    calls["state"]["response"] = {"data": [{"index": 1, "embedding": [0.0, 1.0]},
                                           {"index": 0, "embedding": [1.0, 0.0]}]}
    with app.app_context():
        vectors = get_embedder().embed(["a", "b"])
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert calls["captured"][0]["url"] == "https://api.openai.com/v1/embeddings"


def test_mistral_and_local_embeddings_use_the_same_shape(app, monkeypatch, calls):
    calls["state"]["response"] = {"data": [{"index": 0, "embedding": [0.5, 0.5]}]}
    _config(app, monkeypatch, EMBEDDING_PROVIDER="mistral", EMBEDDING_MODEL="mistral-embed", MISTRAL_API_KEY="k")
    with app.app_context():
        assert get_embedder().embed(["a"]) == [[0.5, 0.5]]
    _config(app, monkeypatch, EMBEDDING_PROVIDER="openai_compatible", EMBEDDING_MODEL="nomic",
            EMBEDDING_BASE_URL="http://embed:8080/v1")
    with app.app_context():
        assert get_embedder().embed(["a"]) == [[0.5, 0.5]]
    assert calls["captured"][1]["url"] == "http://embed:8080/v1/embeddings"


def test_google_embeddings_use_batch_endpoint(app, monkeypatch, calls):
    _config(app, monkeypatch, EMBEDDING_PROVIDER="google", EMBEDDING_MODEL="gemini-embedding-test",
            GOOGLE_API_KEY="k")
    calls["state"]["response"] = {"embeddings": [{"values": [0.1, 0.2]}]}
    with app.app_context():
        assert get_embedder().embed(["a"]) == [[0.1, 0.2]]
    sent = calls["captured"][0]
    assert sent["url"].endswith(":batchEmbedContents")
    assert "k" not in sent["url"]


def test_anthropic_has_no_embeddings(app, monkeypatch):
    _config(app, monkeypatch, EMBEDDING_PROVIDER="anthropic", EMBEDDING_MODEL="x")
    with app.app_context():
        with pytest.raises(AIUnavailable):
            get_embedder()


def test_embedder_name_includes_model_so_switching_triggers_recompute(app, monkeypatch, calls):
    _config(app, monkeypatch, EMBEDDING_PROVIDER="openai", EMBEDDING_MODEL="m1", OPENAI_API_KEY="k")
    with app.app_context():
        assert get_embedder().name == "openai:m1"
    _config(app, monkeypatch, EMBEDDING_MODEL="m2")
    with app.app_context():
        assert get_embedder().name == "openai:m2"


def test_unconfigured_embeddings_fall_back_to_lexical(app, monkeypatch):
    _config(app, monkeypatch, EMBEDDING_PROVIDER="openai", OPENAI_API_KEY="")
    with app.app_context():
        assert isinstance(_embedder(), LexicalEmbedder)


def test_profiles_from_another_model_are_not_compared(app, monkeypatch):
    me = make_user(app, email="me@example.com", verified=True)
    other = make_user(app, email="other@example.com", verified=True)
    with app.app_context():
        for user_id in (me, other):
            profile = db.session.get(User, user_id).profile
            profile.q_can_help = "tax help"
            profile.q_looking_for = "a designer"
            profile.allow_matching = True
            refresh_embeddings(profile)
        db.session.commit()
        # Профиль «другого» посчитан другой моделью — в подборку не попадает
        db.session.get(User, other).profile.embed_provider = "openai:old-model"
        db.session.commit()
        assert candidates(db.session.get(User, me)) == []
