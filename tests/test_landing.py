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
    assert 'src="/static/js/landing.js?v=' in body
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


def test_landing_shows_benefits_law_search_and_plan_comparison_from_the_config(app, client):
    from config.plans import ASSISTANT_DAILY, TOOL_LIMITS

    page = client.get("/").get_data(as_text=True)

    assert 'id="benefits"' in page and 'id="audience"' in page and 'id="law-search"' in page
    assert 'class="compare"' in page and "style=" not in page
    for plan, limit in ASSISTANT_DAILY.items():
        assert f"<td>{limit}</td>" in page
    assert f"<td>{TOOL_LIMITS['premium']['laws']}</td>" in page


def test_landing_counts_laws_only_when_the_corpus_is_loaded(app, client):
    from app.extensions import db
    from app.models import Law

    assert 'class="stat-number">6,000+' not in client.get("/").get_data(as_text=True)
    with app.app_context():
        db.session.add_all([Law(slug=f"de-{i}", jurisdiction="DE") for i in range(1000)] + [Law(slug="il-privacy", jurisdiction="IL")])
        db.session.commit()

    page = client.get("/").get_data(as_text=True)

    assert 'class="stat-number">1,000+' in page and 'class="stat-number">1</span>wichtige israelische Gesetze' in page
