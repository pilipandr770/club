from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import db
from ...models import Consent, IntroRequest, Profile, User
from ...profile_guide import (CARD_HINTS, FIELD_BY_KEY, COVER_KEYS, COVER_THEMES, DEFAULT_COVER, FIELDS, OPEN_TO, OPEN_TO_LABELS,
                              PHOTO_EXAMPLES, PHOTO_HINT, completeness, matchable, missing_for_matching, open_to_keys,
                              split_tags)
from ...services.audit import audit
from ...services.consents import latest_consent, record_consent
from ... import social
from ...services import community, intros
from ...services.intros import IntroError
from ...services.entitlements import requires_feature
from ...services.ai import llm_enabled
from ...services.matching import get_matches, refresh_embeddings
from ...services.media import MediaError, process_image, profile_photo_key
from ...utils import clean_url, utcnow

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
            profile.updated_at = utcnow()
            refresh_embeddings(profile)
            audit("profile.updated", target=f"user:{current_user.id}", actor_id=current_user.id)
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
        cover_themes=COVER_THEMES,
        open_to_options=OPEN_TO,
        open_to_selected=open_to_keys(profile),
        card_hints=CARD_HINTS,
        networks=social.NETWORKS,
        social_values=profile.social_links or {},
    )


@bp.route("/card", methods=["POST"])
@requires_feature("profile.edit")
def save_card():
    """Настройки карточки-минисайта: тема, чего ищу, языки, ссылки. На подбор не влияют, эмбеддинги не пересчитываются."""
    profile = _profile_for(current_user)
    theme = request.form.get("cover_theme", DEFAULT_COVER)
    profile.cover_theme = theme if theme in COVER_KEYS else DEFAULT_COVER
    chosen = [key for key in request.form.getlist("open_to") if key in OPEN_TO_LABELS]
    profile.open_to = ",".join(dict.fromkeys(chosen))
    profile.languages = ", ".join(split_tags(request.form.get("languages", "")))[:160]
    bad = []
    for key in ("website_url", "linkedin_url"):
        raw = request.form.get(key, "").strip()
        cleaned = clean_url(raw)
        if raw and not cleaned:
            bad.append(key)
        setattr(profile, key, cleaned[:300] if cleaned else None)
    saved = {}
    for key, _label, _domains, _base in social.NETWORKS:
        raw = request.form.get(f"social_{key}", "").strip()
        value = social.normalize(key, raw)
        if raw and not value:
            bad.append(key)
        if value:
            saved[key] = value
    profile.social_links = saved or None
    profile.updated_at = utcnow()
    audit("profile.card_updated", target=f"user:{current_user.id}", actor_id=current_user.id)
    db.session.commit()
    if bad:
        flash(_("Some links were not saved: they must be web addresses of the chosen network."), "error")
    else:
        flash(_("Your card is saved."), "success")
    return redirect(url_for("profile.edit") + "#card")


@bp.route("/matches")
@requires_feature("community.read")
def matches():
    """Подборка участников. Видны имя, заголовок, город и метка, без сырых оценок и контактов."""
    if not current_user.profile or not matchable(current_user.profile):
        return render_template("modules/profile_matches.html", cards=[], ready=False,
                               missing=missing_for_matching(current_user.profile) if current_user.profile else [],
                               fields=FIELDS, llm_on=llm_enabled())
    cards = []
    for other, label, text in get_matches(current_user):
        cards.append({"member": other, "label": label, "explanation": text,
                      "state": intros.link_state(current_user, other),
                      "contact": intros.visible_contact(current_user, other)})
    return render_template("modules/profile_matches.html", cards=cards, ready=True, missing=[], fields=FIELDS,
                           llm_on=llm_enabled())


PUBLICATION_KINDS = (("directory", "visible_in_directory"), ("matching", "allow_matching"),
                     ("social_links", "links_public"))


@bp.route("/privacy", methods=["GET", "POST"])
@requires_feature("profile.edit")
def privacy():
    """Единое место для решений о публикации и матчинге. Каждое решение пишется в consents."""
    profile = _profile_for(current_user)
    if request.method == "POST":
        for kind, flag in PUBLICATION_KINDS:
            value = bool(request.form.get(flag))
            record_consent(current_user, kind, value)
            setattr(profile, flag, value)
        record_consent(current_user, "voice", bool(request.form.get("voice_consent")))
        profile.updated_at = utcnow()
        refresh_embeddings(profile)
        audit("privacy.decided", target=f"user:{current_user.id}", actor_id=current_user.id,
              details=f"directory={profile.visible_in_directory} matching={profile.allow_matching} "
                      f"links={profile.links_public}")
        db.session.commit()
        flash(_("Your choices are saved."), "success")
        return redirect(url_for("profile.privacy"))

    history = (Consent.query.filter(Consent.user_id == current_user.id,
                                    Consent.kind.in_([kind for kind, _flag in PUBLICATION_KINDS] + ["voice"]))
               .order_by(Consent.created_at.desc(), Consent.id.desc()).limit(20).all())
    return render_template("modules/profile_privacy.html", profile=profile, history=history,
                           decided=latest_consent(current_user, "directory") is not None,
                           voice_ok=latest_consent(current_user, "voice") is True)


@bp.route("/intro/<int:user_id>", methods=["POST"])
@requires_feature("community.read")
def request_intro(user_id):
    other = db.session.get(User, user_id) or abort(404)
    # со страницы карточки возвращаем на карточку, иначе в подборку (адрес от пользователя не берём)
    back = (url_for("profile.view", user_id=other.id) if request.form.get("back") == "card"
            else url_for("profile.matches"))
    try:
        intros.request_intro(current_user, other, request.form.get("message", ""))
        db.session.commit()
    except IntroError as exc:
        db.session.rollback()
        flash(_intro_error(exc.code), "error")
        return redirect(back)
    flash(_("Your request is sent. The other member decides whether to share contact details."), "success")
    return redirect(back)


@bp.route("/intros")
@requires_feature("community.read")
def intros_inbox():
    incoming, outgoing = intros.inbox(current_user)
    return render_template("modules/profile_intros.html", incoming=incoming, outgoing=outgoing)


@bp.route("/intros/<int:intro_id>/respond", methods=["POST"])
@requires_feature("community.read")
def respond_intro(intro_id):
    intro = db.session.get(IntroRequest, intro_id) or abort(404)
    try:
        intros.respond(current_user, intro, request.form.get("answer") == "accept")
        db.session.commit()
    except IntroError as exc:
        db.session.rollback()
        flash(_intro_error(exc.code), "error")
    return redirect(url_for("profile.intros_inbox"))


@bp.route("/intros/<int:intro_id>/withdraw", methods=["POST"])
@requires_feature("community.read")
def withdraw_intro(intro_id):
    intro = db.session.get(IntroRequest, intro_id) or abort(404)
    try:
        intros.withdraw(current_user, intro)
        db.session.commit()
    except IntroError as exc:
        db.session.rollback()
        flash(_intro_error(exc.code), "error")
    return redirect(url_for("profile.intros_inbox"))


def _intro_error(code):
    messages = {
        "own_profile": _("You cannot send a request to yourself."),
        "not_available": _("This member cannot be contacted right now."),
        "already_open": _("There is already a request between you two."),
        "daily_limit": _("You have reached today's limit of requests. Please try again tomorrow."),
        "forbidden": _("This is not available to you."),
        "already_answered": _("This request has already been answered."),
    }
    return messages.get(code, _("Something went wrong. Please try again."))


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
    """Сообщество: все участники, которые решили быть в каталоге, с поиском и фильтрами."""
    q = request.args.get("q", "").strip()
    city = request.args.get("city", "").strip()
    industry = request.args.get("industry", "").strip()
    open_to = request.args.get("open_to", "").strip()
    if open_to not in OPEN_TO_LABELS:
        open_to = ""
    page = request.args.get("page", 1, type=int) or 1
    members, total = community.search(q, city, industry, open_to, page)
    cities, industries = community.facets()
    listed, verified = community.counts()
    mine = current_user.profile
    return render_template(
        "modules/profile_directory.html", members=members, total=total, q=q, city=city, industry=industry,
        open_to=open_to, open_to_options=OPEN_TO, cities=cities, industries=industries, page=page,
        page_size=community.PAGE_SIZE, listed=listed, verified=verified,
        in_directory=bool(mine and mine.visible_in_directory), open_to_labels=OPEN_TO_LABELS,
        split_tags=split_tags, open_to_keys=open_to_keys, filtered=bool(q or city or industry or open_to))


@bp.route("/<int:user_id>")
@requires_feature("community.read")
def view(user_id):
    """Карточка участника как минисайт. Ссылки и e-mail видны только после принятого запроса на контакт."""
    user = db.session.get(User, user_id) or abort(404)
    profile = user.profile
    is_self = user.id == current_user.id
    if not is_self and (profile is None or not profile.visible_in_directory or user.status != "active"
                        or not user.is_verified):
        abort(404)
    state = None if is_self else intros.link_state(current_user, user)
    return render_template(
        "modules/profile_view.html", member=user, profile=profile, fields_by_key=FIELD_BY_KEY, is_self=is_self, state=state,
        contact=intros.visible_contact(current_user, user),
        links=social.card_links(profile) if profile and (is_self or state == "accepted" or profile.links_public) else [],
        links_hidden=bool(profile and not is_self and state != "accepted" and not profile.links_public
                          and social.card_links(profile)),
        content=community.member_content(user),
        tags=split_tags(profile.expertise) if profile else [], languages=split_tags(profile.languages) if profile else [],
        open_to=open_to_keys(profile) if profile else [], open_to_labels=OPEN_TO_LABELS,
        can_intro=not is_self and state is None,
        viewer_ready=matchable(current_user.profile) if current_user.profile else False,
        target_ready=matchable(profile) if profile else False)
