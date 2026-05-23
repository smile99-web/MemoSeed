from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.user import User


class DailyPlan(Base):
    __tablename__ = "daily_plans"
    __table_args__ = (
        UniqueConstraint("user_id", "plan_date", name="uq_daily_plans_user_date"),
        CheckConstraint("warmup_review_minutes >= 0", name="ck_daily_plans_warmup_review_minutes"),
        CheckConstraint("new_learning_minutes >= 0", name="ck_daily_plans_new_learning_minutes"),
        CheckConstraint("sentence_training_minutes >= 0", name="ck_daily_plans_sentence_training_minutes"),
        CheckConstraint("mistake_reinforcement_minutes >= 0", name="ck_daily_plans_mistake_reinforcement_minutes"),
        CheckConstraint("new_word_limit >= 0", name="ck_daily_plans_new_word_limit"),
        CheckConstraint("new_phrase_limit >= 0", name="ck_daily_plans_new_phrase_limit"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    warmup_review_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    new_learning_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    sentence_training_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    mistake_reinforcement_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    new_word_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_phrase_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strategy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user: Mapped["User"] = relationship(back_populates="daily_plans")
