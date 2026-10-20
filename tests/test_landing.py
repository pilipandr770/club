def test_landing_explains_the_concept(client):
    body = client.get("/").get_data(as_text=True)

    assert "Finde die Menschen, die dein Projekt weiterbringen" in body
    assert "So funktioniert der Club" in body
    assert "Was wir versprechen" in body
    assert "Tarife" in body or "Plans" in body or "Pläne" in body
    assert "Häufige Fragen" in body or "Questions" in body or "Fragen" in body


def test_landing_uses_only_self_hosted_scripts(client):
    body = client.get("/").get_data(as_text=True)

    assert "<script>" not in body
    assert 'src="/static/js/landing.js"' in body
    assert "cdn." not in body
    assert "unpkg" not in body


def test_landing_script_and_styles_are_served(client):
    script = client.get("/static/js/landing.js")
    styles = client.get("/static/css/app.css")

    assert script.status_code == 200
    assert b"data-sphere" in script.data
    assert styles.status_code == 200
    assert b".tilt-card" in styles.data


def test_landing_keeps_the_strict_content_security_policy(client):
    response = client.get("/")

    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
