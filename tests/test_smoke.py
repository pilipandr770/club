def test_index_renders_german_by_default(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'lang="de"' in body
    assert "Willkommen" in body


def test_healthz_checks_database(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_security_headers_present(client):
    response = client.get("/")

    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_page_has_no_inline_scripts_or_styles(client):
    body = client.get("/").get_data(as_text=True)

    assert "<style" not in body
    assert "style=" not in body
    assert "<script>" not in body
    assert "translate=\"no\"" not in body
