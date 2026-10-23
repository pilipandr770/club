"""SEO-Check: öffentliche Seite abrufen, technisch und inhaltlich prüfen.

Es wird nur die eine öffentliche Seite plus robots.txt und sitemap.xml abgerufen (kein Crawl, keine Anmeldung).
Schutz vor SSRF liegt in services/safe_fetch.py. Der Bericht ist eine Momentaufnahme und keine Ranking-Garantie.
"""

from flask_babel import gettext
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from .common import ToolError as SeoCheckError
from .common import _f, _get, ai_report, normalize_url, score_of

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- HTML-Auswertung
class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.links: list[tuple[str, str]] = []  # (rel, href) für <link>
        self.h: dict[str, list[str]] = {f"h{i}": [] for i in range(1, 7)}
        self._h_open: str | None = None
        self.imgs = 0
        self.imgs_alt = 0
        self.anchors: list[str] = []
        self.jsonld = 0
        self._script_ld = False
        self.lang = ""
        self.text_parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link":
            self.links.append((a.get("rel", "").lower(), a.get("href", "")))
        elif tag in self.h:
            self._h_open = tag
            self.h[tag].append("")
        elif tag == "img":
            self.imgs += 1
            if a.get("alt", "").strip():
                self.imgs_alt += 1
        elif tag == "a" and a.get("href"):
            self.anchors.append(a["href"])
        elif tag == "script":
            self._skip += 1
            self._script_ld = "ld+json" in a.get("type", "").lower()
            if self._script_ld:
                self.jsonld += 1
        elif tag in ("style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self.h:
            self._h_open = None
        elif tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
            self._script_ld = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._h_open and self.h[self._h_open]:
            self.h[self._h_open][-1] += data
        if not self._skip:
            self.text_parts.append(data)


def _words(text: str) -> int:
    return len(re.findall(r"\w+", text, flags=re.UNICODE))


# --------------------------------------------------------------------------- Prüfungen
def _f(key, cat, status, title, detail, weight, fix=""):
    from .common import _f as finding
    return finding(key, cat, status, title, detail, weight, fix)


def analyze(url: str, keywords: list[str]) -> dict:
    url = normalize_url(url)
    resp, body, elapsed, final_url = _get(url)
    fp = urlparse(final_url)
    base = f"{fp.scheme}://{fp.netloc}"
    findings: list[dict] = []

    ctype = resp.headers.get("Content-Type", "")
    if resp.status_code >= 400:
        raise SeoCheckError(gettext('Die Seite antwortet mit dem Fehlercode %(value0)s.', value0=f'{resp.status_code}'))
    if "html" not in ctype.lower() and body[:200].lstrip()[:1] != b"<":
        raise SeoCheckError(gettext('Unter dieser Adresse liegt keine Webseite (kein HTML).'))
    html = body.decode(resp.encoding or "utf-8", errors="replace")
    pg = _Page()
    try:
        pg.feed(html)
    except Exception:  # pragma: no cover - kaputtes HTML
        log.info("HTML-Parser: unvollständig")
    text = " ".join(pg.text_parts)
    words = _words(text)
    title = " ".join(pg.title.split())
    desc = pg.meta.get("description", "").strip()
    h1s = [" ".join(t.split()) for t in pg.h["h1"] if t.strip()]
    T, I, S, K = "Technik", "Inhalt", "Snippets & Social", "Keywords"

    # Technik
    https_ok = fp.scheme == "https"
    findings.append(_f("https", T, "ok" if https_ok else "fail", gettext('Sichere Verbindung (HTTPS)'),
                       gettext('Die Seite wird über HTTPS ausgeliefert.') if https_ok else gettext('Die Seite lädt ohne HTTPS.'), 10,
                       gettext('HTTPS einrichten und alle http-Adressen dauerhaft (301) auf https umleiten.')))
    st = "ok" if elapsed < 1.0 else "warn" if elapsed < 2.5 else "fail"
    findings.append(_f("speed", T, st, gettext('Antwortzeit des Servers'), gettext('%(value0)s Sekunden bis das HTML da ist.', value0=f'{elapsed:.2f}'), 6,
                       gettext('Caching aktivieren, Hosting/Plugins prüfen, Bilder und Skripte verkleinern.')))
    kb = len(body) / 1024
    findings.append(_f("size", T, "ok" if kb < 500 else "warn", gettext('Größe der HTML-Seite'), gettext('%(value0)s KB.', value0=f'{kb:.0f}'), 3,
                       gettext('Unnötigen Code und eingebettete Daten aus dem HTML entfernen.')))
    robots_url = f"{base}/robots.txt"
    robots_txt, sitemap_in_robots = "", False
    try:
        rr, rb, _, _ = _get(robots_url, max_bytes=100_000)
        if rr.status_code == 200 and rb:
            robots_txt = rb.decode("utf-8", errors="replace")
            sitemap_in_robots = bool(re.search(r"(?im)^\s*sitemap:", robots_txt))
    except SeoCheckError:
        pass
    blocks_all = bool(re.search(r"(?is)user-agent:\s*\*\s*(?:[^\n]*\n)*?\s*disallow:\s*/\s*$", robots_txt, re.M))
    if not robots_txt:
        findings.append(_f("robots", T, "warn", gettext('robots.txt'), gettext('Keine robots.txt gefunden.'), 5,
                           gettext('Eine einfache robots.txt anlegen und darin die Sitemap nennen.')))
    elif blocks_all:
        findings.append(_f("robots", T, "fail", gettext('robots.txt sperrt die ganze Seite'),
                           gettext('Die Datei enthält „Disallow: /“ für alle Suchmaschinen.'), 12,
                           gettext('Die Sperre entfernen, sonst wird die Seite nicht in Suchergebnissen erscheinen.')))
    else:
        findings.append(_f("robots", T, "ok", gettext('robots.txt'), gettext('Vorhanden und sperrt die Seite nicht.'), 5))
    sm_ok = sitemap_in_robots
    if not sm_ok:
        try:
            sr, sb, _, _ = _get(f"{base}/sitemap.xml", max_bytes=200_000)
            sm_ok = sr.status_code == 200 and (b"<urlset" in sb[:5000] or b"<sitemapindex" in sb[:5000])
        except SeoCheckError:
            sm_ok = False
    findings.append(_f("sitemap", T, "ok" if sm_ok else "warn", gettext('XML-Sitemap'),
                       gettext('Sitemap gefunden.') if sm_ok else gettext('Keine Sitemap gefunden.'), 5,
                       gettext('Eine sitemap.xml erzeugen (die meisten CMS können das) und in der robots.txt eintragen.')))
    canon = next((h for rel, h in pg.links if "canonical" in rel), "")
    findings.append(_f("canonical", T, "ok" if canon else "warn", gettext('Canonical-Link'),
                       gettext('Gesetzt auf %(value0)s', value0=f'{canon[:120]}') if canon else gettext('Kein Canonical-Link.'), 4,
                       gettext('Pro Seite ein <link rel="canonical"> auf die bevorzugte Adresse setzen (vermeidet Duplikate).')))
    robots_meta = pg.meta.get("robots", "").lower()
    noindex = "noindex" in robots_meta
    findings.append(_f("noindex", T, "fail" if noindex else "ok", gettext('Indexierung erlaubt'),
                       gettext('Die Seite trägt „noindex“ und wird nicht in Suchergebnissen gelistet.') if noindex
                       else gettext('Kein „noindex“ gefunden.'), 12,
                       gettext('Das noindex-Tag entfernen, wenn die Seite gefunden werden soll.')))
    findings.append(_f("lang", T, "ok" if pg.lang else "warn", gettext('Sprachangabe'), gettext('lang="%(value0)s"', value0=f'{pg.lang}') if pg.lang else gettext('Keine Sprache im <html>-Tag.'), 2,
                       gettext('Im <html>-Tag lang="de" (oder die passende Sprache) setzen.')))
    findings.append(_f("viewport", T, "ok" if "viewport" in pg.meta else "fail", gettext('Mobile Darstellung (Viewport)'),
                       gettext('Viewport-Angabe vorhanden.') if "viewport" in pg.meta else gettext('Keine Viewport-Angabe — mobil oft unbrauchbar.'), 5,
                       gettext('<meta name="viewport" content="width=device-width, initial-scale=1"> ergänzen und Layout mobil prüfen.')))

    # Inhalt
    tl = len(title)
    findings.append(_f("title", I, "ok" if 30 <= tl <= 62 else "warn" if tl else "fail", gettext('Seitentitel (Title)'),
                       gettext('„%(value0)s“ (%(value1)s Zeichen)', value0=f'{title[:90]}', value1=f'{tl}') if tl else gettext('Kein Title gefunden.'), 10,
                       gettext('Einen eindeutigen Titel mit 30–60 Zeichen schreiben, wichtigstes Thema/Keyword vorn.')))
    dl = len(desc)
    findings.append(_f("description", I, "ok" if 70 <= dl <= 165 else "warn" if dl else "fail", gettext('Meta-Beschreibung'),
                       gettext('%(value0)s Zeichen.', value0=f'{dl}') if dl else gettext('Keine Meta-Beschreibung.'), 8,
                       gettext('Eine Beschreibung mit 70–160 Zeichen schreiben, die zum Klick einlädt.')))
    findings.append(_f("h1", I, "ok" if len(h1s) == 1 else "warn" if len(h1s) > 1 else "fail", gettext('Hauptüberschrift (H1)'),
                       gettext('„%(value0)s“', value0=f'{h1s[0][:90]}') if len(h1s) == 1 else (gettext('%(value0)s H1-Überschriften.', value0=f'{len(h1s)}') if h1s else gettext('Keine H1.')), 7,
                       gettext('Genau eine H1 pro Seite, die das Thema klar benennt.')))
    h2n = len(pg.h["h2"])
    findings.append(_f("h2", I, "ok" if h2n >= 2 else "warn", gettext('Gliederung (H2/H3)'), gettext('%(value0)s Zwischenüberschriften (H2).', value0=f'{h2n}'), 3,
                       gettext('Den Text mit mehreren H2-Zwischenüberschriften gliedern.')))
    findings.append(_f("words", I, "ok" if words >= 300 else "warn" if words >= 120 else "fail", gettext('Textumfang'),
                       gettext('ca. %(value0)s Wörter.', value0=f'{words}'), 5, gettext('Mehr hilfreichen, eigenständigen Text bieten (Richtwert ab 300 Wörter).')))
    if pg.imgs:
        ratio = pg.imgs_alt / pg.imgs
        findings.append(_f("alt", I, "ok" if ratio >= 0.9 else "warn" if ratio >= 0.5 else "fail", gettext('Alt-Texte bei Bildern'),
                           gettext('%(value0)s von %(value1)s Bildern haben einen Alt-Text.', value0=f'{pg.imgs_alt}', value1=f'{pg.imgs}'), 5,
                           gettext('Jedem inhaltlichen Bild eine kurze, beschreibende Alternativbeschreibung geben.')))
    host = fp.hostname or ""
    internal = [a for a in pg.anchors if a.startswith("/") or host in a]
    findings.append(_f("links", I, "ok" if len(internal) >= 3 else "warn", gettext('Interne Verlinkung'),
                       gettext('%(value0)s interne Links.', value0=f'{len(internal)}'), 3, gettext('Wichtige Seiten untereinander verlinken, mit sprechenden Linktexten.')))

    # Snippets & Social
    og = [k for k in ("og:title", "og:description", "og:image") if pg.meta.get(k)]
    findings.append(_f("og", S, "ok" if len(og) == 3 else "warn" if og else "fail", gettext('Open Graph (Vorschau in sozialen Netzwerken)'),
                       gettext('%(value0)s von 3 Angaben vorhanden.', value0=f'{len(og)}'), 4, gettext('og:title, og:description und og:image setzen — bestimmt die Vorschau bei LinkedIn, WhatsApp & Co.')))
    findings.append(_f("jsonld", S, "ok" if pg.jsonld else "warn", gettext('Strukturierte Daten (JSON-LD)'),
                       gettext('%(value0)s Block/Blöcke gefunden.', value0=f'{pg.jsonld}') if pg.jsonld else gettext('Keine strukturierten Daten.'), 5,
                       gettext('Schema.org-Auszeichnung ergänzen (z. B. Organization, LocalBusiness, Event, Article).')))

    # Keywords
    kws = [k.strip() for k in keywords if k.strip()][:5]
    lower = text.lower()
    for kw in kws:
        k = kw.lower()
        in_t, in_h1, in_d = k in title.lower(), any(k in h.lower() for h in h1s), k in desc.lower()
        n = lower.count(k)
        parts = [("Title", in_t), ("H1", in_h1), ("Beschreibung", in_d)]
        hit = sum(1 for _, v in parts if v)
        findings.append(_f(f"kw:{kw}", K, "ok" if hit >= 2 and n >= 2 else "warn" if hit or n else "fail",
                           gettext('Keyword „%(value0)s“', value0=f'{kw}'),
                           gettext('In %(value0)s; %(value1)s× im Text.', value0=f"{', '.join((n_ for n_, v in parts if v)) or gettext('none of the core elements')}", value1=f'{n}'), 6,
                           gettext('„%(value0)s“ natürlich in Title, H1 und Beschreibung sowie im Text verwenden — ohne Keyword-Stopfen.', value0=f'{kw}')))

    score = score_of(findings)
    return {"url": final_url, "score": score, "findings": findings,
            "facts": {"title": title, "description": desc, "h1": h1s[:3], "words": words, "seconds": round(elapsed, 2),
                      "kb": round(kb), "lang": pg.lang}}


SYSTEM = (
    "Du bist ein erfahrener SEO-Berater im Club und schreibst einen Prüfbericht für Unternehmer:innen "
    "ohne SEO-Vorwissen. Ton: ruhig, klar, wertschätzend, Deutsch, du-Form. Nutze NUR die Prüfergebnisse "
    "(erfinde keine Zahlen, Rankings oder Wettbewerber). Sortiere die Maßnahmen nach Wirkung geteilt durch "
    "Aufwand. Verspreche niemals bestimmte Platzierungen oder Besucherzahlen. Antworte NUR als JSON: "
    '{"zusammenfassung": "3–4 Sätze", "massnahmen": [{"prio": 1, "titel": "…", "warum": "1–2 Sätze, warum das zählt", '
    '"so_gehts": "konkrete Schritte, 1–3 Sätze", "aufwand": "gering|mittel|hoch", "wirkung": "hoch|mittel|niedrig"}] '
    '(5–7 Einträge, nur echte Schwächen), "keyword_hinweise": "2–3 Sätze, leer wenn keine Keywords geprüft", '
    '"naechste_schritte": "2 Sätze"}. '
    "WICHTIG für gültiges JSON: In Textwerten keine doppelten Anführungszeichen (nutze ‚einfache‘), keine "
    "Zeilenumbrüche, keine Aufzählungszeichen."
)


def build_report(result, keywords):
    payload = {"url": result["url"], "score": result["score"], "seite": result["facts"], "keywords": keywords,
               "ergebnisse": [{k: f[k] for k in ("cat", "status", "title", "detail", "fix")}
                              for f in result["findings"]]}
    return ai_report(SYSTEM, payload, result)
