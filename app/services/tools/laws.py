"""Gesetzes-Suche: Fundstellen aus dem deutschen Bundesrecht, wörtlich zitiert und sprachlich eingeordnet.

Die semantische Suche läuft in einem separaten internen Dienst (laws-api, nur im Docker-Netz erreichbar), der Club ruft nur
GET /search auf. Der zitierte Gesetzestext kommt unverändert aus der Datenbank (kein KI-Text). Die KI-Einordnung erklärt
ausschließlich, was im Text steht — keine Rechtsberatung, keine Bewertung des Einzelfalls.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

from .common import ToolError, json_from_llm

log = logging.getLogger(__name__)

TIMEOUT = 12
MAX_HITS = 8
CATEGORIES = (
    "Verfassungsrecht", "Zivilrecht", "Handelsrecht", "Gesellschaftsrecht", "Insolvenzrecht", "Verfahrensrecht",
    "Anwaltsrecht", "Wettbewerbsrecht", "Notarrecht", "Strafrecht", "Strafverfahrensrecht", "Wirtschaftsstrafrecht",
    "IT-/Digitalrecht", "Datenschutzrecht", "IT-Sicherheitsrecht", "Urheberrecht", "Wirtschaftsrecht",
    "Finanzaufsichtsrecht", "Steuerrecht", "Steuerverfahrensrecht",
)


def enabled() -> bool:
    return bool(current_app.config.get("LAWS_API_URL"))


def search(query: str, category: str = "", k: int = 6) -> list[dict]:
    """Treffer der internen Suche; jeder trägt den Original-Gesetzestext."""
    base = current_app.config.get("LAWS_API_URL")
    if not base:
        raise ToolError("Die Gesetzes-Suche ist auf dieser Installation nicht eingerichtet.")
    query = query.strip()[:800]
    if not query:
        raise ToolError("Bitte gib eine Frage oder ein Stichwort ein.")
    params = {"q": query, "k": max(1, min(MAX_HITS, k))}
    if category in CATEGORIES:
        params["category"] = category
    try:
        with urllib.request.urlopen(f"{base}/search?{urllib.parse.urlencode(params)}", timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError):
        log.warning("laws-api nicht erreichbar")
        raise ToolError("Die Gesetzes-Suche ist gerade nicht erreichbar. Bitte versuch es in ein paar Minuten erneut.") from None
    except ValueError:
        raise ToolError("Die Gesetzes-Suche hat eine unerwartete Antwort geliefert.") from None
    if not isinstance(data, list):
        raise ToolError("Die Gesetzes-Suche hat eine unerwartete Antwort geliefert.")
    hits = []
    for i, h in enumerate(data[:MAX_HITS]):
        if not isinstance(h, dict) or not h.get("text"):
            continue
        try:
            score = round(float(h.get("score", 0)), 3)
        except (TypeError, ValueError):
            score = 0.0
        hits.append({"id": i + 1, "slug": str(h.get("slug", ""))[:80],
                     "law_abbreviation": str(h.get("law_abbreviation", ""))[:120],
                     "law_title": str(h.get("law_title", ""))[:300], "category": str(h.get("category", ""))[:80],
                     "enbez": str(h.get("enbez", ""))[:60], "titel": str(h.get("titel", ""))[:300],
                     "text": str(h.get("text", ""))[:6000], "stand": h.get("stand") or [],
                     "source_url": _official_url(h.get("source_url")), "score": score})
    return hits


def _official_url(value) -> str:
    """Ссылка на источник показывается, только если это https-адрес (данные сервиса — недоверенный ввод)."""
    value = str(value or "")[:300]
    return value if value.startswith("https://") else ""


SYSTEM = (
    "Du hilfst bei der ERSTEN ORIENTIERUNG im deutschen Bundesrecht, indem du ausschließlich vorhandene Gesetzestexte "
    "einordnest. Das ist KEINE Rechtsberatung und du gibst KEINE Handlungsempfehlung — auch nicht indirekt oder "
    "vorsichtig formuliert (kein 'du solltest', 'ich empfehle', 'am besten machst du', 'das bedeutet für dich'). Du "
    "bewertest nie, ob jemand im Recht ist, und wendest den Text nie auf eine persönliche Situation an — du erklärst "
    "ausschließlich, was im zitierten Gesetzestext wörtlich geregelt ist.\n"
    "Unten stehen nummerierte Fundstellen aus einer semantischen Suche über den kompletten deutschen Bundesgesetzkorpus. "
    "Der Wortlaut wird der Person bereits unverändert angezeigt, du musst ihn nicht wiederholen.\n"
    "Für jede Fundstelle, die WIRKLICH zur Frage passt: schreibe 1–2 sachliche Sätze, was der Text bedeutet — rein "
    "sprachliche Erläuterung, keine Einzelfall-Einschätzung. Fundstellen, die nicht wirklich passen (nur oberflächlich "
    "ähnliche Wörter, anderes Thema), listest du in 'nicht_passend' und lässt sie sonst weg — erfinde keine Verbindung. "
    "Wenn KEINE Fundstelle wirklich passt, sag das ehrlich in 'hinweis', statt zu raten oder aus allgemeinem Wissen zu "
    "antworten.\n"
    'Antworte NUR als JSON: {"erlaeuterungen": [{"id": <Nummer>, "text": "…"}], "nicht_passend": [<Nummern>], '
    '"hinweis": "1–2 Sätze, z. B. wenn nichts passt, die Frage mehrere Rechtsgebiete berührt oder eine Fachperson '
    'gefragt werden sollte"}. In Textwerten keine doppelten Anführungszeichen (nutze ‚einfache‘), keine Zeilenumbrüche.'
)


def _fallback(hits: list[dict]) -> dict:
    return {"erlaeuterungen": [], "nicht_passend": [h["id"] for h in hits],
            "hinweis": "Automatische Fundstellen ohne KI-Erläuterung (keine KI-Anbindung konfiguriert). Prüfe die "
                       "zitierten Texte direkt.", "ai": False}


def explain(query: str, hits: list[dict]) -> dict:
    """{erlaeuterungen: [{id, text}], nicht_passend: [id], hinweis, ai}. Erklärt nur, empfiehlt nichts."""
    if not hits:
        return {"erlaeuterungen": [], "nicht_passend": [],
                "hinweis": "Zu dieser Frage wurden keine passenden Fundstellen gefunden.", "ai": False}
    payload = {"frage": query, "fundstellen": [
        {"id": h["id"], "gesetz": h["law_abbreviation"] or h["slug"], "paragraph": h["enbez"], "titel": h["titel"],
         "kategorie": h["category"], "text": h["text"][:1200]} for h in hits]}
    data = json_from_llm(SYSTEM, payload, max_tokens=1400, purpose="law_search")
    if data is None:
        return _fallback(hits)
    valid_ids = {h["id"] for h in hits}
    erl = [{"id": int(e["id"]), "text": str(e["text"])[:600]} for e in data.get("erlaeuterungen", [])
           if isinstance(e, dict) and str(e.get("id", "")).isdigit() and int(e["id"]) in valid_ids and e.get("text")]
    nicht = sorted({int(i) for i in data.get("nicht_passend", []) if str(i).isdigit() and int(i) in valid_ids}
                   - {e["id"] for e in erl})
    return {"erlaeuterungen": erl, "nicht_passend": nicht, "hinweis": str(data.get("hinweis", ""))[:400], "ai": True}
