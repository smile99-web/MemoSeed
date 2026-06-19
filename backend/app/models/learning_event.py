from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class LearningEvent(Base):
    __tablename__ = "learning_events"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    learning_item_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("learning_items.id", ondelete="SET NULL"), nullable=True)
    review_log_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("review_logs.id", ondelete="SET NULL"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(), index=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    event_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    event_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    event_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    event_year: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False)
    review_mode: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    english_text: Mapped[str] = mapped_column(Text, nullable=False)
    chinese_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now())


class LearningMinuteStat(Base):
    __tablename__ = "learning_minute_stats"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stat_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    stat_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    stat_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    total_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spelling_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    english_to_chinese_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chinese_to_english_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phrase_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sentence_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incorrect_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    study_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now())
