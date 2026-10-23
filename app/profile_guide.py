"""Подсказки и примеры для профиля. Примеры написаны нами как образцы, а не взяты из профилей участников.

Чем полнее профиль, тем точнее матчинг. Поэтому у каждого поля есть подсказка и три примера,
а заполненность считается по весам полей.
"""

from dataclasses import dataclass

from flask_babel import lazy_gettext as _l


@dataclass(frozen=True)
class Field:
    key: str
    label: object
    hint: object
    examples: tuple
    weight: int
    multiline: bool = False
    max_length: int = 600
    for_matching: bool = False  # нужно для матчинга


FIELDS = (
    Field(
        key="headline",
        label=_l("Headline"),
        hint=_l("One line: who you are and what you do."),
        examples=(
            _l("Tax adviser for founders in the Rhine-Main area"),
            _l("Full-stack developer building web products for small businesses"),
            _l("Investment manager focused on energy startups"),
        ),
        weight=2,
        max_length=160,
    ),
    Field(
        key="company",
        label=_l("Company"),
        hint=_l("Your company or project name, if you have one."),
        examples=(
            _l("Kanzlei Weber & Partner"),
            _l("Freelance"),
            _l("Solar Shelf GmbH (in founding)"),
        ),
        weight=1,
        max_length=160,
    ),
    Field(
        key="industry",
        label=_l("Industry"),
        hint=_l("The field you work in."),
        examples=(
            _l("Finance and tax"),
            _l("Software"),
            _l("Energy"),
        ),
        weight=1,
        max_length=120,
    ),
    Field(
        key="city",
        label=_l("City"),
        hint=_l("Where you are based. Only the city, not the full address."),
        examples=(
            _l("Frankfurt am Main"),
            _l("Offenbach"),
            _l("Wiesbaden"),
        ),
        weight=1,
        max_length=120,
    ),
    Field(
        key="q_focus",
        label=_l("What I do"),
        hint=_l("Describe your work in two or three sentences. Concrete details help most."),
        examples=(
            _l("I advise freelancers and small companies on tax returns and bookkeeping. Most of my clients are in construction and retail."),
            _l("I build and maintain customer portals with Python and React. I also run a small team of two developers."),
            _l("I look at early-stage energy companies and prepare them for their first investment round."),
        ),
        weight=2,
        multiline=True,
        max_length=800,
        for_matching=True,
    ),
    Field(
        key="q_challenge",
        label=_l("My main challenge right now"),
        hint=_l("What is the hardest part of your work at the moment?"),
        examples=(
            _l("Finding reliable clients outside my current network."),
            _l("I need a second developer but cannot afford a full-time salary yet."),
            _l("Investors ask for numbers I do not have yet."),
        ),
        weight=2,
        multiline=True,
        max_length=600,
        for_matching=True,
    ),
    Field(
        key="q_can_help",
        label=_l("How I can help others"),
        hint=_l("What do you know well that others could use? This is what other members will look for."),
        examples=(
            _l("I can explain tax deadlines for freelancers in plain German."),
            _l("I can review a landing page or a pitch deck for clarity."),
            _l("I can introduce people to small-business owners in the Rhine-Main area."),
        ),
        weight=2,
        multiline=True,
        max_length=600,
        for_matching=True,
    ),
    Field(
        key="q_looking_for",
        label=_l("What I am looking for"),
        hint=_l("Who or what would help you right now? Be specific."),
        examples=(
            _l("A web designer for a two-week project."),
            _l("Co-founder with sales experience in the energy sector."),
            _l("Advice on getting my first angel investment."),
        ),
        weight=2,
        multiline=True,
        max_length=600,
        for_matching=True,
    ),
    Field(
        key="expertise",
        label=_l("Skills and topics"),
        hint=_l("Separate with commas. Five to ten short words work best."),
        examples=(
            _l("tax law, bookkeeping, DATEV"),
            _l("Python, React, product design"),
            _l("energy storage, fundraising, financial models"),
        ),
        weight=2,
        max_length=500,
        for_matching=True,
    ),
    Field(
        key="bio",
        label=_l("About me"),
        hint=_l("A few sentences about yourself. Optional, but it helps people get to know you."),
        examples=(
            _l("I grew up in Hesse, started my first company at 25, and enjoy long walks along the Main."),
            _l("Father of two, coffee lover, always reading something about economics."),
        ),
        weight=1,
        multiline=True,
        max_length=1200,
    ),
)

PHOTO_WEIGHT = 2
PHOTO_HINT = _l("A clear photo of your face helps people remember you. JPEG, PNG or WebP, up to 5 MB.")
PHOTO_EXAMPLES = (
    _l("Head and shoulders, natural light, plain background."),
    _l("No group photos, no sunglasses, no heavy filters."),
)

FIELD_BY_KEY = {field.key: field for field in FIELDS}


def _filled(profile, key):
    return bool(getattr(profile, key, "") and str(getattr(profile, key)).strip())


def completeness(profile) -> int:
    """Заполненность профиля в процентах по весам полей, включая фото."""
    total = sum(f.weight for f in FIELDS) + PHOTO_WEIGHT
    done = sum(f.weight for f in FIELDS if _filled(profile, f.key))
    if profile.photo_key:
        done += PHOTO_WEIGHT
    return round(100 * done / total)


def matchable(profile) -> bool:
    """Профиль пригоден для матчинга: есть что предложить и что искать, и участник дал согласие."""
    return bool(profile.allow_matching and _filled(profile, "q_can_help")
                and (_filled(profile, "q_looking_for") or _filled(profile, "q_challenge")))


def missing_for_matching(profile) -> list[str]:
    """Какие поля мешают матчингу, для подсказки участнику."""
    missing = []
    if not _filled(profile, "q_can_help"):
        missing.append("q_can_help")
    if not (_filled(profile, "q_looking_for") or _filled(profile, "q_challenge")):
        missing.append("q_looking_for")
    return missing
