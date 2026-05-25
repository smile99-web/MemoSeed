from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class WordReviewTask(Base):
    __tablename__ = "word_review_tasks"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    word_memory_state_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("word_memory_states.id", ondelete="CASCADE"), nullable=True, index=True)
    learning_item_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("learning_items.id", ondelete="SET NULL"), nullable=True, index=True)
    word: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str] = mapped_column(Text, nullable=False)
    choices: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    source: Mapped[str] = mapped_column(String(120), nullable=False, default="word-memory")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
