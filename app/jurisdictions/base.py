"""Интерфейс пакета законов страны (ARCHITECTURE.md §4).

Пакет описывает страну: язык и направление текста, категории для фильтра, оговорку и источник корпуса.
Корпус (`law_docs`, `law_chunks`) общий, законы разделены колонкой `jurisdiction`; загрузчик пакета отдаёт словари
в формате `services/laws/sync.store_law`.
"""

from typing import Protocol


class JurisdictionPack(Protocol):
    code: str  # "DE"
    name: object  # LazyString, например _l("Germany")
    enabled: bool
    currency: str  # "EUR"
    disclaimer: object  # LazyString: оговорка для инструментов
    language: str  # язык официального текста: "de", "he"
    rtl: bool  # текст пишется справа налево
    categories: tuple  # категории для фильтра в поиске по закону
    corpus_note: object  # LazyString: что за корпус и насколько он официален
    source_label: object  # LazyString: подпись ссылки на источник в отчёте
