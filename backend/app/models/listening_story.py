from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ListeningStory(Base):
    """AI 生成的双语听力故事（听力故事模块）。

    sentences 是按播放顺序排列的 [{"en": "...", "zh": "..."}] 列表：
    前端逐句先播英文 TTS、再播中文 TTS，一篇播完随机切下一篇。
    故事用词来自孩子平时练习的单词（见 services/listening_stories.py）。
    """

    __tablename__ = "listening_stories"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    theme: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    sentences: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
