from flask_babel import lazy_gettext as _l

CATEGORIES = (
    "Verfassungsrecht", "Zivilrecht", "Handelsrecht", "Gesellschaftsrecht", "Insolvenzrecht", "Verfahrensrecht",
    "Anwaltsrecht", "Wettbewerbsrecht", "Notarrecht", "Strafrecht", "Strafverfahrensrecht", "Wirtschaftsstrafrecht",
    "IT-/Digitalrecht", "Datenschutzrecht", "IT-Sicherheitsrecht", "Urheberrecht", "Wirtschaftsrecht",
    "Finanzaufsichtsrecht", "Steuerrecht", "Steuerverfahrensrecht",
)


class DePack:
    code = "DE"
    name = _l("Germany")
    enabled = True
    currency = "EUR"
    disclaimer = _l(
        "General information, not legal advice. Applies to German law only."
    )
    language = "de"
    rtl = False
    categories = CATEGORIES
    corpus_note = _l("German federal law from gesetze-im-internet.de, the official publication of the Federal Ministry of Justice.")
    source_label = _l("Official source")
