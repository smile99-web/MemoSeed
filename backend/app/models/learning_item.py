from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.course import Course
    from app.models.memory_state import MemoryState
    from app.models.mistake_log import MistakeLog
    from app.models.review_log import ReviewLog
    from app.models.user import User


class LearningItem(Base):
    __tablename__ = "learning_items"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=True, index=True)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    english_text: Mapped[str] = mapped_column(Text, nullable=False)
    chinese_text: Mapped[str] = mapped_column(Text, nullable=False)
    phonetic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    difficulty_level: Mapped[int] = mapped_column(nullable=False, default=1)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user: Mapped["User"] = relationship(back_populates="learning_items")
    course: Mapped["Course | None"] = relationship(back_populates="learning_items")
    memory_state: Mapped["MemoryState"] = relationship(back_populates="learning_item", cascade="all, delete-orphan", uselist=False)
    review_logs: Mapped[list["ReviewLog"]] = relationship(back_populates="learning_item", cascade="all, delete-orphan")
    mistake_logs: Mapped[list["MistakeLog"]] = relationship(back_populates="learning_item", cascade="all, delete-orphan")
