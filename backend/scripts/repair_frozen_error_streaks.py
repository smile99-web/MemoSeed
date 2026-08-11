"""
Repair frozen consecutive-error streaks (2026-08-11 状态死锁数据修复).

背景：句子流的成功走 assisted 遥测分支（不碰 FSRS/计数器），失败却经错词
端点照常 +1——单向棘轮让一批词（多为已退休的视觉词）的
consecutive_error_count 永久冻结在高位（生产实测 'with' ce=104、强度
0.85、20 个无提示答对日仍被扣押在 difficult）。derive_word_status 已加
新鲜度门控（>14 天无新测试的 streak 不再一票否决），本脚本把存量计数
重算为真实值：每个词的最近真实测试记录里的连续失败尾部长度。

同时修复 MemoryState（item 级）的同名列——熔断/停放闸口读的是它。

Usage
-----
    python scripts/repair_frozen_error_streaks.py --dry-run          # 只报告
    python scripts/repair_frozen_error_streaks.py                    # 全部用户
    python scripts/repair_frozen_error_streaks.py --user <uuid>
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from collections import Counter

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_THIS_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.memory_state import MemoryState  # noqa: E402
from app.models.review_log import ReviewLog  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.word_memory_state import WordMemoryState  # noqa: E402
from app.services.memory_scheduler import ASSISTED_REVIEW_MODES  # noqa: E402
from app.services.word_memory import derive_word_status, get_recent_word_test_stats  # noqa: E402

# 与 get_recent_word_test_stats 同一口径：真实测试 = word-*/handwriting-* 且非辅助
_REAL_TEST_MODES = (
    ReviewLog.review_mode.like("word-%") | ReviewLog.review_mode.like("handwriting-%")
)


def _trailing_failure_streak(db, user_id, learning_item_id, window: int = 20) -> int | None:
    """连续失败尾部长度。无真实测试记录返回 None（保持现值）。"""
    rows = db.scalars(
        select(ReviewLog.is_correct)
        .where(
            ReviewLog.user_id == user_id,
            ReviewLog.learning_item_id == learning_item_id,
            _REAL_TEST_MODES,
            ReviewLog.review_mode.notin_(sorted(ASSISTED_REVIEW_MODES)),
        )
        .order_by(ReviewLog.reviewed_at.desc())
        .limit(window)
    ).all()
    if not rows:
        return None
    streak = 0
    for is_correct in rows:
        if is_correct:
            break
        streak += 1
    return streak


def repair_user(db, user_id, dry_run: bool = False) -> Counter:
    transitions: Counter = Counter()
    word_states = db.scalars(
        select(WordMemoryState).where(WordMemoryState.user_id == user_id)
    ).all()
    for ws in word_states:
        if ws.learning_item_id is None:
            continue
        recomputed = _trailing_failure_streak(db, user_id, ws.learning_item_id)
        if recomputed is None:
            continue
        old_ce = ws.consecutive_error_count or 0
        if recomputed != old_ce:
            transitions[f"ce {old_ce} -> {recomputed}"] += 1
        # 状态重算（derive 已有新鲜度门控；用重算后的真实 streak 作为输入，
        # dry-run 下用替身避免污染 session）
        probe = copy.copy(ws)
        probe.consecutive_error_count = recomputed
        stats = get_recent_word_test_stats(db, user_id, ws.learning_item_id) if (ws.memory_strength or 0) >= 0.5 else None
        new_status = derive_word_status(probe, *(stats if stats else (None, None, None)))
        if new_status != ws.status:
            transitions[f"status {ws.status} -> {new_status}"] += 1
        if not dry_run:
            ws.consecutive_error_count = recomputed
            ws.status = new_status
            db.add(ws)

    # item 级 MemoryState 同步修复（熔断/停放闸口读 item ce）
    item_states = db.scalars(
        select(MemoryState)
        .join_from(MemoryState, WordMemoryState, WordMemoryState.memory_state_id == MemoryState.id)
        .where(WordMemoryState.user_id == user_id)
    ).all()
    for ms in item_states:
        recomputed = _trailing_failure_streak(db, user_id, ms.learning_item_id)
        if recomputed is not None and recomputed != (ms.consecutive_error_count or 0):
            transitions[f"item_ce {ms.consecutive_error_count} -> {recomputed}"] += 1
            if not dry_run:
                ms.consecutive_error_count = recomputed
                db.add(ms)
    return transitions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", dest="user_id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        user_ids = [args.user_id] if args.user_id else [
            str(row[0]) for row in db.execute(select(User.id)).all()
        ]
        for uid in user_ids:
            transitions = repair_user(db, uid, dry_run=args.dry_run)
            print(f"user {uid}:")
            for label, count in sorted(transitions.items()):
                print(f"  {label}: {count}")
            if not transitions:
                print("  (no changes)")
            if args.dry_run:
                db.rollback()
            else:
                db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
