"""Языковые модели. Провайдер и модель выбираются в .env: LLM_PROVIDER и LLM_MODEL.

Поддерживаются anthropic, openai, google, mistral и openai_compatible (локальные серверы:
Ollama, llama.cpp, vLLM и любой сервер с API в стиле OpenAI — адрес в LLM_BASE_URL).
Ключи берутся из <PROVIDER>_API_KEY; для локального сервера ключ не нужен.
"""

from flask import current_app

from .http import AIUnavailable, post_json

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1"
MISTRAL_URL = "https://api.mistral.ai/v1"
GOOGLE_URL = "https://generativelanguage.googleapis.com/v1beta"

PROVIDERS = ("none", "anthropic", "openai", "google", "mistral", "openai_compatible")


def _settings():
    cfg = current_app.config
    provider = (cfg.get("LLM_PROVIDER") or "none").strip().lower()
    if provider not in PROVIDERS:
        raise AIUnavailable("unknown_provider")
    return provider, cfg.get("LLM_MODEL", ""), cfg


def _key(cfg, name: str) -> str:
    return cfg.get(f"{name.upper()}_API_KEY", "") or ""


def complete(system: str, user: str, max_tokens: int = 900, timeout: int = 30) -> str:
    provider, model, cfg = _settings()
    if provider == "none":
        raise AIUnavailable("disabled")

    if provider == "anthropic":
        key = _key(cfg, "anthropic")
        if not key:
            raise AIUnavailable("no_key")
        body = {"model": model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        # Рассуждающие модели тратят часть max_tokens на «мысли», и ответ обрывается на полуслове.
        # Нашим задачам (короткие ответы, JSON) рассуждение не нужно: выключаем его.
        try:
            data = post_json(ANTHROPIC_URL, headers, {**body, "thinking": {"type": "disabled"}}, timeout)
        except AIUnavailable as exc:
            if str(exc) != "http_400":  # модель не знает параметр thinking: повторяем без него
                raise
            data = post_json(ANTHROPIC_URL, headers, body, timeout)
        return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")

    if provider == "google":
        key = _key(cfg, "google")
        if not key:
            raise AIUnavailable("no_key")
        data = post_json(f"{GOOGLE_URL}/models/{model}:generateContent", {"x-goog-api-key": key}, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }, timeout)
        candidates = data.get("candidates") or []
        parts = (candidates[0].get("content") or {}).get("parts", []) if candidates else []
        return "".join(part.get("text", "") for part in parts)

    # openai, mistral и openai_compatible говорят одним форматом chat/completions
    base, key = _openai_style_base(provider, cfg)
    headers = {"authorization": f"Bearer {key}"} if key else {}
    token_field = "max_completion_tokens" if provider == "openai" else "max_tokens"
    data = post_json(f"{base}/chat/completions", headers, {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        token_field: max_tokens,
    }, timeout)
    choices = data.get("choices") or []
    return ((choices[0].get("message") or {}).get("content") or "") if choices else ""


def _openai_style_base(provider: str, cfg):
    if provider == "openai":
        key = _key(cfg, "openai")
        if not key:
            raise AIUnavailable("no_key")
        return OPENAI_URL, key
    if provider == "mistral":
        key = _key(cfg, "mistral")
        if not key:
            raise AIUnavailable("no_key")
        return MISTRAL_URL, key
    base = (cfg.get("LLM_BASE_URL") or "").rstrip("/")
    if not base:
        raise AIUnavailable("no_base_url")
    return base, cfg.get("LLM_API_KEY", "") or ""
