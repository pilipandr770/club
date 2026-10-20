"""Единая точка для ИИ: модели выбираются настройками (.env), код не меняется.

  LLM_PROVIDER / LLM_MODEL / LLM_BASE_URL        — объяснения матчинга
  EMBEDDING_PROVIDER / EMBEDDING_MODEL / ...     — векторы для подбора
  <PROVIDER>_API_KEY                             — ключи (ANTHROPIC, OPENAI, GOOGLE, MISTRAL)
"""

from flask import current_app

from . import embeddings, llm
from .http import AIUnavailable

__all__ = ["AIUnavailable", "ask", "embed_provider", "get_embedder", "llm_enabled", "status"]


def ask(system: str, user: str, max_tokens: int = 900) -> str:
    return llm.complete(system, user, max_tokens=max_tokens)


def get_embedder():
    return embeddings.get_embedder()


def llm_enabled() -> bool:
    try:
        provider, _model, cfg = llm._settings()
        if provider == "none":
            return False
        if provider == "openai_compatible":
            return bool(cfg.get("LLM_BASE_URL"))
        return bool(cfg.get(f"{provider.upper()}_API_KEY"))
    except AIUnavailable:
        return False


def embed_provider() -> str:
    """Имя текущего провайдера эмбеддингов (с моделью). Лексический — если ничего не настроено."""
    try:
        return get_embedder().name
    except AIUnavailable:
        return "unavailable"


def status() -> dict:
    cfg = current_app.config
    return {
        "llm_provider": cfg.get("LLM_PROVIDER"),
        "llm_model": cfg.get("LLM_MODEL"),
        "llm_enabled": llm_enabled(),
        "embedding_provider": cfg.get("EMBEDDING_PROVIDER"),
        "embedding_model": cfg.get("EMBEDDING_MODEL"),
        "embedding_name": embed_provider(),
    }
