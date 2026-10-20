import io
import os

from PIL import Image

from app.extensions import db
from app.models import Consent, Profile, User
from app.profile_guide import completeness, matchable
from tests.conftest import login, make_user


def _png_bytes(size=(64, 64), exif=False):
    image = Image.new("RGB", size, (200, 60, 30))
    out = io.BytesIO()
    kwargs = {}
    if exif:
        exif_data = Image.Exif()
        exif_data[0x010F] = "SecretCamera"  # Make
        kwargs["exif"] = exif_data.tobytes()
    image.save(out, format="PNG", **kwargs)
    return out.getvalue()


def _profile_with(app, email, **fields):
    user_id = make_user(app, email=email, verified=True)
    with app.app_context():
        user = db.session.get(User, user_id)
        profile = user.profile
        for key, value in fields.items():
            setattr(profile, key, value)
        db.session.commit()
    return user_id


# --- заполненность и матчинг -------------------------------------------------

def test_empty_profile_is_not_complete_and_not_matchable(app):
    user_id = make_user(app, verified=True)
    with app.app_context():
        profile = db.session.get(User, user_id).profile
        assert completeness(profile) == 0
        assert matchable(profile) is False


def test_completeness_grows_with_fields_and_photo(app):
    user_id = _profile_with(app, "a@example.com", headline="Tax adviser", q_focus="I advise founders.")
    with app.app_context():
        before = completeness(db.session.get(User, user_id).profile)
    with app.app_context():
        profile = db.session.get(User, user_id).profile
        profile.photo_key = "avatars/x.png"
        db.session.commit()
        after_photo = completeness(profile)

    assert 0 < before < after_photo <= 100


def test_matching_needs_consent_and_the_matching_fields(app):
    user_id = _profile_with(app, "m@example.com", q_can_help="I review pitch decks.",
                            q_looking_for="A designer.")
    with app.app_context():
        profile = db.session.get(User, user_id).profile
        assert matchable(profile) is False  # нет согласия
        profile.allow_matching = True
        assert matchable(profile) is True


# --- редактирование и согласия ------------------------------------------------

def test_member_saves_profile_text(app, client):
    make_user(app, email="e@example.com", verified=True)
    login(client, "e@example.com")

    response = client.post("/profile/", data={"headline": "Designer", "q_focus": "Brand work",
                                              "expertise": "Figma, typography"})

    assert response.status_code == 302
    with app.app_context():
        profile = Profile.query.one()
        assert profile.headline == "Designer"
        assert profile.expertise == "Figma, typography"
        assert profile.updated_at is not None


def test_too_long_field_is_refused(app, client):
    make_user(app, email="l@example.com", verified=True)
    login(client, "l@example.com")

    response = client.post("/profile/", data={"q_focus": "x" * 801}, follow_redirects=True)

    assert "Bitte prüfe diese Felder: q_focus" in response.get_data(as_text=True)


def test_directory_and_matching_are_off_by_default_and_recorded_on_change(app, client):
    user_id = make_user(app, email="c@example.com", verified=True)
    login(client, "c@example.com")
    with app.app_context():
        assert Profile.query.one().visible_in_directory is False
        assert Profile.query.one().allow_matching is False

    client.post("/profile/", data={"visible_in_directory": "1"})
    client.post("/profile/", data={"visible_in_directory": "1"})  # без изменения — новой записи нет
    client.post("/profile/", data={})  # снял галочку

    with app.app_context():
        rows = [(c.kind, c.granted) for c in Consent.query.filter_by(user_id=user_id, kind="directory")
                .order_by(Consent.id)]
        assert rows == [("directory", True), ("directory", False)]


# --- каталог и видимость -----------------------------------------------------

def test_directory_shows_only_members_who_opted_in(app, client):
    _profile_with(app, "shown@example.com", headline="Shown person", visible_in_directory=True)
    _profile_with(app, "hidden@example.com", headline="Hidden person", visible_in_directory=False)
    make_user(app, email="viewer@example.com", verified=True)
    login(client, "viewer@example.com")

    body = client.get("/profile/directory").get_data(as_text=True)

    assert "Shown person" in body
    assert "Hidden person" not in body


def test_guest_without_verification_cannot_see_directory(app, client):
    make_user(app, email="new@example.com", verified=False)
    login(client, "new@example.com")

    assert client.get("/profile/directory").status_code == 302


def test_hidden_profile_is_404_for_others_but_visible_to_owner(app, client):
    hidden_id = _profile_with(app, "private@example.com", headline="Secret", visible_in_directory=False)
    make_user(app, email="viewer@example.com", verified=True)
    login(client, "viewer@example.com")
    assert client.get(f"/profile/{hidden_id}").status_code == 404

    client.post("/logout")
    login(client, "private@example.com")
    assert client.get(f"/profile/{hidden_id}").status_code == 200


# --- фото --------------------------------------------------------------------

def test_photo_is_saved_stripped_of_metadata_and_served_to_verified_members(app, client):
    user_id = make_user(app, email="p@example.com", verified=True)
    login(client, "p@example.com")

    response = client.post("/profile/photo", data={
        "photo": (io.BytesIO(_png_bytes(exif=True)), "me.png"),
    }, content_type="multipart/form-data")

    assert response.status_code == 302
    with app.app_context():
        profile = db.session.get(User, user_id).profile
        assert profile.photo_key.endswith(".png")
        stored = app.extensions["media"].read(profile.photo_key)
        assert stored is not None
        assert b"SecretCamera" not in stored
        assert "exif" not in Image.open(io.BytesIO(stored)).info

    served = client.get(f"/profile/photo/{user_id}")
    assert served.status_code == 200
    assert served.mimetype == "image/png"


def test_non_image_upload_is_refused(app, client):
    user_id = make_user(app, email="x@example.com", verified=True)
    login(client, "x@example.com")

    client.post("/profile/photo", data={"photo": (io.BytesIO(b"<script>alert(1)</script>"), "a.png")},
                content_type="multipart/form-data", follow_redirects=True)

    with app.app_context():
        assert db.session.get(User, user_id).profile.photo_key is None


def test_gif_and_oversized_uploads_are_refused(app, client):
    user_id = make_user(app, email="g@example.com", verified=True)
    login(client, "g@example.com")
    gif = io.BytesIO()
    Image.new("RGB", (8, 8)).save(gif, format="GIF")

    client.post("/profile/photo", data={"photo": (io.BytesIO(gif.getvalue()), "a.gif")},
                content_type="multipart/form-data")
    with app.app_context():
        assert db.session.get(User, user_id).profile.photo_key is None

    limit = app.config["MEDIA_MAX_BYTES"]
    big = b"\x89PNG" + b"0" * (limit + 10)
    client.post("/profile/photo", data={"photo": (io.BytesIO(big), "big.png")},
                content_type="multipart/form-data")
    with app.app_context():
        assert db.session.get(User, user_id).profile.photo_key is None


def test_photo_is_not_served_to_unverified_visitors(app, client):
    user_id = make_user(app, email="v@example.com", verified=True)
    login(client, "v@example.com")
    client.post("/profile/photo", data={"photo": (io.BytesIO(_png_bytes()), "a.png")},
                content_type="multipart/form-data")
    client.post("/logout")

    make_user(app, email="stranger@example.com", verified=False)
    login(client, "stranger@example.com")

    assert client.get(f"/profile/photo/{user_id}").status_code == 302


def test_remove_photo_deletes_file(app, client):
    user_id = make_user(app, email="r@example.com", verified=True)
    login(client, "r@example.com")
    client.post("/profile/photo", data={"photo": (io.BytesIO(_png_bytes()), "a.png")},
                content_type="multipart/form-data")
    with app.app_context():
        key = db.session.get(User, user_id).profile.photo_key
        path = app.extensions["media"]._path(key)
    assert os.path.exists(path)

    client.post("/profile/photo/remove")

    assert not os.path.exists(path)
    with app.app_context():
        assert db.session.get(User, user_id).profile.photo_key is None
