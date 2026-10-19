"""Интерфейс пакета законов страны (ARCHITECTURE.md §4).

Поля law_corpus, site_checks и disclaimers-правила добавляются в фазах 1–2,
вместе с первыми юридическими инструментами.
"""

from typing import Protocol


class JurisdictionPack(Protocol):
    code: str  # "DE"
    name: object  # LazyString, например _l("Germany")
    enabled: bool
    currency: str  # "EUR"
    disclaimer: object  # LazyString: оговорка для инструментов
