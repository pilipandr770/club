"""Подсказки и примеры для карточки проекта. Примеры — наши образцы, не чужие проекты."""

from dataclasses import dataclass

from flask_babel import lazy_gettext as _l


@dataclass(frozen=True)
class PitchField:
    key: str
    label: object
    hint: object
    examples: tuple
    max_length: int = 1500
    required: bool = False


PITCH_FIELDS = (
    PitchField(
        key="description",
        label=_l("What is it?"),
        hint=_l("Two or three sentences: what the project does and for whom. This is the first thing investors read."),
        examples=(
            _l("An app that reads receipts with a phone camera and prepares the bookkeeping for freelancers."),
            _l("A subscription service that delivers repair kits for household appliances, with video guides."),
            _l("A platform where small energy storage owners sell spare capacity to local businesses."),
        ),
        max_length=3000,
        required=True,
    ),
    PitchField(
        key="problem",
        label=_l("Problem"),
        hint=_l("Whose problem do you solve and how painful is it today? Use a concrete situation or number."),
        examples=(
            _l("Freelancers spend about five hours a month sorting receipts and often lose deductible costs."),
            _l("Small shops pay for peak energy prices because they cannot store power from their own roofs."),
        ),
    ),
    PitchField(
        key="solution",
        label=_l("Solution"),
        hint=_l("How does your product solve it, and why is it better than what people use now?"),
        examples=(
            _l("Photo in, booking out: the app recognises amount, tax rate and category and exports it for the tax adviser."),
            _l("A battery pool with automatic trading, so shops pay a flat monthly fee and save about 15 percent on energy."),
        ),
    ),
    PitchField(
        key="market",
        label=_l("Market and customers"),
        hint=_l("Who pays? How many such customers exist and how do you reach them?"),
        examples=(
            _l("About 1.4 million freelancers in Germany. First channel: tax advisers who recommend the app to clients."),
            _l("Around 60,000 small shops with solar roofs. First channel: partnerships with regional energy suppliers."),
        ),
    ),
    PitchField(
        key="traction",
        label=_l("Progress so far"),
        hint=_l("Facts, not plans: users, revenue, pilot customers, letters of intent, prototype milestones."),
        examples=(
            _l("Beta since March with 320 users, 41 of them paying 9 euros a month."),
            _l("Prototype tested in two shops for six months; letter of intent from one regional supplier."),
        ),
    ),
    PitchField(
        key="business_model",
        label=_l("Business model"),
        hint=_l("How do you earn money? Prices, margins, recurring or one-off revenue."),
        examples=(
            _l("Monthly subscription of 9 euros per user; tax advisers get a partner licence of 99 euros per month."),
            _l("Flat fee per shop plus a share of the trading gains."),
        ),
    ),
    PitchField(
        key="team",
        label=_l("Team"),
        hint=_l("Who is behind the project? Roles, relevant experience, what is still missing."),
        examples=(
            _l("Anna (CEO, ten years in accounting software), Ben (CTO, built two mobile apps). We are looking for a sales lead."),
            _l("Two engineers from the energy sector and an advisor who ran a regional utility."),
        ),
    ),
    PitchField(
        key="use_of_funds",
        label=_l("Use of funds"),
        hint=_l("What would you do with the money, and what will be different after 12 months?"),
        examples=(
            _l("150,000 euros: two developers and marketing. Goal: 3,000 paying users in 12 months."),
            _l("400,000 euros: ten more pilot sites and the trading software."),
        ),
    ),
)

DECK_HINT = _l("Optional: your presentation as a PDF (up to 10 MB, 60 pages). Members can download it. Clubi can also read it to answer questions about your project.")
LOGO_HINT = _l("A clear picture of your product, your logo or your team (JPG, PNG or WebP, up to 5 MB).")
