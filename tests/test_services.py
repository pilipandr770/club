from app.extensions import db
from app.models import Service, ServiceInquiry
from app.services import catalog
from tests.conftest import login, make_user


def _service(app, active=True, provider_email="partner@example.com", title="Tax check"):
    with app.app_context():
        service = Service(slug=catalog.unique_slug(title), title=title, summary="Short", description="Long text",
                          benefits="Fast\nClear", member_benefit="10 % for members", provider_name="Partner GmbH",
                          provider_email=provider_email, active=active)
        db.session.add(service)
        db.session.commit()
        return service.slug


def test_guest_cannot_open_services(app, client):
    _service(app)
    make_user(app, email="guest@example.com", verified=True, plan="guest")
    login(client, "guest@example.com")

    assert client.get("/services/").status_code == 302


def test_starter_sees_only_active_services(app, client):
    _service(app, title="Visible one")
    _service(app, title="Hidden one", active=False)
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    body = client.get("/services/").get_data(as_text=True)

    assert "Visible one" in body
    assert "Hidden one" not in body


def test_provider_email_is_never_shown_to_members(app, client):
    slug = _service(app, provider_email="secret-partner@example.com")
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    body = client.get(f"/services/{slug}").get_data(as_text=True)

    assert "secret-partner@example.com" not in body
    assert "Partner GmbH" in body


def test_request_without_contact_sharing_does_not_send_member_email(app, client, monkeypatch):
    slug = _service(app)
    sent = []
    monkeypatch.setattr(catalog, "send_mail", lambda to, subject, body: sent.append((to, body)) or True)
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    client.post(f"/services/{slug}", data={"message": "Please call me"})

    assert len(sent) == 1
    assert sent[0][0] == "partner@example.com"
    assert "starter@example.com" not in sent[0][1]
    with app.app_context():
        assert ServiceInquiry.query.one().share_contact is False


def test_request_with_contact_sharing_includes_member_email(app, client, monkeypatch):
    slug = _service(app)
    sent = []
    monkeypatch.setattr(catalog, "send_mail", lambda to, subject, body: sent.append((to, body)) or True)
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    client.post(f"/services/{slug}", data={"message": "Call me", "share_contact": "1"})

    assert "starter@example.com" in sent[0][1]


def test_empty_request_is_refused(app, client, monkeypatch):
    slug = _service(app)
    monkeypatch.setattr(catalog, "send_mail", lambda *a, **k: True)
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    client.post(f"/services/{slug}", data={"message": "   "})

    with app.app_context():
        assert ServiceInquiry.query.count() == 0


def test_admin_creates_service_and_changes_request_status(app, client):
    make_user(app, email="boss@example.com", verified=True, admin=True)
    login(client, "boss@example.com")

    client.post("/admin/services/new", data={"title": "Legal review", "summary": "Contracts",
                                            "provider_email": "legal@example.com", "active": "1", "sort": "1"})
    with app.app_context():
        service = Service.query.filter_by(title="Legal review").one()
        assert service.slug == "legal-review"
        service_id = service.id

    client.post(f"/services/{service.slug}", data={"message": "Hi"})  # 302: не участник — ответ не важен
    with app.app_context():
        assert Service.query.get(service_id).active is True


def test_inquiry_status_is_changed_by_admin(app, client, monkeypatch):
    slug = _service(app)
    monkeypatch.setattr(catalog, "send_mail", lambda *a, **k: True)
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")
    client.post(f"/services/{slug}", data={"message": "Hi"})
    client.post("/logout")

    make_user(app, email="boss@example.com", verified=True, admin=True)
    login(client, "boss@example.com")
    with app.app_context():
        inquiry_id = ServiceInquiry.query.one().id
    client.post(f"/admin/services/inquiries/{inquiry_id}/status", data={"status": "won"})

    with app.app_context():
        assert db.session.get(ServiceInquiry, inquiry_id).status == "won"


def test_non_admin_cannot_manage_services(app, client):
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    assert client.get("/admin/services").status_code == 404
    assert client.get("/admin/services/new").status_code == 404


def test_default_services_seed_is_idempotent_and_keeps_edits(app):
    from app.services.catalog_defaults import DEFAULT_SERVICES, seed_default_services

    with app.app_context():
        assert seed_default_services() == len(DEFAULT_SERVICES)
        service = Service.query.filter_by(slug="ki-chatbots").one()
        service.title = "Edited by admin"
        db.session.commit()

        assert seed_default_services() == 0
        assert Service.query.filter_by(slug="ki-chatbots").one().title == "Edited by admin"
