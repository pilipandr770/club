"""Реестр пакетов законов. Новая страна = новый пакет, остальной код не меняется."""

from .de.pack import DePack


class UnknownJurisdiction(KeyError):
    pass


_PACKS = {pack.code: pack for pack in (DePack(),)}


def get_pack(code):
    try:
        return _PACKS[code]
    except KeyError:
        raise UnknownJurisdiction(code) from None


def enabled_packs():
    return [pack for pack in _PACKS.values() if pack.enabled]
