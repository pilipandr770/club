import pytest

from app.config import Config
from app.jurisdictions import UnknownJurisdiction, enabled_packs, get_pack


def test_de_pack_is_registered_and_enabled():
    pack = get_pack("DE")

    assert pack.code == "DE"
    assert pack.enabled is True
    assert pack.currency == "EUR"


def test_default_jurisdiction_is_de():
    assert Config.DEFAULT_JURISDICTION == "DE"
    assert get_pack(Config.DEFAULT_JURISDICTION).code == "DE"


def test_unknown_jurisdiction_is_refused():
    """Нет пакета — ошибка, а не тихий откат на немецкое право (ARCHITECTURE.md §4)."""
    with pytest.raises(UnknownJurisdiction):
        get_pack("IL")


def test_enabled_packs_lists_de():
    assert [pack.code for pack in enabled_packs()] == ["DE"]
