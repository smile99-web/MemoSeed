from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class GeneratedSentence(Base):
    __tablename__ = "generated_sentences"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    focus_words_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    difficulty_level: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    english_text: Mapped[str] = mapped_column(Text, nullable=False)
    chinese_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
