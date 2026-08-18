from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint, func
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
    # ── 二期改造(2026-08-18): 五维独立进度 ──────────────────────────
    # 一个词的"会"拆成五个可独立毕业的维度:听得出/认得义/读得准/写得出/
    # 用得对。*_days 是"独立日期数"(每天最多 +1,跨天证明间隔效应);
    # speak 只需通过一次(发音是门槛性能力,不需要跨天)。
    # 某个维度失败只回炉该维度,其余维度进度保留——简单词不再因单一维度
    # 的失误被全维度反复纠缠(家长反馈问题2的结构性修复)。
    dim_listen_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dim_listen_last_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    dim_meaning_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dim_meaning_last_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    dim_speak_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dim_spell_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dim_spell_last_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    dim_use_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dim_use_last_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 最近一次失败发生在哪个维度(listen/meaning/speak/spell/use)——调度
    # 据此把该维度的回炉练习排到最前。
    dim_last_failed: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
