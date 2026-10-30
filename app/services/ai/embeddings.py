"""Эмбеддинги. Провайдер и модель выбираются в .env: EMBEDDING_PROVIDER и EMBEDDING_MODEL.

Имя провайдера с моделью записывается в профиль (embed_provider). Если модель сменилась,
эмбеддинги пересчитываются (flask reembed), а в подборку попадают только профили с актуальной моделью.

Anthropic эмбеддингов не предоставляет, поэтому этого варианта нет.
"""

import hashlib
import math
import re

from flask import current_app

from . import usage
from .http import AIUnavailable, post_json

OPENAI_URL = "https://api.openai.com/v1"
MISTRAL_URL = "https://api.mistral.ai/v1"
GOOGLE_URL = "https://generativelanguage.googleapis.com/v1beta"
DIM = 512  # размер лексического вектора

PROVIDERS = ("lexical", "none", "openai", "google", "mistral", "openai_compatible")


class LexicalEmbedder:
    """Слова с хешированием (знаковый «hashing trick»). Детерминированный, без внешних сервисов.

    Видит совпадения слов, но не смысл. Годится для разработки, тестов и резервного режима.
    """

    provider = "lexical"

    def __init__(self):
        self.name = "lexical-v1"

    def embed(self, texts):
        return [self._vector(text) for text in texts]

    def _vector(self, text):
        vector = [0.0] * DIM
        for token in re.findall(r"\w+", text.lower()):
            if len(token) < 3:
                continue
            digest = hashlib.md5(token.encode("utf-8"), usedforsecurity=False).digest()
            index = int.from_bytes(digest[:4], "big") % DIM
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


class RemoteEmbedder:
    """Эмбеддинги через HTTP-API. Имя включает модель, чтобы смена модели запускала пересчёт."""

    def __init__(self, provider: str, model: str, base: str, key: str):
        self.provider = provider
        self.model = model
        self.base = base
        self.key = key
        self.name = f"{provider}:{model}"

    def embed(self, texts):
        if self.provider == "google":
            return self._google(texts)
        headers = {"authorization": f"Bearer {self.key}"} if self.key else {}
        data = post_json(f"{self.base}/embeddings", headers, {"model": self.model, "input": list(texts)})
        items = sorted(data.get("data") or [], key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in items]
        if len(vectors) != len(texts) or not all(vectors):
            raise AIUnavailable("bad_embedding_response")
        usage.record("embeddings", self.name, (data.get("usage") or {}).get("prompt_tokens", 0))
        return vectors

    def _google(self, texts):
        model = self.model if self.model.startswith("models/") else f"models/{self.model}"
        requests = [{"model": model, "content": {"parts": [{"text": text}]}} for text in texts]
        data = post_json(f"{self.base}/{model}:batchEmbedContents", {"x-goog-api-key": self.key},
                         {"requests": requests})
        vectors = [item.get("values") for item in data.get("embeddings") or []]
        if len(vectors) != len(texts) or not all(vectors):
            raise AIUnavailable("bad_embedding_response")
        return vectors


def get_embedder():
    cfg = current_app.config
    provider = (cfg.get("EMBEDDING_PROVIDER") or "lexical").strip().lower()
    if provider not in PROVIDERS:
        raise AIUnavailable("unknown_provider")
    if provider == "lexical":
        return LexicalEmbedder()
    if provider == "none":
        raise AIUnavailable("disabled")
    model = cfg.get("EMBEDDING_MODEL", "") or ""
    if not model:
        raise AIUnavailable("no_model")
    if provider == "openai":
        key = cfg.get("OPENAI_API_KEY", "")
        if not key:
            raise AIUnavailable("no_key")
        return RemoteEmbedder(provider, model, OPENAI_URL, key)
    if provider == "mistral":
        key = cfg.get("MISTRAL_API_KEY", "")
        if not key:
            raise AIUnavailable("no_key")
        return RemoteEmbedder(provider, model, MISTRAL_URL, key)
    if provider == "google":
        key = cfg.get("GOOGLE_API_KEY", "")
        if not key:
            raise AIUnavailable("no_key")
        return RemoteEmbedder(provider, model, GOOGLE_URL, key)
    base = (cfg.get("EMBEDDING_BASE_URL") or "").rstrip("/")
    if not base:
        raise AIUnavailable("no_base_url")
    return RemoteEmbedder(provider, model, base, cfg.get("EMBEDDING_API_KEY", "") or "")
