"""Двухфакторный вход: настройка, вход с кодом, резервные коды, защита от повтора, обязательность для админов, сброс."""

import time

import pyotp
import pytest

from app.extensions import db
from app.models import AuditLog, RecoveryCode, User
from app.services import twofactor
from tests.conftest import PASSWORD, login, make_user

EMAIL = "member@example.com"


def _code(secret, offset=0):
    """Код для шага времени со сдвигом: сервер принимает соседние шаги, но каждый шаг только один раз."""
    return pyotp.TOTP(secret).at(time.time() + 30 * offset)


def _enable(app, email=EMAIL, **kwargs):
    uid = make_user(app, email, verified=True, **kwargs)
    secret = pyotp.random_base32()
    with app.app_context():
        user = db.session.get(User, uid)
        codes = twofactor.enable(user, secret, _code(secret))
        db.session.commit()
    return uid, secret, codes


def _free_step(app, uid):
    """В жизни следующий код приходит через 30 секунд; в тесте просто забываем последний принятый шаг."""
    with app.app_context():
        db.session.get(User, uid).totp_last_step = None
        db.session.commit()


def _password_step(client, email=EMAIL):
    return client.post("/login", data={"email": email, "password": PASSWORD})


def _logged_in(client):
    return client.get("/app/").status_code == 200


def test_setup_flow_shows_qr_stores_the_secret_encrypted_and_gives_recovery_codes(app, client):
    uid = make_user(app, EMAIL, verified=True)
    login(client)
    client.post("/app/security/setup")
    page = client.get("/app/security").get_data(as_text=True)
    assert "<svg" in page and "style=" not in page.split("<svg", 1)[1].split("</svg>", 1)[0]
    with client.session_transaction() as session:
        secret = session["totp_setup"]
    assert secret in page  # ключ для ручного ввода
    wrong = client.post("/app/security/confirm", data={"code": "000000"})
    assert wrong.status_code == 200 and b"recovery-codes" not in wrong.data
    done = client.post("/app/security/confirm", data={"code": _code(secret)}).get_data(as_text=True)
    assert done.count("<code>") == 8
    with app.app_context():
        user = db.session.get(User, uid)
        assert user.has_two_factor and secret not in user.totp_secret_enc
        assert RecoveryCode.query.filter_by(user_id=uid).count() == 8
        assert "2fa.enabled" in {row.action for row in AuditLog.query.all()}


def test_password_alone_is_not_enough_when_two_factor_is_on(app, client):
    _enable(app)
    response = _password_step(client)
    assert response.status_code == 302 and response.headers["Location"].endswith("/login/2fa")
    assert not _logged_in(client)


def test_correct_code_completes_the_login_and_keeps_the_next_page(app, client):
    _uid, secret, _codes = _enable(app)
    client.post("/login?next=/app/security", data={"email": EMAIL, "password": PASSWORD})
    response = client.post("/login/2fa", data={"code": _code(secret, 1)})
    assert response.status_code == 302 and response.headers["Location"].endswith("/app/security")
    assert _logged_in(client)


def test_wrong_code_is_refused_and_five_failures_end_the_attempt(app, client):
    _uid, secret, _codes = _enable(app)
    _password_step(client)
    for _ in range(4):
        assert client.post("/login/2fa", data={"code": "123456"}).status_code == 200
    last = client.post("/login/2fa", data={"code": "123456"})
    assert last.status_code == 302 and last.headers["Location"].endswith("/login")
    # даже верный код теперь не пускает без нового ввода пароля
    assert client.post("/login/2fa", data={"code": _code(secret, 1)}).status_code == 302
    assert not _logged_in(client)


def test_a_code_cannot_be_used_twice(app, client):
    _uid, secret, _codes = _enable(app)
    code = _code(secret, 1)
    _password_step(client)
    assert client.post("/login/2fa", data={"code": code}).status_code == 302
    other = app.test_client()
    _password_step(other)
    again = other.post("/login/2fa", data={"code": code})
    assert again.status_code == 200 and not _logged_in(other)


def test_the_step_page_needs_a_password_step_first_and_expires(app, client):
    _enable(app)
    assert client.get("/login/2fa").status_code == 302
    _password_step(client)
    with client.session_transaction() as session:
        pending = session["2fa_pending"]
        pending["at"] = time.time() - 10 * 60
        session["2fa_pending"] = pending
    assert client.get("/login/2fa").status_code == 302


def test_a_recovery_code_works_once(app, client):
    _uid, _secret, codes = _enable(app)
    _password_step(client)
    assert client.post("/login/2fa", data={"code": codes[0].upper()}).status_code == 302
    assert _logged_in(client)
    other = app.test_client()
    _password_step(other)
    assert other.post("/login/2fa", data={"code": codes[0]}).status_code == 200
    assert not _logged_in(other)
    with app.app_context():
        assert "2fa.recovery_code_used" in {row.action for row in AuditLog.query.all()}


def test_administrators_must_set_up_two_factor_before_the_admin_area(app, client):
    app.config["ADMIN_2FA_REQUIRED"] = True
    try:
        make_user(app, "admin@example.com", verified=True, admin=True)
        login(client, "admin@example.com")
        response = client.get("/admin/")
        assert response.status_code == 302 and response.headers["Location"].endswith("/app/security")
        assert client.get("/app/security").status_code == 200  # настроить можно
    finally:
        app.config["ADMIN_2FA_REQUIRED"] = False


def test_administrators_with_two_factor_enter_the_admin_area_and_cannot_switch_it_off(app, client):
    app.config["ADMIN_2FA_REQUIRED"] = True
    try:
        _uid, secret, _codes = _enable(app, "admin@example.com", admin=True)
        client.post("/login", data={"email": "admin@example.com", "password": PASSWORD})
        client.post("/login/2fa", data={"code": _code(secret, 1)})
        assert client.get("/admin/").status_code == 200
        client.post("/app/security/disable", data={"password": PASSWORD, "code": _code(secret, -1)})
        with app.app_context():
            assert User.query.filter_by(email="admin@example.com").one().has_two_factor
    finally:
        app.config["ADMIN_2FA_REQUIRED"] = False


def test_a_member_can_switch_it_off_with_password_and_code_only(app, client):
    uid, secret, _codes = _enable(app)
    _password_step(client)
    client.post("/login/2fa", data={"code": _code(secret, 1)})
    _free_step(app, uid)
    client.post("/app/security/disable", data={"password": "wrong-password", "code": _code(secret, -1)})
    with app.app_context():
        assert db.session.get(User, uid).has_two_factor
    _free_step(app, uid)
    client.post("/app/security/disable", data={"password": PASSWORD, "code": _code(secret, -1)})
    with app.app_context():
        user = db.session.get(User, uid)
        assert not user.has_two_factor and user.totp_secret_enc is None
        assert RecoveryCode.query.filter_by(user_id=uid).count() == 0
    assert _password_step(app.test_client()).headers["Location"].endswith("/app/")


def test_new_recovery_codes_replace_the_old_ones(app, client):
    uid, secret, old = _enable(app)
    _password_step(client)
    client.post("/login/2fa", data={"code": _code(secret, 1)})
    _free_step(app, uid)
    page = client.post("/app/security/codes", data={"password": PASSWORD, "code": _code(secret, -1)}).get_data(as_text=True)
    assert page.count("<code>") == 8
    with app.app_context():
        user = db.session.get(User, uid)
        assert twofactor.use_recovery_code(user, old[0]) is False


def test_admin_resets_another_members_two_factor_with_a_note_but_not_their_own(app, client):
    admin_id = make_user(app, "admin@example.com", verified=True, admin=True)
    member_id, _secret, _codes = _enable(app)
    login(client, "admin@example.com")
    client.post(f"/admin/users/{member_id}/reset-2fa", data={"note": "x"})
    with app.app_context():
        assert db.session.get(User, member_id).has_two_factor  # без пометки не сбрасывается
    client.post(f"/admin/users/{member_id}/reset-2fa", data={"note": "Per Video-Anruf erkannt"})
    with app.app_context():
        assert not db.session.get(User, member_id).has_two_factor
        assert "admin.2fa_reset" in {row.action for row in AuditLog.query.all()}
        admin = db.session.get(User, admin_id)
        with pytest.raises(twofactor.TwoFactorError):
            twofactor.admin_reset(admin, admin, "Selbst zurücksetzen")


def test_export_tells_whether_two_factor_is_on_and_never_the_secret(app, client):
    _uid, secret, _codes = _enable(app)
    _password_step(client)
    client.post("/login/2fa", data={"code": _code(secret, 1)})
    data = client.get("/app/privacy/export").get_data(as_text=True)
    assert "zwei_faktor_aktiv" in data and secret not in data
