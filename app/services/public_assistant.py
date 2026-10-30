"""Публичный помощник Clubi на лендинге. Ничего не сохраняет: ни вопросы, ни ответы, ни счётчики по людям.

Защита от расходов: короткие вопросы, короткая история, ограниченный ответ, лимиты по IP и общий суточный предел
(в blueprint). Знания берутся только из app/public_kb.py.
"""

import logging

from flask_babel import gettext as _

from ..public_kb import KNOWLEDGE, SYSTEM
from .ai import AIUnavailable, llm, llm_enabled

log = logging.getLogger(__name__)

MESSAGE_MAX = 500
HISTORY_TURNS = 6
HISTORY_CHARS = 600
MAX_TOKENS = 450


class PublicAssistantError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _clean(value, cap):
    """Недоверенный текст: в одну строку, без угловых скобок (ими отделяются данные), обрезан."""
    text = " ".join(str(value or "").split())
    return text.replace("<", "‹").replace(">", "›")[:cap]


def build_prompt(message: str, history) -> str:
    turns = []
    for item in (history or [])[-HISTORY_TURNS:]:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        who = "Besucher" if item["role"] == "user" else "Clubi"
        turns.append(f"{who}: {_clean(item.get('text'), HISTORY_CHARS)}")
    return (f"<wissen>\n{KNOWLEDGE}\n</wissen>\n"
            f"Bisheriger Verlauf:\n{chr(10).join(turns) or '(leer)'}\n\n"
            f"<frage>{_clean(message, MESSAGE_MAX)}</frage>\n\n"
            "(Antworte in der Sprache dieser Frage.)")


def ask_public(message: str, history=None) -> str:
    message = (message or "").strip()
    if not message:
        raise PublicAssistantError("empty")
    if not llm_enabled():
        raise PublicAssistantError("unavailable")
    try:
        answer = llm.complete(SYSTEM, build_prompt(message, history), max_tokens=MAX_TOKENS, timeout=30,
                              feature="public_chat").strip()
    except AIUnavailable as exc:
        log.warning("Öffentlicher Assistent nicht verfügbar: %s", exc)
        raise PublicAssistantError("unavailable") from None
    if not answer:
        raise PublicAssistantError("unavailable")
    return answer


def error_text(code: str) -> str:
    return {
        "empty": _("Please write a question."),
        "limit": _("That was a lot of questions in a short time. Please try again in a few minutes."),
        "unavailable": _("The assistant is not available right now. You can still look around and register below."),
    }.get(code, _("Something went wrong. Please try again."))
