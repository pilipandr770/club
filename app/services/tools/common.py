"""Общее для встроенных инструментов: безопасная загрузка, оценка находок, отчёт через ИИ с запасным вариантом.

Новые находки и отчёты создаются на выбранном языке и сохраняются в ToolRun.
Исторические отчёты и исходные данные не переписываются при смене языка.
"""

import json
import logging
import time

from flask_babel import get_locale, gettext

from ..ai import llm
from ..ai.http import AIUnavailable
from ..safe_fetch import FetchError, fetch
from ..safe_fetch import normalize_url as _normalize

log = logging.getLogger(__name__)


class ToolError(Exception):
    """Понятное пользователю локализованное сообщение."""


_FETCH_MESSAGES = {
    "empty": "Bitte gib die Adresse deiner Website ein.",
    "invalid": "Das ist keine gültige Web-Adresse (z. B. https://www.beispiel.de).",
    "credentials": "Adressen mit Zugangsdaten sind nicht erlaubt.",
    "port": "Es sind nur die Standard-Ports 80 und 443 erlaubt.",
    "dns": "Diese Domain konnte nicht aufgelöst werden. Ist die Adresse richtig geschrieben?",
    "private": "Diese Adresse ist nicht öffentlich erreichbar und kann nicht geprüft werden.",
    "tls": "Das HTTPS-Zertifikat der Seite ist ungültig oder abgelaufen — das ist schon ein wichtiger Befund.",
    "unreachable": "Die Seite ist nicht erreichbar (Zeitüberschreitung oder Verbindungsfehler).",
    "redirects": "Zu viele Weiterleitungen hintereinander.",
}


class Headers(dict):
    """Заголовки без учёта регистра ключей."""

    def get(self, key, default=None):
        return super().get(key.lower(), default)

    def __contains__(self, key):
        return super().__contains__(key.lower())


class Resp:
    def __init__(self, fetched):
        self.status_code = fetched.status
        self.headers = Headers(fetched.headers)
        self.encoding = fetched.charset
        self.cookies = list(fetched.cookies)


def normalize_url(raw):
    try:
        return _normalize(raw)
    except FetchError as exc:
        from ...i18n_labels import display_label
        raise ToolError(display_label(_FETCH_MESSAGES.get(exc.code, _FETCH_MESSAGES["invalid"]))) from None


def _get(url, max_bytes=2 * 1024 * 1024):
    """(ответ, тело, секунды, конечный адрес) — совместимо с логикой проверок."""
    try:
        fetched = fetch(url, max_bytes=max_bytes)
    except FetchError as exc:
        from ...i18n_labels import display_label
        raise ToolError(display_label(_FETCH_MESSAGES.get(exc.code, _FETCH_MESSAGES["unreachable"]))) from None
    return Resp(fetched), fetched.body, fetched.elapsed, fetched.url


def _f(key, cat, status, title, detail, weight, fix=""):
    from ...i18n_labels import display_label
    return {"key": key, "cat": display_label(cat), "status": status, "title": display_label(title),
            "detail": detail, "weight": weight, "fix": display_label(fix)}


def score_of(findings):
    scored = [f for f in findings if f["status"] != "info" and f["weight"] > 0]
    weight = sum(f["weight"] for f in scored)
    got = sum(f["weight"] * (1 if f["status"] == "ok" else 0.5 if f["status"] == "warn" else 0) for f in scored)
    return round(100 * got / weight) if weight else 0


def json_from_llm(system, payload, max_tokens=4500, purpose=""):
    """Ответ модели как JSON, две попытки. None — если ИИ недоступен или ответ не разобрать."""
    language = "Russian" if str(get_locale()) == "ru" else "German"
    system += f" Write all human-readable text values in {language}. Keep JSON keys, enum codes and original legal quotations unchanged."
    started = time.monotonic()
    for attempt in (1, 2):
        if attempt == 2 and time.monotonic() - started > 40:
            break
        try:
            raw = llm.complete(
                system + (" Deine letzte Antwort war kein gültiges JSON (vermutlich zu lang). Antworte kompakter "
                          "und achte streng auf die Syntax." if attempt == 2 else ""),
                json.dumps(payload, ensure_ascii=False), max_tokens=max_tokens, timeout=60, feature="tools")
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end <= start:
                raise ValueError(f"kein JSON, Anfang: {raw[:80]!r}")
            return json.loads(raw[start:end + 1], strict=False)
        except AIUnavailable as exc:
            log.info("%s: ИИ недоступен (%s)", purpose, exc)
            return None
        except ValueError as exc:
            log.warning("%s: неверный JSON (попытка %s): %s", purpose, attempt, exc)
    return None


def _fallback_report(result):
    bad = [f for f in result["findings"] if f["status"] not in ("ok", "info")]
    bad.sort(key=lambda f: (f["status"] != "fail", -f["weight"]))
    actions = [{"prio": i + 1, "titel": f["title"], "warum": f["detail"], "so_gehts": f["fix"],
                "aufwand": "mittel", "wirkung": "hoch" if f["weight"] >= 8 else "mittel" if f["weight"] >= 5 else "niedrig"}
               for i, f in enumerate(bad[:6])]
    s = result["score"]
    verdict = (gettext("Very well positioned") if s >= 85 else gettext("Solid, with clear room for improvement") if s >= 65
               else gettext("Significant improvements needed"))
    return {"zusammenfassung": gettext("Check result: %(verdict)s (score %(score)s/100). The most useful improvements are listed below in order of impact.", verdict=verdict, score=s),
            "massnahmen": actions, "keyword_hinweise": "",
            "naechste_schritte": gettext("Apply the actions in order, then check the page again."),
            "ai": False}


def ai_report(system, payload, result):
    """Отчёт моделью; без ИИ или при ошибке — отчёт по правилам."""
    data = json_from_llm(system + " Fasse dich kurz: jeder Textwert höchstens zwei Sätze, insgesamt höchstens "
                         "6 Maßnahmen.", payload, purpose="tool_report")
    if data is None:
        return _fallback_report(result)
    try:
        acts = [{"prio": int(a.get("prio", i + 1)), "titel": str(a.get("titel", ""))[:160],
                 "warum": str(a.get("warum", ""))[:500], "so_gehts": str(a.get("so_gehts", ""))[:600],
                 "aufwand": str(a.get("aufwand", "mittel")), "wirkung": str(a.get("wirkung", "mittel"))}
                for i, a in enumerate(data.get("massnahmen", [])[:8]) if a.get("titel")]
        if not acts:
            raise ValueError("keine Maßnahmen")
        return {"zusammenfassung": str(data.get("zusammenfassung", ""))[:1200], "massnahmen": acts,
                "keyword_hinweise": str(data.get("keyword_hinweise", ""))[:800],
                "naechste_schritte": str(data.get("naechste_schritte", ""))[:600], "ai": True}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        log.info("Отчёт: запасной вариант (%s)", exc)
        return _fallback_report(result)
