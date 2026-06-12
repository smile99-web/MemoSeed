"""Learning Replay System: event recording, heatmap, hour/minute replay."""

from datetime import UTC, date, datetime, timedelta
from typing import Iterable
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from app.models.learning_event import LearningEvent, LearningMinuteStat
from app.models.review_log import ReviewLog
from app.models.mistake_log import MistakeLog
from app.models.learning_item import LearningItem
from app.models.user import User

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

# Color intensity bands (minutes of study per day)
COLOR_LEVELS = [
    (0, 0, "#ebedf0"),       # gray
    (1, 15, "#9be9a8"),      # light green
    (16, 30, "#40c463"),     # mid green
    (31, 45, "#30a14e"),     # dark green
    (46, 9999, "#216e39"),   # deepest green
]


def color_for_minutes(minutes: int) -> str:
    for lo, hi, color in COLOR_LEVELS:
        if lo <= minutes <= hi:
            return color
    return COLOR_LEVELS[0][2]


def categorize_review_mode(review_mode: str | None) -> str:
    """Map review_mode to one of: spelling, english_to_chinese, chinese_to_english, phrase, sentence, other."""
    if not review_mode:
        return "other"
    rm = review_mode.lower()
    if "chinese_to_english" in rm or "listen_spell" in rm or "hidden_recall" in rm or "missing_letter" in rm:
        return "chinese_to_english"
    if "english_to_chinese" in rm or "listen_choose" in rm or "match_translation" in rm:
        return "english_to_chinese"
    if "sentence" in rm:
        return "sentence"
    if "phrase" in rm:
        return "phrase"
    if "spell" in rm or "recall" in rm or "preview" in rm or "context" in rm or "hinted" in rm:
        return "spelling"
    return "other"


def record_learning_event(
    db: Session,
    user_id: UUID,
    review_log: ReviewLog,
    learning_item: LearningItem | None,
    duration_ms: int = 0,
) -> LearningEvent | None:
    """Persist a single review attempt as a learning event + increment minute stat.

    Returns None if the review was unscored (no real attempt).
    """
    if review_log.score is None or review_log.reviewed_at is None:
        return None

    local_dt = review_log.reviewed_at.astimezone(LOCAL_TIMEZONE)
    event = LearningEvent(
        user_id=user_id,
        learning_item_id=review_log.learning_item_id,
        review_log_id=review_log.id,
        occurred_at=review_log.reviewed_at,
        event_date=local_dt.date(),
        event_hour=local_dt.hour,
        event_minute=local_dt.minute,
        event_week=local_dt.isocalendar()[1],
        event_year=local_dt.year,
        item_type=(learning_item.item_type if learning_item else "word"),
        review_mode=review_log.review_mode,
        is_correct=review_log.is_correct,
        score=review_log.score,
        english_text=(learning_item.english_text if learning_item else ""),
        chinese_text=(learning_item.chinese_text if learning_item else None),
        response_text=review_log.response_text,
        duration_ms=max(0, duration_ms),
        error_type=review_log.error_type,
    )
    db.add(event)
    db.flush()
    _increment_minute_stat(db, event)
    return event


def _increment_minute_stat(db: Session, event: LearningEvent) -> None:
    """Upsert pre-aggregated minute stats — fast heatmap/histogram queries."""
    category = categorize_review_mode(event.review_mode)
    stmt = select(LearningMinuteStat).where(
        LearningMinuteStat.user_id == event.user_id,
        LearningMinuteStat.stat_date == event.event_date,
        LearningMinuteStat.stat_hour == event.event_hour,
        LearningMinuteStat.stat_minute == event.event_minute,
    )
    stat = db.scalar(stmt)
    if stat is None:
        stat = LearningMinuteStat(
            user_id=event.user_id,
            stat_date=event.event_date,
            stat_hour=event.event_hour,
            stat_minute=event.event_minute,
            total_events=0,
            spelling_events=0,
            english_to_chinese_events=0,
            chinese_to_english_events=0,
            phrase_events=0,
            sentence_events=0,
            correct_events=0,
            incorrect_events=0,
            study_duration_ms=0,
        )
        db.add(stat)
        db.flush()
    stat.total_events = (stat.total_events or 0) + 1
    if category == "spelling":
        stat.spelling_events = (stat.spelling_events or 0) + 1
    elif category == "english_to_chinese":
        stat.english_to_chinese_events = (stat.english_to_chinese_events or 0) + 1
    elif category == "chinese_to_english":
        stat.chinese_to_english_events = (stat.chinese_to_english_events or 0) + 1
    elif category == "phrase":
        stat.phrase_events = (stat.phrase_events or 0) + 1
    elif category == "sentence":
        stat.sentence_events = (stat.sentence_events or 0) + 1
    if event.is_correct:
        stat.correct_events = (stat.correct_events or 0) + 1
    else:
        stat.incorrect_events = (stat.incorrect_events or 0) + 1
    stat.study_duration_ms = (stat.study_duration_ms or 0) + event.duration_ms
    stat.updated_at = datetime.now(UTC)


def build_heatmap(db: Session, user_id: UUID, year: int | None = None) -> dict:
    """Return year-view heatmap: minutes studied per day for the given year."""
    target_year = year or datetime.now(LOCAL_TIMEZONE).year
    start = date(target_year, 1, 1)
    end = date(target_year, 12, 31)

    rows = db.execute(
        select(
            LearningMinuteStat.stat_date,
            func.sum(LearningMinuteStat.study_duration_ms).label("ms"),
            func.sum(LearningMinuteStat.total_events).label("events"),
        )
        .where(
            LearningMinuteStat.user_id == user_id,
            LearningMinuteStat.stat_date >= start,
            LearningMinuteStat.stat_date <= end,
        )
        .group_by(LearningMinuteStat.stat_date)
    ).all()
    days = [
        {
            "date": d.isoformat(),
            "minutes": round((ms or 0) / 60000, 1),
            "events": int(events or 0),
            "color": color_for_minutes(round((ms or 0) / 60000)),
        }
        for d, ms, events in rows
    ]
    total_minutes = sum(d["minutes"] for d in days)
    total_days = sum(1 for d in days if d["events"] > 0)
    return {
        "year": target_year,
        "days": days,
        "total_minutes": round(total_minutes, 1),
        "active_days": total_days,
    }


def build_day_detail(db: Session, user_id: UUID, day: date) -> dict:
    """All events for a single day, grouped by hour."""
    rows = db.execute(
        select(LearningMinuteStat)
        .where(
            LearningMinuteStat.user_id == user_id,
            LearningMinuteStat.stat_date == day,
        )
        .order_by(LearningMinuteStat.stat_hour.asc(), LearningMinuteStat.stat_minute.asc())
    ).scalars().all()
    total_events = sum(r.total_events for r in rows)
    total_correct = sum(r.correct_events for r in rows)
    total_ms = sum(r.study_duration_ms for r in rows)
    total_minutes = round(total_ms / 60000, 1)
    accuracy = round((total_correct / total_events) * 100, 1) if total_events else 0.0
    mistake_count = db.scalar(
        select(func.count(MistakeLog.id)).where(
            MistakeLog.user_id == user_id,
            MistakeLog.occurred_at >= datetime.combine(day, datetime.min.time(), tzinfo=LOCAL_TIMEZONE),
            MistakeLog.occurred_at < datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=LOCAL_TIMEZONE),
        )
    ) or 0

    hours_map: dict[int, list[dict]] = {}
    for r in rows:
        hours_map.setdefault(r.stat_hour, []).append({
            "minute": r.stat_minute,
            "spelling": r.spelling_events,
            "english_to_chinese": r.english_to_chinese_events,
            "chinese_to_english": r.chinese_to_english_events,
            "phrase": r.phrase_events,
            "sentence": r.sentence_events,
            "total": r.total_events,
            "correct": r.correct_events,
            "incorrect": r.incorrect_events,
            "accuracy": round((r.correct_events / r.total_events) * 100, 1) if r.total_events else 0.0,
        })

    hours = [
        {
            "hour": h,
            "label": f"{h:02d}:00-{h+1:02d}:00",
            "minutes": minutes,
        }
        for h, minutes in sorted(hours_map.items())
    ]
    return {
        "date": day.isoformat(),
        "study_minutes": total_minutes,
        "total_events": total_events,
        "accuracy": accuracy,
        "mistake_count": mistake_count,
        "hours": hours,
    }


def build_hour_detail(db: Session, user_id: UUID, day: date, hour: int) -> dict:
    """One minute breakdown for a given hour."""
    rows = db.execute(
        select(LearningMinuteStat)
        .where(
            LearningMinuteStat.user_id == user_id,
            LearningMinuteStat.stat_date == day,
            LearningMinuteStat.stat_hour == hour,
        )
        .order_by(LearningMinuteStat.stat_minute.asc())
    ).scalars().all()
    return {
        "date": day.isoformat(),
        "hour": hour,
        "minutes": [
            {
                "minute": r.stat_minute,
                "spelling": r.spelling_events,
                "english_to_chinese": r.english_to_chinese_events,
                "chinese_to_english": r.chinese_to_english_events,
                "phrase": r.phrase_events,
                "sentence": r.sentence_events,
                "total": r.total_events,
                "correct": r.correct_events,
                "incorrect": r.incorrect_events,
                "accuracy": round((r.correct_events / r.total_events) * 100, 1) if r.total_events else 0.0,
                "study_seconds": round(r.study_duration_ms / 1000, 1),
            }
            for r in rows
        ],
    }


def build_minute_events(db: Session, user_id: UUID, day: date, hour: int, minute: int) -> list[dict]:
    """All learning events for a specific minute."""
    rows = db.execute(
        select(LearningEvent)
        .where(
            LearningEvent.user_id == user_id,
            LearningEvent.event_date == day,
            LearningEvent.event_hour == hour,
            LearningEvent.event_minute == minute,
        )
        .order_by(LearningEvent.occurred_at.asc())
    ).scalars().all()
    return [
        {
            "id": str(r.id),
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            "english_text": r.english_text,
            "chinese_text": r.chinese_text,
            "response_text": r.response_text,
            "is_correct": r.is_correct,
            "score": r.score,
            "review_mode": r.review_mode,
            "duration_ms": r.duration_ms,
            "error_type": r.error_type,
        }
        for r in rows
    ]


def backfill_events_from_review_logs(db: Session, user_id: UUID) -> int:
    """One-time migration: convert existing review_logs into learning_events + minute stats."""
    existing = db.scalar(select(func.count(LearningEvent.id)).where(LearningEvent.user_id == user_id)) or 0
    if existing > 0:
        return 0
    logs = db.execute(
        select(ReviewLog, LearningItem)
        .outerjoin(LearningItem, LearningItem.id == ReviewLog.learning_item_id)
        .where(ReviewLog.user_id == user_id, ReviewLog.reviewed_at.isnot(None))
        .order_by(ReviewLog.reviewed_at.asc())
    ).all()
    count = 0
    for log, item in logs:
        if log.score is None:
            continue
        record_learning_event(db, user_id, log, item, duration_ms=0)
        count += 1
    db.commit()
    return count
