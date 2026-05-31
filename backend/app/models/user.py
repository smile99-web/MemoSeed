from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.ai_daily_report import AiDailyReport
    from app.models.course import Course
    from app.models.course_package import CoursePackage
    from app.models.daily_plan import DailyPlan
    from app.models.learning_item import LearningItem
    from app.models.refresh_token import RefreshToken
    from app.models.study_time_log import StudyTimeLog
    from app.models.tts_usage_log import TtsUsageLog
    from app.models.user_model_settings import UserModelSettings


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    course_packages: Mapped[list["CoursePackage"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    courses: Mapped[list["Course"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    learning_items: Mapped[list["LearningItem"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    daily_plans: Mapped[list["DailyPlan"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    ai_daily_reports: Mapped[list["AiDailyReport"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    study_time_logs: Mapped[list["StudyTimeLog"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    model_settings: Mapped["UserModelSettings | None"] = relationship(back_populates="user", cascade="all, delete-orphan", uselist=False)
    tts_usage_logs: Mapped[list["TtsUsageLog"]] = relationship(back_populates="user", cascade="all, delete-orphan")
