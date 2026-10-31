import io
import json
import zipfile
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import Law, LawChunk, LawSection, LawSyncState
from app.services.laws import embed, importer, search as law_search, sync as law_sync
from app.services.laws.parse import chunk_id, chunks_of, is_gone, parse_law_xml, slugify, split_long_text
from app.services.tools import laws as laws_tool
from app.utils import utcnow
from tests.conftest import login, make_user

XML = """<?xml version="1.0" encoding="UTF-8"?>
<dokumente>
 <norm><metadaten><jurabk>BGB</jurabk><langue>Bürgerliches Gesetzbuch</langue><ausfertigung-datum>1896-08-18</ausfertigung-datum>
   <standangabe><standtyp>Stand</standtyp><standkommentar>zuletzt geändert 2026</standkommentar></standangabe></metadaten>
   <textdaten><text><Content><P>Eingangsformel des Gesetzes.</P></Content></text></textdaten></norm>
 <norm><metadaten><jurabk>BGB</jurabk><enbez>§ 433</enbez><titel>Vertragstypische Pflichten beim Kaufvertrag</titel></metadaten>
   <textdaten><text><Content><P>Durch den Kaufvertrag wird der Verkäufer einer Sache verpflichtet, dem Käufer die Sache zu übergeben und das Eigentum zu verschaffen.</P></Content></text></textdaten></norm>
 <norm><metadaten><jurabk>BGB</jurabk><enbez>§ 434</enbez><titel>Weggefallen</titel></metadaten>
   <textdaten><text><Content><P>(weggefallen)</P></Content></text></textdaten></norm>
 <norm><metadaten><enbez>§ 535</enbez><titel>Inhalt und Hauptpflichten des Mietvertrags</titel></metadaten>
   <textdaten><text><Content><P>Durch den Mietvertrag wird der Vermieter verpflichtet, dem Mieter den Gebrauch der Mietsache zu gewähren.</P></Content></text></textdaten></norm>
</dokumente>"""

ZIP_URL = "https://www.gesetze-im-internet.de/bgb/xml.zip"


def one_hot(text: str) -> list[float]:
    """Тестовый «эмбеддинг»: 1024 числа, единица на позиции по первому ключевому слову."""
    words = ("kaufvertrag", "mietvertrag", "datenschutz")
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


def make_zip(xml=XML) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("BJNR001950896.xml", xml)
    return out.getvalue()


def load_bgb(with_vectors=True):
    law = law_sync.law_from_zip("bgb", ZIP_URL, make_zip())
    law["category"] = "Zivilrecht"
    law_sync.store_law(law, "sha-1", embed_new=with_vectors)
    return law


# --------------------------------------------------------------------------- Разбор
def test_parser_reads_sections_titles_and_skips_empty():
    law = parse_law_xml(XML.encode(), "bgb", ZIP_URL)

    enbez = [s["enbez"] for s in law["sections"]]
    assert enbez == ["", "§ 433", "§ 434", "§ 535"]
    assert law["law_abbreviation"] == "BGB" and law["law_title"] == "Bürgerliches Gesetzbuch"
    assert law["stand"] and law["source_url"] == "https://www.gesetze-im-internet.de/bgb/"


def test_cancelled_sections_have_no_chunks_and_ids_are_stable():
    law = parse_law_xml(XML.encode(), "bgb", ZIP_URL)

    chunks = list(chunks_of(law))

    assert [c[1] for c in chunks] == ["", "§ 433", "§ 535"]
    assert chunks[1][0] == chunk_id("bgb", "§ 433", 1, 0)
    assert is_gone("(weggefallen)") and not is_gone("Text")
    assert slugify(ZIP_URL) == "bgb"


def test_long_text_is_split_by_paragraphs():
    text = "\n".join(["Absatz " + "x" * 900] * 10)

    parts = split_long_text(text, 4000)

    assert len(parts) >= 3 and all(len(p) <= 4000 for p in parts) and "".join(parts).replace("\n", "") == text.replace("\n", "")


# --------------------------------------------------------------------------- Хранилище и поиск
def test_store_law_saves_sections_chunks_and_vectors(app, fake_embeddings):
    with app.app_context():
        load_bgb()

        assert Law.query.one().category == "Zivilrecht"
        assert LawSection.query.count() == 4
        assert LawChunk.query.count() == 3 and LawChunk.query.filter(LawChunk.embedding.isnot(None)).count() == 3


def test_text_search_works_without_any_vectors(app, monkeypatch):
    monkeypatch.setattr(embed, "available", lambda: False)
    with app.app_context():
        load_bgb(with_vectors=False)

        hits = law_search.search("Pflichten des Verkäufers beim Kaufvertrag")

        assert hits and hits[0]["enbez"] == "§ 433" and hits[0]["law_abbreviation"] == "BGB"
        assert "Eigentum zu verschaffen" in hits[0]["text"]


def test_direct_reference_comes_first(app, monkeypatch):
    monkeypatch.setattr(embed, "available", lambda: False)
    with app.app_context():
        load_bgb(with_vectors=False)

        hits = law_search.search("Was steht in § 535 BGB?")

        assert hits[0]["enbez"] == "§ 535"


def test_vector_search_ranks_by_meaning_and_returns_the_whole_section(app, fake_embeddings):
    with app.app_context():
        load_bgb()

        hits = law_search.search("Kaufvertrag Verkäufer", k=2)

        assert hits[0]["enbez"] == "§ 433" and hits[0]["score"] > 0.9
        assert hits[0]["matched_part"] and hits[0]["source_url"].startswith("https://")


def test_category_filter_and_unknown_category(app, monkeypatch):
    monkeypatch.setattr(embed, "available", lambda: False)
    with app.app_context():
        load_bgb(with_vectors=False)

        assert law_search.search("Kaufvertrag", category="Zivilrecht")
        assert law_search.search("Kaufvertrag", category="Steuerrecht") == []


# --------------------------------------------------------------------------- Синхронизация
@pytest.fixture()
def network(monkeypatch):
    state = {"xml": XML, "toc": [("Bürgerliches Gesetzbuch", ZIP_URL)]}
    monkeypatch.setattr(law_sync, "fetch_toc", lambda: list(state["toc"]))
    monkeypatch.setattr(law_sync, "_download", lambda url, timeout=40: make_zip(state["xml"]))
    return state


def test_sync_adds_then_skips_unchanged_then_updates_only_changed_chunks(app, network, fake_embeddings, monkeypatch):
    calls = []
    original = embed.embed_texts
    monkeypatch.setattr(embed, "embed_texts", lambda texts: calls.append(len(texts)) or original(texts))
    with app.app_context():
        first = law_sync.sync(delay=0)
        second = law_sync.sync(delay=0)
        network["xml"] = XML.replace("das Eigentum zu verschaffen", "das Eigentum frei von Rechten zu verschaffen")
        third = law_sync.sync(delay=0)

        assert (first["new"], second["unchanged"], third["updated"]) == (1, 1, 1)
        assert calls == [3, 1]  # beim Update nur der geänderte Abschnitt
        assert "frei von Rechten" in LawChunk.query.filter_by(enbez="§ 433").one().text
        assert db.session.get(LawSyncState, 1).summary["updated"] == 1


def test_sync_removes_laws_that_left_the_table_of_contents(app, network, fake_embeddings):
    with app.app_context():
        law_sync.sync(delay=0)
        network["toc"] = [("Anderes Gesetz", "https://www.gesetze-im-internet.de/xyz/xml.zip")]
        network["xml"] = XML.replace("BGB", "XYZ")

        law_sync.sync(delay=0)

        assert Law.query.filter_by(slug="bgb").count() == 0 and Law.query.filter_by(slug="xyz").count() == 1


def test_sync_survives_a_broken_download(app, network, fake_embeddings, monkeypatch):
    def broken(url, timeout=40):
        raise OSError("Verbindung unterbrochen")

    monkeypatch.setattr(law_sync, "_download", broken)
    with app.app_context():
        summary = law_sync.sync(delay=0)

        assert summary["errors"] and "bgb" in summary["errors"][0] and Law.query.count() == 0


def test_weekly_sync_window(app):
    monday_night = utcnow().replace(hour=2) - timedelta(days=utcnow().weekday())
    with app.app_context():
        assert law_sync.sync_due(monday_night) is False  # корпуса ещё нет
        db.session.add(Law(slug="bgb", abbreviation="BGB"))
        db.session.commit()
        assert law_sync.sync_due(monday_night) is True
        assert law_sync.sync_due(monday_night + timedelta(days=1)) is False  # не понедельник
        db.session.add(LawSyncState(id=1, last_run_at=monday_night - timedelta(hours=1), summary={}))
        db.session.commit()
        assert law_sync.sync_due(monday_night) is False  # сегодня уже обновляли


# --------------------------------------------------------------------------- Перенос из старого сервиса
def test_import_from_old_service(app, tmp_path, monkeypatch):
    (tmp_path / "bgb.json").write_text(json.dumps({
        "slug": "bgb", "law_abbreviation": "BGB", "law_title": "Bürgerliches Gesetzbuch", "category": "Zivilrecht",
        "source_url": "http://www.gesetze-im-internet.de/bgb/", "stand": ["Stand 2026"],
        "sections": [{"enbez": "§ 433", "titel": "Kaufvertrag", "text": "Durch den Kaufvertrag wird ...", "stand": []}]}),
        encoding="utf-8")
    (tmp_path / "_manifest.json").write_text(json.dumps({"bgb": {"sha256": "abc", "fetched_at": "2026-07-01T00:00:00Z"}}))
    pages = [
        {"points": [{"id": "11111111-1111-1111-1111-111111111111", "vector": one_hot("kaufvertrag"),
                     "payload": {"slug": "bgb", "enbez": "§ 433", "titel": "Kaufvertrag", "text": "Durch den Kaufvertrag wird ..."}},
                    {"id": "22222222-2222-2222-2222-222222222222", "vector": one_hot("x"),
                     "payload": {"slug": "bgb", "enbez": "§ 1", "text": "(weggefallen)"}},
                    {"id": "33333333-3333-3333-3333-333333333333", "vector": one_hot("x"),
                     "payload": {"slug": "unbekannt", "enbez": "§ 1", "text": "Text"}}], "next_page_offset": "next"},
        {"points": [], "next_page_offset": None},
    ]
    monkeypatch.setattr(importer, "_scroll", lambda qdrant, collection, offset: pages.pop(0))
    with app.app_context():
        assert importer.import_laws(tmp_path, progress=lambda m: None) == 1
        assert importer.import_laws(tmp_path, progress=lambda m: None) == 0  # повтор безопасен
        added = importer.import_vectors("http://qdrant:6333", progress=lambda m: None)

        assert added == 1 and LawChunk.query.one().embedding[0] == 1.0
        assert Law.query.one().sha256 == "abc" and LawSection.query.one().text.startswith("Durch den Kaufvertrag")


# --------------------------------------------------------------------------- Инструмент
def test_tool_uses_local_corpus_without_external_service(app, client, monkeypatch):
    monkeypatch.setattr(embed, "available", lambda: False)
    monkeypatch.setattr(laws_tool, "explain", lambda q, hits, j="DE": {"erlaeuterungen": [{"id": 1, "text": "Regelt den Kauf."}],
                                                               "nicht_passend": [], "hinweis": "", "ai": True})
    with app.app_context():
        load_bgb(with_vectors=False)
    assert app.config["LAWS_API_URL"] == ""
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    location = client.post("/services/tools/laws", data={"query": "Kaufvertrag Pflichten"}).headers["Location"]
    page = client.get(location).get_data(as_text=True)

    assert "Eigentum zu verschaffen" in page and "Regelt den Kauf." in page


def test_tool_stays_hidden_without_corpus_and_service(app, client):
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    assert client.get("/services/tools/laws").status_code == 404
