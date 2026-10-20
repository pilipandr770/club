from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import db
from ...models import Profile, User
from ...profile_guide import FIELDS, PHOTO_EXAMPLES, PHOTO_HINT, completeness, matchable, missing_for_matching
from ...services.audit import audit
from ...services.consents import record_consent
from ...services.entitlements import requires_feature
from ...services.matching import get_matches, refresh_embeddings
from ...services.media import MediaError, process_image, profile_photo_key
from ...utils import utcnow

bp = Blueprint("profile", __name__, url_prefix="/profile")

PHOTO_MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def _profile_for(user):
    if user.profile is None:
        user.profile = Profile()
        db.session.add(user.profile)
    return user.profile


def _photo_error(code):
    messages = {
        "too_large": _("The photo is too large. Please use a file up to 5 MB."),
        "too_large_pixels": _("The photo resolution is too high. Please use a smaller photo."),
        "bad_format": _("Please upload a JPEG, PNG or WebP photo."),
        "not_an_image": _("This file is not a valid photo."),
        "empty": _("The file is empty."),
    }
    return messages.get(code, _("The photo could not be saved."))


def _read_text_fields(form):
    """Текстовые поля из формы. Возвращает (значения, ошибки)."""
    values, errors = {}, []
    for field in FIELDS:
        raw = form.get(field.key, "").strip()
        if len(raw) > field.max_length:
            errors.append(field.key)
        values[field.key] = raw
    return values, errors


def _apply_consent(user, kind, flag_name, new_value):
    profile = user.profile
    old_value = getattr(profile, flag_name)
    if old_value == new_value:
        return None
    setattr(profile, flag_name, new_value)
    record_consent(user, kind, new_value)
    return f"{kind} {'on' if new_value else 'off'}"


@bp.route("/", methods=["GET", "POST"])
@requires_feature("profile.edit")
def edit():
    profile = _profile_for(current_user)
    errors = []
    if request.method == "POST":
        values, errors = _read_text_fields(request.form)
        if len(values["headline"]) > 160:
            errors.append("headline")
        if not errors:
            for key, value in values.items():
                setattr(profile, key, value)
            changes = [
                _apply_consent(current_user, "directory", "visible_in_directory",
                               bool(request.form.get("visible_in_directory"))),
                _apply_consent(current_user, "matching", "allow_matching",
                               bool(request.form.get("allow_matching"))),
            ]
            profile.updated_at = utcnow()
            refresh_embeddings(profile)
            audit("profile.updated", target=f"user:{current_user.id}", actor_id=current_user.id,
                  details="; ".join(c for c in changes if c))
            db.session.commit()
            flash(_("Your profile is saved."), "success")
            return redirect(url_for("profile.edit"))
        flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")

    return render_template(
        "modules/profile_edit.html",
        profile=profile,
        fields=FIELDS,
        errors=errors,
        form=request.form if request.method == "POST" else None,
        photo_hint=PHOTO_HINT,
        photo_examples=PHOTO_EXAMPLES,
        percent=completeness(profile),
        matchable=matchable(profile),
        missing=missing_for_matching(profile),
    )


@bp.route("/matches")
@requires_feature("community.read")
def matches():
    """Подборка участников. Видны имя, заголовок, город и метка, без сырых оценок и контактов."""
    if not current_user.profile or not matchable(current_user.profile):
        return render_template("modules/profile_matches.html", cards=[], ready=False,
                               missing=missing_for_matching(current_user.profile) if current_user.profile else [],
                               fields=FIELDS, llm_on=bool(current_app.config.get("ANTHROPIC_API_KEY")))
    cards = [{"member": other, "label": label, "explanation": text}
             for other, label, text in get_matches(current_user)]
    return render_template("modules/profile_matches.html", cards=cards, ready=True, missing=[], fields=FIELDS,
                           llm_on=bool(current_app.config.get("ANTHROPIC_API_KEY")))


@bp.route("/photo", methods=["POST"])
@requires_feature("profile.edit")
def upload_photo():
    profile = _profile_for(current_user)
    upload = request.files.get("photo")
    if upload is None or not upload.filename:
        flash(_("Please choose a photo first."), "error")
        return redirect(url_for("profile.edit"))
    max_bytes = current_app.config["MEDIA_MAX_BYTES"]
    try:
        data, ext, _mime = process_image(upload.read(max_bytes + 1), max_bytes)
    except MediaError as exc:
        flash(_photo_error(exc.code), "error")
        return redirect(url_for("profile.edit"))

    storage = current_app.extensions["media"]
    key = profile_photo_key(current_user.id, ext)
    storage.save(key, data)
    old_key = profile.photo_key
    profile.photo_key = key
    audit("profile.photo_uploaded", target=f"user:{current_user.id}", actor_id=current_user.id)
    db.session.commit()
    if old_key:
        storage.delete(old_key)
    flash(_("Your photo is saved."), "success")
    return redirect(url_for("profile.edit"))


@bp.route("/photo/remove", methods=["POST"])
@requires_feature("profile.edit")
def remove_photo():
    profile = _profile_for(current_user)
    if profile.photo_key:
        old_key = profile.photo_key
        profile.photo_key = None
        audit("profile.photo_removed", target=f"user:{current_user.id}", actor_id=current_user.id)
        db.session.commit()
        current_app.extensions["media"].delete(old_key)
        flash(_("Your photo is removed."), "success")
    return redirect(url_for("profile.edit"))


@bp.route("/photo/<int:user_id>")
@requires_feature("community.read")
def photo(user_id):
    """Фото отдаёт только сервер и только верифицированным участникам: к файлам нет прямых ссылок."""
    user = db.session.get(User, user_id) or abort(404)
    if user.profile is None or not user.profile.photo_key:
        abort(404)
    if user.profile is not None and not user.profile.visible_in_directory and user.id != current_user.id:
        abort(404)
    key = user.profile.photo_key
    data = current_app.extensions["media"].read(key)
    if data is None:
        abort(404)
    mime = PHOTO_MIME.get(key.rsplit(".", 1)[-1], "application/octet-stream")
    response = Response(data, mimetype=mime)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@bp.route("/directory")
@requires_feature("community.read")
def directory():
    users = (User.query.join(Profile, Profile.user_id == User.id)
             .filter(Profile.visible_in_directory.is_(True), User.status == "active",
                     User.verification_status == "verified")
             .order_by(User.first_name).limit(300).all())
    return render_template("modules/profile_directory.html", users=users)


@bp.route("/<int:user_id>")
@requires_feature("community.read")
def view(user_id):
    user = db.session.get(User, user_id) or abort(404)
    profile = user.profile
    if user.id != current_user.id and (profile is None or not profile.visible_in_directory
                                       or user.status != "active"):
        abort(404)
    return render_template("modules/profile_view.html", member=user, profile=profile, fields=FIELDS,
                           is_self=user.id == current_user.id)
