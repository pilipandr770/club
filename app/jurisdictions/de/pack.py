from flask_babel import lazy_gettext as _l


class DePack:
    code = "DE"
    name = _l("Germany")
    enabled = True
    currency = "EUR"
    disclaimer = _l(
        "General information, not legal advice. Applies to German law only."
    )
