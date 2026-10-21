"""Подсказки и примеры для встречи. Примеры — наши образцы, не чужие встречи."""

from dataclasses import dataclass

from flask_babel import lazy_gettext as _l


@dataclass(frozen=True)
class EventField:
    key: str
    label: object
    hint: object
    examples: tuple
    max_length: int = 1500
    required: bool = False


EVENT_FIELDS = (
    EventField(
        key="description",
        label=_l("What is it about?"),
        hint=_l("Two or three sentences: what happens, why it is worth coming and what the mood will be."),
        examples=(
            _l("An evening for founders and freelancers: short talks about first customers, then relaxed conversations over dinner."),
            _l("A hands-on workshop: we build a simple landing page together and everyone leaves with a working draft."),
            _l("A Sunday walk along the Main with coffee stops. A chance to get to know club members outside of work."),
        ),
        max_length=2500,
        required=True,
    ),
    EventField(
        key="audience",
        label=_l("Who is it for?"),
        hint=_l("Who should come and who will get the most out of it. Say if beginners are welcome."),
        examples=(
            _l("Founders, freelancers and anyone planning to start a business. No preparation needed."),
            _l("Members with some experience in marketing; bring a project you want feedback on."),
        ),
    ),
    EventField(
        key="agenda",
        label=_l("How does it work?"),
        hint=_l("The schedule in short lines: arrival, main part, breaks, end. Fixed times help people plan."),
        examples=(
            _l("18:30 arrival and drinks. 19:00 two short talks. 20:00 dinner and open conversations. 22:00 end."),
            _l("10:00 welcome, 10:30 workshop part one, 12:00 lunch break, 13:00 part two, 15:00 wrap-up."),
        ),
    ),
    EventField(
        key="requirements",
        label=_l("What to bring and what to know"),
        hint=_l("Things to bring, costs on site, accessibility, dress code, deadlines or rules."),
        examples=(
            _l("Bring a laptop. Drinks and dinner are paid on site, about 30 euros. The venue is wheelchair accessible."),
            _l("Comfortable shoes and a bottle of water. We walk about 8 km at an easy pace. Cancellation is possible until the day before."),
        ),
    ),
)

TYPE_LABELS = {
    "meetup": _l("Meetup"),
    "workshop": _l("Workshop"),
    "talk": _l("Talk"),
    "dinner": _l("Dinner"),
    "outdoor": _l("Outdoor"),
    "online": _l("Online session"),
    "other": _l("Other"),
}
FORMAT_LABELS = {
    "in_person": _l("In person"),
    "online": _l("Online"),
    "hybrid": _l("Hybrid"),
}


def type_label(value):
    return TYPE_LABELS.get(value, value)


def format_label(value):
    return FORMAT_LABELS.get(value, value)


IMAGE_HINT = _l("A photo that shows the place or the mood of the meeting (JPG, PNG or WebP, up to 5 MB).")
VENUE_HINT = _l("Name of the place and a general address, for example a cafe or a co-working space. Do not write a private home address.")
ONLINE_HINT = _l("For online or hybrid meetings: the link to join. Only members who sign up can see it.")
