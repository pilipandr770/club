"""Учёт расхода ИИ: счётчики по функциям, предохранитель по токенам, страница в админке."""

import pytest

from app.extensions import db
from app.models import AiUsage
from app.services.ai import llm, usage
from app.services.ai.http import AIUnavailable
from tests.conftest import login, make_user


@pytest.fixture()
def anthropic(app, monkeypatch):
    """Подменяет сетевой вызов: Anthropic отвечает «ok» и сообщает расход токенов."""
    app.config.update(LLM_PROVIDER="anthropic", LLM_MODEL="test-model", ANTHROPIC_API_KEY="key")
    calls = []

    def fake_post(url, headers, body, timeout=30):
        calls.append(body)
        return {"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 120, "output_tokens": 30}}

    monkeypatch.setattr(llm, "post_json", fake_post)
    return calls


def _rows(app):
    with app.app_context():
        return [(r.feature, r.model, r.calls, r.failures, r.tokens_in, r.tokens_out) for r in AiUsage.query.all()]


def test_a_call_is_counted_with_its_tokens_and_feature(app, anthropic):
    with app.app_context():
        assert llm.complete("s", "u", feature="assistant") == "ok"
        llm.complete("s", "u", feature="assistant")
    assert _rows(app) == [("assistant", "test-model", 2, 0, 240, 60)]


def test_provider_errors_are_counted_as_failures_but_missing_keys_are_not(app, monkeypatch):
    app.config.update(LLM_PROVIDER="anthropic", LLM_MODEL="test-model", ANTHROPIC_API_KEY="key")

    def broken(*args, **kwargs):
        raise AIUnavailable("http_500")

    monkeypatch.setattr(llm, "post_json", broken)
    with app.app_context():
        with pytest.raises(AIUnavailable):
            llm.complete("s", "u", feature="tools")
        app.config["ANTHROPIC_API_KEY"] = ""
        with pytest.raises(AIUnavailable):
            llm.complete("s", "u", feature="tools")
    assert _rows(app) == [("tools", "test-model", 0, 1, 0, 0)]


def test_daily_budget_pauses_calls_without_touching_the_provider(app, anthropic):
    app.config.update(AI_DAILY_TOKEN_BUDGET=300, AI_MONTHLY_TOKEN_BUDGET=0, AI_PUBLIC_DAILY_TOKENS=0)
    with app.app_context():
        llm.complete("s", "u", feature="matching")  # 150 токенов
        llm.complete("s", "u", feature="matching")  # 300 токенов: лимит исчерпан
        with pytest.raises(AIUnavailable, match="budget"):
            llm.complete("s", "u", feature="matching")
    assert len(anthropic) == 2


def test_monthly_budget_pauses_calls(app, anthropic):
    app.config.update(AI_DAILY_TOKEN_BUDGET=0, AI_MONTHLY_TOKEN_BUDGET=150, AI_PUBLIC_DAILY_TOKENS=0)
    with app.app_context():
        llm.complete("s", "u", feature="assistant")
        with pytest.raises(AIUnavailable, match="budget"):
            llm.complete("s", "u", feature="assistant")


def test_public_chat_has_its_own_cap_that_leaves_members_untouched(app, anthropic):
    app.config.update(AI_DAILY_TOKEN_BUDGET=0, AI_MONTHLY_TOKEN_BUDGET=0, AI_PUBLIC_DAILY_TOKENS=150)
    with app.app_context():
        llm.complete("s", "u", feature="public_chat")
        with pytest.raises(AIUnavailable, match="budget"):
            llm.complete("s", "u", feature="public_chat")
        assert llm.complete("s", "u", feature="assistant") == "ok"


def test_embeddings_and_voice_do_not_count_against_the_token_budget(app, anthropic):
    app.config.update(AI_DAILY_TOKEN_BUDGET=100, AI_MONTHLY_TOKEN_BUDGET=0, AI_PUBLIC_DAILY_TOKENS=0)
    with app.app_context():
        usage.record("embeddings", "mistral:mistral-embed", 5000)
        usage.record("voice", "voxtral-mini-latest")
        assert llm.complete("s", "u", feature="assistant") == "ok"


def test_zero_limits_turn_the_safeguard_off(app, anthropic):
    app.config.update(AI_DAILY_TOKEN_BUDGET=0, AI_MONTHLY_TOKEN_BUDGET=0, AI_PUBLIC_DAILY_TOKENS=0)
    with app.app_context():
        for _ in range(3):
            llm.complete("s", "u", feature="assistant")


def test_openai_style_usage_is_read(app, monkeypatch):
    app.config.update(LLM_PROVIDER="mistral", LLM_MODEL="m", MISTRAL_API_KEY="key")
    monkeypatch.setattr(llm, "post_json", lambda *a, **k: {
        "choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}})
    with app.app_context():
        llm.complete("s", "u", feature="panel")
    assert _rows(app) == [("panel", "m", 1, 0, 7, 3)]


def test_summary_estimates_cost_and_flags_the_limit(app, anthropic):
    app.config.update(AI_DAILY_TOKEN_BUDGET=150, AI_MONTHLY_TOKEN_BUDGET=1000)
    with app.app_context():
        llm.complete("s", "u", feature="assistant")
        data = usage.summary()
    assert data["today"]["tokens"] == 150 and data["today"]["over"] and data["blocked"]
    assert data["month"]["share"] == 15 and not data["month"]["over"]
    assert data["today"]["cost"] == pytest.approx((120 * 0.10 + 30 * 0.50) / 1_000_000)


def test_admin_page_shows_usage_and_is_hidden_from_members(app, client, anthropic):
    make_user(app, "admin@example.com", verified=True, admin=True)
    make_user(app, "member@example.com", verified=True)
    with app.app_context():
        llm.complete("s", "u", feature="assistant")
    login(client, "member@example.com")
    assert client.get("/admin/ai").status_code == 404
    admin = app.test_client()
    login(admin, "admin@example.com")
    page = admin.get("/admin/ai").get_data(as_text=True)
    assert "assistant" in page and "test-model" in page
    assert "/admin/ai" in admin.get("/admin/").get_data(as_text=True)


def test_usage_table_stores_no_member_data(app):
    assert {c.name for c in AiUsage.__table__.columns} == {"day", "feature", "model", "calls", "failures", "tokens_in", "tokens_out"}
