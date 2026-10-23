"""Sicherheits-Check (passiv) — Leistung „Black Box“.

Wie ein normaler Browser- bzw. Mailserver-Besuch: EIN HTTPS-Abruf, EIN TLS-Handshake auf Port 443 und öffentliche
DNS-Abfragen (SPF, DMARC, DKIM-Selektoren, MX, CAA, DNSSEC) sowie /.well-known/security.txt. Kein Portscan, kein Schwachstellen-
scan, keine Angriffsmuster. Das ist ausdrücklich KEIN Penetrationstest. Schutz vor SSRF: nur öffentliche IP-Adressen.
"""
from __future__ import annotations

from flask_babel import gettext

import logging
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..safe_fetch import FetchError, public_ips
from .common import ToolError as SeoCheckError
from . import security_extra as extra
from .common import _f, _get, ai_report, score_of

log = logging.getLogger(__name__)

TLS, HDR, COO, MAIL, DNS = "Verschlüsselung (TLS)", "HTTP-Sicherheits-Header", "Cookies", "E-Mail-Sicherheit", "DNS und Kontakt"
DKIM_SELECTORS = ["default", "google", "selector1", "selector2", "k1", "s1", "mail", "dkim"]
SECOND_LEVEL = {"co.uk", "org.uk", "com.au", "co.nz", "com.br", "co.za"}


def hostname_from(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise SeoCheckError(gettext('Bitte gib die Domain deiner Website ein.'))
    if "://" not in raw:
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme not in ("http", "https"):
        raise SeoCheckError(gettext('Das ist keine gültige Domain (z. B. beispiel.de).'))
    host = (p.hostname or "").lower().rstrip(".")
    if not host or " " in raw or "." not in host or p.username or p.password:
        raise SeoCheckError(gettext('Das ist keine gültige Domain (z. B. beispiel.de).'))
    if re.fullmatch(r"[\d.:]+", host):
        raise SeoCheckError(gettext('Bitte eine Domain statt einer IP-Adresse angeben.'))
    if p.port not in (None, 80, 443):
        raise SeoCheckError(gettext('Es sind nur die Standard-Ports 80 und 443 erlaubt.'))
    return host


def org_domain(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# --------------------------------------------------------------------------- Netzwerk-Bausteine (einzeln testbar)
def _assert_public(host: str) -> None:
    try:
        public_ips(host)
    except FetchError:
        raise SeoCheckError(gettext('Diese Domain ist nicht öffentlich erreichbar oder konnte nicht aufgelöst werden.')) from None


def _tls(host: str) -> dict:
    """Ein TLS-Handshake auf 443 gegen eine geprüft öffentliche IP."""
    try:
        ip = public_ips(host)[0]
    except FetchError:
        return {"reachable": False, "error": "keine öffentliche Adresse"}
    out: dict = {"reachable": False}
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((ip, 443), timeout=6) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as s:
                cert = s.getpeercert()
                out.update(reachable=True, valid=True, version=s.version(), cipher=(s.cipher() or ("", "", 0))[0])
                out["not_after"] = ssl.cert_time_to_seconds(cert["notAfter"])
                out["issuer"] = " ".join(v for rdn in cert.get("issuer", ()) for k, v in rdn if k == "organizationName")
    except ssl.SSLCertVerificationError as exc:
        out.update(reachable=True, valid=False, error=str(exc.verify_message or exc)[:160])
    except (OSError, ssl.SSLError) as exc:
        out.update(reachable=False, error=str(exc)[:160])
    return out


def _resolver():
    try:
        import dns.resolver
    except ImportError:  # pragma: no cover - dnspython в requirements, но проверка не должна падать без него
        return None
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 2
    return resolver


def _txt(name: str) -> list[str]:
    resolver = _resolver()
    if resolver is None:
        return []
    try:
        return ["".join(p.decode() for p in rr.strings) for rr in resolver.resolve(name, "TXT")]
    except Exception:
        return []


def _has(name: str, rtype: str) -> bool:
    resolver = _resolver()
    if resolver is None:
        return False
    try:
        return bool(list(resolver.resolve(name, rtype)))
    except Exception:
        return False


def _fetch(url: str, max_bytes: int = 200_000):
    return _get(url, max_bytes=max_bytes)


# --------------------------------------------------------------------------- Prüfung
def analyze(raw: str) -> dict:
    host = hostname_from(raw)
    _assert_public(host)
    org = org_domain(host)
    F: list[dict] = []

    # ---- HTTP(S) abrufen (Startseite) und Weiterleitung von http
    try:
        resp, page_body, elapsed, final = _fetch(f"https://{host}/")
        https_ok = True
    except SeoCheckError as exc:
        resp, page_body, final, https_ok, https_err = None, b"", "", False, str(exc)
    hdr = {k.lower(): v for k, v in (resp.headers.items() if resp is not None else [])}
    cookies: list[str] = list(resp.cookies) if resp is not None else []

    # ---- TLS
    tls = _tls(host)
    F.append(_f("https", TLS, "ok" if https_ok and tls.get("reachable") else "fail", gettext('HTTPS erreichbar'),
                gettext('Die Seite ist über HTTPS erreichbar.') if https_ok and tls.get("reachable")
                else gettext('HTTPS nicht nutzbar: %(value0)s', value0=f"{tls.get('error') or (https_err if not https_ok else '')}"), 10,
                gettext("HTTPS mit gültigem Zertifikat einrichten (z. B. kostenlos über Let's Encrypt).")))
    if tls.get("reachable"):
        F.append(_f("cert", TLS, "ok" if tls.get("valid") else "fail", gettext('Zertifikat gültig und passend zur Domain'),
                    gettext('Zertifikat wird von Browsern akzeptiert.') + (gettext(' Aussteller: %(value0)s.', value0=f"{tls['issuer']}") if tls.get("issuer") else "")
                    if tls.get("valid") else gettext('Zertifikat wird nicht akzeptiert: %(value0)s', value0=f"{tls.get('error', 'unbekannter Fehler')}"), 12,
                    gettext('Ein gültiges, zur Domain passendes Zertifikat installieren und die Kette vollständig ausliefern.')))
        if tls.get("not_after"):
            days = int((tls["not_after"] - time.time()) // 86400)
            F.append(_f("cert_exp", TLS, "ok" if days > 30 else "warn" if days > 7 else "fail", gettext('Laufzeit des Zertifikats'),
                        gettext('Läuft in %(value0)s Tagen ab.', value0=f'{days}') if days >= 0 else gettext('Seit %(value0)s Tagen abgelaufen.', value0=f'{-days}'), 6,
                        gettext('Automatische Erneuerung einrichten und überwachen.')))
        ver = tls.get("version", "")
        F.append(_f("tls_ver", TLS, "ok" if ver in ("TLSv1.3", "TLSv1.2") else "fail", gettext('TLS-Version'),
                    gettext('Ausgehandelt: %(value0)s.', value0=f'{ver}') if ver else gettext('Nicht ermittelt.'), 6,
                    gettext('Nur TLS 1.2 und 1.3 zulassen; ältere Protokolle abschalten.')))
    try:
        r2, _, _, f2 = _fetch(f"http://{host}/", max_bytes=20_000)
        redirected = urlparse(f2).scheme == "https"
    except SeoCheckError:
        redirected = False
    F.append(_f("redir", TLS, "ok" if redirected else "warn", gettext('Weiterleitung von http auf https'),
                gettext('Aufrufe über http werden auf https umgeleitet.') if redirected else gettext('http-Aufrufe landen nicht auf https.'), 5,
                gettext('Alle http-Anfragen dauerhaft (301) auf https umleiten.')))

    # ---- Header
    if resp is not None:
        hsts = hdr.get("strict-transport-security", "")
        m = re.search(r"max-age=(\d+)", hsts)
        age = int(m.group(1)) if m else 0
        F.append(_f("hsts", HDR, "ok" if age >= 15552000 else "warn" if hsts else "fail", gettext('HSTS (erzwingt HTTPS im Browser)'),
                    gettext('max-age=%(value0)s Sekunden%(value1)s.', value0=f'{age}', value1=f"{(gettext('Including subdomains') if 'includesubdomains' in hsts.lower() else '')}") if hsts
                    else gettext('Header fehlt.'), 6, gettext('Strict-Transport-Security mit max-age von mindestens 6 Monaten setzen.')))
        csp = hdr.get("content-security-policy", "")
        F.append(_f("csp", HDR, "ok" if csp else "warn", gettext('Content-Security-Policy'), gettext('Gesetzt.') if csp else gettext('Nicht gesetzt.'), 5,
                    gettext('Eine Content-Security-Policy gegen das Einschleusen fremder Skripte einführen (erst im Report-Only-Modus testen).')))
        frame = "x-frame-options" in hdr or "frame-ancestors" in csp.lower()
        F.append(_f("frame", HDR, "ok" if frame else "warn", gettext('Schutz vor Einbettung (Clickjacking)'),
                    gettext('Gesetzt.') if frame else gettext('Weder X-Frame-Options noch frame-ancestors vorhanden.'), 3,
                    gettext('X-Frame-Options: DENY (oder SAMEORIGIN) bzw. CSP frame-ancestors setzen.')))
        F.append(_f("xcto", HDR, "ok" if "x-content-type-options" in hdr else "warn", gettext('X-Content-Type-Options'),
                    gettext('Gesetzt.') if "x-content-type-options" in hdr else gettext('Nicht gesetzt.'), 2, gettext('X-Content-Type-Options: nosniff setzen.')))
        F.append(_f("refpol", HDR, "ok" if "referrer-policy" in hdr else "warn", gettext('Referrer-Policy'),
                    gettext('Gesetzt.') if "referrer-policy" in hdr else gettext('Nicht gesetzt.'), 2,
                    gettext('Referrer-Policy: strict-origin-when-cross-origin setzen.')))
        F.append(_f("permpol", HDR, "ok" if "permissions-policy" in hdr else "info", gettext('Permissions-Policy'),
                    gettext('Gesetzt.') if "permissions-policy" in hdr else gettext('Nicht gesetzt (empfohlen).'), 1,
                    gettext('Nicht benötigte Browser-Funktionen (Kamera, Mikrofon, Standort) per Permissions-Policy sperren.')))
        leak = [f"{k}: {v}" for k, v in hdr.items() if k in ("server", "x-powered-by", "x-aspnet-version") and re.search(r"\d+\.\d+", v)]
        F.append(_f("leak", HDR, "warn" if leak else "ok", gettext('Keine Software-Versionen im Header'),
                    gettext('Preisgegeben: ') + "; ".join(leak) if leak else gettext('Keine Versionsnummern in Server-Headern erkannt.'), 3,
                    gettext('Versionsangaben in Server- und X-Powered-By-Headern entfernen.')))

    # ---- Cookies
    if cookies:
        def flag(c, name):
            return re.search(rf"(?i);\s*{name}\b", c) is not None
        total = len(cookies)
        no_secure = sum(1 for c in cookies if not flag(c, "secure"))
        no_http = sum(1 for c in cookies if not flag(c, "httponly"))
        no_same = sum(1 for c in cookies if not flag(c, "samesite"))
        weak = sum(1 for c in cookies if not (flag(c, "secure") and flag(c, "httponly") and flag(c, "samesite")))
        F.append(_f("cookies", COO, "ok" if weak == 0 else "warn" if weak < total else "fail", gettext('Cookie-Schutzattribute'),
                    gettext('%(value0)s Cookie(s): %(value1)s× ohne Secure, %(value2)s× ohne HttpOnly, %(value3)s× ohne SameSite.', value0=f'{total}', value1=f'{no_secure}', value2=f'{no_http}', value3=f'{no_same}'), 6,
                    gettext('Cookies mit Secure, HttpOnly (falls ohne Skriptzugriff) und SameSite=Lax/Strict setzen.')))
    else:
        F.append(_f("cookies", COO, "info", gettext('Cookie-Schutzattribute'), gettext('Beim ersten Aufruf werden keine Cookies gesetzt.'), 0))

    # ---- E-Mail-Sicherheit (DNS)
    spf_recs = [t for t in _txt(host) + (_txt(org) if org != host else []) if t.lower().startswith("v=spf1")]
    spf = spf_recs[0] if spf_recs else ""
    if not spf and not extra.records(org, "MX"):
        F.append(_f("spf", MAIL, "warn", gettext('SPF'), gettext('Die Domain empfängt keine Mails, ist aber nicht gegen Missbrauch als Absender geschützt.'), 3,
                    gettext('SPF „v=spf1 -all“ eintragen: damit ist klar, dass von dieser Domain keine Mails kommen dürfen.')))
    elif not spf:
        F.append(_f("spf", MAIL, "fail", gettext('SPF'), gettext('Kein SPF-Eintrag: Fremde können leichter in deinem Namen Mails versenden.'), 6,
                    gettext('SPF-Eintrag (TXT, „v=spf1 … -all“) mit den erlaubten Versendern anlegen.')))
    else:
        end = spf.split()[-1].lower()
        st = "ok" if end == "-all" else "warn" if end == "~all" else "fail"
        F.append(_f("spf", MAIL, st, gettext('SPF'), gettext('Eintrag vorhanden, Abschluss „%(value0)s“.', value0=f'{end}') + (gettext(' Empfohlen ist „-all“.') if st == "warn" else ""), 6,
                    gettext('SPF strikt mit „-all“ (oder mindestens „~all“) abschließen; „+all“ und „?all“ schützen nicht.')))
    dmarc_recs = [t for t in _txt(f"_dmarc.{host}") + (_txt(f"_dmarc.{org}") if org != host else []) if t.lower().startswith("v=dmarc1")]
    if not dmarc_recs:
        F.append(_f("dmarc", MAIL, "fail", gettext('DMARC'), gettext('Kein DMARC-Eintrag.'), 8,
                    gettext('DMARC-Eintrag anlegen (zuerst p=none mit Berichten, dann p=quarantine oder p=reject).')))
    else:
        pol = re.search(r"(?i)\bp=(\w+)", dmarc_recs[0])
        pol = (pol.group(1).lower() if pol else "none")
        F.append(_f("dmarc", MAIL, "ok" if pol in ("quarantine", "reject") else "warn", gettext('DMARC'),
                    gettext('Richtlinie p=%(value0)s.', value0=f'{pol}') + (gettext(' Nur Beobachtung, kein Schutz.') if pol == "none" else ""), 8,
                    gettext('Nach der Beobachtungsphase auf p=quarantine bzw. p=reject umstellen.')))
        F.extend(extra.dmarc_findings(dmarc_recs[0]))
    dkim = next((s for s in DKIM_SELECTORS if any("p=" in t for t in _txt(f"{s}._domainkey.{org}"))), None)
    F.append(_f("dkim", MAIL, "ok" if dkim else "info", gettext('DKIM'),
                gettext('Schlüssel gefunden (Selektor „%(value0)s“).', value0=f'{dkim}') if dkim else gettext('Über gängige Selektoren kein Schlüssel gefunden — das beweist nichts, da Selektoren frei wählbar sind. Bitte beim Mailanbieter prüfen.'), 3,
                gettext('DKIM beim Mailanbieter aktivieren und den Schlüssel im DNS veröffentlichen.')))

    # ---- DNS und Kontakt
    F.append(_f("dnssec", DNS, "ok" if _has(org, "DS") else "warn", gettext('DNSSEC'), gettext('Signiert (DS-Eintrag vorhanden).')
                if _has(org, "DS") else gettext('Nicht aktiviert.'), 3, gettext('DNSSEC beim Domain-Registrar bzw. DNS-Anbieter aktivieren.')))
    has_caa = _has(org, "CAA")
    F.append(_f("caa", DNS, "ok" if has_caa else "info", gettext('CAA (erlaubte Zertifikatsaussteller)'),
                gettext('Vorhanden.') if has_caa else gettext('Nicht gesetzt (empfohlen).'), 2,
                gettext('CAA-Eintrag anlegen, damit nur gewünschte Stellen Zertifikate ausstellen dürfen.')))
    sectxt = False
    sectxt_text = None
    try:
        r3, body, _, _ = _fetch(f"https://{host}/.well-known/security.txt", max_bytes=20_000)
        sectxt = r3.status_code == 200 and b"contact:" in body.lower()
        if sectxt:
            sectxt_text = body.decode("utf-8", errors="replace")
    except SeoCheckError:
        pass
    F.append(_f("sectxt", DNS, "ok" if sectxt else "warn", gettext('security.txt (Meldestelle für Sicherheitslücken)'),
                gettext('Vorhanden.') if sectxt else gettext('Keine /.well-known/security.txt gefunden.'), 3,
                gettext('Eine security.txt mit Kontakt für Sicherheitsmeldungen unter /.well-known/ veröffentlichen (RFC 9116).')))

    # Zusatzprüfungen (parallel, jede darf scheitern); doppelte Schlüssel der Hauptprüfung werden nicht überschrieben
    known = {f["key"] for f in F}
    html = page_body.decode(resp.encoding or "utf-8", errors="replace") if resp is not None else ""
    try:
        F.extend(f for f in extra.run_extras(host, org, html, hdr, https_ok, bool(cookies), spf, sectxt_text)
                 if f["key"] not in known)
    except Exception:  # noqa: BLE001 - die Hauptprüfung bleibt auch ohne Zusatzprüfungen gültig
        log.exception("Zusatzprüfungen fehlgeschlagen")

    return {"url": f"https://{host}", "score": score_of(F), "findings": F,
            "facts": {"host": host, "domain": org, "tls": tls.get("version", ""), "aussteller": tls.get("issuer", ""),
                      "pruefungen": len(F)}}


_SYSTEM = ("Du bist Berater für IT-Sicherheit im Club und schreibst einen Prüfbericht für Unternehmer:innen ohne "
           "Technik-Vorwissen. Ton: ruhig, klar, sachlich, Deutsch, du-Form. Grundlage ist ausschließlich eine PASSIVE Prüfung "
           "öffentlich sichtbarer Einstellungen (TLS, HTTP-Header, Seiteninhalt und eingebundene Drittanbieter, Cookies, E-Mail-DNS, "
           "Domain-Laufzeit, öffentlich sichtbare Subdomains). Sage klar, dass das KEIN Penetrationstest "
           "und keine Schwachstellenprüfung ist und Systeme, Software-Stände und Zugänge nicht geprüft wurden. Keine Rechtsberatung. "
           "Nutze NUR die Prüfergebnisse (erfinde nichts). Ordne Maßnahmen nach Risiko (kritisch zuerst), dann nach Wirkung geteilt "
           "durch Aufwand; erkläre jede Maßnahme verständlich und sage, wer sie umsetzt (z. B. Hoster, Webagentur, Mailanbieter). "
           "Bezüge nur: Art. 32 DSGVO (Sicherheit der Verarbeitung) und allgemein NIS2-Anforderungen an technische Maßnahmen; keine "
           "Paragrafen erfinden, keine Bußgelder nennen. Antworte NUR als JSON: {\"zusammenfassung\": \"3–4 Sätze\", \"massnahmen\": "
           "[{\"prio\": 1, \"titel\": \"…\", \"warum\": \"1–2 Sätze\", \"so_gehts\": \"1–3 Sätze, wer was tut\", \"aufwand\": "
           "\"gering|mittel|hoch\", \"wirkung\": \"hoch|mittel|niedrig\"}] (4–7 Einträge, nur echte Schwächen), \"keyword_hinweise\": "
           "\"\", \"naechste_schritte\": \"2 Sätze, inkl. Hinweis, dass ein Penetrationstest tiefer prüft\"}. "
           "WICHTIG für gültiges JSON: In Textwerten keine doppelten Anführungszeichen (nutze ‚einfache‘), keine Zeilenumbrüche.")


def build_report(result: dict) -> dict:
    payload = {"domain": result["facts"]["host"], "score": result["score"], "erkannt": result["facts"],
               "ergebnisse": [{k: f[k] for k in ("cat", "status", "title", "detail", "fix")} for f in result["findings"]
                              if f["status"] != "ok"]}
    rep = ai_report(_SYSTEM, payload, result)
    rep["keyword_hinweise"] = ""
    if not rep["ai"]:
        rep["naechste_schritte"] = ("Setze die Maßnahmen der Reihe nach um (vieles erledigt dein Hoster oder Mailanbieter in "
                                    "wenigen Minuten) und prüfe danach erneut. Ein Penetrationstest prüft deutlich tiefer.")
    return rep
