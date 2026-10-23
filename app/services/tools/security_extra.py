"""Zusätzliche passive Prüfungen für den Sicherheits-Check.

Alles hier sieht aus wie ein normaler Besuch: eine Seite abrufen, öffentliche DNS-Einträge lesen, öffentliche Verzeichnisse
(RDAP, Zertifikats-Transparenz) abfragen. Keine Portscans, keine Pfad-Suche nach Geheimnissen, keine Angriffsmuster.
Die Auswertung steckt in reinen Funktionen (leicht testbar); die Netzwerkaufrufe sind dünn und scheitern leise zu „nicht ermittelbar“.
"""
from __future__ import annotations

from flask import current_app, has_app_context
from flask_babel import force_locale, get_locale, gettext
from ...i18n_labels import display_label

import json
import logging
import re
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse

from ..safe_fetch import FetchError, fetch, public_ips
from .common import _f

log = logging.getLogger(__name__)

CONTENT = "Seiteninhalt und Drittanbieter"
MAIL = "E-Mail-Sicherheit"
DOMAIN = "Domain und Registrierung"
SUBS = "Sichtbare Subdomains"
TLS = "Verschlüsselung (TLS)"
HDR = "HTTP-Sicherheits-Header"

SENSITIVE_NAMES = ("dev", "test", "staging", "stage", "admin", "intern", "internal", "vpn", "jenkins", "git", "gitlab",
                   "old", "backup", "beta", "intranet", "phpmyadmin", "db", "sql", "demo", "preprod", "uat", "sandbox")
SENSITIVE_ROBOTS = ("admin", "backup", "private", "intern", "secret", "config", "db", "sql", "dump", "staging", "login")


# --------------------------------------------------------------------------- Header
def csp_issues(csp: str) -> list[str]:
    """Schwächen einer Content-Security-Policy (leer = nichts Auffälliges)."""
    if not csp:
        return []
    directives = {}
    for part in csp.split(";"):
        bits = part.strip().split()
        if bits:
            directives[bits[0].lower()] = [b.lower() for b in bits[1:]]
    script = directives.get("script-src") or directives.get("default-src") or []
    issues = []
    if "'unsafe-inline'" in script:
        issues.append("erlaubt 'unsafe-inline' für Skripte")
    if "'unsafe-eval'" in script:
        issues.append("erlaubt 'unsafe-eval'")
    if "*" in script or "https:" in script or "http:" in script:
        issues.append("erlaubt Skripte von beliebigen Quellen")
    if "data:" in script:
        issues.append("erlaubt data:-Skripte")
    if "object-src" not in directives and "default-src" not in directives:
        issues.append("object-src fehlt")
    if "base-uri" not in directives:
        issues.append("base-uri fehlt")
    return [display_label(value) for value in issues]


def hsts_preload_ready(hsts: str) -> bool:
    m = re.search(r"max-age=(\d+)", hsts or "")
    return bool(m and int(m.group(1)) >= 31536000 and "includesubdomains" in hsts.lower() and "preload" in hsts.lower())


def header_findings(hdr: dict, has_cookies: bool) -> list[dict]:
    out = []
    csp = hdr.get("content-security-policy", "")
    issues = csp_issues(csp)
    if csp:
        out.append(_f("csp_quality", HDR, "warn" if issues else "ok", gettext('Qualität der Content-Security-Policy'),
                      gettext('Auffällig: ') + "; ".join(issues) + gettext('.') if issues else gettext('Keine typischen Schwächen erkannt.'), 3,
                      gettext("Die Policy verschärfen: kein 'unsafe-inline'/'unsafe-eval', Skriptquellen einzeln benennen, object-src 'none' und base-uri 'self' setzen.")))
    acao = hdr.get("access-control-allow-origin", "")
    if acao:
        creds = hdr.get("access-control-allow-credentials", "").lower() == "true"
        bad = acao == "*" and creds
        out.append(_f("cors", HDR, "fail" if bad else "info" if acao == "*" else "ok", gettext('CORS-Freigabe'),
                      gettext('Alle Herkünfte erlaubt, zusammen mit Zugangsdaten: gefährliche Kombination.') if bad
                      else gettext('Alle Herkünfte dürfen die Antwort lesen (bei öffentlichen Inhalten meist gewollt).') if acao == "*"
                      else gettext('Freigabe nur für %(value0)s.', value0=f'{acao[:80]}'), 4 if bad else 0,
                      gettext('Access-Control-Allow-Origin nicht auf * setzen, wenn Zugangsdaten oder private Daten im Spiel sind.')))
    cache = hdr.get("cache-control", "").lower()
    if has_cookies:
        safe = "no-store" in cache or "private" in cache
        out.append(_f("cache_cookie", HDR, "ok" if safe else "info", gettext('Caching bei Seiten mit Cookies'),
                      gettext('Cache-Control schützt personenbezogene Antworten.') if safe
                      else gettext("Die Seite setzt Cookies, aber Cache-Control sagt nichts zu 'private' oder 'no-store'."), 1,
                      gettext('Bei Seiten mit persönlichen Inhalten Cache-Control: private bzw. no-store setzen.')))
    hsts = hdr.get("strict-transport-security", "")
    if hsts:
        ready = hsts_preload_ready(hsts)
        out.append(_f("hsts_preload", HDR, "ok" if ready else "info", gettext('HSTS-Preload'),
                      gettext('Erfüllt die Voraussetzungen für die Browser-Preload-Liste.') if ready
                      else gettext('Noch nicht preload-fähig (max-age ≥ 1 Jahr, includeSubDomains und preload nötig).'), 0,
                      gettext('Optional: HSTS mit max-age=31536000; includeSubDomains; preload setzen und auf hstspreload.org eintragen.')))
    modern = [name for name in ("cross-origin-opener-policy", "cross-origin-resource-policy") if name in hdr]
    out.append(_f("modern_hdr", HDR, "ok" if modern else "info", gettext('Moderne Isolations-Header (COOP/CORP)'),
                  gettext('Gesetzt: ') + ", ".join(modern) + gettext('.') if modern else gettext('Nicht gesetzt (optional, stärkt die Trennung der Seite).'), 0,
                  gettext('Optional Cross-Origin-Opener-Policy: same-origin setzen, wenn keine Pop-ups fremder Seiten nötig sind.')))
    return out


# --------------------------------------------------------------------------- Seiteninhalt
class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.generator = ""
        self.scripts: list[tuple[str, bool]] = []  # (src, hat integrity)
        self.styles: list[str] = []
        self.iframes: list[str] = []
        self.insecure: list[str] = []
        self.forms: list[dict] = []
        self._form = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta" and a.get("name", "").lower() == "generator":
            self.generator = a.get("content", "")[:120]
        elif tag == "script" and a.get("src"):
            self.scripts.append((a["src"], bool(a.get("integrity"))))
        elif tag == "link" and "stylesheet" in a.get("rel", "").lower() and a.get("href"):
            self.styles.append(a["href"])
        elif tag == "iframe" and a.get("src"):
            self.iframes.append(a["src"])
        elif tag == "form":
            self._form = {"action": a.get("action", ""), "password": False}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None and a.get("type", "").lower() == "password":
            self._form["password"] = True
        for key in ("src", "href", "action", "data"):
            value = a.get(key, "")
            if value.startswith("http://") and tag in ("script", "link", "img", "iframe", "source", "video", "audio",
                                                         "embed", "object", "form"):
                self.insecure.append(value[:100])

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None


def parse_page(html: str) -> _Page:
    page = _Page()
    try:
        page.feed(html[:400_000])
    except Exception:  # noqa: BLE001 - kaputtes HTML ist kein Fehler der Prüfung
        log.info("HTML nur teilweise lesbar")
    return page


def external_hosts(page: _Page, host: str) -> list[str]:
    own = host.split(".", 1)[-1] if host.count(".") >= 2 else host
    found = []
    for url in [s for s, _ in page.scripts] + page.styles + page.iframes:
        parsed = urlparse(url if "//" in url else "")
        h = (parsed.hostname or "").lower()
        if h and h != host and not h.endswith("." + own) and h not in found:
            found.append(h)
    return found


# bekannte veraltete Versionen: (Muster, Name, Grenze, Grund)
_OUTDATED = (
    (re.compile(r"jquery[-/.]v?(\d+)\.(\d+)\.(\d+)", re.I), "jQuery", (3, 5, 0), "bekannte Sicherheitslücken vor 3.5.0"),
    (re.compile(r"bootstrap[-/@.]v?(\d+)\.(\d+)\.(\d+)", re.I), "Bootstrap", (4, 3, 1), "Sicherheitslücken in älteren Versionen"),
    (re.compile(r"angular(?:js)?[-/@.]v?(1)\.(\d+)\.(\d+)", re.I), "AngularJS", (2, 0, 0), "AngularJS 1.x wird nicht mehr gepflegt"),
)


def outdated_components(page: _Page, hdr: dict) -> list[str]:
    found = []
    for src, _ in page.scripts:
        for pattern, name, limit, reason in _OUTDATED:
            m = pattern.search(src)
            if m:
                version = tuple(int(x) for x in m.groups())
                if version < limit:
                    found.append(f"{name} {'.'.join(map(str, version))} ({reason})")
    gen = page.generator
    m = re.search(r"WordPress\s+(\d+)\.(\d+)", gen, re.I)
    if m and (int(m.group(1)), int(m.group(2))) < (6, 0):
        found.append(f"{gen} (veraltete Version öffentlich sichtbar)")
    elif gen and re.search(r"\d+\.\d+", gen):
        found.append(f"Generator-Angabe verrät die Version: {gen}")
    php = re.search(r"PHP/(\d+)\.(\d+)", hdr.get("x-powered-by", "") + " " + hdr.get("server", ""))
    if php and (int(php.group(1)), int(php.group(2))) < (8, 1):
        found.append(f"PHP {php.group(1)}.{php.group(2)} (Support beendet)")
    return list(dict.fromkeys(found))


def content_findings(html: str, host: str, hdr: dict, https_ok: bool) -> list[dict]:
    page = parse_page(html)
    out = []
    if https_ok:
        insecure = page.insecure
        out.append(_f("mixed", CONTENT, "fail" if insecure else "ok", gettext('Keine unsicheren Inhalte auf der HTTPS-Seite'),
                      gettext('%(value0)s Inhalt(e) über http geladen, z. B. %(value1)s.', value0=f'{len(insecure)}', value1=f'{insecure[0]}') if insecure
                      else gettext('Keine http-Inhalte auf der Startseite gefunden.'), 5,
                      gettext('Alle Skripte, Bilder und Formulare über https laden (Mixed Content beheben).')))
    external = external_hosts(page, host)
    out.append(_f("third_party", CONTENT, "info", gettext('Drittanbieter-Quellen auf der Startseite'),
                  gettext('%(value0)s fremde Quelle(n): %(value1)s.', value0=f'{len(external)}', value1=f"{', '.join(external[:8])}") if external
                  else gettext('Skripte, Schriften und Frames kommen nur von der eigenen Domain.'), 0,
                  gettext('Jede fremde Quelle prüfen: wird sie wirklich gebraucht, steht sie in der Datenschutzerklärung?')))
    no_sri = [s for s, has in page.scripts if not has and s.startswith(("http://", "https://", "//"))
              and (urlparse(s if "//" in s else "").hostname or host) != host]
    if no_sri:
        out.append(_f("sri", CONTENT, "warn", gettext('Fremde Skripte ohne Integritätsprüfung (SRI)'),
                      gettext('%(value0)s fremde(s) Skript(e) ohne integrity-Attribut, z. B. %(value1)s.', value0=f'{len(no_sri)}', value1=f'{no_sri[0][:80]}'), 3,
                      gettext('Bei fremden Skripten ein integrity-Attribut setzen oder die Dateien selbst hosten.')))
    pw_bad = [f for f in page.forms if f["password"] and f["action"].startswith("http://")]
    out.append(_f("pw_form", CONTENT, "fail" if pw_bad else "ok", gettext('Passwortfelder nur über https'),
                  gettext('Ein Formular mit Passwortfeld sendet die Daten über http.') if pw_bad
                  else gettext('Keine Passwortformulare mit unsicherem Ziel gefunden.'), 6 if pw_bad else 2,
                  gettext('Formulare mit Passwortfeld ausschließlich über https absenden.')))
    old = outdated_components(page, hdr)
    out.append(_f("versions", CONTENT, "warn" if old else "ok", gettext('Sichtbare Software-Versionen'),
                  "; ".join(old) + gettext('.') if old else gettext('Keine veralteten oder verratenen Versionen in Seite und Headern erkannt.'), 4,
                  gettext('Veraltete Komponenten aktualisieren und Versionsangaben aus Seite und Headern entfernen.')))
    return out


# --------------------------------------------------------------------------- E-Mail
def spf_lookup_count(record: str, fetch_txt, _depth: int = 0, _budget: list | None = None) -> int:
    """Zählt DNS-Abfragen laut RFC 7208 (include, a, mx, ptr, exists, redirect), inkl. verschachtelter includes."""
    budget = _budget if _budget is not None else [12]
    count = 0
    for term in record.split()[1:]:
        t = term.lstrip("+-~?").lower()
        if t.startswith(("include:", "redirect=")):
            count += 1
            if _depth < 4 and budget[0] > 0:
                budget[0] -= 1
                domain = t.split(":", 1)[-1].split("=", 1)[-1]
                inner = next((r for r in fetch_txt(domain) if r.lower().startswith("v=spf1")), "")
                if inner:
                    count += spf_lookup_count(inner, fetch_txt, _depth + 1, budget)
        elif t.startswith(("a:", "mx:", "exists:", "ptr")) or t in ("a", "mx"):
            count += 1
    return count


def parse_dmarc(record: str) -> dict:
    tags = {}
    for part in record.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            tags[key.strip().lower()] = value.strip()
    return tags


def dmarc_findings(record: str) -> list[dict]:
    tags = parse_dmarc(record)
    out = []
    policy = tags.get("p", "none").lower()
    sub = tags.get("sp", "").lower()
    if sub and policy in ("quarantine", "reject") and sub == "none":
        out.append(_f("dmarc_sp", MAIL, "warn", gettext('DMARC für Subdomains'), gettext('Subdomains haben die schwächere Richtlinie sp=none.'), 2,
                      gettext('sp auf quarantine oder reject setzen oder entfernen.')))
    pct = tags.get("pct", "100")
    if pct.isdigit() and int(pct) < 100 and policy != "none":
        out.append(_f("dmarc_pct", MAIL, "warn", gettext('DMARC-Anteil'), gettext('Die Richtlinie gilt nur für %(value0)s %% der Mails.', value0=f'{pct}'), 2,
                      gettext('pct nach der Testphase auf 100 setzen.')))
    if "rua" not in tags:
        out.append(_f("dmarc_rua", MAIL, "info", gettext('DMARC-Berichte'), gettext('Keine Adresse für Auswertungsberichte (rua) eingetragen.'), 1,
                      gettext('Eine rua-Adresse eintragen, um Missbrauch und Fehlkonfigurationen zu sehen.')))
    return out


def mta_sts_mode(policy_text: str) -> str:
    m = re.search(r"(?im)^\s*mode\s*:\s*(\w+)", policy_text or "")
    return m.group(1).lower() if m else ""


# --------------------------------------------------------------------------- Domain
def rdap_expiry(data: dict):
    """(Ablaufdatum als datetime oder None, Registrar-Name) aus einer RDAP-Antwort."""
    expires = None
    for event in data.get("events", []):
        if event.get("eventAction") == "expiration" and event.get("eventDate"):
            try:
                expires = datetime.fromisoformat(event["eventDate"].replace("Z", "+00:00"))
            except ValueError:
                pass
    registrar = ""
    for entity in data.get("entities", []):
        if "registrar" in entity.get("roles", []):
            for item in (entity.get("vcardArray") or [None, []])[1]:
                if item and item[0] == "fn":
                    registrar = str(item[3])[:80]
    return expires, registrar


def rdap_base_for(bootstrap: dict, tld: str) -> str:
    """Адрес RDAP-сервиса зоны по файлу IANA (data.iana.org/rdap/dns.json); пусто, если зона не найдена."""
    for names, urls in bootstrap.get("services", []):
        if tld.lower() in [n.lower() for n in names] and urls:
            return urls[0].rstrip("/")
    return ""


def certspotter_entries(data: list) -> list[dict]:
    """Ответ Cert Spotter -> та же форма, что у crt.sh: [{'name_value': 'a
b'}]."""
    return [{"name_value": chr(10).join(item.get("dns_names", []))} for item in data if isinstance(item, dict)]


def subdomain_names(entries: list[dict], org: str) -> list[str]:
    names = set()
    for entry in entries:
        for name in str(entry.get("name_value", "")).splitlines():
            name = name.strip().lower().lstrip("*.")
            if name.endswith(org) and name != org:
                names.add(name)
    return sorted(names)


def sensitive_subdomains(names: list[str], org: str) -> list[str]:
    hits = []
    for name in names:
        label = name[: -len(org)].rstrip(".").split(".")
        if any(part in SENSITIVE_NAMES or part.rstrip("0123456789") in SENSITIVE_NAMES for part in label):
            hits.append(name)
    return hits


def robots_hints(text: str) -> list[str]:
    hints = []
    for line in text.splitlines():
        m = re.match(r"(?i)\s*disallow:\s*(\S+)", line)
        if m and any(word in m.group(1).lower() for word in SENSITIVE_ROBOTS):
            hints.append(m.group(1)[:60])
    return hints[:5]


def security_txt_findings(text: str) -> list[dict]:
    out = []
    m = re.search(r"(?im)^\s*expires\s*:\s*(\S+)", text)
    if m:
        try:
            when = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            expired = when < datetime.now(timezone.utc)
            out.append(_f("sectxt_exp", DOMAIN, "warn" if expired else "ok", gettext('security.txt aktuell'),
                          gettext('Das Ablaufdatum (Expires) ist überschritten.') if expired else gettext('Ablaufdatum liegt in der Zukunft.'), 1,
                          gettext('Das Expires-Datum in der security.txt erneuern.')))
        except ValueError:
            pass
    else:
        out.append(_f("sectxt_exp", DOMAIN, "info", gettext('security.txt aktuell'), gettext('Kein Expires-Feld (nach RFC 9116 vorgeschrieben).'), 0,
                      gettext('Ein Expires-Feld mit einem Datum in höchstens einem Jahr ergänzen.')))
    return out


# --------------------------------------------------------------------------- Netzwerk (dünn, scheitert leise)
def _resolver():
    try:
        import dns.resolver
    except ImportError:  # pragma: no cover
        return None
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 2.5
    return resolver


def txt(name: str) -> list[str]:
    resolver = _resolver()
    if resolver is None:
        return []
    try:
        return ["".join(p.decode() for p in rr.strings) for rr in resolver.resolve(name, "TXT")]
    except Exception:  # noqa: BLE001
        return []


def records(name: str, rtype: str) -> list[str]:
    resolver = _resolver()
    if resolver is None:
        return []
    try:
        return [str(rr) for rr in resolver.resolve(name, rtype)]
    except Exception:  # noqa: BLE001
        return []


def tls_protocol_support(host: str) -> dict:
    """Welche TLS-Versionen nimmt der Server an? 1.2 und 1.3 sicher, 1.0/1.1 als Best-Effort-Test."""
    try:
        ip = public_ips(host)[0]
    except FetchError:
        return {}

    def attempt(minimum, maximum, legacy=False):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.minimum_version, ctx.maximum_version = minimum, maximum
            if legacy:
                ctx.set_ciphers("ALL:@SECLEVEL=0")
        except (ValueError, ssl.SSLError):
            return None  # dieser Client kann die Version nicht sprechen: nicht prüfbar
        try:
            with socket.create_connection((ip, 443), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=host):
                    return True
        except (OSError, ssl.SSLError):
            return False

    return {"tls13": attempt(ssl.TLSVersion.TLSv1_3, ssl.TLSVersion.TLSv1_3),
            "tls12": attempt(ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_2),
            "legacy": attempt(ssl.TLSVersion.TLSv1, ssl.TLSVersion.TLSv1_1, legacy=True)}


def tls_findings(host: str) -> list[dict]:
    support = tls_protocol_support(host)
    if not support:
        return []
    out = []
    if support.get("tls13") is not None:
        out.append(_f("tls13", TLS, "ok" if support["tls13"] else "info", gettext('TLS 1.3'),
                      gettext('Der Server unterstützt TLS 1.3.') if support["tls13"] else gettext('TLS 1.3 wird nicht angeboten.'), 2,
                      gettext('TLS 1.3 beim Hoster aktivieren (schneller und sicherer).')))
    if support.get("legacy"):
        out.append(_f("tls_legacy", TLS, "fail", gettext('Veraltete TLS-Versionen'),
                      gettext('Der Server akzeptiert TLS 1.0 oder 1.1, die als unsicher gelten.'), 6,
                      gettext('TLS 1.0 und 1.1 abschalten; nur TLS 1.2 und 1.3 zulassen.')))
    elif support.get("legacy") is False:
        out.append(_f("tls_legacy", TLS, "ok", gettext('Veraltete TLS-Versionen'),
                      gettext('TLS 1.0/1.1 wurden nicht akzeptiert (Best-Effort-Test).'), 3, ""))
    return out


def mail_findings(org: str, host: str, spf: str) -> list[dict]:
    out = []
    mx = records(org, "MX")
    if not mx:
        out.append(_f("no_mx", MAIL, "info", gettext('Mailserver (MX)'), gettext('Die Domain hat keinen MX-Eintrag und empfängt keine Mails.'), 0,
                      gettext('Wenn die Domain nie Mails versendet, SPF „v=spf1 -all“ und DMARC p=reject setzen.')))
    else:
        out.append(_f("has_mx", MAIL, "info", gettext('Mailserver (MX)'), gettext('%(value0)s MX-Eintrag/Einträge vorhanden.', value0=f'{len(mx)}'), 0))
    if spf:
        lookups = spf_lookup_count(spf, txt)
        too_many = lookups > 10
        out.append(_f("spf_lookups", MAIL, "fail" if too_many else "ok", gettext('SPF: Anzahl der DNS-Abfragen'),
                      gettext('%(value0)s Abfragen; erlaubt sind höchstens 10, sonst wird SPF ungültig.', value0=f'{lookups}') if too_many
                      else gettext('%(value0)s von höchstens 10 Abfragen.', value0=f'{lookups}'), 4,
                      gettext('SPF vereinfachen: überflüssige includes entfernen oder Versender zusammenfassen.')))
        if re.search(r"(?i)(?:^|\s)[+?~-]?ptr\b", spf):
            out.append(_f("spf_ptr", MAIL, "warn", gettext('SPF: ptr-Mechanismus'), gettext('ptr ist veraltet und langsam.'), 2, gettext('ptr aus dem SPF-Eintrag entfernen.')))
    sts = [t for t in txt(f"_mta-sts.{org}") if t.lower().startswith("v=stsv1")]
    mode = ""
    if sts:
        try:
            r = fetch(f"https://mta-sts.{org}/.well-known/mta-sts.txt", max_bytes=20_000)
            mode = mta_sts_mode(r.text) if r.status == 200 else ""
        except FetchError:
            mode = ""
    if mx:
        out.append(_f("mta_sts", MAIL, "ok" if mode == "enforce" else "warn" if sts else "info", gettext('MTA-STS (verschlüsselter Mailempfang)'),
                      gettext('Aktiv im Modus enforce.') if mode == "enforce" else gettext('Eingerichtet, aber Modus „%(value0)s“.', value0=f"{mode or 'unbekannt'}") if sts
                      else gettext('Nicht eingerichtet (empfohlen, wenn die Domain Mails empfängt).'), 2,
                      gettext('MTA-STS mit mode: enforce einrichten, damit eingehende Mails verschlüsselt zugestellt werden.')))
        tlsrpt = [t for t in txt(f"_smtp._tls.{org}") if t.lower().startswith("v=tlsrptv1")]
        out.append(_f("tls_rpt", MAIL, "ok" if tlsrpt else "info", gettext('TLS-RPT (Berichte zur Mailverschlüsselung)'),
                      gettext('Eingerichtet.') if tlsrpt else gettext('Nicht eingerichtet (optional).'), 0,
                      gettext('Optional einen TLS-RPT-Eintrag anlegen, um Zustellprobleme der Verschlüsselung zu sehen.')))
    return out


def domain_findings(org: str, host: str) -> list[dict]:
    out = []
    ns = records(org, "NS")
    if ns:
        out.append(_f("ns_count", DOMAIN, "ok" if len(ns) >= 2 else "warn", gettext('Namenserver'),
                      gettext('%(value0)s Namenserver eingetragen.', value0=f'{len(ns)}') if len(ns) >= 2 else gettext('Nur ein Namenserver: fällt er aus, ist die Domain weg.'), 3,
                      gettext('Mindestens zwei Namenserver, am besten bei unterschiedlichen Anbietern, eintragen.')))
    www = f"www.{org}"
    cname = records(www, "CNAME")
    if cname:
        target = cname[0].rstrip(".")
        resolves = bool(records(target, "A") or records(target, "AAAA"))
        out.append(_f("dangling", DOMAIN, "ok" if resolves else "fail", gettext('Verwaiste Weiterleitung (CNAME)'),
                      gettext('Die www-Adresse zeigt auf ein erreichbares Ziel.') if resolves
                      else gettext('www zeigt auf %(value0)s, das nicht mehr existiert: Übernahme durch Fremde möglich (Subdomain Takeover).', value0=f'{target}'), 6,
                      gettext('Den CNAME-Eintrag löschen oder auf ein existierendes Ziel umstellen.')))
    try:
        base = _rdap_base(org.rsplit(".", 1)[-1])
        r = fetch(f"{base}/domain/{org}", max_bytes=300_000) if base else None
        if r is not None and r.status == 200:
            expires, registrar = rdap_expiry(json.loads(r.text))
            if expires:
                days = (expires - datetime.now(timezone.utc)).days
                out.append(_f("domain_exp", DOMAIN, "ok" if days > 60 else "warn" if days > 14 else "fail", gettext('Domain-Laufzeit'),
                              gettext('Läuft in %(value0)s Tagen ab', value0=f'{days}') + (gettext(' (Registrar: %(value0)s).', value0=f'{registrar}') if registrar else gettext('.'))
                              if days >= 0 else gettext('Seit %(value0)s Tagen abgelaufen.', value0=f'{-days}'), 5,
                              gettext('Automatische Verlängerung beim Registrar aktivieren und eine gepflegte Kontakt-Adresse hinterlegen.')))
            else:
                out.append(_f("domain_exp", DOMAIN, "info", gettext('Domain-Laufzeit'),
                              gettext('Die Registrierungsstelle dieser Endung veröffentlicht kein Ablaufdatum (bei .de üblich).'), 0,
                              gettext('Im Kundenkonto des Registrars prüfen, wann die Domain abläuft, und automatische Verlängerung aktivieren.')))
    except (FetchError, ValueError):
        pass
    try:
        r = fetch(f"https://{host}/robots.txt", max_bytes=100_000)
        hints = robots_hints(r.text) if r.status == 200 else []
        if hints:
            out.append(_f("robots_hint", DOMAIN, "info", gettext('Hinweise in der robots.txt'),
                          gettext('Die robots.txt nennt sensible Pfade: ') + ", ".join(hints) + gettext('. Sie ist öffentlich lesbar.'), 1,
                          gettext('Sensible Bereiche per Zugangsschutz sichern; die robots.txt ist kein Schutz.')))
    except FetchError:
        pass
    return out


_BOOTSTRAP: dict = {"at": 0.0, "data": {}}


def _rdap_base(tld: str) -> str:
    """RDAP-Dienst der Endung laut IANA-Bootstrap (einmal pro Tag geladen); leer, wenn unbekannt."""
    now = time.time()
    if now - _BOOTSTRAP["at"] > 86400 or not _BOOTSTRAP["data"]:
        try:
            r = fetch("https://data.iana.org/rdap/dns.json", max_bytes=2_000_000)
            if r.status == 200:
                _BOOTSTRAP.update(at=now, data=json.loads(r.text))
        except (FetchError, ValueError):
            pass
    # .de (DENIC) steht nicht im IANA-Bootstrap, hat aber einen eigenen RDAP-Dienst
    return rdap_base_for(_BOOTSTRAP["data"], tld) or {"de": "https://rdap.denic.de"}.get(tld.lower(), "")


def _certificate_entries(org: str):
    """Zertifikats-Transparenzdaten: erst Cert Spotter, dann crt.sh (dort oft Zeitüberschreitungen). None = nichts erreichbar."""
    try:
        r = fetch(f"https://api.certspotter.com/v1/issuances?domain={org}&include_subdomains=true&expand=dns_names",
                  max_bytes=1_500_000)
        if r.status == 200:
            return certspotter_entries(json.loads(r.text))
    except (FetchError, ValueError):
        pass
    try:
        r = fetch(f"https://crt.sh/?q=%25.{org}&output=json", max_bytes=1_500_000)
    except FetchError:
        return None
    if r.status != 200:
        return None
    text = r.text
    try:
        return json.loads(text)
    except ValueError:  # bei großen Domains wird die Antwort gekürzt: gültigen Anfang verwenden
        cut = text.rfind("},{")
        try:
            return json.loads(text[:cut + 1] + "]") if cut > 0 else []
        except ValueError:
            return []


def subdomain_findings(org: str) -> list[dict]:
    entries = _certificate_entries(org)
    if entries is None:
        return [_f("subs", SUBS, "info", gettext('Öffentlich sichtbare Subdomains'), gettext('Die Zertifikats-Verzeichnisse waren nicht erreichbar.'), 0)]
    names = subdomain_names(entries, org)
    if not names:
        return [_f("subs", SUBS, "info", gettext('Öffentlich sichtbare Subdomains'), gettext('In den öffentlichen Zertifikatslisten wurden keine Subdomains gefunden.'), 0)]
    risky = sensitive_subdomains(names, org)
    detail = gettext("%(count)s subdomains in public certificate logs, for example %(examples)s.", count=len(names), examples=", ".join(names[:5]))
    out = [_f("subs", SUBS, "info", gettext('Öffentlich sichtbare Subdomains'), detail, 0,
              gettext('Prüfen, ob alle Subdomains noch gebraucht werden; nicht mehr genutzte Einträge entfernen.'))]
    if risky:
        out.append(_f("subs_risky", SUBS, "warn", gettext('Subdomains, die nach Test- oder Verwaltungssystemen aussehen'),
                      gettext('Öffentlich sichtbar: ') + ", ".join(risky[:6]) + gettext('.'), 3,
                      gettext('Test-, Admin- und Entwicklungssysteme nicht öffentlich erreichbar lassen oder mit Zugangsschutz sichern.')))
    return out


# --------------------------------------------------------------------------- Zusammenspiel
def run_extras(host: str, org: str, html: str, hdr: dict, https_ok: bool, has_cookies: bool, spf: str,
               security_txt: str | None, budget_seconds: int = 35) -> list[dict]:
    """Alle Zusatzprüfungen parallel. Jede darf scheitern, ohne die Hauptprüfung zu stören."""
    jobs = {
        "headers": lambda: header_findings(hdr, has_cookies),
        "content": lambda: content_findings(html, host, hdr, https_ok) if html else [],
        "tls": lambda: tls_findings(host),
        "mail": lambda: mail_findings(org, host, spf),
        "domain": lambda: domain_findings(org, host),
        "subs": lambda: subdomain_findings(org),
        "sectxt": lambda: security_txt_findings(security_txt) if security_txt else [],
    }
    results: dict[str, list[dict]] = {}
    app = current_app._get_current_object() if has_app_context() else None
    locale = str(get_locale()) if app else "de"

    def run_job(job):
        if app is None:
            return job()
        with app.app_context(), force_locale(locale):
            return job()

    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = {pool.submit(run_job, job): name for name, job in jobs.items()}
        try:
            for future in as_completed(futures, timeout=budget_seconds):
                try:
                    results[futures[future]] = future.result()
                except Exception:  # noqa: BLE001
                    log.exception("Zusatzprüfung %s fehlgeschlagen", futures[future])
        except TimeoutError:
            log.warning("Zusatzprüfungen nicht rechtzeitig fertig: %s", sorted(set(jobs) - set(results)))
    order = ("headers", "content", "tls", "mail", "domain", "subs", "sectxt")
    return [finding for name in order for finding in results.get(name, [])]
