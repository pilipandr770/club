from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..utils import utcnow

TOPICS = ("games", "sport", "outdoor", "creative", "learning", "family", "neighbours", "other")
JOIN_MODES = ("open", "approval")
CLUB_STATUSES = ("pending", "open", "rejected", "closed")
MEMBER_STATUSES = ("pending", "member", "declined", "left")


class MiniClub(db.Model):
    """Мини-клуб: группа участников по интересу. Организует участник, не клуб; администратор проверяет текст.

    Клуб только знакомит людей: помещений и платежей нет, правила группы задаёт организатор в рамках общих правил
    (см. miniclub_guide.py): без игр на деньги и без услуг, требующих разрешения (например платный присмотр за детьми).
    """

    __tablename__ = "miniclubs"

    id: Mapped[int] = mapped_column(primary_key=True)
    organizer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    topic: Mapped[str] = mapped_column(String(12), default="other")
    description: Mapped[str] = mapped_column(Text, default="")
    audience: Mapped[str] = mapped_column(Text, default="", server_default="")
    schedule: Mapped[str] = mapped_column(Text, default="", server_default="")
    rules: Mapped[str] = mapped_column(Text, default="", server_default="")  # правила группы, их задаёт организатор
    city: Mapped[str] = mapped_column(String(80), default="", server_default="")
    online: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    capacity: Mapped[int | None] = mapped_column(Integer)
    join_mode: Mapped[str] = mapped_column(String(10), default="approval")
    image_key: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(300))
    last_announcement_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    organizer = relationship("User")
    members: Mapped[list["MiniClubMember"]] = relationship(back_populates="club", cascade="all, delete-orphan",
                                                           order_by="MiniClubMember.created_at")
    reports: Mapped[list["MiniClubReport"]] = relationship(back_populates="club", cascade="all, delete-orphan",
                                                           order_by="MiniClubReport.created_at")

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'open', 'rejected', 'closed')", name="ck_miniclubs_status"),
        CheckConstraint("join_mode IN ('open', 'approval')", name="ck_miniclubs_join_mode"),
    )


class MiniClubMember(db.Model):
    """Участие в мини-клубе. Заявка, принятие, выход — один и тот же ряд, история остаётся."""

    __tablename__ = "miniclub_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("miniclubs.id", ondelete="CASCADE"), index=True)
    club: Mapped[MiniClub] = relationship(back_populates="members")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user = relationship("User")
    status: Mapped[str] = mapped_column(String(10), default="pending")
    message: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Участник разрешил организатору увидеть свой e-mail (флажок при заявке)
    share_contact: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (UniqueConstraint("club_id", "user_id", name="uq_miniclub_members_club_user"),)


class MiniClubReport(db.Model):
    """Жалоба участника на мини-клуб. Рассматривает администратор."""

    __tablename__ = "miniclub_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("miniclubs.id", ondelete="CASCADE"), index=True)
    club: Mapped[MiniClub] = relationship(back_populates="reports")
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reporter = relationship("User")
    reason: Mapped[str] = mapped_column(Text)
    handled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
