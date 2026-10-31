import pytest

from app.extensions import db
from app.models import ToolRun
from app.services import safe_fetch
from app.services.tools import compliance, laws, panel, security, seo
from app.services.tools import common
from tests.conftest import login, make_user

PAGE = """<!doctype html><html lang="de"><head><title>Muster GmbH - Steuerberatung in Frankfurt am Main</title>
<meta name="description" content="Steuerberatung für Gründer und kleine Unternehmen in Frankfurt, mit klaren Festpreisen und persönlicher Betreuung.">
<meta name="viewport" content="width=device-width, initial-scale=1"><link rel="canonical" href="https://muster.example/">
</head><body><h1>Steuerberatung</h1><h2>Leistungen</h2><h2>Kontakt</h2>
<a href="/impressum">Impressum</a><a href="/datenschutz">Datenschutz</a><a href="/a">A</a><a href="/b">B</a><a href="/c">C</a>
<img src="x.png" alt="Logo"><p>Text</p></body></html>"""


class FakeResp:
    def __init__(self, status=200, headers=None, cookies=None):
        self.status_code = status
        self.headers = common.Headers({"content-type": "text/html", **(headers or {})})
        self.encoding = "utf-8"
        self.cookies = cookies or []


def fake_get(pages):
    def _get(url, max_bytes=0):
        for suffix, body in pages.items():
            if url.rstrip("/").endswith(suffix.rstrip("/")) and suffix != "/":
                return FakeResp(), body.encode(), 0.2, url
        if url.endswith("/robots.txt") or url.endswith("/sitemap.xml"):
            raise common.ToolError("nicht erreichbar")
        return FakeResp(), pages["/"].encode(), 0.2, url
    return _get


# --------------------------------------------------------------------------- Schutz vor SSRF
@pytest.mark.parametrize("raw", ["", "ftp://example.com", "https://user:pw@example.com", "https://example.com:8080",
                                 "https://exa mple.com"])
def test_normalize_url_rejects_unsafe_input(raw):
    with pytest.raises(safe_fetch.FetchError):
        safe_fetch.normalize_url(raw)


def test_normalize_url_adds_scheme():
    assert safe_fetch.normalize_url("example.com") == "https://example.com"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1"])
def test_private_addresses_are_refused(monkeypatch, address):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", (address, 0))])

    with pytest.raises(safe_fetch.FetchError) as exc:
        safe_fetch.public_ips("whatever.example")

    assert exc.value.code == "private"


def test_one_private_record_among_public_ones_is_refused(monkeypatch):
    records = [(0, 0, 0, "", ("93.184.216.34", 0)), (0, 0, 0, "", ("10.0.0.1", 0))]
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", lambda *a, **k: records)

    with pytest.raises(safe_fetch.FetchError):
        safe_fetch.public_ips("mixed.example")


def test_redirect_to_private_host_is_blocked(monkeypatch):
    addresses = {"good.example": "93.184.216.34", "evil.example": "127.0.0.1"}
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo",
                        lambda host, *a, **k: [(0, 0, 0, "", (addresses[host], 0))])

    class Redirecting:
        def __init__(self, parsed, ip):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            class Response:
                status = 302
                msg = type("Msg", (), {"get_all": lambda self, name: []})()

                @staticmethod
                def getheaders():
                    return [("Location", "http://evil.example/secret")]
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(safe_fetch, "_PinnedConnection", Redirecting)

    with pytest.raises(safe_fetch.FetchError) as exc:
        safe_fetch.fetch("http://good.example/")

    assert exc.value.code == "private"


# --------------------------------------------------------------------------- Проверки
def test_seo_analysis_scores_a_reasonable_page(monkeypatch):
    monkeypatch.setattr(seo, "_get", fake_get({"/": PAGE}))

    result = seo.analyze("https://muster.example/", ["Steuerberatung"])

    keys = {f["key"] for f in result["findings"]}
    assert {"https", "title", "description", "h1", "kw:Steuerberatung"} <= keys
    assert 0 < result["score"] <= 100


def test_compliance_finds_missing_consent_for_trackers(monkeypatch):
    page = PAGE.replace("</head>", '<script src="https://www.googletagmanager.com/gtm.js"></script></head>')
    pages = {"/": page, "/impressum": "<html><body>Impressum</body></html>",
             "/datenschutz": "<html><body>Datenschutz</body></html>"}
    monkeypatch.setattr(compliance, "_get", fake_get(pages))

    result = compliance.analyze("https://muster.example/")

    consent = next(f for f in result["findings"] if f["key"] == "cmp")
    assert consent["status"] == "fail"


def test_security_rejects_ip_and_bad_ports():
    for raw in ("", "127.0.0.1", "https://example.com:8443", "localhost"):
        with pytest.raises(common.ToolError):
            security.hostname_from(raw)


def test_report_falls_back_to_rules_without_ai(app, monkeypatch):
    result = {"score": 40, "findings": [
        {"status": "fail", "title": "HTTPS", "detail": "x", "weight": 10, "fix": "do it", "cat": "T", "key": "https"}]}
    monkeypatch.setattr(common, "json_from_llm", lambda *a, **k: None)

    report = common.ai_report("system", {}, result)

    assert report["ai"] is False
    assert report["massnahmen"][0]["titel"] == "HTTPS"


# --------------------------------------------------------------------------- Страницы и лимиты
def _use_stub_seo(monkeypatch):
    stub = {"url": "https://muster.example/", "score": 70, "facts": {}, "findings": [
        {"key": "https", "cat": "Technik", "status": "ok", "title": "HTTPS", "detail": "ok", "weight": 10, "fix": ""}]}
    monkeypatch.setattr(seo, "analyze", lambda url, kws: stub)
    monkeypatch.setattr(seo, "build_report", lambda result, kws: {
        "zusammenfassung": "Gut", "massnahmen": [], "keyword_hinweise": "", "naechste_schritte": "", "ai": True})


def test_guest_cannot_open_tools(app, client):
    make_user(app, email="guest@example.com", verified=True, plan="guest")
    login(client, "guest@example.com")

    assert client.get("/services/tools/").status_code == 302


def test_starter_runs_seo_check_and_sees_report(app, client, monkeypatch):
    _use_stub_seo(monkeypatch)
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    response = client.post("/services/tools/seo", data={"target": "muster.example"})

    assert response.status_code == 302
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Gut" in page and "HTTPS" in page


def test_monthly_limit_is_enforced_per_plan(app, client, monkeypatch):
    _use_stub_seo(monkeypatch)
    user_id = make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")
    for _ in range(5):  # лимит starter для seo — 5
        assert client.post("/services/tools/seo", data={"target": "muster.example"}).status_code == 302

    client.post("/services/tools/seo", data={"target": "muster.example"})

    with app.app_context():
        assert ToolRun.query.filter_by(user_id=user_id, tool="seo").count() == 5


def test_failed_check_does_not_spend_the_limit(app, client, monkeypatch):
    def broken(url, kws):
        raise common.ToolError("Die Seite ist nicht erreichbar.")

    monkeypatch.setattr(seo, "analyze", broken)
    user_id = make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    client.post("/services/tools/seo", data={"target": "muster.example"})

    with app.app_context():
        assert ToolRun.query.filter_by(user_id=user_id).count() == 0


def test_reports_are_private_to_their_owner(app, client, monkeypatch):
    _use_stub_seo(monkeypatch)
    make_user(app, email="a@example.com", verified=True, plan="starter")
    login(client, "a@example.com")
    location = client.post("/services/tools/seo", data={"target": "muster.example"}).headers["Location"]
    client.post("/logout")
    make_user(app, email="b@example.com", verified=True, plan="premium")
    login(client, "b@example.com")

    assert client.get(location).status_code == 404


def test_laws_tool_is_hidden_without_service_url(app, client):
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    assert client.get("/services/tools/laws").status_code == 404


def test_laws_tool_shows_quotes_and_explanation(app, client, monkeypatch):
    app.config["LAWS_API_URL"] = "http://laws.internal"
    hits = [{"id": 1, "slug": "bgb", "law_abbreviation": "BGB", "law_title": "Bürgerliches Gesetzbuch",
             "category": "Zivilrecht", "enbez": "§ 433", "titel": "Kaufvertrag", "text": "Durch den Kaufvertrag ...",
             "stand": [], "source_url": "javascript:alert(1)", "score": 0.9}]
    monkeypatch.setattr(laws, "search", lambda q, c="", k=6, jurisdiction="DE": hits)
    monkeypatch.setattr(laws, "explain", lambda q, h, j="DE": {"erlaeuterungen": [{"id": 1, "text": "Regelt den Kauf."}],
                                                       "nicht_passend": [], "hinweis": "", "ai": True})
    try:
        make_user(app, email="starter@example.com", verified=True, plan="starter")
        login(client, "starter@example.com")

        location = client.post("/services/tools/laws", data={"query": "Kaufvertrag"}).headers["Location"]
        page = client.get(location).get_data(as_text=True)
    finally:
        app.config["LAWS_API_URL"] = ""

    assert "Durch den Kaufvertrag" in page and "Regelt den Kauf." in page


def test_law_source_url_must_be_https():
    assert laws._official_url("javascript:alert(1)") == ""
    assert laws._official_url("https://www.gesetze-im-internet.de/bgb/") != ""


# --------------------------------------------------------------------------- Панель
def test_panel_form_validation():
    data, errors = panel_form({"product_name": "", "description": "short", "price_b": "20"})

    assert {"product_name", "description", "price_a"} <= set(errors)

    data, errors = panel_form({"product_name": "App", "description": "x" * 50, "price_a": "29,90", "n": "50"})
    assert not errors and data["price_a"] == 29.9 and data["n"] == 50


def panel_form(form):
    from app.modules.services.tools import _panel_form

    return _panel_form(form)


def test_panel_run_completes_and_reports(app, client, monkeypatch):
    answers = iter([
        {"kaufabsicht": "nach_test", "kaufwahrscheinlichkeit": 60.0, "relevanz": 8.0, "max_preis": 40.0,
         "zu_billig_preis": 5.0, "hauptgrund": "Spart Zeit", "hauptbedenken": "Preis", "hauptnutzen": "Zeit",
         "aenderungswunsch": "Mehr Funktionen"},
    ] * 100)
    monkeypatch.setattr(panel, "ask_persona", lambda persona, run, price: next(answers))
    monkeypatch.setattr(panel, "json_from_llm", lambda *a, **k: None)
    make_user(app, email="starter@example.com", verified=True, plan="growth")
    login(client, "starter@example.com")

    response = client.post("/services/tools/panel", data={
        "product_name": "Buchhaltungs-App", "description": "Eine App für Freelancer, die Belege automatisch erkennt.",
        "price_a": "29,90", "n": "20", "audience": "beide", "regional": "de", "unit": "monat"})

    assert response.status_code == 302
    with app.app_context():
        run = ToolRun.query.filter_by(tool="panel").one()
        assert run.status == "done" and run.n_done == 20
        assert run.result["n_valid"] == 20
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Spart Zeit" in page or "Preis" in page
