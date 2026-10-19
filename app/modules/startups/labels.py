"""Подписи категорий и стадий для шаблонов. Строки на английском, перевод в translations/de."""

from flask_babel import lazy_gettext as _l

CATEGORY_LABELS = {
    "fintech": _l("Fintech"),
    "health": _l("Health"),
    "climate": _l("Climate"),
    "ai": _l("AI"),
    "education": _l("Education"),
    "consumer": _l("Consumer"),
    "other": _l("Other"),
}

STAGE_LABELS = {
    "idea": _l("Idea"),
    "prototype": _l("Prototype"),
    "revenue": _l("First revenue"),
    "growth": _l("Growth"),
}


def category_label(value):
    return CATEGORY_LABELS.get(value, value)


def stage_label(value):
    return STAGE_LABELS.get(value, value)
