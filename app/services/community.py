"""Сообщество: каталог участников и содержимое карточки. Видны только участники, давшие согласие на каталог."""

from datetime import date

from sqlalchemy import func, or_

from ..extensions import db
from ..models import Profile, User

PAGE_SIZE = 24


def visible_query():
    """Проверенные активные участники, которые сами решили быть в каталоге."""
    return (User.query.join(Profile, Profile.user_id == User.id)
            .filter(Profile.visible_in_directory.is_(True), User.status == "active",
                    User.verification_status == "verified", User.email_confirmed_at.isnot(None)))


def search(q: str = "", city: str = "", industry: str = "", open_to: str = "", page: int = 1):
    """Участники по запросу и фильтрам. Возвращает (список, всего)."""
    query = visible_query()
    q = (q or "").strip()[:80]
    if q:
        like = f"%{q}%"
        query = query.filter(or_(User.first_name.ilike(like), Profile.headline.ilike(like),
                                 Profile.company.ilike(like), Profile.industry.ilike(like),
                                 Profile.city.ilike(like), Profile.expertise.ilike(like),
                                 Profile.q_focus.ilike(like), Profile.q_can_help.ilike(like)))
    if city:
        query = query.filter(func.lower(Profile.city) == city.lower())
    if industry:
        query = query.filter(func.lower(Profile.industry) == industry.lower())
    if open_to:
        # ключи лежат строкой через запятую: границы запятыми исключают частичные совпадения
        query = query.filter(func.concat(",", Profile.open_to, ",").like(f"%,{open_to},%"))
    total = query.count()
    page = max(1, page)
    members = (query.order_by(User.first_name, User.id)
               .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all())
    return members, total


def facets():
    """Города и отрасли, которые есть у участников каталога, для фильтров."""
    def distinct(column):
        rows = (visible_query().with_entities(column).filter(column != "")
                .group_by(column).order_by(func.count().desc(), column).limit(40).all())
        return [value for (value,) in rows]
    return distinct(Profile.city), distinct(Profile.industry)


def counts():
    """(в каталоге, всего проверенных): честная картина для пустого начала."""
    listed = visible_query().count()
    verified = User.query.filter(User.status == "active", User.verification_status == "verified").count()
    return listed, verified


def member_content(user):
    """Опубликованное участником в клубе: стартапы, поездки, встречи, мини-клубы."""
    from ..models.events import Event
    from ..models.miniclubs import MiniClub
    from ..models.startups import Startup
    from ..models.travel import Trip
    from ..utils import utcnow

    startups = (Startup.query.filter_by(owner_id=user.id, status="approved")
                .order_by(Startup.id.desc()).limit(6).all())
    trips = (Trip.query.filter(Trip.author_id == user.id, Trip.status == "open",
                               Trip.ends_on >= date.today()).order_by(Trip.starts_on).limit(6).all())
    events = (Event.query.filter(Event.organizer_id == user.id, Event.status == "open",
                                 Event.ends_at >= utcnow()).order_by(Event.starts_at).limit(6).all())
    clubs = (MiniClub.query.filter_by(organizer_id=user.id, status="open")
             .order_by(MiniClub.id.desc()).limit(6).all())
    return {"startups": startups, "trips": trips, "events": events, "clubs": clubs}
