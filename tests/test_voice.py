"""Голосовой ввод Clubi: согласие, распознавание через подменённого провайдера, ошибки, отзыв согласия."""

import io

import pytest

from app.extensions import db
from app.models import User
from app.services.ai.http import AIUnavailable
from app.services.consents import latest_consent
from tests.conftest import login, make_user


def _audio(data=b"fake-audio", mime="audio/webm"):
    return {"audio": (io.BytesIO(data), "clip", mime)}


@pytest.fixture()
def member(app, client):
    user_id = make_user(app, email="voice@example.com", verified=True, plan="starter")
    login(client, "voice@example.com")
    return user_id


def test_transcribe_requires_consent(app, client, member):
    response = client.post("/assistant/transcribe", data=_audio(), content_type="multipart/form-data")
    assert response.status_code == 403 and response.get_json()["code"] == "consent"


def test_consent_then_transcribe_returns_text(app, client, member, monkeypatch):
    monkeypatch.setattr("app.blueprints.assistant.transcribe", lambda data, mime: "Wer kann mir bei der Steuer helfen?")
    assert client.post("/assistant/voice-consent").status_code == 200
    with app.app_context():
        assert latest_consent(db.session.get(User, member), "voice") is True
    response = client.post("/assistant/transcribe", data=_audio(), content_type="multipart/form-data")
    assert response.status_code == 200
    assert response.get_json()["text"] == "Wer kann mir bei der Steuer helfen?"


def test_empty_and_silent_recordings(app, client, member, monkeypatch):
    client.post("/assistant/voice-consent")
    assert client.post("/assistant/transcribe", data={}, content_type="multipart/form-data").status_code == 400
    monkeypatch.setattr("app.blueprints.assistant.transcribe", lambda data, mime: "")
    assert client.post("/assistant/transcribe", data=_audio(), content_type="multipart/form-data").status_code == 422


def test_provider_failure_gives_friendly_error(app, client, member, monkeypatch):
    client.post("/assistant/voice-consent")

    def boom(data, mime):
        raise AIUnavailable("http_500")
    monkeypatch.setattr("app.blueprints.assistant.transcribe", boom)
    response = client.post("/assistant/transcribe", data=_audio(), content_type="multipart/form-data")
    assert response.status_code == 503 and "error" in response.get_json()


def test_unsupported_format_is_rejected(app, client, member):
    client.post("/assistant/voice-consent")
    response = client.post("/assistant/transcribe", data=_audio(mime="text/plain"), content_type="multipart/form-data")
    assert response.status_code == 400


def test_speech_service_falls_back_and_skips_missing_keys(app, monkeypatch):
    from app.services.ai import speech

    calls = []

    def fake_post(provider, key, model, data, mime):
        calls.append(provider)
        if provider == "mistral":
            raise AIUnavailable("http_503")
        return "hallo"
    monkeypatch.setattr(speech, "_post", fake_post)
    with app.app_context():
        app.config.update(STT_PROVIDERS="mistral,openai", MISTRAL_API_KEY="m", OPENAI_API_KEY="o")
        assert speech.transcribe(b"x", "audio/webm;codecs=opus") == "hallo"
        assert calls == ["mistral", "openai"]
        calls.clear()
        app.config.update(MISTRAL_API_KEY="", OPENAI_API_KEY="o")
        assert speech.transcribe(b"x", "audio/webm") == "hallo" and calls == ["openai"]
        app.config.update(OPENAI_API_KEY="")
        with pytest.raises(AIUnavailable):
            speech.transcribe(b"x", "audio/webm")


def test_microphone_allowed_for_own_origin_only(app, client, member):
    policy = client.get("/app/").headers.get("Permissions-Policy", "")
    assert "microphone=(self)" in policy and "camera=()" in policy


def test_privacy_page_can_withdraw_voice_consent(app, client, member):
    client.post("/assistant/voice-consent")
    client.post("/profile/privacy", data={"visible_in_directory": "1"})
    with app.app_context():
        assert latest_consent(db.session.get(User, member), "voice") is False
