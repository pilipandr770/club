"""Подсказки, правила и дисклеймер мини-клубов. Тексты правил — черновик для проверки юристом (CLAUDE.md, «Отделы»).

Позиция клуба: это онлайн-платформа без помещений и платежей. Клуб знакомит участников и помогает общаться.
Мини-клубы организуют участники. Запрещены игры на деньги и услуги, для которых нужно разрешение
(например платный присмотр за детьми). Строки на английском, перевод — в translations/de.
"""

import re
from dataclasses import dataclass

from flask_babel import lazy_gettext as _l


@dataclass(frozen=True)
class ClubField:
    key: str
    label: object
    hint: object
    examples: tuple
    max_length: int = 1500
    required: bool = False


CLUB_FIELDS = (
    ClubField(
        key="description",
        label=_l("What is this mini-club about?"),
        hint=_l("Two or three sentences: what you do together, what the mood is and why someone should join."),
        examples=(
            _l("We play chess on Tuesday evenings, for fun and without stakes. Beginners and experienced players are welcome."),
            _l("A small group that goes fishing on weekends at lakes around Frankfurt and shares tips and gear."),
            _l("Neighbours who help each other for free: carrying, small repairs, a lift to the doctor, a chat over coffee."),
        ),
        max_length=2500,
        required=True,
    ),
    ClubField(
        key="audience",
        label=_l("Who is it for?"),
        hint=_l("Who fits the group: level, age, language, interests. Members are adults."),
        examples=(
            _l("Adults of any level who enjoy chess. We speak German and English."),
            _l("Parents with small children who want to meet other families. Parents stay with their children at all times."),
        ),
    ),
    ClubField(
        key="schedule",
        label=_l("When and how often?"),
        hint=_l("Regular days and times, or how you agree on dates. Specific meetings can be planned later."),
        examples=(
            _l("Every Tuesday from 19:00 to 21:30, in a cafe or online."),
            _l("About once a month on a Saturday morning; dates are agreed in the group."),
        ),
    ),
    ClubField(
        key="rules",
        label=_l("Rules of your group"),
        hint=_l("The members set their own rules: respect, punctuality, costs shared among members. The club rules for mini-clubs always apply as well."),
        examples=(
            _l("Be friendly and on time. Costs such as petrol or entrance fees are shared among those who join."),
            _l("We play without stakes. Please cancel early if you cannot come."),
        ),
    ),
)

TOPIC_LABELS = {
    "games": _l("Games without stakes"),
    "sport": _l("Sport and movement"),
    "outdoor": _l("Outdoors and trips"),
    "creative": _l("Creative and hobbies"),
    "learning": _l("Learning and languages"),
    "family": _l("Parents and children together"),
    "neighbours": _l("Neighbourly help"),
    "other": _l("Other"),
}
JOIN_LABELS = {
    "open": _l("Open: everyone can join right away"),
    "approval": _l("By request: you accept new members"),
}


def topic_label(value):
    return TOPIC_LABELS.get(value, value)


def join_label(value):
    return JOIN_LABELS.get(value, value)


IMAGE_HINT = _l("A photo that shows what you do together (JPG, PNG or WebP, up to 5 MB).")

# Короткий дисклеймер: показывается на каждой странице мини-клубов и в форме создания
DISCLAIMER_SHORT = _l("Mini-clubs are run by members, not by the club. The club is an online platform: it has no premises, "
                      "organises no games or care and handles no payments. It only helps members get to know each other.")

# Полные правила: (заголовок, текст). Страница /miniclubs/rules
RULES = (
    (_l("What the club is and is not"),
     _l("The club is an online platform. We have no premises and do not host meetings, games or care of any kind. "
        "We bring members together and help them get in touch. Mini-clubs are created and run by members. "
        "The founder and the participants are responsible for their meetings, their rules and their behaviour.")),
    (_l("Not allowed in mini-clubs"),
     _l("Gambling and games for money or valuables: stakes, bets, pots, tournaments with cash prizes. "
        "Offers that need an official permit, for example paid childcare (day care, childminding), care or nursing services, "
        "medical or therapeutic treatment, serving alcohol for money. "
        "Offers to look after other people's children without their parents. Anything illegal or harmful.")),
    (_l("Allowed and welcome"),
     _l("Games for fun without stakes, such as chess or cards. Trips, hobbies, sport, learning together. "
        "Neighbourly help between members without payment. Parent-and-child meetings where the parents stay with their children. "
        "Shared costs, for example petrol or an entrance fee, that participants split among themselves.")),
    (_l("Families and private arrangements"),
     _l("Adults only can be members. Children take part only together with their parents. "
        "If families know each other and their children visit one another, that is a private matter between the families. "
        "It is not a mini-club offer, and a mini-club must not advertise it or offer it for money.")),
    (_l("Money"),
     _l("The club handles no payments and does not arrange paid services. What members agree on among themselves, "
        "such as sharing costs, is their own matter. A mini-club may not collect fees for care, games or other regulated services.")),
    (_l("Reports and removal"),
     _l("Every mini-club has a report button. We review mini-clubs before they are published and remove any that break these rules. "
        "Members who break them can be removed.")),
    (_l("Please note"),
     _l("These rules are not legal advice. The club team can change or remove mini-clubs at any time.")),
)

# Слова, которые подсвечиваются администратору при проверке (не блокируют: проверка человеком).
# Немецкий, английский и русский: участники пишут на разных языках.
_FLAGGED = (
    r"um geld", r"geldeins", r"\beinsatz", r"\bwett", r"\bpoker\b", r"glücksspiel", r"glucksspiel", r"casino", r"\bpot\b",
    r"for money", r"stakes", r"\bbet(s|ting)?\b", r"gambl", r"cash prize",
    r"на деньги", r"ставк", r"азартн", r"\bпокер", r"казино",
    r"kinderbetreuung", r"tagesmutter", r"tagespflege", r"kindergarten", r"\bkita\b", r"babysit", r"kinder ohne eltern",
    r"childcare", r"day ?care", r"childminding", r"детский сад", r"присмотр", r"\bняня", r"без родителей",
    r"pflegedienst", r"pflege gegen", r"therapie", r"behandlung", r"alkohol",
)
_FLAGGED_RE = re.compile("|".join(_FLAGGED), re.IGNORECASE)


def flagged_terms(*texts) -> list[str]:
    """Найденные в текстах подозрительные слова (для подсветки администратору). Пустой список — ничего не найдено."""
    found = []
    for text in texts:
        for match in _FLAGGED_RE.finditer(text or ""):
            word = match.group(0).strip().lower()
            if word and word not in found:
                found.append(word)
    return found[:8]
