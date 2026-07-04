"""Points & reward system for motivating young English learners."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.learning_item import LearningItem
from app.models.user_points import PointsLog, UserPoints

# Daily awards are by LOCAL day, not UTC. The product's intent is "the
# child gets credit for studying today (in their timezone)", so we use
# the configured local timezone throughout this module. On a VPS
# deployed in Asia/Shanghai this resolves to CST; on a US server it
# would follow the user's expectation. The previous implementation
# used date.today() (naive local) but compared against last_awarded
# converted to UTC, which broke the "once per local day" contract on
# any non-UTC server (e.g. 8am Shanghai vs 0am UTC).
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

# --- Points rules ---
POINTS_CORRECT_NO_HINT = 10      # correct spelling without hint
POINTS_CORRECT_HINTED = 5        # correct with hint
POINTS_CORRECT_PREVIEW = 3       # correct after word preview
POINTS_PERFECT_SENTENCE = 20     # all words correct in a sentence
POINTS_STREAK_5 = 15             # 5 correct in a row
POINTS_STREAK_10 = 30            # 10 correct in a row
POINTS_STREAK_15 = 50            # 15 correct in a row
POINTS_DAILY_STUDY = 30          # study at least once today
POINTS_COURSE_COMPLETE = 100     # finish a course
POINTS_MASTER_WORD = 20          # master a difficult word (3 consecutive correct)
POINTS_WRONG = -2                # wrong answer (minor penalty)

# --- Levels ---
LEVEL_THRESHOLDS = [
    (0,   1, "🌱 初学者"),
    (100, 2, "🌿 小达人"),
    (300, 3, "🌳 小能手"),
    (600, 4, "⭐ 小学霸"),
    (1000, 5, "🏆 小天才"),
    (2000, 6, "👑 英语大师"),
]


def _get_or_create_points(db: Session, user_id: UUID) -> UserPoints:
    points = db.scalar(select(UserPoints).where(UserPoints.user_id == user_id))
    if points is None:
        points = UserPoints(user_id=user_id)
        db.add(points)
        db.flush()
    return points


def compute_level(total_points: int) -> tuple[int, str]:
    """Return (level_number, level_label) for the given total points."""
    current_level, current_label = 1, "🌱 初学者"
    for threshold, level_num, label in LEVEL_THRESHOLDS:
        if total_points >= threshold and level_num >= current_level:
            current_level = level_num
            current_label = label
    return current_level, current_label


def award_points(
    db: Session,
    user_id: UUID,
    points_change: int,
    reason: str,
    detail: str | None = None,
    learning_item_id: UUID | None = None,
) -> dict[str, Any]:
    """Award (or deduct) points and return the updated state."""
    if points_change == 0:
        return _points_response(db, user_id)

    user_points = _get_or_create_points(db, user_id)
    old_total = user_points.total_points
    old_level = user_points.level

    user_points.total_points = max(0, old_total + points_change)
    new_level, new_label = compute_level(user_points.total_points)
    level_up = new_level > old_level
    user_points.level = new_level

    # Log the transaction
    log = PointsLog(
        user_id=user_id,
        points_changed=points_change,
        reason=reason,
        detail=detail,
        learning_item_id=learning_item_id,
    )
    db.add(log)
    db.add(user_points)

    return {
        "total_points": user_points.total_points,
        "level": user_points.level,
        "level_label": new_label,
        "points_changed": points_change,
        "level_up": level_up,
        "old_level": old_level if level_up else None,
        "new_level": new_level if level_up else None,
    }


def award_daily_study_points(db: Session, user_id: UUID) -> dict[str, Any]:
    """Award points for today's study session (once per local day).

    Compares the user's last-awarded date against the LOCAL timezone
    (Asia/Shanghai), not UTC. Without this fix, on a UTC server the
    child could not claim the day's points until 8am local time, and
    the streak counters on UserPoints were never actually incremented
    (the previous 'yesterday' calculation always yielded today).
    """
    user_points = _get_or_create_points(db, user_id)
    today = datetime.now(LOCAL_TIMEZONE).date()

    # Compute the date of the previous award in LOCAL time. The
    # last_awarded_date column is stored as a tz-aware UTC timestamp
    # (server_default=now() on PostgreSQL writes timestamptz); convert
    # to local before extracting the date.
    last_local_date: date | None = None
    if user_points.last_awarded_date is not None:
        last_dt = user_points.last_awarded_date
        if last_dt.tzinfo is None:
            # Defensive: legacy naive rows assume UTC.
            last_dt = last_dt.replace(tzinfo=UTC)
        last_local_date = last_dt.astimezone(LOCAL_TIMEZONE).date()

    # Already awarded today — no-op, but still return updated state so
    # the caller can confirm "already_awarded" without an extra round-trip.
    if last_local_date is not None and last_local_date >= today:
        return {"already_awarded": True, **award_points(db, user_id, 0, "daily_study")}

    # Update streak.
    #   - Never awarded before OR gap > 1 day → reset streak to 1
    #   - Awarded yesterday → increment streak
    #   - Awarded today (caught above by early return) → no change
    if last_local_date is None or (today - last_local_date) > timedelta(days=1):
        new_streak = 1
    else:
        # last_local_date is exactly yesterday (today-1)
        new_streak = (user_points.current_streak_days or 0) + 1
    user_points.current_streak_days = new_streak
    if new_streak > (user_points.longest_streak_days or 0):
        user_points.longest_streak_days = new_streak

    result = award_points(db, user_id, POINTS_DAILY_STUDY, "daily_study", f"每日学习奖励 +{POINTS_DAILY_STUDY}")
    user_points.last_awarded_date = datetime.now(UTC)
    db.add(user_points)
    return result


def get_points_summary(db: Session, user_id: UUID) -> dict[str, Any]:
    """Get the user's current points, level, and recent history."""
    user_points = _get_or_create_points(db, user_id)

    # Recent 20 transactions
    recent_logs = db.scalars(
        select(PointsLog)
        .where(PointsLog.user_id == user_id)
        .order_by(PointsLog.created_at.desc())
        .limit(20)
    ).all()

    # Today's earned
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_points = db.scalar(
        select(func.sum(PointsLog.points_changed)).where(
            PointsLog.user_id == user_id,
            PointsLog.created_at >= today_start,
            PointsLog.points_changed > 0,
        )
    ) or 0

    # Next level progress
    next_threshold = None
    for threshold, level_num, label in LEVEL_THRESHOLDS:
        if threshold > user_points.total_points:
            next_threshold = threshold
            break

    progress_pct = 0
    if next_threshold:
        prev_threshold = 0
        for t, _, _ in LEVEL_THRESHOLDS:
            if t < next_threshold and t <= user_points.total_points:
                prev_threshold = t
        if next_threshold > prev_threshold:
            progress_pct = round((user_points.total_points - prev_threshold) / (next_threshold - prev_threshold) * 100)

    _, level_label = compute_level(user_points.total_points)

    return {
        "total_points": user_points.total_points,
        "level": user_points.level,
        "level_label": level_label,
        "today_points": int(today_points),
        "next_level_points": next_threshold,
        "next_level_progress_pct": progress_pct,
        "recent_logs": [
            {
                "points_changed": log.points_changed,
                "reason": log.reason,
                "detail": log.detail,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in recent_logs
        ],
    }


def _points_response(db: Session, user_id: UUID) -> dict[str, Any]:
    """Internal helper — return current state without changing points."""
    user_points = _get_or_create_points(db, user_id)
    _, label = compute_level(user_points.total_points)
    return {
        "total_points": user_points.total_points,
        "level": user_points.level,
        "level_label": label,
        "points_changed": 0,
        "level_up": False,
    }
