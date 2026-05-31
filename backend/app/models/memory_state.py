from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.learning_item import LearningItem


class MemoryState(Base):
    __tablename__ = "memory_states"
    __table_args__ = (
        CheckConstraint("interval_days >= 0", name="ck_memory_states_interval_days"),
        CheckConstraint("ease_factor >= 1.3", name="ck_memory_states_ease_factor"),
        CheckConstraint("memory_strength >= 0.0 AND memory_strength <= 1.0", name="ck_memory_states_memory_strength"),
        CheckConstraint("forget_risk >= 0.0 AND forget_risk <= 1.0", name="ck_memory_states_forget_risk"),
        CheckConstraint("repetition_count >= 0", name="ck_memory_states_repetition_count"),
        CheckConstraint("lapse_count >= 0", name="ck_memory_states_lapse_count"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    learning_item_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("learning_items.id", ondelete="CASCADE"), unique=True, nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ease_factor: Mapped[float] = mapped_column(Float, nullable=False, default=2.5)
    memory_strength: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    forget_risk: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    repetition_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lapse_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recall_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hinted_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preview_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    short_term_stability: Mapped[float | None] = mapped_column(Float, nullable=True, default=1.0)
    last_short_term_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    learning_item: Mapped["LearningItem"] = relationship(back_populates="memory_state")
