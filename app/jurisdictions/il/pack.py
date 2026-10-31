from flask_babel import lazy_gettext as _l

from .catalog import CATEGORIES


class IlPack:
    code = "IL"
    name = _l("Israel")
    enabled = True
    currency = "ILS"
    disclaimer = _l(
        "General information, not legal advice. Applies to Israeli law only. "
        "Only the Hebrew text is authoritative."
    )
    language = "he"
    rtl = True
    categories = CATEGORIES
    corpus_note = _l(
        "A selection of the main Israeli statutes in Hebrew, from the Open Law Book (Hebrew Wikisource). It is an "
        "unofficial consolidated text maintained by volunteers. Check the binding wording in the official publication "
        "(Sefer HaChukim) before relying on it."
    )
    source_label = _l("Open Law Book (unofficial consolidated text)")
