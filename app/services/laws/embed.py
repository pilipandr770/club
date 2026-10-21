"""Векторы для корпуса законов: всегда mistral-embed (1024), чтобы запрос и корпус жили в одном пространстве.

Без MISTRAL_API_KEY векторов нет, и поиск работает по тексту (полнотекстовый запасной вариант).
"""

import logging

from flask import current_app

from ..ai.http import AIUnavailable, post_json

log = logging.getLogger(__name__)

URL = "https://api.mistral.ai/v1/embeddings"
MODEL = "mistral-embed"
BATCH = 32


def available() -> bool:
    return bool(current_app.config.get("MISTRAL_API_KEY"))


def embed_texts(texts: list[str]) -> list[list[float] | None]:
    """Векторы для списка текстов. При сбое пакета пробует по одному; безнадёжные тексты получают None."""
    key = current_app.config.get("MISTRAL_API_KEY", "")
    if not key or not texts:
        return [None] * len(texts)
    out: list[list[float] | None] = []
    for start in range(0, len(texts), BATCH):
        batch = texts[start:start + BATCH]
        try:
            data = post_json(URL, {"authorization": f"Bearer {key}"}, {"model": MODEL, "input": batch}, timeout=60)
            out.extend(item["embedding"] for item in data["data"])
        except (AIUnavailable, KeyError, TypeError):
            for text in batch:  # слишком длинный или «плохой» кусок не должен ронять весь пакет
                try:
                    data = post_json(URL, {"authorization": f"Bearer {key}"}, {"model": MODEL, "input": [text]}, timeout=60)
                    out.append(data["data"][0]["embedding"])
                except (AIUnavailable, KeyError, TypeError, IndexError):
                    log.warning("Kein Vektor für einen Textabschnitt (Länge %s)", len(text))
                    out.append(None)
    return out


def embed_query(text: str) -> list[float] | None:
    vectors = embed_texts([text[:2000]])
    return vectors[0] if vectors else None
