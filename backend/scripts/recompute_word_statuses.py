"""
Recompute word_memory_states.status with the P8 recency-gated rules.

P8 added a recent-accuracy gate to derive_word_status: cumulative counters
never decrease, so chronically-failing words with a lucky history (e.g.
'early': 152 lapses, 33% accuracy over its last 10 reviews) displayed as
"mastered" and were parked for 30 days by park_mastered_words while still
failing daily. Statuses are recomputed live on every review sync, but words
that are not reviewed for a while keep the stale label — and park_mastered_words
now trusts `status == "mastered"` as its sole criterion.

Run ONCE after deploying the P8 backend so every existing row is aligned
with the new rules before the first queue build.

Usage
-----
    # from the backend root on the VPS (venv active)
    python scripts/recompute_word_statuses.py                # all users
    python scripts/recompute_word_statuses.py --user <uuid>  # single user
    python scripts/recompute_word_statuses.py --dry-run      # report only

The script connects to the same database the backend uses (settings from
app.core.config), so run it with the same env vars as uvicorn.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

# --- Cross-platform path handling ---------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_THIS_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.word_memory_state import WordMemoryState  # noqa: E402
from app.services.word_memory import derive_word_status, get_recent_word_test_stats  # noqa: E402


def recompute_user(db, user_id, dry_run: bool = False) -> Counter:
    """Recompute every word_memory_state status for one user.

    Returns a Counter of old_status -> new_status transitions.
    """
    transitions: Counter = Counter()
    word_states = db.scalars(
        select(WordMemoryState).where(WordMemoryState.user_id == user_id)
    ).all()
    for word_state in word_states:
        # Mirror the live sync logic (word_memory.sync_word_memory_from_review):
        # only spend the recency query on words near mastery territory.
        # P16/P21: stats are REAL-test only (assisted phases excluded) and now
        # include the spaced-proof signals (distinct correct days, test count)
        # the P21 graduation gate requires.
        recent_accuracy = None
        recent_correct_days = None
        recent_test_count = None
        if (word_state.memory_strength or 0) >= 0.5:
            stats = get_recent_word_test_stats(db, user_id, word_state.learning_item_id)
            if stats is not None:
                recent_accuracy, recent_correct_days, recent_test_count = stats
        new_status = derive_word_status(word_state, recent_accuracy, recent_correct_days, recent_test_count)
        if new_status != word_state.status:
            transitions[f"{word_state.status} -> {new_status}"] += 1
            if not dry_run:
                word_state.status = new_status
                db.add(word_state)
        else:
            transitions[f"unchanged:{new_status}"] += 1
    if not dry_run:
        db.commit()
    return transitions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", dest="user_id", default=None, help="single user UUID")
    parser.add_argument("--dry-run", action="store_true", help="report transitions without writing")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.user_id:
            user_ids = [args.user_id]
        else:
            user_ids = [str(row) for row in db.scalars(select(User.id)).all()]
        for user_id in user_ids:
            transitions = recompute_user(db, user_id, dry_run=args.dry_run)
            print(f"user {user_id}:")
            for label, count in sorted(transitions.items()):
                print(f"  {label}: {count}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
