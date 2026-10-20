"""Тестовые участники для разработки и проверки тарифов. Команды не работают в production."""

from flask import current_app

from ..extensions import db
from ..models import Profile, User
from ..utils import utcnow
from .consents import record_consent

DOMAIN = "club.test"  # зарезервированный домен для тестов, письма туда не уйдут

# (логин, тариф, проверка личности, админ)
TEST_USERS = (
    ("guest", "guest", True, False),
    ("starter", "starter", True, False),
    ("growth", "growth", True, False),
    ("premium", "premium", True, False),
    ("unverified", "guest", False, False),
    ("admin", "premium", True, True),
)


class SeedError(Exception):
    pass


def check_allowed(seed_password):
    if current_app.config.get("APP_ENV") == "production":
        raise SeedError("Тестовых участников нельзя создавать в production.")
    if not seed_password or len(seed_password) < 10:
        raise SeedError("TEST_SEED_PASSWORD в .env не задан или короче 10 символов.")


def seed_test_users(seed_password):
    """Создаёт или обновляет тестовых участников. Повторный запуск ничего не ломает."""
    check_allowed(seed_password)
    results = []
    for login, plan, verified, admin in TEST_USERS:
        email = f"{login}@{DOMAIN}"
        user = User.query.filter_by(email=email).first()
        created = user is None
        if created:
            user = User(email=email, first_name=login.capitalize(), last_name="Test")
            db.session.add(user)
            user.profile = Profile(residence_country="DE")
            for kind in ("privacy", "terms", "identity"):
                record_consent(user, kind, True, source="seed")
        user.set_password(seed_password)
        user.plan = plan
        user.is_admin = admin
        user.status = "active"
        user.email_confirmed_at = user.email_confirmed_at or utcnow()
        if verified:
            if not user.is_verified:
                user.verified_at = utcnow()
            user.verification_status = "verified"
            user.verification_reason = None
        else:
            user.verification_status = "none"
            user.verified_at = None
        results.append((email, plan, "создан" if created else "обновлён", admin, verified))
    db.session.commit()
    return results


DEMO_DOMAIN = "club.test"

# Вымышленные участники для демонстраций. Не реальные люди: имена и тексты придуманы.
DEMO_PROFILES = (
    dict(login="lena", first="Lena", plan="growth", headline="Steuerberaterin für Gründerinnen",
         company="Kanzlei Berger", industry="Finanzen und Steuern", city="Frankfurt am Main",
         q_focus="Ich betreue Freiberufler und junge Unternehmen bei Steuererklärungen, Buchhaltung und Förderanträgen.",
         q_challenge="Ich möchte mehr Gründerinnen aus dem Technologiebereich als Mandantinnen gewinnen.",
         q_can_help="Ich erkläre Fristen und Förderungen in verständlichem Deutsch und begleite Erstgespräche.",
         q_looking_for="Eine Webagentur, die mir eine klare Website für Mandanten baut.",
         expertise="Steuerrecht, Buchhaltung, Förderanträge, DATEV", bio="Mutter von zwei Kindern, liebt lange Spaziergänge am Main."),
    dict(login="jonas", first="Jonas", plan="starter", headline="Full-Stack-Entwickler",
         company="Freelance", industry="Software", city="Offenbach",
         q_focus="Ich entwickle Kundenportale und Buchungssysteme für kleine Betriebe, mit Python und React.",
         q_challenge="Mir fehlen Kunden außerhalb meines bisherigen Netzwerks.",
         q_can_help="Ich prüfe Konzepte und Prototypen und helfe bei technischen Entscheidungen.",
         q_looking_for="Einen Vertriebspartner mit Erfahrung im Handwerk.",
         expertise="Python, React, PostgreSQL, Produktentwicklung", bio="Fährt gern Rad und baut in der Freizeit kleine Elektronikprojekte."),
    dict(login="mira", first="Mira", plan="premium", headline="Investment Managerin mit Fokus Energie",
         company="Grüne Kapital GmbH", industry="Energie", city="Wiesbaden",
         q_focus="Ich begleite junge Energie- und Speicherunternehmen auf dem Weg zur ersten Finanzierungsrunde.",
         q_challenge="Viele Gründer können ihre Zahlen noch nicht belastbar darstellen.",
         q_can_help="Ich gebe Feedback auf Finanzmodelle und stelle Kontakte zu Business Angels her.",
         q_looking_for="Starke Gründerteams im Bereich Batteriespeicher.",
         expertise="Finanzmodelle, Fundraising, Energiespeicher", bio="Segelt, liest viel Wirtschaftsgeschichte."),
    dict(login="tom", first="Tom", plan="growth", headline="Designer für Marken und Produkte",
         company="Studio Tom", industry="Design", city="Frankfurt am Main",
         q_focus="Ich gestalte Marken, Websites und Verpackungen für kleine Unternehmen.",
         q_challenge="Ich brauche Projekte mit einem festen Zeitplan, damit ich besser planen kann.",
         q_can_help="Ich prüfe Landingpages und Pitch-Decks auf Verständlichkeit und Gestaltung.",
         q_looking_for="Einen Texter für Website und Newsletter.",
         expertise="Branding, UX, Figma, Verpackungsdesign", bio="Sammelt alte Plakate und fotografiert gern Architektur."),
    dict(login="sara", first="Sara", plan="starter", headline="Reiseberaterin für Gruppenreisen",
         company="Reisewerk", industry="Reisen", city="Mainz",
         q_focus="Ich organisiere Gruppenreisen nach Portugal und Italien für Teams und Freundeskreise.",
         q_challenge="Ich suche verlässliche Unterkünfte mit flexiblen Stornobedingungen.",
         q_can_help="Ich plane Reisen für Gruppen von zehn bis zwanzig Personen und kenne gute Restaurants.",
         q_looking_for="Einen Partner für Transfers in Lissabon.",
         expertise="Reiseplanung, Gruppenreisen, Portugal, Italien", bio="Spricht Portugiesisch und Italienisch."),
)


def seed_demo_profiles(seed_password):
    """Создаёт демо-участников с заполненными анкетами. Для показа клуба, не для реальных людей."""
    from ..services.matching import refresh_embeddings

    check_allowed(seed_password)
    created = 0
    for item in DEMO_PROFILES:
        email = f"demo-{item['login']}@{DEMO_DOMAIN}"
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(email=email, first_name=item["first"], last_name="Demo", plan=item["plan"])
            db.session.add(user)
            user.profile = Profile(residence_country="DE")
            created += 1
        user.set_password(seed_password)
        user.status = "active"
        user.email_confirmed_at = user.email_confirmed_at or utcnow()
        user.verification_status = "verified"
        user.verified_at = user.verified_at or utcnow()
        user.verification_reason = "manual_override"
        profile = user.profile
        for key in ("headline", "company", "industry", "city", "q_focus", "q_challenge", "q_can_help",
                    "q_looking_for", "expertise", "bio"):
            setattr(profile, key, item.get(key, ""))
        profile.visible_in_directory = True
        profile.allow_matching = True
        db.session.flush()
        for kind in ("directory", "matching"):
            record_consent(user, kind, True, source="demo-seed")
        refresh_embeddings(profile)
    db.session.commit()
    return created
