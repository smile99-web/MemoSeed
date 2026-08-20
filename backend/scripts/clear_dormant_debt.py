"""
One-time reset of dormant FSRS review debt (2026-08-20).

Context
-------
The child has 171 review items "due today" but ~70% are old debt from
mid-July (e.g. "It is cool outside.", next_review_at = 2026-07-17 with
memory_strength = 0.04). These sleepy items are stats-noise: the queue
never reaches them, so they never graduate, so they never leave, so the
queue stays at 170+ forever and the real weak words (red/test/time,
already-reviewed-in-the-past-week) get pushed back behind 120 irrelevant
items.

The FSRS algorithm legitimately pushes dormant low-strength items back to
long intervals (it cannot tell "abandoned" from "sleeping" from "very
weak"). Rather than fix the algorithm, reset what the algorithm cannot
see: bump these items into a fresh 7-day hold and pretend they were
"seen today" so the new 5-dim priority logic catches them again at the
start of the next teaching cycle.

Safety
------
- ONLY touches rows where
  next_review_at < NOW() - INTERVAL '7 days'  (truly abandoned, not
                                          today's due queue)
  AND memory_strength < 0.3                  (still weak, not near-mastered)
- Items at or near graduation (strength >= 0.3) are kept untouched — they
  were about to repeat.
- last_reviewed_at is bumped to NOW() so the new orderings see them as
  "recently seen" and the dirty-fail gate (planned separately) still
  applies.

Run
---
    cd /opt/MemoSeed/backend
    .venv/bin/python -m scripts.clear_dormant_debt --dry-run   # report only
    .venv/bin/python -m scripts.clear_dormant_debt --apply      # actually run
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_ROOT = _THIS_DIR.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select, update  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.learning_item import LearningItem  # noqa: E402
from app.models.memory_state import MemoryState  # noqa: E402

logger = logging.getLogger("clear_dormant_debt")

# Items whose next_review_at is more than this many days in the past are
# considered "abandoned" — they have not been touched by the scheduler in
# at least this many days. The new-word/sprint/reteach wheels will surface
# them again, but only after they leave the immediate due queue.
DORMANT_DAYS = 7
# Items whose memory_strength is at or above this are kept untouched —
# they were about to repeat and might graduate this week.
PROTECTED_MIN_STRENGTH = 0.3
# Where to push them. 7 days lets the new teaching cycle (5 stages) finish
# before they re-enter the queue.
DEFER_DAYS = 7


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset dormant FSRS review debt (one-off, 2026-08-20)")
    parser.add_argument("--dry-run", action="store_true", help="report only, change nothing (default)")
    parser.add_argument("--apply", action="store_true", help="actually update the rows")
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("use either --dry-run or --apply, not both")
    is_apply = args.apply  # default is dry-run
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    now = datetime.now(UTC)
    dormant_cutoff = now - timedelta(days=DORMANT_DAYS)
    defer_until = now + timedelta(days=DEFER_DAYS)

    with SessionLocal() as db:
        stmt = (
            update(MemoryState)
            .where(
                MemoryState.next_review_at < dormant_cutoff,
                MemoryState.memory_strength < PROTECTED_MIN_STRENGTH,
            )
            .values(
                # 推到一周后,让新教学循环先跑完。
                next_review_at=defer_until,
                # 标记"刚看过",配合弱词优先级机制让它们仍能在五维里继续推进。
                last_reviewed_at=now,
            )
        )
        if not is_apply:
            # count via a non-destructive probe
            from sqlalchemy import func as _func
            count = db.scalar(
                select(_func.count()).where(
                    MemoryState.next_review_at < dormant_cutoff,
                    MemoryState.memory_strength < PROTECTED_MIN_STRENGTH,
                )
            )
            joined = db.execute(
                select(MemoryState.learning_item_id, LearningItem.english_text, MemoryState.memory_strength, MemoryState.next_review_at)
                .join(LearningItem, LearningItem.id == MemoryState.learning_item_id)
                .where(
                    MemoryState.next_review_at < dormant_cutoff,
                    MemoryState.memory_strength < PROTECTED_MIN_STRENGTH,
                )
                .order_by(MemoryState.next_review_at.asc())
                .limit(10)
            ).all()
            logger.info("[dry-run] would reset %d dormant words (showing first 10):", int(count or 0))
            for item_id, english, strength, due in joined:
                logger.info("  %-30s strength=%.2f due=%s", english, float(strength), due)
        else:
            result = db.execute(stmt)
            db.commit()
            logger.info("[applied] reset %d dormant-word memory_states to next_review_at = %s", int(result.rowcount or 0), defer_until)


if __name__ == "__main__":
    main()
