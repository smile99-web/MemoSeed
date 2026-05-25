from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class WordMemoryState(Base):
    __tablename__ = "word_memory_states"
    __table_args__ = (UniqueConstraint("user_id", "word", name="uq_word_memory_states_user_word"),)

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    word: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    learning_item_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("learning_items.id", ondelete="SET NULL"), nullable=True, index=True)
    memory_state_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("memory_states.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="teaching")
    memory_strength: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    forget_risk: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    consecutive_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recall_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hinted_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preview_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hidden_recall_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    no_hint_correct_date_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_no_hint_correct_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_answer_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_type_counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    task_type_counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    next_micro_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    micro_review_stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
