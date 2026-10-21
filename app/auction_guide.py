"""Подсказки и примеры для лота аукциона. Примеры — наши образцы, не чужие лоты."""

from flask_babel import lazy_gettext as _l

DESCRIPTION_HINT = _l("What is it, what condition is it in, what is included? Honest details earn trust and better bids.")
DESCRIPTION_EXAMPLES = (
    _l("Mid-century armchair, teak and wool cover, 1962. Small scratches on the left armrest, otherwise very good. Height 82 cm."),
    _l("Mountain bike, size M, bought in 2023, about 800 km. New tyres, serviced in September. Includes lock and lights."),
    _l("Signed print, edition 12 of 50, 40 x 60 cm, framed behind glass. Certificate of authenticity included."),
)
PICKUP_HINT = _l("Where is the item and how can it be handed over: pick-up place, shipping, who pays for it. Do not write your private address.")
PICKUP_EXAMPLES = (
    _l("Pick-up in Frankfurt (Nordend) on weekday evenings. Shipping within Germany possible, buyer pays about 35 euros."),
    _l("Hand-over at the club meeting in March or by courier at the buyer's cost."),
)
PHOTO_HINT = _l("A clear photo of the item in good light (JPG, PNG or WebP, up to 5 MB).")

MODE_HELP = _l("Rising price: members bid, the highest bid wins when time runs out. Falling price: the price drops at fixed intervals, the first member who accepts the current price wins.")
RESERVE_HELP = _l("Optional minimum price. If the highest bid stays below it, the lot is not sold. Members only see whether the reserve is reached, not the amount.")
SNIPE_HELP = _l("A bid in the last two minutes extends the auction by two minutes, so nobody loses at the last second.")
DURATIONS = (1, 3, 7, 14, 30)

CONDITION_LABELS = {
    "new": _l("New"),
    "like_new": _l("Like new"),
    "good": _l("Good condition"),
    "used": _l("Used"),
}


def condition_label(value):
    return CONDITION_LABELS.get(value, value)
