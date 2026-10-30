import os

from flask import Flask, request
import importlib

from flask_babel import get_locale, lazy_gettext as _l
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

from . import cli
from .config import CONFIGS
from .extensions import babel, csrf, db, limiter, login_manager, migrate
from .i18n import select_locale, select_timezone
from .services.identity import StripeIdentityProvider

# CSP: без inline-скриптов и стилей, без внешних CDN (CLAUDE.md, раздел «Интерфейс и безопасность»)
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self' https://checkout.stripe.com https://verify.stripe.com; "
    "frame-ancestors 'none'"
)


def create_app(env=None):
    env = env or os.environ.get("APP_ENV", "development")
    app = Flask(__name__)
    app.config.from_object(CONFIGS[env])
    if env == "production" and not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY не задан: в production без него нельзя запускаться")

    # Единственный вход — Caddy, он один стоит перед приложением
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    db.init_app(app)
    migrate.init_app(app, db)
    babel.init_app(
        app,
        locale_selector=select_locale,
        timezone_selector=select_timezone,
    )
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = _l("Please log in to access this page.")
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id):
        from .models import User

        return db.session.get(User, int(user_id))

    from .services.media import LocalStorage

    app.extensions["media"] = LocalStorage(app.config["MEDIA_ROOT"])
    app.extensions["identity_provider"] = StripeIdentityProvider(
        app.config["STRIPE_SECRET_KEY"], app.config["STRIPE_WEBHOOK_SECRET"]
    )

    from .blueprints.admin import bp as admin_bp
    from .blueprints.assistant import bp as assistant_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.member import bp as member_bp
    from .blueprints.public import bp as public_bp
    from .blueprints.webhooks import bp as webhooks_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(member_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(assistant_bp)

    # Модули регистрируются только если включены в ENABLED_MODULES: выключенный адрес отвечает 404
    from .modules import enabled_modules

    for spec in enabled_modules(app.config["ENABLED_MODULES"]):
        app.register_blueprint(importlib.import_module(spec.package).bp)

    @app.context_processor
    def inject_nav():
        from .services.entitlements import has_feature

        if not current_user.is_authenticated:
            return {"nav_modules": [], "assistant_on": False}
        specs = [spec for spec in enabled_modules(app.config["ENABLED_MODULES"])
                 if has_feature(current_user, spec.feature)]
        assistant_on = has_feature(current_user, "assistant.use")
        voice_ok = False
        if assistant_on:
            from .services.consents import latest_consent

            voice_ok = latest_consent(current_user, "voice") is True
        return {"nav_modules": specs, "assistant_on": assistant_on, "voice_ok": voice_ok}

    from . import models  # noqa: F401  (регистрирует модели для миграций)

    cli.register(app)

    error_messages = {
        400: _l("The request could not be processed. Refresh the page and try again."),
        401: _l("Please log in to access this page."),
        403: _l("You do not have access to this page."),
        404: _l("This page could not be found."),
        405: _l("This action is not available for this request."),
        413: _l("The uploaded file is too large."),
        429: _l("Too many requests. Please wait a little and try again."),
        500: _l("The server could not complete the request. Please try again later."),
    }

    def translated_error(error):
        from flask import render_template
        response = error.get_response()
        response.set_data(render_template("errors/http.html", code=error.code,
                                          message=error_messages[error.code]))
        response.content_type = "text/html; charset=utf-8"
        return response

    for code in error_messages:
        app.register_error_handler(code, translated_error)

    # Текущий язык для <html lang="..."> и current_user в шаблонах
    app.jinja_env.globals["get_locale"] = get_locale
    from .i18n_labels import display_label
    app.jinja_env.filters["label"] = display_label
    from .modules.startups.labels import category_label, stage_label

    from .services.intros import status_label as intro_status_label

    # Версия статики в адресе файла: после деплоя браузер не возьмёт старый CSS из кеша
    static_dir = os.path.join(app.root_path, "static")
    asset_version = str(int(max(os.path.getmtime(os.path.join(root, name))
                                for root, _dirs, names in os.walk(static_dir) for name in names)))
    app.jinja_env.globals["asset_version"] = asset_version

    from .services.catalog import status_label as inquiry_status_label

    from .auction_guide import condition_label
    from .event_guide import format_label as event_format_label
    from .event_guide import type_label as event_type_label
    from .miniclub_guide import join_label as miniclub_join_label
    from .miniclub_guide import topic_label as miniclub_topic_label
    from .utils import format_eur, utc_to_local_input

    app.jinja_env.filters["eur"] = format_eur
    app.jinja_env.filters["local_input"] = utc_to_local_input
    app.jinja_env.globals.update(event_type_label=event_type_label, event_format_label=event_format_label,
                                 miniclub_topic_label=miniclub_topic_label, miniclub_join_label=miniclub_join_label)
    app.jinja_env.globals.update(condition_label=condition_label, category_label=category_label, stage_label=stage_label,
                                 intro_status_label=intro_status_label,
                                 inquiry_status_label=inquiry_status_label)

    @app.after_request
    def count_public_visit(response):
        from flask import request

        if (request.method == "GET" and response.status_code == 200 and response.mimetype == "text/html"
                and not current_user.is_authenticated):
            from .services import analytics

            analytics.track_page(request, request.endpoint or "")
        return response

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=(), payment=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # Страницы вошедшего участника не должны оставаться в кеше общего компьютера и открываться кнопкой «назад» после выхода
        if response.mimetype == "text/html" and current_user.is_authenticated:
            response.headers.setdefault("Cache-Control", "private, no-store")
        # HSTS только в production с https-адресом сайта (локально без TLS он помешал бы работе браузера).
        # Схему берём из BASE_URL: TLS заканчивается на Traefik, внутри приложение видит обычный http.
        if app.config.get("APP_ENV") == "production" and app.config["BASE_URL"].startswith("https://"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=15552000")
        return response

    return app
