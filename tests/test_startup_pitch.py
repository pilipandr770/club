import io

import pytest
from PIL import Image

from app.extensions import db
from app.models import Startup, User
from app.services import assistant
from app.services.ai import llm
from tests.conftest import PASSWORD, login, make_user


def _png():
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (20, 90, 200)).save(out, format="PNG")
    return out.getvalue()


def _pdf(text="Seed round presentation with ARR 120k"):
    """Минимальный валидный PDF с одной страницей и текстом."""
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


FORM = {"name": "Solar Shelf", "tagline": "Energy for shops", "description": "We store solar power for shops.",
        "problem": "Peak prices hurt small shops.", "solution": "A shared battery pool.", "category": "climate",
        "stage": "prototype", "team": "Two engineers"}


def _post(client, url="/startups/new", files=None, **extra):
    data = {**FORM, **extra}
    for key, (name, content, mime) in (files or {}).items():
        data[key] = (io.BytesIO(content), name, mime)
    return client.post(url, data=data, content_type="multipart/form-data")


def _approve(app, startup_id):
    with app.app_context():
        startup = db.session.get(Startup, startup_id)
        startup.status = "approved"
        db.session.commit()


def _startup_id(app):
    with app.app_context():
        return Startup.query.one().id


def test_founder_creates_card_with_picture_and_deck(app, client):
    make_user(app, email="founder@example.com", verified=True, plan="growth")
    login(client, "founder@example.com")

    response = _post(client, files={"logo": ("logo.png", _png(), "image/png"),
                                    "deck": ("deck.pdf", _pdf(), "application/pdf")})

    assert response.status_code == 302
    with app.app_context():
        startup = Startup.query.one()
        assert startup.status == "pending" and startup.logo_key and startup.deck_key
        assert "ARR 120k" in startup.deck_text
        assert startup.problem == "Peak prices hurt small shops."


def test_fake_pdf_is_refused_and_nothing_is_created(app, client):
    make_user(app, email="founder@example.com", verified=True, plan="growth")
    login(client, "founder@example.com")

    _post(client, files={"deck": ("deck.pdf", b"not a pdf at all", "application/pdf")})

    with app.app_context():
        assert Startup.query.count() == 0


def test_picture_that_is_not_an_image_is_refused(app, client):
    make_user(app, email="founder@example.com", verified=True, plan="growth")
    login(client, "founder@example.com")

    _post(client, files={"logo": ("logo.png", b"<script>alert(1)</script>", "image/png")})

    with app.app_context():
        assert Startup.query.count() == 0


def test_unpublished_files_are_private_to_owner_and_admin(app, client):
    make_user(app, email="founder@example.com", verified=True, plan="growth")
    login(client, "founder@example.com")
    _post(client, files={"logo": ("logo.png", _png(), "image/png"), "deck": ("deck.pdf", _pdf(), "application/pdf")})
    sid = _startup_id(app)
    assert client.get(f"/startups/{sid}/deck").status_code == 200
    client.post("/logout")

    make_user(app, email="other@example.com", verified=True, plan="starter")
    login(client, "other@example.com")
    assert client.get(f"/startups/{sid}").status_code == 404
    assert client.get(f"/startups/{sid}/deck").status_code == 404
    assert client.get(f"/startups/{sid}/logo").status_code == 404
    client.post("/logout")

    make_user(app, email="boss@example.com", verified=True, admin=True, plan="premium")
    login(client, "boss@example.com")
    assert client.get(f"/startups/{sid}").status_code == 200
    assert client.get(f"/startups/{sid}/deck").status_code == 200


def test_approved_card_is_visible_to_every_verified_member_and_pdf_is_a_download(app, client):
    make_user(app, email="founder@example.com", verified=True, plan="growth")
    login(client, "founder@example.com")
    _post(client, files={"deck": ("deck.pdf", _pdf(), "application/pdf")})
    sid = _startup_id(app)
    _approve(app, sid)
    client.post("/logout")
    make_user(app, email="guest@example.com", verified=True, plan="guest")
    login(client, "guest@example.com")

    page = client.get(f"/startups/{sid}").get_data(as_text=True)
    deck = client.get(f"/startups/{sid}/deck")

    assert "A shared battery pool." in page or "Eine gemeinsame" in page or "battery pool" in page
    assert deck.mimetype == "application/pdf"
    assert deck.headers["Content-Disposition"].startswith("attachment")


def test_unverified_user_cannot_get_files(app, client):
    make_user(app, email="founder@example.com", verified=True, plan="growth")
    login(client, "founder@example.com")
    _post(client, files={"deck": ("deck.pdf", _pdf(), "application/pdf")})
    sid = _startup_id(app)
    _approve(app, sid)
    client.post("/logout")
    make_user(app, email="new@example.com", verified=False)
    login(client, "new@example.com")

    assert client.get(f"/startups/{sid}/deck").status_code == 302


def test_removing_the_deck_deletes_it(app, client):
    make_user(app, email="founder@example.com", verified=True, plan="growth")
    login(client, "founder@example.com")
    _post(client, files={"deck": ("deck.pdf", _pdf(), "application/pdf")})
    sid = _startup_id(app)
    with app.app_context():
        old_key = db.session.get(Startup, sid).deck_key
    assert app.extensions["media"].read(old_key)

    _post(client, url=f"/startups/{sid}/edit", remove_deck="1")

    with app.app_context():
        startup = db.session.get(Startup, sid)
        assert startup.deck_key is None and startup.deck_text == ""
    assert app.extensions["media"].read(old_key) is None


def test_clubi_sees_full_card_and_pdf_text(app, client, monkeypatch):
    seen = []

    def fake_complete(system, user, max_tokens=900, timeout=30):
        seen.append(user)
        return "Antwort"

    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(assistant, "llm_enabled", lambda: True)
    make_user(app, email="founder@example.com", verified=True, plan="growth")
    login(client, "founder@example.com")
    _post(client, files={"deck": ("deck.pdf", _pdf(), "application/pdf")}, traction="Beta with 320 users")
    sid = _startup_id(app)
    _approve(app, sid)
    client.post("/logout")
    make_user(app, email="investor@example.com", verified=True, plan="growth")
    login(client, "investor@example.com")

    client.post("/assistant/ask", json={"message": "Wie ist der Traction?", "path": f"/startups/{sid}"})

    prompt = seen[0]
    assert "Beta with 320 users" in prompt and "ARR 120k" in prompt and "Peak prices" in prompt
    assert "founder@example.com" not in prompt


def test_starter_sees_locked_call_to_action_and_growth_sees_button(app, client):
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")
    assert b"/startups/new" not in client.get("/startups/").data
    client.post("/logout")
    make_user(app, email="growth@example.com", verified=True, plan="growth")
    login(client, "growth@example.com")

    assert b"/startups/new" in client.get("/startups/").data


def test_deleting_the_account_removes_startup_files(app, client, provider):
    user_id = make_user(app, email="founder@example.com", verified=True, plan="growth")
    login(client, "founder@example.com")
    _post(client, files={"logo": ("logo.png", _png(), "image/png"), "deck": ("deck.pdf", _pdf(), "application/pdf")})
    with app.app_context():
        startup = Startup.query.one()
        keys = [startup.logo_key, startup.deck_key]

    client.post("/app/privacy/delete", data={"password": PASSWORD})

    for key in keys:
        assert app.extensions["media"].read(key) is None
    with app.app_context():
        assert db.session.get(User, user_id) is None
