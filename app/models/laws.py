"""Корпус федерального права (gesetze-im-internet.de) внутри клуба: тексты, куски для поиска, векторы.

Данные публичные (amtliche Werke, § 5 UrhG) и целиком восстанавливаются командой `flask laws-sync`,
поэтому таблицы law_* не входят в бэкапы (pg_dump -T 'law_*'). Векторы — pgvector, поиск по косинусу;
для запасного полнотекстового поиска у кусков есть сгенерированный tsvector (немецкая морфология).
"""

from datetime import datetime

from sqlalchemy import Computed, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from ..extensions import db
from ..utils import utcnow

VECTOR_DIM = 1024  # mistral-embed


class Vector(UserDefinedType):
    """Тип pgvector без внешнего пакета: значение — список чисел, в БД уходит как текст '[1,2,...]'."""

    cache_ok = True

    def __init__(self, dim: int = VECTOR_DIM):
        self.dim = dim

    def get_col_spec(self, **kw):
        return f"vector({self.dim})"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            return "[" + ",".join(f"{float(x):.7g}" for x in value) + "]"
        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None:
                return None
            return [float(x) for x in str(value).strip("[]").split(",") if x]
        return process


class Law(db.Model):
    """Один закон или акт."""

    __tablename__ = "law_docs"

    slug: Mapped[str] = mapped_column(String(120), primary_key=True)
    abbreviation: Mapped[str] = mapped_column(String(120), default="")
    title: Mapped[str] = mapped_column(String(400), default="")
    category: Mapped[str] = mapped_column(String(80), default="Unclassified", index=True)
    source_url: Mapped[str] = mapped_column(String(300), default="")
    stand: Mapped[list] = mapped_column(JSON, default=list)
    sha256: Mapped[str] = mapped_column(String(64), default="")  # хеш zip-файла источника
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LawSection(db.Model):
    """Параграф целиком: его показываем пользователю дословно."""

    __tablename__ = "law_sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(ForeignKey("law_docs.slug", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    enbez: Mapped[str] = mapped_column(String(80), default="")
    titel: Mapped[str] = mapped_column(String(400), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    stand: Mapped[list] = mapped_column(JSON, default=list)

    __table_args__ = (Index("ix_law_sections_slug_enbez", "slug", "enbez"),)


class LawChunk(db.Model):
    """Кусок параграфа для поиска (длинные параграфы режутся по 4000 знаков). id — как в старом сервисе."""

    __tablename__ = "law_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(ForeignKey("law_docs.slug", ondelete="CASCADE"), index=True)
    enbez: Mapped[str] = mapped_column(String(80), default="")
    titel: Mapped[str] = mapped_column(String(400), default="")
    part: Mapped[str] = mapped_column(String(20), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    text_hash: Mapped[str] = mapped_column(String(64), default="")
    embedding = mapped_column(Vector(VECTOR_DIM), nullable=True)
    search_vector = mapped_column(
        TSVECTOR, Computed("to_tsvector('german', coalesce(titel, '') || ' ' || coalesce(text, ''))", persisted=True))

    __table_args__ = (Index("ix_law_chunks_search", "search_vector", postgresql_using="gin"),)


class LawSyncState(db.Model):
    """Одна строка: когда последний раз синхронизировали корпус и что получилось."""

    __tablename__ = "law_sync_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
