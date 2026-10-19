from pathlib import Path

from babel.messages.mofile import read_mo
from babel.messages.pofile import read_po
from werkzeug.datastructures import LanguageAccept

from app.i18n import pick_locale, resolve_timezone

ROOT = Path(__file__).resolve().parent.parent
DE_DIR = ROOT / "translations" / "de" / "LC_MESSAGES"
ENABLED = ["de"]


def test_user_locale_wins_over_cookie_and_header():
    accept = LanguageAccept([("ru", 1.0), ("de", 0.5)])

    assert pick_locale("de", "ru", accept, ["de", "en"], "de") == "de"


def test_cookie_used_when_no_user_locale():
    assert pick_locale(None, "en", None, ["de", "en"], "de") == "en"


def test_accept_language_best_match_among_enabled():
    accept = LanguageAccept([("en", 1.0), ("de", 0.5)])

    assert pick_locale(None, None, accept, ["de", "en"], "de") == "en"


def test_unsupported_language_falls_back_to_default():
    accept = LanguageAccept([("ru", 1.0)])

    assert pick_locale(None, "fr", accept, ["de"], "de") == "de"


def test_unknown_timezone_falls_back_to_default():
    assert resolve_timezone("Mars/Olympus", "Europe/Berlin") == "Europe/Berlin"
    assert resolve_timezone(None, "Europe/Berlin") == "Europe/Berlin"
    assert resolve_timezone("Asia/Jerusalem", "Europe/Berlin") == "Asia/Jerusalem"


def test_german_catalog_is_complete():
    """Каждая строка из messages.pot должна быть переведена в de (CLAUDE.md, раздел «Переводы»)."""
    with (ROOT / "translations" / "messages.pot").open("rb") as f:
        template = read_po(f)
    with (DE_DIR / "messages.po").open("rb") as f:
        catalog = read_po(f, locale="de")

    missing = []
    for message in template:
        if not message.id:
            continue
        translated = catalog.get(message.id)
        if translated is None or not translated.string or translated.fuzzy:
            missing.append(message.id)

    assert missing == [], f"Не переведено: {missing}"


def test_compiled_catalog_exists_and_works():
    with (DE_DIR / "messages.mo").open("rb") as f:
        catalog = read_mo(f)

    assert catalog.get("Welcome").string == "Willkommen"


def test_index_is_translated_when_locale_is_de(client):
    body = client.get("/", headers={"Accept-Language": "de"}).get_data(as_text=True)

    assert "Mitgliedschaft erfordert" in body
