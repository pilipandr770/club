"""Клиент Anthropic Messages API без внешних зависимостей. Ключ только из .env (ANTHROPIC_API_KEY).

Если ключа нет или запрос не удался, бросаем LLMUnavailable: вызывающий код пишет шаблонный текст.
"""

import json
import urllib.error
import urllib.request

from flask import current_app

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class LLMUnavailable(Exception):
    pass


def ask(system: str, user: str, max_tokens: int = 900, timeout: int = 30) -> str:
    key = current_app.config.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise LLMUnavailable("no_key")
    body = json.dumps({
        "model": current_app.config["ANTHROPIC_MODEL"],
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise LLMUnavailable(type(exc).__name__) from None
    return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
