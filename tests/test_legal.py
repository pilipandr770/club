"""Правовые страницы: Impressum, Datenschutz, Nutzungsbedingungen. Доступны всем, на двух языках, из подвала и формы регистрации."""

import pytest

PAGES = ("/impressum", "/datenschutz", "/nutzungsbedingungen")


@pytest.mark.parametrize("path", PAGES)
def test_pages_are_public_in_german_by_default(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert 'lang="de"' in response.get_data(as_text=True)


@pytest.mark.parametrize("path", PAGES)
def test_pages_have_a_russian_translation_that_points_to_the_german_original(client, path):
    page = client.get(f"{path}?lang=ru").get_data(as_text=True)
    assert 'lang="ru"' in page and "lang=de" in page


def test_unknown_language_parameter_falls_back_to_the_interface_language(client):
    assert 'lang="de"' in client.get("/impressum?lang=xx").get_data(as_text=True)


def test_impressum_shows_the_operator_data_from_the_configuration(app, client):
    app.config.update(LEGAL_NAME="Erika Muster", LEGAL_STREET="Musterweg 1", LEGAL_CITY="12345 Musterstadt",
                      LEGAL_EMAIL="kontakt@example.org", LEGAL_VAT_ID="DE123456789")
    page = client.get("/impressum").get_data(as_text=True)
    for expected in ("Erika Muster", "Musterweg 1", "12345 Musterstadt", "kontakt@example.org", "DE123456789", "§ 5 DDG"):
        assert expected in page


def test_company_details_appear_once_they_are_configured(app, client):
    assert "Registereintrag" not in client.get("/impressum").get_data(as_text=True)
    app.config.update(LEGAL_FORM="UG (haftungsbeschränkt)", LEGAL_REGISTER="HRB 12345, Amtsgericht Frankfurt am Main",
                      LEGAL_REPRESENTATIVE="Erika Muster")
    page = client.get("/impressum").get_data(as_text=True)
    assert "UG (haftungsbeschränkt)" in page and "HRB 12345" in page and "Vertretungsberechtigt" in page


def test_footer_links_to_all_documents_on_every_page(client):
    for path in ("/", "/login", "/register"):
        page = client.get(path).get_data(as_text=True)
        assert 'href="/impressum"' in page and 'href="/datenschutz"' in page and 'href="/nutzungsbedingungen"' in page


def test_old_agb_address_redirects_permanently(client):
    response = client.get("/agb")
    assert response.status_code == 301 and response.headers["Location"].endswith("/nutzungsbedingungen")


def test_registration_form_links_the_documents_and_asks_for_biometric_consent(client):
    page = client.get("/register").get_data(as_text=True)
    assert 'href="/datenschutz"' in page and 'href="/nutzungsbedingungen"' in page
    assert "biometr" in page.lower()


def test_privacy_notice_names_every_provider_that_receives_data(client):
    page = client.get("/datenschutz").get_data(as_text=True)
    for provider in ("Stripe", "Anthropic", "Mistral", "Cloudflare", "Hostinger", "Google", "OpenAI"):
        assert provider in page
    assert "Art. 9" in page  # biometrische Daten bei der Identitätsprüfung


def test_documents_are_not_counted_by_the_visitor_statistics(app, client):
    from app.extensions import db
    from app.models.analytics import VisitStat

    client.get("/impressum", headers={"User-Agent": "Mozilla/5.0 Chrome/120", "CF-Connecting-IP": "203.0.113.9"})
    with app.app_context():
        assert db.session.query(VisitStat).count() == 0
