"""Законы Израиля: разбор вики-разметки Open Law Book, синхронизация, поиск и инструмент с параметром страны."""

import pytest

from app.extensions import db
from app.jurisdictions import UnknownJurisdiction, get_pack
from app.jurisdictions.il import source
from app.jurisdictions.il.catalog import CATALOG, IlLaw
from app.jurisdictions.il.wikitext import parse_law, render
from app.models import Law, LawChunk, LawSection, LawSyncState
from app.services.laws import embed, il_sync, search as law_search, sync as law_sync
from app.services.laws.parse import chunks_of
from app.services.tools import ToolError
from app.services.tools import laws as laws_tool
from tests.conftest import login, make_user
from tests.test_laws_local import XML, ZIP_URL, make_zip

PRIVACY = "{{ח:התחלה}}\n{{ח:כותרת|חוק הגנת הפרטיות, התשמ״א–1981}}\n\n{{ח:פתיח-התחלה}}\n" \
          "{{ח:מאגר|2000234}} {{ח:תיבה|ס״ח תשמ״א, 128|חוק הגנת הפרטיות|https://fs.knesset.gov.il/9/law/x.pdf}}; " \
          "{{ח:תיבה|תשס״ו, 1|תיקון מס׳ 10|https://fs.knesset.gov.il/y.pdf}}.\n{{ח:סוגר}}\n{{ח:מפריד}}\n\n" \
          "{{ח:קטע2||תוכן עניינים}}\n<div class=\"law-toc\">\n<div>{{ח:פנימי|פרק א|פרק א׳: פגיעה בפרטיות}}</div>\n</div>\n\n" \
          "{{ח:קטע2|פרק א|פרק א׳: פגיעה בפרטיות}}\n\n" \
          "{{ח:סעיף|1|איסור הפגיעה בפרטיות}}\n{{ח:ת}} לא יפגע אדם בפרטיות של זולתו ללא הסכמתו.\n\n" \
          "{{ח:סעיף|2|פגיעה בפרטיות מהי|תיקון: תשס״ו, תשע״א}}\n{{ח:ת}} פגיעה בפרטיות היא אחת מאלה:\n" \
          "{{ח:תת|(1)}} בילוש או התחקות אחרי אדם;{{ח:הערה|הנוסח שולב בחוק אחר.}}\n" \
          "{{ח:תת|(2)}} האזנה האסורה על פי {{ח:חיצוני|חוק האזנת סתר|חוק האזנת סתר, התשל״ט–1979}}.\n\n" \
          "{{ח:סעיף|3|סעיף שבוטל}}\n{{ח:ת}} (בוטל).\n\n" \
          "{{ח:חתימות|נתקבל בכנסת.}}\n* '''שם'''<br>ראש הממשלה\n{{ח:סוגר}}\n\n{{ח:סוף}}\n{{חוקי יסוד}}\n[[קטגוריה:חוקים]]"

CONTRACTS = "{{ח:התחלה}}\n{{ח:כותרת|חוק החוזים (חלק כללי), התשל״ג–1973}}\n{{ח:פתיח-התחלה}}\n{{ח:סוגר}}\n" \
            "{{ח:קטע2|פרק א|פרק א׳: כריתת החוזה}}\n" \
            "{{ח:סעיף|2|הצעה}}\n{{ח:ת}} פנייתו של אדם לחברו היא בגדר הצעה, אם היא מעידה על גמירת דעתו של המציע.\n" \
            "{{ח:סוף}}"


def one_hot(text: str) -> list[float]:
    words = ("פרטיות", "הצעה", "kaufvertrag")
    vector = [0.0] * 1024
    for i, word in enumerate(words):
        if word in text.lower():
            vector[i] = 1.0
            return vector
    vector[1023] = 1.0
    return vector


@pytest.fixture()
def fake_embeddings(monkeypatch):
    monkeypatch.setattr(embed, "available", lambda: True)
    monkeypatch.setattr(embed, "embed_texts", lambda texts: [one_hot(t) for t in texts])
    monkeypatch.setattr(embed, "embed_query", lambda text: one_hot(text))


@pytest.fixture()
def no_vectors(monkeypatch):
    monkeypatch.setattr(embed, "available", lambda: False)


def privacy_law(revid=1001):
    return parse_law(PRIVACY, "il-privacy", "חוק הגנת הפרטיות", revid, "2026-10-12T09:00:24Z", "Datenschutzrecht")


def load_il(with_vectors=True):
    law_sync.store_law(privacy_law(), "sha-il", embed_new=with_vectors)
    law_sync.store_law(parse_law(CONTRACTS, "il-contracts-general", "חוק החוזים (חלק כללי)", 2002, "2026-01-08T05:30:13Z",
                                 "Zivilrecht"), "sha-il2", embed_new=with_vectors)


def load_bgb(with_vectors=True):
    law = law_sync.law_from_zip("bgb", ZIP_URL, make_zip(XML))
    law["category"] = "Zivilrecht"
    law_sync.store_law(law, "sha-de", embed_new=with_vectors)


# --------------------------------------------------------------------------- Разбор разметки
def test_parser_reads_sections_headings_and_metadata():
    law = privacy_law()

    assert law["law_abbreviation"] == "חוק הגנת הפרטיות" and law["law_title"] == "חוק הגנת הפרטיות, התשמ״א–1981"
    assert [s["enbez"] for s in law["sections"]] == ["סעיף 1", "סעיף 2", "סעיף 3"]
    first = law["sections"][0]
    assert first["titel"] == "איסור הפגיעה בפרטיות" and first["text"] == "לא יפגע אדם בפרטיות של זולתו ללא הסכמתו."
    assert first["context"] == "חוק הגנת הפרטיות · פרק א׳: פגיעה בפרטיות · סעיף 1 – איסור הפגיעה בפרטיות"
    assert law["sections"][1]["stand"] == ["תיקון: תשס״ו, תשע״א"]
    assert law["revision"] == "1001" and "oldid=1001" in law["source_url"] and law["jurisdiction"] == "IL"
    assert law["stand"][0].endswith("(revision 1001)") and len(law["stand"]) == 3


def test_parser_keeps_subitems_drops_footnotes_links_and_signatures():
    text = privacy_law()["sections"][1]["text"]

    assert text.splitlines() == ["פגיעה בפרטיות היא אחת מאלה:", "  (1) בילוש או התחקות אחרי אדם;",
                                 "  (2) האזנה האסורה על פי חוק האזנת סתר, התשל״ט–1979."]
    assert "{{" not in text and "הנוסח שולב" not in text
    assert "ראש הממשלה" not in " ".join(s["text"] for s in privacy_law()["sections"])


def test_section_that_starts_with_subitems_has_no_leading_indent():
    law = parse_law("{{ח:כותרת|חוק, תש״ף–2020}}\n{{ח:סעיף|6|יציאה}}\n{{ח:תת|(א)}} חופשי לצאת.\n{{ח:תת|(ב)}} חופשי להיכנס.\n{{ח:סוף}}",
                    "il-x", "חוק", 1, "2026-01-01T00:00:00Z", "Zivilrecht")

    assert law["sections"][0]["text"] == "(א) חופשי לצאת.\n(ב) חופשי להיכנס."


def test_repealed_sections_have_no_chunks():
    law = privacy_law()

    assert [c[1] for c in chunks_of(law)] == ["סעיף 1", "סעיף 2"]
    assert next(chunks_of(law))[4].startswith("חוק הגנת הפרטיות · פרק א׳")  # контекст стоит перед текстом куска


def test_unknown_templates_do_not_break_rendering():
    assert render("א {{ח:הערה|скрыто}} ב {{מוקטן|קטן}} ג {{ח:חיצוני|x|שם}}") == "א  ב קטן ג שם"


# --------------------------------------------------------------------------- Реестр
def test_il_pack_is_registered_with_hebrew_text_and_disclaimer():
    pack = get_pack("IL")

    assert pack.enabled and pack.rtl and pack.language == "he" and pack.currency == "ILS"
    assert "Israeli law only" in str(pack.disclaimer) and "Verfassungsrecht" in pack.categories
    with pytest.raises(UnknownJurisdiction):
        get_pack("FR")


def test_catalog_slugs_and_pages_are_unique():
    assert len({law.slug for law in CATALOG}) == len(CATALOG) == len({law.page for law in CATALOG})
    assert all(law.slug.startswith("il-") for law in CATALOG)


# --------------------------------------------------------------------------- Хранилище и поиск
def test_store_law_marks_the_jurisdiction_everywhere(app, fake_embeddings):
    with app.app_context():
        load_il()
        load_bgb()

        assert {l.jurisdiction for l in Law.query.filter(Law.slug.like("il-%"))} == {"IL"}
        assert Law.query.filter_by(slug="bgb").one().jurisdiction == "DE"
        assert Law.query.filter_by(slug="il-privacy").one().revision == "1001"
        assert LawChunk.query.filter_by(jurisdiction="IL").count() == 3 and LawChunk.query.filter_by(jurisdiction="DE").count() == 3
        assert law_search.ready_jurisdictions() == ["DE", "IL"]
        assert law_search.corpus_stats("IL")["laws"] == 2 and law_search.corpus_stats()["laws"] == 3


def test_vector_search_stays_inside_the_chosen_country(app, fake_embeddings):
    with app.app_context():
        load_il()
        load_bgb()

        israeli = law_search.search("הגנה על פרטיות", jurisdiction="IL")
        german = law_search.search("Kaufvertrag Verkäufer", jurisdiction="DE")

        assert israeli and {h["jurisdiction"] for h in israeli} == {"IL"} and israeli[0]["law_abbreviation"] == "חוק הגנת הפרטיות"
        assert german and {h["jurisdiction"] for h in german} == {"DE"} and german[0]["enbez"] == "§ 433"
        assert israeli[0]["source_url"].startswith("https://he.wikisource.org/") and "revision 1001" in israeli[0]["source_version"]


def test_foreign_language_question_is_searched_by_its_hebrew_wording(app, fake_embeddings, monkeypatch):
    asked = []
    monkeypatch.setattr(law_search.llm, "complete", lambda system, user, **kw: asked.append((user, kw["feature"])) or "פגיעה בפרטיות")
    with app.app_context():
        load_il()

        hits = law_search.search("Wann verletzt jemand meine Privatsphäre?", jurisdiction="IL")

        assert asked == [("Wann verletzt jemand meine Privatsphäre?", "law_translate")]
        assert hits[0]["slug"] == "il-privacy"
        assert law_search.search("פגיעה בפרטיות", jurisdiction="IL") and len(asked) == 1  # ивритский вопрос не переводим


def test_translation_failure_falls_back_to_the_original_question(app, fake_embeddings, monkeypatch):
    def down(system, user, **kw):
        raise law_search.AIUnavailable("disabled")

    monkeypatch.setattr(law_search.llm, "complete", down)
    with app.app_context():
        assert law_search.hebrew_query("privacy") == "privacy"


def test_list_items_are_part_of_their_section_not_separate_sections():
    law = parse_law("{{ח:כותרת|חוק, תש״ף–2020}}\n{{ח:סעיף|5|מטרות}}\n{{ח:ת}} המטרות הן:\n{{ח:סעיף*|(1)|}} צדקה;\n"
                    "{{ח:סעיף*|(2)|}} סעד.\n{{ח:סוף}}",
                    "il-x", "חוק", 1, "2026-01-01T00:00:00Z", "Zivilrecht")

    assert [s["enbez"] for s in law["sections"]] == ["סעיף 5"]
    assert law["sections"][0]["text"].splitlines() == ["המטרות הן:", "(1) צדקה;", "(2) סעד."]


def test_unknown_country_is_refused_instead_of_falling_back_to_german_law(app):
    with app.app_context():
        with pytest.raises(UnknownJurisdiction):
            law_search.search("anything", jurisdiction="FR")


def test_direct_reference_in_hebrew_and_english(app, no_vectors):
    with app.app_context():
        load_il(with_vectors=False)

        hebrew = law_search.search("מה כתוב בסעיף 2 לחוק הגנת הפרטיות?", jurisdiction="IL")
        english = law_search.search("section 2 contracts law (general part)", jurisdiction="IL")
        alias = law_search.search("Article 1 privacy protection law", jurisdiction="IL")

        assert hebrew[0]["enbez"] == "סעיף 2" and hebrew[0]["slug"] == "il-privacy" and hebrew[0]["score"] == 1.0
        assert english[0]["slug"] == "il-contracts-general" and english[0]["enbez"] == "סעיף 2"
        assert alias[0]["enbez"] == "סעיף 1"


def test_hebrew_text_search_without_vectors_ignores_prefixes(app, no_vectors):
    with app.app_context():
        load_il(with_vectors=False)

        hits = law_search.search("האם מותר לפגוע בפרטיות של אדם", jurisdiction="IL")

        assert hits and hits[0]["slug"] == "il-privacy"
        assert "פרטיות" in law_search.hebrew_terms("בפרטיות") and law_search.hebrew_terms("מה האם") == []


def test_category_filter_works_inside_the_country(app, no_vectors):
    with app.app_context():
        load_il(with_vectors=False)

        assert law_search.search("הצעה חוזה", category="Zivilrecht", jurisdiction="IL")
        assert law_search.search("הצעה חוזה", category="Datenschutzrecht", jurisdiction="IL") == []


# --------------------------------------------------------------------------- Синхронизация
@pytest.fixture()
def wiki(monkeypatch):
    state = {"revisions": {}, "pages": {}}
    monkeypatch.setattr(source, "fetch_revisions", lambda laws=None: dict(state["revisions"]))

    def fetch_law(law: IlLaw, delay=0.0):
        revid, timestamp = state["revisions"][law.page]
        return parse_law(state["pages"][law.page], law.slug, law.page, revid, timestamp, law.category)

    monkeypatch.setattr(source, "fetch_law", fetch_law)
    state["revisions"] = {"חוק הגנת הפרטיות": (11, "2026-10-12T09:00:24Z")}
    state["pages"] = {"חוק הגנת הפרטיות": PRIVACY}
    return state


def test_il_sync_adds_skips_unchanged_and_updates_on_new_revision(app, wiki, fake_embeddings):
    with app.app_context():
        first = il_sync.sync(only=["il-privacy"], delay=0)
        second = il_sync.sync(only=["il-privacy"], delay=0)
        wiki["revisions"]["חוק הגנת הפרטיות"] = (12, "2026-10-20T09:00:00Z")
        wiki["pages"]["חוק הגנת הפרטיות"] = PRIVACY.replace("ללא הסכמתו", "בלי הסכמתו")
        third = il_sync.sync(only=["il-privacy"], delay=0)

        assert (first["new"], second["unchanged"], third["updated"]) == (1, 1, 1)
        assert Law.query.filter_by(slug="il-privacy").one().revision == "12"
        assert "בלי הסכמתו" in LawSection.query.filter_by(slug="il-privacy", enbez="סעיף 1").one().text
        assert db.session.get(LawSyncState, il_sync.STATE_ID).summary["updated"] == 1


def test_il_sync_reports_missing_pages_and_keeps_going(app, wiki, fake_embeddings):
    with app.app_context():
        summary = il_sync.sync(only=["il-privacy", "il-companies"], delay=0)

        assert summary["new"] == 1 and any("il-companies" in e for e in summary["errors"])


def test_german_sync_never_deletes_israeli_laws(app, fake_embeddings, monkeypatch):
    monkeypatch.setattr(law_sync, "fetch_toc", lambda: [("Bürgerliches Gesetzbuch", ZIP_URL)])
    monkeypatch.setattr(law_sync, "_download", lambda url, timeout=40: make_zip(XML))
    with app.app_context():
        load_il()

        summary = law_sync.sync(delay=0)

        assert summary["new"] == 1 and Law.query.filter_by(jurisdiction="IL").count() == 2


def test_weekly_il_sync_window(app):
    from datetime import timedelta

    from app.utils import utcnow

    tuesday_night = utcnow().replace(hour=2) - timedelta(days=utcnow().weekday() - 1)
    with app.app_context():
        assert il_sync.sync_due(tuesday_night) is False  # корпуса ещё нет
        db.session.add(Law(slug="il-privacy", jurisdiction="IL", abbreviation="x"))
        db.session.commit()
        assert il_sync.sync_due(tuesday_night) is True
        assert il_sync.sync_due(tuesday_night + timedelta(days=1)) is False


# --------------------------------------------------------------------------- Инструмент
def test_tool_searches_israeli_law_and_shows_country_badge_hebrew_text_and_source(app, client, no_vectors, monkeypatch):
    monkeypatch.setattr(laws_tool, "explain", lambda q, hits, jurisdiction="DE": {
        "erlaeuterungen": [{"id": 1, "text": "Regelt die Privatsphäre."}], "nicht_passend": [], "hinweis": "", "ai": True})
    with app.app_context():
        load_il(with_vectors=False)
        load_bgb(with_vectors=False)
    make_user(app, email="lawyer@example.com", verified=True, plan="starter")
    login(client, "lawyer@example.com")

    form = client.get("/services/tools/laws").get_data(as_text=True)
    assert 'value="IL"' in form and 'value="DE"' in form and "oldid=1001" in form and 'dir="rtl"' in form

    location = client.post("/services/tools/laws", data={"query": "פגיעה בפרטיות", "jurisdiction": "IL"}).headers["Location"]
    page = client.get(location).get_data(as_text=True)

    assert "IL · Israel" in page and "israelisches Recht" in page and 'dir="rtl"' in page and 'lang="he"' in page
    assert "לא יפגע אדם בפרטיות" in page and "oldid=1001" in page and "Regelt die Privatsphäre." in page
    assert "Eigentum zu verschaffen" not in page


def test_tool_refuses_a_country_without_corpus_and_defaults_to_germany(app, client, no_vectors):
    with app.app_context():
        load_bgb(with_vectors=False)
    make_user(app, email="member@example.com", verified=True, plan="starter")
    login(client, "member@example.com")

    refused = client.post("/services/tools/laws", data={"query": "Kaufvertrag", "jurisdiction": "IL"}, follow_redirects=True)
    assert refused.status_code == 200 and "Location" not in refused.headers
    assert b"IL" not in client.get("/services/tools/laws").data.split(b'name="jurisdiction"')[1].split(b"</select>")[0]

    location = client.post("/services/tools/laws", data={"query": "Kaufvertrag"}).headers["Location"]
    assert "DE · " in client.get(location).get_data(as_text=True)
    with app.app_context(), pytest.raises(ToolError):
        laws_tool.search("x", jurisdiction="FR")


# --------------------------------------------------------------------------- Каталог услуг
def test_catalog_offers_both_law_searches_and_links_to_the_tool_with_the_country(app, client, no_vectors):
    from app.services.catalog_defaults import seed_default_services

    with app.app_context():
        seed_default_services()
        load_il(with_vectors=False)
        load_bgb(with_vectors=False)
    make_user(app, email="member@example.com", verified=True, plan="starter")
    login(client, "member@example.com")

    listing = client.get("/services/").get_data(as_text=True)
    israel = client.get("/services/gesetze-schnellsuche-il").get_data(as_text=True)
    germany = client.get("/services/gesetze-schnellsuche-de").get_data(as_text=True)
    tool = client.get("/services/tools/laws?jurisdiction=IL").get_data(as_text=True)

    assert "KI-Schnellsuche im deutschen Recht" in listing and "KI-Schnellsuche im israelischen Recht" in listing
    assert "/services/tools/laws?jurisdiction=IL" in israel and "/services/tools/laws?jurisdiction=DE" in germany
    assert '<option value="IL" selected>' in tool


def test_law_service_has_no_tool_button_while_the_corpus_is_missing(app, client):
    from app.services.catalog_defaults import seed_default_services

    with app.app_context():
        seed_default_services()
    make_user(app, email="member@example.com", verified=True, plan="starter")
    login(client, "member@example.com")

    page = client.get("/services/gesetze-schnellsuche-il").get_data(as_text=True)

    assert "KI-Schnellsuche im israelischen Recht" in page and "/services/tools/laws" not in page


# --------------------------------------------------------------------------- Вопросы к Clubi по найденным нормам
def test_clubi_sees_the_found_provisions_and_the_report_offers_questions(app, client, no_vectors, monkeypatch):
    from app.models import ToolRun
    from app.services import assistant
    from app.services.ai import llm

    seen = []
    monkeypatch.setattr(llm, "complete", lambda system, user, **kw: seen.append((system, user)) or "Antwort von Clubi")
    monkeypatch.setattr(assistant, "llm_enabled", lambda: True)
    uid = make_user(app, email="member@example.com", verified=True, plan="starter")
    hit = {"id": 1, "slug": "il-privacy", "jurisdiction": "IL", "law_abbreviation": "חוק הגנת הפרטיות", "enbez": "סעיף 1",
           "titel": "איסור הפגיעה בפרטיות", "text": "לא יפגע אדם בפרטיות של זולתו ללא הסכמתו."}
    with app.app_context():
        run = ToolRun(user_id=uid, tool="laws", subject="Privatsphäre", status="done",
                      result={"hits": [hit], "jurisdiction": "IL"},
                      report={"erlaeuterungen": [{"id": 1, "text": "Verbietet Eingriffe ohne Einwilligung."}],
                              "nicht_passend": [], "hinweis": "", "ai": True})
        db.session.add(run)
        db.session.commit()
        run_id = run.id
    login(client, "member@example.com")

    page = client.get(f"/services/tools/run/{run_id}").get_data(as_text=True)
    answer = client.post("/assistant/ask", json={"message": "Erkläre das", "path": f"/services/tools/run/{run_id}", "history": []})

    assert page.count("data-assistant-ask=") == 4
    assert answer.status_code == 200 and answer.get_json()["context"]
    system, prompt = seen[0]
    assert "לא יפגע אדם בפרטיות" in prompt and "Verbietet Eingriffe" in prompt and "jurisdiction: IL" in prompt
    assert "Gesetzesfundstellen" in system


def test_services_page_shows_the_included_tools_with_a_button_per_country(app, client, no_vectors):
    with app.app_context():
        load_il(with_vectors=False)
        load_bgb(with_vectors=False)
    make_user(app, email="member@example.com", verified=True, plan="starter")
    login(client, "member@example.com")

    page = client.get("/services/").get_data(as_text=True)

    assert 'id="tools-title"' in page and "/services/tools/seo" in page
    assert "/services/tools/laws?jurisdiction=DE" in page and "/services/tools/laws?jurisdiction=IL" in page
