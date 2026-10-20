from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..utils import utcnow

INQUIRY_STATUSES = ("new", "contacted", "won", "lost")


class Service(db.Model):
    """Услуга партнёра клуба. Контакт партнёра (provider_email) участникам не показывается."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    benefits: Mapped[str] = mapped_column(Text, default="")  # одна строка — одна выгода
    member_benefit: Mapped[str] = mapped_column(String(300), default="")  # условия для участников клуба
    price_hint: Mapped[str] = mapped_column(String(120), default="")
    provider_name: Mapped[str] = mapped_column(String(160), default="")
    provider_email: Mapped[str] = mapped_column(String(255), default="")  # только для писем партнёру
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    inquiries: Mapped[list["ServiceInquiry"]] = relationship(back_populates="service",
                                                             cascade="all, delete-orphan")

    @property
    def benefits_list(self) -> list[str]:
        return [line.strip() for line in (self.benefits or "").splitlines() if line.strip()]


class ServiceInquiry(db.Model):
    """Запрос участника партнёру. Контакт участника уходит партнёру только при явном согласии."""

    __tablename__ = "service_inquiries"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), index=True)
    service: Mapped[Service] = relationship(back_populates="inquiries")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user = relationship("User")
    message: Mapped[str] = mapped_column(Text, default="")
    share_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        CheckConstraint("status IN ('new', 'contacted', 'won', 'lost')", name="ck_service_inquiry_status"),
    )
