"""Анонимная статистика: уникальные за день без cookie и IP, страна и город из Cloudflare, приватность, админка."""

from datetime import date, timedelta

from app.extensions import db
from app.models.analytics import VisitSeen, VisitStat
from app.services import analytics
from app.utils import utcnow
from tests.conftest import login, make_user

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
PHONE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"}


def _count(app, metric, **filters):
    with app.app_context():
        query = db.session.query(db.func.coalesce(db.func.sum(VisitStat.count), 0)).filter(VisitStat.metric == metric)
        for key, value in filters.items():
            query = query.filter(getattr(VisitStat, key) == value)
        return query.scalar()


def _visit(client, headers=None, path="/"):
    merged = {**BROWSER, "CF-Connecting-IP": "203.0.113.7", **(headers or {})}
    return client.get(path, headers=merged)


def test_same_visitor_is_unique_once_per_day_but_views_are_counted(app, client):
    _visit(client)
    _visit(client)
    _visit(client, {"CF-Connecting-IP": "203.0.113.8"})
    assert _count(app, "views") == 3
    assert _count(app, "visitors") == 2


def test_country_city_source_and_device_are_recorded(app, client):
    _visit(client, {**PHONE, "CF-IPCountry": "DE", "CF-IPCity": "Frankfurt am Main",
                    "Referer": "https://www.google.com/search?q=x"})
    _visit(client, {"CF-IPCountry": "IL", "CF-Connecting-IP": "198.51.100.1"}, path="/?utm_source=Instagram&utm_medium=cpc")
    assert _count(app, "visitors", country="DE", city="Frankfurt am Main", source="google.com", device="mobile") == 1
    assert _count(app, "visitors", country="IL", source="instagram", device="desktop") == 1


def test_unknown_country_markers_are_stored_as_empty(app, client):
    _visit(client, {"CF-IPCountry": "XX", "CF-IPCity": "Nowhere"})
    assert _count(app, "visitors", country="", city="") == 1


def test_bots_and_privacy_signals_and_members_are_not_counted(app, client):
    _visit(client, {"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"})
    _visit(client, {"DNT": "1"})
    _visit(client, {"Sec-GPC": "1"})
    client.get("/")  # тестовый клиент без заголовков браузера
    assert _count(app, "views") == 0
    make_user(app, email="m@example.com", verified=True)
    login(client, "m@example.com")
    _visit(client)
    assert _count(app, "views") == 0


def test_only_public_pages_and_successful_html_are_counted(app, client):
    _visit(client, path="/login")
    _visit(client, path="/register")
    _visit(client, path="/healthz")
    _visit(client, path="/does-not-exist")
    assert _count(app, "views") == 2
    assert _count(app, "register_views") == 1


def test_nothing_personal_is_stored(app, client):
    _visit(client, {"CF-IPCountry": "DE", "CF-Connecting-IP": "203.0.113.77"})
    with app.app_context():
        everything = " ".join(str(v) for row in db.session.query(VisitStat).all()
                              for v in (row.page, row.country, row.city, row.source, row.device))
        seen = db.session.query(VisitSeen).all()
        assert seen
        assert "203.0.113" not in everything and "Mozilla" not in everything
        assert all(len(row.vhash) == 32 and "203.0.113" not in row.vhash for row in seen)


def test_hash_changes_every_day_so_visits_cannot_be_linked(app):
    with app.test_request_context("/", headers={**BROWSER, "CF-Connecting-IP": "203.0.113.7"}):
        from flask import request
        today = analytics.visitor_hash(request, date(2026, 10, 30))
        tomorrow = analytics.visitor_hash(request, date(2026, 10, 31))
    assert today != tomorrow


def test_old_fingerprints_are_purged(app, client):
    _visit(client)
    with app.app_context():
        db.session.add(VisitSeen(day=utcnow().date() - timedelta(days=2), vhash="a" * 32, kind="visit"))
        db.session.commit()
        assert analytics.purge_old() == 1
        db.session.commit()
        assert VisitSeen.query.count() >= 1  # сегодняшний остался


def test_chat_questions_are_counted(app, client, monkeypatch):
    from app.services import public_assistant

    monkeypatch.setattr(public_assistant.llm, "complete", lambda *a, **k: "Hallo!")
    monkeypatch.setattr(public_assistant, "llm_enabled", lambda: True)
    for _ in range(2):
        client.post("/assistant/public", json={"message": "Hi"}, headers=BROWSER)
    assert _count(app, "chat_questions") == 2
    assert _count(app, "chat_users") == 1


def test_admin_page_shows_totals_and_non_admin_gets_404(app, client):
    _visit(client, {"CF-IPCountry": "DE", "CF-IPCity": "Mainz"})
    make_user(app, email="m@example.com", verified=True)
    login(client, "m@example.com")
    assert client.get("/admin/analytics").status_code == 404
    make_user(app, email="boss@example.com", verified=True, admin=True)
    client.post("/logout")
    login(client, "boss@example.com")
    page = client.get("/admin/analytics?days=7", headers={"CF-IPCountry": "DE"}).get_data(as_text=True)
    assert "Mainz" in page and "<svg" in page
    assert client.get("/admin/analytics?days=999").status_code == 200  # неверный период: берётся 30
    assert "/admin/analytics" in client.get("/admin/").get_data(as_text=True)


def test_analytics_can_be_switched_off(app, client):
    app.config["ANALYTICS_ENABLED"] = False
    _visit(client)
    assert _count(app, "views") == 0
