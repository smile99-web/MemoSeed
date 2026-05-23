from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.user import User


class AiDailyReport(Base):
    __tablename__ = "ai_daily_reports"
    __table_args__ = (
        UniqueConstraint("user_id", "report_date", name="uq_ai_daily_reports_user_date"),
        CheckConstraint("accuracy_rate >= 0.0 AND accuracy_rate <= 1.0", name="ck_ai_daily_reports_accuracy_rate"),
        CheckConstraint("spelling_error_rate >= 0.0 AND spelling_error_rate <= 1.0", name="ck_ai_daily_reports_spelling_error_rate"),
        CheckConstraint("sentence_error_rate >= 0.0 AND sentence_error_rate <= 1.0", name="ck_ai_daily_reports_sentence_error_rate"),
        CheckConstraint("study_duration_minutes >= 0", name="ck_ai_daily_reports_study_duration_minutes"),
        CheckConstraint("review_backlog_count >= 0", name="ck_ai_daily_reports_review_backlog_count"),
        CheckConstraint("high_forget_risk_count >= 0", name="ck_ai_daily_reports_high_forget_risk_count"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    accuracy_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    spelling_error_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sentence_error_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    study_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_backlog_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    high_forget_risk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_day_strategy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped["User"] = relationship(back_populates="ai_daily_reports")
