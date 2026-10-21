from datetime import datetime, timedelta, timezone

import pytest

from app.services.tools import security, security_extra as x
from app.services.tools.common import Headers

PAGE = """<!doctype html><html><head><meta name="generator" content="WordPress 5.2.1">
<script src="https://code.jquery.com/jquery-1.12.4.min.js"></script>
<script src="https://cdn.example.net/lib.js" integrity="sha384-abc"></script>
<script src="/static/app.js"></script>
<link rel="stylesheet" href="https://fonts.example.org/css">
<link rel="stylesheet" href="http://insecure.example.com/old.css">
</head><body><img src="http://img.example.com/a.png">
<form action="http://shop.example.com/login"><input type="password" name="p"></form>
<iframe src="https://video.example.net/embed"></iframe></body></html>"""


def keys(findings):
    return {f["key"]: f for f in findings}


def test_csp_weaknesses_are_found():
    assert x.csp_issues("") == []
    weak = x.csp_issues("default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' *")
    assert any("unsafe-inline" in i for i in weak) and any("unsafe-eval" in i for i in weak)
    assert any("beliebigen Quellen" in i for i in weak)
    assert x.csp_issues("default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'") == []


def test_header_findings_flag_dangerous_cors_and_preload_state():
    hdr = Headers({"access-control-allow-origin": "*", "access-control-allow-credentials": "true",
                   "strict-transport-security": "max-age=63072000; includeSubDomains; preload",
                   "content-security-policy": "script-src 'unsafe-inline'"})

    found = keys(x.header_findings(hdr, has_cookies=True))

    assert found["cors"]["status"] == "fail"
    assert found["hsts_preload"]["status"] == "ok"
    assert found["csp_quality"]["status"] == "warn"
    assert found["cache_cookie"]["status"] == "info"


def test_page_content_checks_find_mixed_content_third_parties_and_old_versions():
    found = keys(x.content_findings(PAGE, "example.com", Headers({"x-powered-by": "PHP/7.4.3"}), https_ok=True))

    assert found["mixed"]["status"] == "fail" and "http://" in found["mixed"]["detail"]
    assert "code.jquery.com" in found["third_party"]["detail"]
    assert found["sri"]["status"] == "warn"  # jQuery von fremdem Host ohne integrity
    assert found["pw_form"]["status"] == "fail"
    versions = found["versions"]
    assert versions["status"] == "warn"
    assert "jQuery 1.12.4" in versions["detail"] and "PHP 7.4" in versions["detail"] and "WordPress 5.2" in versions["detail"]


def test_clean_page_has_no_findings_to_worry_about():
    html = '<html><head><script src="/a.js"></script></head><body><form action="/login"><input type="password"></form></body></html>'

    found = keys(x.content_findings(html, "example.com", Headers({}), https_ok=True))

    assert found["mixed"]["status"] == "ok" and found["pw_form"]["status"] == "ok" and found["versions"]["status"] == "ok"
    assert "sri" not in found


def test_spf_lookup_count_follows_includes():
    records = {"a.example": ["v=spf1 include:b.example mx -all"], "b.example": ["v=spf1 a include:c.example ~all"],
               "c.example": ["v=spf1 ip4:1.2.3.4 -all"]}
    top = "v=spf1 include:a.example a mx ptr -all"

    count = x.spf_lookup_count(top, lambda domain: records.get(domain, []))

    # include a (1) + b (1) + mx (1) + include b... a (1) + c (1) + top a, mx, ptr (3) = 8
    assert count == 8


def test_spf_with_too_many_lookups_is_detected():
    top = "v=spf1 " + " ".join(f"include:s{i}.example" for i in range(12)) + " -all"

    assert x.spf_lookup_count(top, lambda domain: []) > 10


def test_dmarc_details():
    found = keys(x.dmarc_findings("v=DMARC1; p=quarantine; sp=none; pct=50"))

    assert found["dmarc_sp"]["status"] == "warn" and found["dmarc_pct"]["status"] == "warn" and "dmarc_rua" in found
    assert x.dmarc_findings("v=DMARC1; p=reject; rua=mailto:r@example.com") == []


def test_mta_sts_mode_parser():
    assert x.mta_sts_mode("version: STSv1\nmode: enforce\nmax_age: 86400") == "enforce"
    assert x.mta_sts_mode("") == ""


def test_rdap_expiry_and_registrar():
    soon = (datetime.now(timezone.utc) + timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {"events": [{"eventAction": "registration", "eventDate": "2010-01-01T00:00:00Z"},
                       {"eventAction": "expiration", "eventDate": soon}],
            "entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "Example Registrar GmbH"]]]}]}

    expires, registrar = x.rdap_expiry(data)

    assert registrar == "Example Registrar GmbH"
    assert 18 <= (expires - datetime.now(timezone.utc)).days <= 20


def test_subdomains_from_certificate_logs():
    entries = [{"name_value": "example.com\nwww.example.com"}, {"name_value": "*.example.com"},
               {"name_value": "staging.example.com"}, {"name_value": "vpn.example.com\nshop.other.com"},
               {"name_value": "dev2.example.com"}]

    names = x.subdomain_names(entries, "example.com")

    assert names == ["dev2.example.com", "staging.example.com", "vpn.example.com", "www.example.com"]
    assert x.sensitive_subdomains(names, "example.com") == ["dev2.example.com", "staging.example.com", "vpn.example.com"]


def test_robots_hints_and_security_txt_expiry():
    assert x.robots_hints("User-agent: *\nDisallow: /admin/\nDisallow: /blog\nDisallow: /backup-2020") == ["/admin/", "/backup-2020"]
    expired = keys(x.security_txt_findings("Contact: mailto:a@b.de\nExpires: 2020-01-01T00:00:00Z"))
    fresh = keys(x.security_txt_findings(f"Contact: x\nExpires: {(datetime.now(timezone.utc) + timedelta(days=200)).isoformat()}"))
    assert expired["sectxt_exp"]["status"] == "warn" and fresh["sectxt_exp"]["status"] == "ok"


def test_extra_jobs_never_break_the_main_result(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    for name in ("tls_findings", "mail_findings", "domain_findings", "subdomain_findings"):
        monkeypatch.setattr(x, name, boom)

    result = x.run_extras("example.com", "example.com", "<html></html>", Headers({}), True, False, "", None)

    assert any(f["key"] == "modern_hdr" for f in result)  # Header-Prüfung lief trotzdem


def test_analyze_merges_extras_without_duplicate_keys(monkeypatch):
    monkeypatch.setattr(security, "_assert_public", lambda host: None)
    monkeypatch.setattr(security, "_tls", lambda host: {"reachable": True, "valid": True, "version": "TLSv1.3",
                                                        "not_after": 4102444800, "issuer": "Test CA"})

    class Resp:
        status_code = 200
        encoding = "utf-8"
        headers = Headers({})
        cookies = []

    monkeypatch.setattr(security, "_fetch", lambda url, max_bytes=200_000: (Resp(), PAGE.encode(), 0.1, url))
    monkeypatch.setattr(security, "_txt", lambda name: [])
    monkeypatch.setattr(security, "_has", lambda name, rtype: False)
    monkeypatch.setattr(x, "run_extras", lambda *a, **k: [
        {"key": "https", "cat": "x", "status": "fail", "title": "dup", "detail": "", "weight": 1, "fix": ""},
        {"key": "extra1", "cat": "x", "status": "ok", "title": "new", "detail": "", "weight": 1, "fix": ""}])
    monkeypatch.setattr(x, "records", lambda name, rtype: [])

    result = security.analyze("example.com")

    all_keys = [f["key"] for f in result["findings"]]
    assert all_keys.count("https") == 1 and "extra1" in all_keys
    assert result["facts"]["pruefungen"] == len(all_keys)


@pytest.mark.parametrize("raw", ["", "127.0.0.1", "localhost", "https://example.com:8443"])
def test_bad_domains_are_still_refused(raw):
    from app.services.tools.common import ToolError

    with pytest.raises(ToolError):
        security.hostname_from(raw)


def test_rdap_bootstrap_lookup_and_certspotter_mapping():
    bootstrap = {"services": [[["de", "at"], ["https://rdap.denic.de/"]], [["com"], ["https://rdap.verisign.com/com/v1/"]]]}

    assert x.rdap_base_for(bootstrap, "DE") == "https://rdap.denic.de"
    assert x.rdap_base_for(bootstrap, "xyz") == ""
    entries = x.certspotter_entries([{"dns_names": ["example.com", "dev.example.com"]}, "junk", {"dns_names": []}])
    assert x.subdomain_names(entries, "example.com") == ["dev.example.com"]
