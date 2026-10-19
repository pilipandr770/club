"""Модули клуба. Модуль подключается через реестр и включается настройкой ENABLED_MODULES."""

from dataclasses import dataclass

from flask_babel import lazy_gettext as _l


@dataclass(frozen=True)
class ModuleSpec:
    key: str  # короткое имя, оно же префикс URL и ключ в ENABLED_MODULES
    title: object  # LazyString для меню
    feature: str  # функция из config/plans.py, без которой модуль закрыт
    package: str  # пакет с blueprint `bp`

    @property
    def endpoint(self):
        """Точка входа модуля: профиль открывается сразу на форме, остальные — с главной страницы модуля."""
        return "profile.edit" if self.key == "profile" else f"{self.key}.index"


MODULES = (
    ModuleSpec("profile", _l("About me"), "profile.edit", "app.modules.profile"),
    ModuleSpec("services", _l("Services"), "services.use", "app.modules.services"),
    ModuleSpec("travel", _l("Travel"), "travel.book", "app.modules.travel"),
    ModuleSpec("startups", _l("Startups for investors"), "startups.browse", "app.modules.startups"),
    ModuleSpec("auction", _l("Auction"), "auction.access", "app.modules.auction"),
)

MODULE_KEYS = tuple(module.key for module in MODULES)


def enabled_modules(enabled_keys):
    """Модули, которые включены в настройках, в порядке реестра."""
    return [module for module in MODULES if module.key in set(enabled_keys)]
