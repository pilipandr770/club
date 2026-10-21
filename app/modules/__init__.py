"""Модули клуба. Модуль подключается через реестр и включается настройкой ENABLED_MODULES."""

from dataclasses import dataclass

from flask_babel import lazy_gettext as _l


@dataclass(frozen=True)
class ModuleSpec:
    key: str  # короткое имя, оно же префикс URL и ключ в ENABLED_MODULES
    title: object  # LazyString для меню
    feature: str  # функция из config/plans.py, без которой модуль закрыт
    package: str  # пакет с blueprint `bp`
    description: object = ""  # короткое пояснение для плитки на главной кабинета

    @property
    def endpoint(self):
        """Точка входа модуля: профиль открывается сразу на форме, остальные — с главной страницы модуля."""
        return "profile.edit" if self.key == "profile" else f"{self.key}.index"


MODULES = (
    ModuleSpec("profile", _l("About me"), "profile.edit", "app.modules.profile",
               _l("Tell the club who you are, what you need and how you can help. This powers your matches.")),
    ModuleSpec("services", _l("Services"), "services.use", "app.modules.services",
               _l("Partner offers for members and built-in tools: website checks, law search, market panel.")),
    ModuleSpec("travel", _l("Travel"), "community.read", "app.modules.travel",
               _l("Join shared trips or offer your own journey to other members.")),
    ModuleSpec("startups", _l("Startups for investors"), "community.read", "app.modules.startups",
               _l("Present your startup to investors or discover young companies.")),
    ModuleSpec("auction", _l("Auction"), "auction.access", "app.modules.auction",
               _l("Exclusive lots with rising or falling prices.")),
)

MODULE_KEYS = tuple(module.key for module in MODULES)


def enabled_modules(enabled_keys):
    """Модули, которые включены в настройках, в порядке реестра."""
    return [module for module in MODULES if module.key in set(enabled_keys)]
