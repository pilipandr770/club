from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import db
from ...models import Profile
from ...services.audit import audit
from ...services.entitlements import requires_feature

bp = Blueprint("profile", __name__, url_prefix="/profile")

HEADLINE_MAX = 160
BIO_MAX = 2000


@bp.route("/", methods=["GET", "POST"])
@requires_feature("profile.edit")
def edit():
    profile = current_user.profile
    if profile is None:
        profile = Profile(user=current_user)
        db.session.add(profile)

    errors = {}
    if request.method == "POST":
        headline = request.form.get("headline", "").strip()
        bio = request.form.get("bio", "").strip()
        if len(headline) > HEADLINE_MAX:
            errors["headline"] = _("Please keep the headline under 160 characters.")
        if len(bio) > BIO_MAX:
            errors["bio"] = _("Please keep the text under 2000 characters.")
        if not errors:
            profile.headline = headline
            profile.bio = bio
            audit("profile.updated", target=f"user:{current_user.id}", actor_id=current_user.id)
            db.session.commit()
            flash(_("Your profile is saved."), "success")
            return redirect(url_for("profile.edit"))

    return render_template("modules/profile.html", profile=profile, errors=errors,
                           headline_max=HEADLINE_MAX, bio_max=BIO_MAX)
