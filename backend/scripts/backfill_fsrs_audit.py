"""
Backfill FSRS audit fields for historical review_logs and memory_states.

After running database/init/009_fsrs_audit_fields.sql, the new audit columns
exist but historical rows have NULL. This script fills them in with the
*current* `user_model_settings.fsrsWeights` for each user, plus the algorithm
version and scheduler-type label that would be applied *today*.

This is best-effort: historical rows reflect the weights that were active at
the time of the backfill, not at the time of the review. If the user re-fits
later, the backfill becomes stale and should be re-run.

Usage
-----
    # from project root (Windows or macOS)
    python backend/scripts/backfill_fsrs_audit.py                    # backfill all users
    python backend/scripts/backfill_fsrs_audit.py --user <uuid>      # single user
    python backend/scripts/backfill_fsrs_audit.py --dry-run         # count, no writes

The script connects to the same database the backend uses. The DB URL is
read from `app.core.config.settings.database_url` (same as the rest of the
backend), so set the same env vars you use for `uvicorn`.
"""

from __future__ import annotations

import argparse
import os
import sys
from uuid import UUID

# --- Cross-platform path handling ---------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_THIS_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from sqlalchemy import select, update  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.memory_state import MemoryState  # noqa: E402
from app.models.review_log import ReviewLog  # noqa: E402
from app.services.memory_scheduler import get_scheduler_metadata  # noqa: E402


def backfill_user(db, user_id: UUID, dry_run: bool = False) -> tuple[int, int]:
    """Backfill a single user. Returns (review_logs_updated, memory_states_updated)."""
    metadata = get_scheduler_metadata(db, user_id)
    snapshot = metadata.fsrs_params_snapshot
    scheduler_type = metadata.scheduler_type
    algorithm_version = metadata.algorithm_version

    # review_logs: previous_interval / new_interval / next_review_at
    # depend on memory_state history at the time of the review. We don't have
    # that history here (would need a temporal table or trigger); leave those
    # columns NULL and only fill the scheduler metadata fields.
    rl_stmt = (
        update(ReviewLog)
        .where(ReviewLog.user_id == user_id, ReviewLog.scheduler_type.is_(None))
        .values(
            scheduler_type=scheduler_type,
            algorithm_version=algorithm_version,
            fsrs_params_snapshot=snapshot,
        )
    )

    ms_stmt = (
        update(MemoryState)
        .where(MemoryState.learning_item_id.in_(
            select(ReviewLog.learning_item_id).where(ReviewLog.user_id == user_id).distinct()
        ), MemoryState.scheduler_type.is_(None))
        .values(
            scheduler_type=scheduler_type,
            algorithm_version=algorithm_version,
            fsrs_params_snapshot=snapshot,
        )
    )

    if dry_run:
        # Count rows that *would* be updated.
        from sqlalchemy import func
        rl_count = db.scalar(
            select(func.count(ReviewLog.id))
            .where(ReviewLog.user_id == user_id, ReviewLog.scheduler_type.is_(None))
        ) or 0
        ms_count = db.scalar(
            select(func.count(MemoryState.id))
            .where(MemoryState.learning_item_id.in_(
                select(ReviewLog.learning_item_id).where(ReviewLog.user_id == user_id).distinct()
            ), MemoryState.scheduler_type.is_(None))
        ) or 0
        return int(rl_count), int(ms_count)

    rl_result = db.execute(rl_stmt)
    ms_result = db.execute(ms_stmt)
    return rl_result.rowcount, ms_result.rowcount


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill FSRS audit fields")
    parser.add_argument("--user", type=str, default=None, help="Specific user UUID (default: all users with review_logs)")
    parser.add_argument("--dry-run", action="store_true", help="Count rows without writing")
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN — no writes will be made")
        print()

    db = SessionLocal()
    try:
        if args.user:
            user_ids = [UUID(args.user)]
        else:
            user_ids = [
                row[0] for row in db.execute(select(ReviewLog.user_id).distinct()).all()
            ]
        if not user_ids:
            print("No users found with review_logs.")
            return 0

        print(f"Backfilling {len(user_ids)} user(s)...")
        total_rl = total_ms = 0
        for uid in user_ids:
            rl_count, ms_count = backfill_user(db, uid, dry_run=args.dry_run)
            total_rl += rl_count
            total_ms += ms_count
            print(f"  user={uid}  review_logs={rl_count}  memory_states={ms_count}")
        if not args.dry_run:
            db.commit()
        print()
        print(f"Total: review_logs={total_rl}  memory_states={total_ms}")
        if args.dry_run:
            print("(dry run — re-run without --dry-run to apply)")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
