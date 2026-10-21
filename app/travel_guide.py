"""Подсказки и примеры для карточки поездки. Примеры — наши образцы, не чужие поездки."""

from dataclasses import dataclass

from flask_babel import lazy_gettext as _l


@dataclass(frozen=True)
class TripField:
    key: str
    label: object
    hint: object
    examples: tuple
    max_length: int = 1500
    required: bool = False


# description — основное поле; остальные разделы необязательны
TRIP_FIELDS = (
    TripField(
        key="description",
        label=_l("What is the trip about?"),
        hint=_l("Two or three sentences: the idea, the mood and who you would like to travel with."),
        examples=(
            _l("A relaxed week in Lisbon for founders: mornings to work, afternoons to explore the city together."),
            _l("A weekend hike in the Black Forest for a small group, with a cosy farmhouse and a shared dinner."),
            _l("Five days in Tuscany by car: wine villages, a cooking class and plenty of time for conversations."),
        ),
        max_length=2000,
        required=True,
    ),
    TripField(
        key="program",
        label=_l("Programme"),
        hint=_l("Day by day, in short lines. Fixed points matter most: arrival, highlights, departure."),
        examples=(
            _l("Day 1: arrival and welcome dinner. Day 2: old town walk and co-working. Day 3: day trip to Sintra."),
            _l("Saturday: hike to the lake, picnic. Sunday: sauna and breakfast, departure at noon."),
        ),
    ),
    TripField(
        key="included",
        label=_l("What is included"),
        hint=_l("What the price covers, or what you will organise for the group."),
        examples=(
            _l("Six nights in shared apartments, breakfast, one guided tour and the welcome dinner."),
            _l("Farmhouse stay and a shared dinner. Everyone brings their own food for the day."),
        ),
    ),
    TripField(
        key="not_included",
        label=_l("What is not included"),
        hint=_l("Costs that everyone pays on their own, so nobody is surprised."),
        examples=(
            _l("Flights, local transport, lunches and personal spending."),
            _l("Travel to the meeting point and travel insurance."),
        ),
    ),
    TripField(
        key="meeting_point",
        label=_l("Meeting point and getting there"),
        hint=_l("Where the group meets and how people arrive. Do not write private addresses of other people."),
        examples=(
            _l("We meet on Monday at 10:00 at the hotel lobby. The airport is 25 minutes away by metro."),
            _l("Car pool from Frankfurt main station on Friday at 17:00, or meet us at the farmhouse."),
        ),
    ),
    TripField(
        key="requirements",
        label=_l("Who is it for and what to know"),
        hint=_l("Fitness level, documents, languages, group rules, deadlines for booking or cancelling."),
        examples=(
            _l("Easy pace, no special fitness needed. Please book by 1 March; free cancellation until 15 March."),
            _l("Walking about 15 km per day. Passport or ID card required. The group speaks German and English."),
        ),
    ),
)

COVER_HINT = _l("A photo that shows the place or the mood of the trip (JPG, PNG or WebP, up to 5 MB).")
PROGRAM_HINT = _l("Optional: a detailed programme or a brochure as a PDF (up to 10 MB, 60 pages). Members can download it. Clubi can also read it to answer questions about the trip.")
