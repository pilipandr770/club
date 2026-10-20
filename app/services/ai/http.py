"""Один HTTP-клиент для всех провайдеров: только стандартная библиотека, без SDK вендоров."""

import json
import urllib.error
import urllib.request


class AIUnavailable(Exception):
    """Провайдер недоступен, ключ не задан или ответ не разобрать. Вызывающий код падает на шаблон."""


def post_json(url: str, headers: dict, body: dict, timeout: int = 30) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"content-type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # В исключение не кладём тело ответа: оно может содержать фрагменты запроса
        raise AIUnavailable(f"http_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise AIUnavailable(type(exc).__name__) from None
