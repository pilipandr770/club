"""Распознавание речи для Clubi: запись уходит провайдеру и тут же забывается, на нашем сервере аудио не хранится.

Порядок провайдеров задаёт STT_PROVIDERS в .env (по умолчанию mistral, затем openai). Провайдер без ключа пропускается.
"""

import json
import urllib.error
import urllib.request
import uuid

from flask import current_app

from .http import AIUnavailable

URLS = {
    "mistral": "https://api.mistral.ai/v1/audio/transcriptions",
    "openai": "https://api.openai.com/v1/audio/transcriptions",
}
MAX_BYTES = 2 * 1024 * 1024
# Разрешённые контейнеры записи: у Chrome и Firefox это webm/ogg, у Safari mp4
EXTENSIONS = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4", "audio/mpeg": "mp3", "audio/wav": "wav"}


def _multipart(fields: dict, filename: str, mime: str, data: bytes):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _post(provider: str, key: str, model: str, data: bytes, mime: str) -> str:
    body, content_type = _multipart({"model": model}, f"clip.{EXTENSIONS[mime]}", mime, data)
    request = urllib.request.Request(URLS[provider], data=body, method="POST",
                                     headers={"authorization": f"Bearer {key}", "content-type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AIUnavailable(f"http_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise AIUnavailable(type(exc).__name__) from None
    return str(payload.get("text") or "").strip()


def transcribe(data: bytes, mime: str) -> str:
    """Текст записи. Бросает AIUnavailable, если ни один провайдер не сработал."""
    mime = (mime or "").split(";")[0].strip().lower()
    if mime not in EXTENSIONS or not data or len(data) > MAX_BYTES:
        raise AIUnavailable("bad_audio")
    cfg = current_app.config
    order = [p.strip().lower() for p in (cfg.get("STT_PROVIDERS") or "").split(",") if p.strip()]
    keys = {"mistral": cfg.get("MISTRAL_API_KEY", ""), "openai": cfg.get("OPENAI_API_KEY", "")}
    models = {"mistral": cfg.get("STT_MISTRAL_MODEL", "voxtral-mini-latest"),
              "openai": cfg.get("STT_OPENAI_MODEL", "gpt-4o-mini-transcribe")}
    last = AIUnavailable("no_key")
    for provider in order:
        if provider not in URLS or not keys.get(provider):
            continue
        try:
            return _post(provider, keys[provider], models[provider], data, mime)
        except AIUnavailable as exc:
            last = exc
    raise last
