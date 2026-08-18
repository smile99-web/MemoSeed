"""
Periodic data-lifecycle maintenance for MemoSeed (run daily via cron).

Three bounded-retention jobs:

1. word_review_tasks — delete completed/superseded tasks older than
   TASK_RETENTION_DAYS. Their history already lives in review_logs; pending
   tasks are never touched. (96% of historical tasks end up superseded —
   the table otherwise grows forever.)

2. study_time_logs — roll heartbeats older than HEARTBEAT_RETENTION_DAYS
   into a per-user cumulative offset stored in user_model_settings
   ("studyTimeTotalOffsetSeconds"), then delete the raw rows. The offset is
   computed with the SAME session-window algorithm as the dashboard
   (_active_study_seconds), so "累计学习时长" stays exact while the
   heartbeat table stays bounded. Week/month/year windows (all shorter
   than the retention) remain computed from raw rows.

3. tts_cache — evict cached audio by last-access time: always evict files
   untouched for TTS_HARD_EVICT_DAYS, and when the directory exceeds
   TTS_CACHE_MAX_BYTES evict least-recently-accessed files until under
   TTS_CACHE_TARGET_BYTES. Deleted audio is simply re-synthesized on the
   next request, so eviction can never break playback — worst case one
   extra provider call.

Usage
-----
    cd /opt/MemoSeed/backend
    .venv/bin/python -m scripts.cleanup_old_data            # apply
    .venv/bin/python -m scripts.cleanup_old_data --dry-run  # report only
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_THIS_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from sqlalchemy import delete, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.study_time_log import StudyTimeLog  # noqa: E402
from app.models.user_model_settings import UserModelSettings  # noqa: E402
from app.models.word_review_task import WordReviewTask  # noqa: E402
from app.services.learning_replay import _active_study_seconds  # noqa: E402
from app.services.tts_cache import _get_cache_dir  # noqa: E402

logger = logging.getLogger("cleanup_old_data")

TASK_RETENTION_DAYS = 30
# 400 days, not 90: the dashboard's longest window is 本年 (365d) and every
# window must stay fully inside the raw-row range to remain exact. Only the
# all-time 累计学习时长 reads the rollup offset, so rows older than any
# dashboard window are the only ones rolled up.
HEARTBEAT_RETENTION_DAYS = 400
STUDY_TIME_OFFSET_KEY = "studyTimeTotalOffsetSeconds"

TTS_HARD_EVICT_DAYS = 180
TTS_CACHE_MAX_BYTES = 400 * 1024 * 1024   # start evicting above 400 MB
TTS_CACHE_TARGET_BYTES = 300 * 1024 * 1024  # ...until back under 300 MB


def cleanup_word_review_tasks(dry_run: bool) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=TASK_RETENTION_DAYS)
    with SessionLocal() as db:
        stmt = delete(WordReviewTask).where(
            WordReviewTask.status.in_(("completed", "superseded")),
            WordReviewTask.updated_at < cutoff,
        )
        if dry_run:
            count = db.scalar(
                select(WordReviewTask.id).where(
                    WordReviewTask.status.in_(("completed", "superseded")),
                    WordReviewTask.updated_at < cutoff,
                ).limit(1)
            )
            # count via a cheap EXISTS probe; exact count only when applying
            logger.info("[dry-run] word_review_tasks: old finished tasks %s", "present" if count else "none")
            return 0
        result = db.execute(stmt)
        db.commit()
        return int(result.rowcount or 0)


def cleanup_study_time_logs(dry_run: bool) -> tuple[int, int]:
    """Roll >90d heartbeats into the per-user offset, then delete them."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=HEARTBEAT_RETENTION_DAYS)
    users_with_old_rows = 0
    deleted_total = 0
    with SessionLocal() as db:
        user_ids = [
            row[0]
            for row in db.execute(
                select(StudyTimeLog.user_id).where(StudyTimeLog.recorded_at < cutoff).distinct()
            ).all()
        ]
        for user_id in user_ids:
            # Seconds that the session-window algorithm credits for the
            # [beginning-of-time, cutoff) range — exactly what deleting the
            # raw rows would otherwise subtract from the all-time total.
            offset_seconds = _active_study_seconds(db, user_id, datetime(2000, 1, 1, tzinfo=UTC), cutoff)
            users_with_old_rows += 1
            if dry_run:
                logger.info("[dry-run] user %s: would offset +%ds and purge heartbeats before %s", user_id, offset_seconds, cutoff.date())
                continue
            settings_row = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == user_id))
            if settings_row is None:
                settings_row = UserModelSettings(user_id=user_id, settings={})
                db.add(settings_row)
                db.flush()
            settings = dict(settings_row.settings or {})
            settings[STUDY_TIME_OFFSET_KEY] = int(settings.get(STUDY_TIME_OFFSET_KEY) or 0) + offset_seconds
            settings_row.settings = settings
            db.add(settings_row)
            result = db.execute(
                delete(StudyTimeLog).where(
                    StudyTimeLog.user_id == user_id,
                    StudyTimeLog.recorded_at < cutoff,
                )
            )
            deleted_total += int(result.rowcount or 0)
        if not dry_run:
            db.commit()
    return users_with_old_rows, deleted_total


def cleanup_tts_cache(dry_run: bool) -> tuple[int, int]:
    """Evict old/overflow cache files. Returns (files_deleted, bytes_freed)."""
    cache_dir: Path = _get_cache_dir()
    if not cache_dir.is_dir():
        return 0, 0
    now_ts = datetime.now().timestamp()
    hard_cutoff_ts = now_ts - TTS_HARD_EVICT_DAYS * 86400

    entries: list[tuple[float, int, Path]] = []  # (atime, size, path)
    total_bytes = 0
    for entry in cache_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        entries.append((stat.st_atime, stat.st_size, entry))
        total_bytes += stat.st_size

    files_deleted = 0
    bytes_freed = 0

    def _evict(path: Path, size: int) -> None:
        nonlocal files_deleted, bytes_freed
        if dry_run:
            logger.info("[dry-run] would evict %s (%d bytes)", path.name, size)
        else:
            try:
                path.unlink()
            except OSError:
                return
        files_deleted += 1
        bytes_freed += size

    # Pass 1: hard evict anything untouched for TTS_HARD_EVICT_DAYS.
    remaining: list[tuple[float, int, Path]] = []
    for atime, size, path in entries:
        if atime < hard_cutoff_ts:
            _evict(path, size)
            total_bytes -= size
        else:
            remaining.append((atime, size, path))

    # Pass 2: size cap — evict least-recently-accessed until under target.
    if total_bytes > TTS_CACHE_MAX_BYTES:
        remaining.sort(key=lambda row: row[0])  # oldest access first
        for atime, size, path in remaining:
            if total_bytes <= TTS_CACHE_TARGET_BYTES:
                break
            _evict(path, size)
            total_bytes -= size

    return files_deleted, bytes_freed


def main() -> None:
    parser = argparse.ArgumentParser(description="MemoSeed data-lifecycle cleanup")
    parser.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    tasks_deleted = cleanup_word_review_tasks(args.dry_run)
    users_rolled, heartbeats_deleted = cleanup_study_time_logs(args.dry_run)
    cache_files, cache_bytes = cleanup_tts_cache(args.dry_run)

    logger.info(
        "%s word_review_tasks deleted=%d; study_time users rolled=%d heartbeats deleted=%d; tts_cache evicted=%d files (%.1f MB)",
        "[dry-run]" if args.dry_run else "[applied]",
        tasks_deleted,
        users_rolled,
        heartbeats_deleted,
        cache_files,
        cache_bytes / 1024 / 1024,
    )


if __name__ == "__main__":
    main()
