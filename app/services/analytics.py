"""Анонимная статистика посещений: страна, город, источник, устройство и уникальные посетители за день.

Что делаем и чего не делаем:
- Cookie и локальное хранилище браузера не используются, IP-адрес и User-Agent не сохраняются.
- Уникальность за день определяется отпечатком HMAC(суточный ключ, IP + User-Agent). Суточный ключ выводится из SECRET_KEY
  и даты, поэтому на следующий день отпечаток другой и посетителя по дням не связать. Строки visit_seen прошлых дней
  удаляются (purge_old), остаются только агрегаты в visit_stats.
- Страна и город берутся из заголовков Cloudflare (CF-IPCountry, CF-IPCity), собственной базы геолокации нет.
- Не учитываем: роботов, посетителей с DNT: 1 или Sec-GPC: 1, вошедших участников, любые страницы кроме публичных.
Ошибки учёта никогда не ломают страницу: запись идёт в отдельной короткой транзакции и глотает исключения.
"""

import hashlib
import hmac
import logging
import re
from datetime import date, timedelta
from urllib.parse import urlsplit

from flask import current_app
from sqlalchemy import func, text

from ..extensions import db
from ..models.analytics import VisitSeen, VisitStat

log = logging.getLogger(__name__)

METRICS = ("visitors", "views", "chat_users", "chat_questions", "register_views", "signups")
PAGES = {"public.index": "home", "auth.login": "login", "auth.register": "register"}
_BOT = re.compile(r"bot|crawl|spider|slurp|curl|wget|python|httpx|requests|headless|preview|monitor|uptime|"
                  r"facebookexternalhit|embedly|scan|check|probe|lighthouse|go-http|java/|okhttp", re.I)
_UNKNOWN_COUNTRY = {"", "XX", "T1", "A1", "A2"}
_purged_day = None


def _salt(day: date) -> bytes:
    secret = (current_app.config.get("SECRET_KEY") or "").encode()
    return hmac.new(secret, f"analytics:{day.isoformat()}".encode(), hashlib.sha256).digest()


def client_ip(req) -> str:
    """Адрес посетителя только для расчёта отпечатка. Cloudflare присылает его в CF-Connecting-IP."""
    forwarded = req.headers.get("CF-Connecting-IP", "").strip()
    if forwarded:
        return forwarded
    return (req.access_route[0] if req.access_route else req.remote_addr) or ""


def visitor_hash(req, day: date) -> str:
    material = f"{client_ip(req)}|{req.headers.get('User-Agent', '')}".encode()
    return hmac.new(_salt(day), material, hashlib.sha256).hexdigest()[:32]


def is_bot(req) -> bool:
    agent = req.headers.get("User-Agent", "")
    # все настоящие браузеры начинают User-Agent с «Mozilla/»; остальное — скрипты и роботы
    return not agent.startswith("Mozilla/") or bool(_BOT.search(agent))


def wants_privacy(req) -> bool:
    return req.headers.get("DNT") == "1" or req.headers.get("Sec-GPC") == "1"


def device_of(req) -> str:
    agent = req.headers.get("User-Agent", "")
    if re.search(r"iPad|Tablet", agent):
        return "tablet"
    if re.search(r"Mobi|Android|iPhone", agent):
        return "mobile"
    return "desktop"


def location_of(req):
    """(страна, город) из заголовков Cloudflare. Пустая строка — неизвестно."""
    country = req.headers.get("CF-IPCountry", "").strip().upper()
    if len(country) != 2 or not country.isalpha() or country in _UNKNOWN_COUNTRY:
        country = ""
    city = " ".join(req.headers.get("CF-IPCity", "").split())[:80] if country else ""
    return country, city


def source_of(req) -> str:
    """Откуда пришёл: метка utm_source или домен реферера. Свой домен и пустое значение — прямой заход ('')."""
    utm = re.sub(r"[^a-zA-Z0-9_.\-]", "", req.args.get("utm_source", ""))[:40].lower()
    if utm:
        return utm
    referrer = req.headers.get("Referer", "")
    host = (urlsplit(referrer).hostname or "").lower().removeprefix("www.") if referrer else ""
    own = (req.host or "").split(":")[0].lower().removeprefix("www.")
    return "" if not host or host == own else host[:60]


def _enabled(req) -> bool:
    return (current_app.config.get("ANALYTICS_ENABLED", True) and not is_bot(req) and not wants_privacy(req))


def _purge_if_new_day(conn, today: date) -> None:
    global _purged_day
    if _purged_day != today:
        conn.execute(text("DELETE FROM visit_seen WHERE day < :today"), {"today": today})
        _purged_day = today


def _bump(conn, day, metric, page="", country="", city="", source="", device="") -> None:
    conn.execute(text(
        "INSERT INTO visit_stats (day, metric, page, country, city, source, device, count) "
        "VALUES (:day, :metric, :page, :country, :city, :source, :device, 1) "
        "ON CONFLICT (day, metric, page, country, city, source, device) "
        "DO UPDATE SET count = visit_stats.count + 1"),
        {"day": day, "metric": metric, "page": page, "country": country, "city": city, "source": source,
         "device": device})


def _first_today(conn, day, vhash, kind) -> bool:
    result = conn.execute(text("INSERT INTO visit_seen (day, vhash, kind) VALUES (:day, :vhash, :kind) "
                               "ON CONFLICT DO NOTHING"), {"day": day, "vhash": vhash, "kind": kind})
    return result.rowcount == 1


def track_page(req, endpoint: str) -> None:
    """Просмотр публичной страницы: просмотры, уникальные посетители дня и уникальные заходы на регистрацию."""
    if endpoint not in PAGES or not _enabled(req):
        return
    try:
        from ..utils import utcnow

        day = utcnow().date()
        vhash = visitor_hash(req, day)
        country, city = location_of(req)
        device, source, page = device_of(req), source_of(req), PAGES[endpoint]
        with db.engine.begin() as conn:
            _purge_if_new_day(conn, day)
            _bump(conn, day, "views", page, country, city, source, device)
            if _first_today(conn, day, vhash, "visit"):
                _bump(conn, day, "visitors", page, country, city, source, device)
            if endpoint == "auth.register" and _first_today(conn, day, vhash, "register"):
                _bump(conn, day, "register_views", page, country, city, "", device)
    except Exception:  # noqa: BLE001  статистика не должна ломать сайт
        log.warning("Analytics: Seitenaufruf nicht gezählt", exc_info=True)


def track_event(req, metric: str, unique_kind: str | None = None, unique_metric: str | None = None) -> None:
    """Событие: вопрос чату, регистрация. unique_kind/unique_metric считают ещё и уникальных за день."""
    if metric not in METRICS or not _enabled(req):
        return
    try:
        from ..utils import utcnow

        day = utcnow().date()
        country, city = location_of(req)
        device = device_of(req)
        with db.engine.begin() as conn:
            _purge_if_new_day(conn, day)
            _bump(conn, day, metric, "", country, city, "", device)
            if unique_kind and unique_metric and _first_today(conn, day, visitor_hash(req, day), unique_kind):
                _bump(conn, day, unique_metric, "", country, city, "", device)
    except Exception:  # noqa: BLE001
        log.warning("Analytics: Ereignis nicht gezählt", exc_info=True)


def track_chat_question(req) -> None:
    track_event(req, "chat_questions", unique_kind="chat", unique_metric="chat_users")


def track_signup(req) -> None:
    track_event(req, "signups")


def purge_old(today: date | None = None) -> int:
    """Удаляет отпечатки прошлых дней. Возвращает число удалённых строк."""
    from ..utils import utcnow

    today = today or utcnow().date()
    return VisitSeen.query.filter(VisitSeen.day < today).delete()


# --- отчёты для админки ------------------------------------------------------------------------

def _since(days: int) -> date:
    from ..utils import utcnow

    return utcnow().date() - timedelta(days=max(1, days) - 1)


def totals(days: int) -> dict:
    rows = (db.session.query(VisitStat.metric, func.sum(VisitStat.count))
            .filter(VisitStat.day >= _since(days)).group_by(VisitStat.metric).all())
    data = {metric: 0 for metric in METRICS}
    data.update({metric: int(count) for metric, count in rows})
    return data


def daily(days: int, metric: str = "visitors") -> list:
    """[(день, число)] за каждый день периода, включая дни без посещений."""
    start = _since(days)
    rows = dict(db.session.query(VisitStat.day, func.sum(VisitStat.count))
                .filter(VisitStat.day >= start, VisitStat.metric == metric).group_by(VisitStat.day).all())
    return [(start + timedelta(days=i), int(rows.get(start + timedelta(days=i), 0))) for i in range(days)]


def top(dimension: str, days: int, metric: str = "visitors", limit: int = 10) -> list:
    """Лучшие значения измерения: country, city, source, device, page. [(значение, число)]."""
    column = {"country": VisitStat.country, "city": VisitStat.city, "source": VisitStat.source,
              "device": VisitStat.device, "page": VisitStat.page}[dimension]
    rows = (db.session.query(column, func.sum(VisitStat.count))
            .filter(VisitStat.day >= _since(days), VisitStat.metric == metric)
            .group_by(column).order_by(func.sum(VisitStat.count).desc()).limit(limit).all())
    return [(value or "", int(count)) for value, count in rows]


def city_rows(days: int, limit: int = 10) -> list:
    """[(город, страна, число)] отдельно по паре «город и страна»: одинаковые названия не смешиваются."""
    rows = (db.session.query(VisitStat.city, VisitStat.country, func.sum(VisitStat.count))
            .filter(VisitStat.day >= _since(days), VisitStat.metric == "visitors", VisitStat.city != "")
            .group_by(VisitStat.city, VisitStat.country)
            .order_by(func.sum(VisitStat.count).desc()).limit(limit).all())
    return [(city, country, int(count)) for city, country, count in rows]
