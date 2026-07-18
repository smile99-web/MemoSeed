from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.course import Course
from app.models.course_completion_log import CourseCompletionLog
from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.review_log import ReviewLog
from app.models.study_time_log import StudyTimeLog
from app.models.user import User
from app.models.user_points import PointsLog
from app.models.word_memory_state import WordMemoryState
from app.models.word_review_task import WordReviewTask
from app.schemas.memory import CourseCompletionRequest, CourseProgressStats, FsrsFitResponse, MemoryDashboardResponse, MemoryScheduleResponse, MemoryStateRead, PointsAwardRequest, PointsSummaryResponse, ReviewForecastResponse, ReviewScoreRequest, StudyTimeLogRequest, TodayProgressResponse
from app.services.memory_dashboard import build_memory_dashboard, build_review_forecast, build_today_progress, check_and_generate_daily_report
from app.services.ai_review_advisor import generate_review_advice, get_todays_recommendations
from app.schemas.review import MistakeLogRead, ReviewLogRead
from app.services.fsrs_fitting import fit_user_fsrs_parameters
from app.services.learning_replay import record_learning_event
from app.services.memory_scheduler import ASSISTED_REVIEW_MODES, schedule_memory_review

router = APIRouter()


@router.get("/dashboard", response_model=MemoryDashboardResponse)
def get_memory_dashboard(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    course_id: UUID | None = None,
) -> MemoryDashboardResponse:
    return build_memory_dashboard(db, current_user.id, course_id=course_id)


@router.post("/focus-rotate")
def rotate_focus_word(
    learning_item_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Rotate a mastered focus word out — push all its review clocks to tomorrow.

    Pushing only MemoryState.next_review_at was not enough: pending
    WordReviewTasks and unresolved MistakeLogs kept re-serving the word in
    the same session, undoing the rotation.
    """
    learning_item = db.scalar(
        select(LearningItem).where(
            LearningItem.id == learning_item_id,
            LearningItem.user_id == current_user.id,
        )
    )
    if learning_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning item not found")

    now = datetime.now(UTC)
    tomorrow = now + timedelta(days=1)
    memory_state = db.scalar(
        select(MemoryState).where(MemoryState.learning_item_id == learning_item.id)
    )
    if memory_state is not None:
        memory_state.next_review_at = tomorrow
        db.add(memory_state)

    word = (learning_item.english_text or "").strip().lower()
    if word:
        # Supersede pending micro-review tasks for this word and push the
        # word-level clock — otherwise the task queue re-serves it today.
        db.execute(
            update(WordReviewTask)
            .where(
                WordReviewTask.user_id == current_user.id,
                func.lower(WordReviewTask.word) == word,
                WordReviewTask.status == "pending",
            )
            .values(status="superseded", updated_at=now)
        )
        db.execute(
            update(WordMemoryState)
            .where(
                WordMemoryState.user_id == current_user.id,
                func.lower(WordMemoryState.word) == word,
            )
            .values(next_micro_review_at=tomorrow, updated_at=now)
        )
    # Rotation means the word is mastered — resolve its open mistakes so the
    # mistake-injection path doesn't pull it back into the queue.
    db.execute(
        update(MistakeLog)
        .where(
            MistakeLog.user_id == current_user.id,
            MistakeLog.learning_item_id == learning_item.id,
            MistakeLog.is_resolved.is_(False),
        )
        .values(is_resolved=True, resolved_at=now)
    )
    db.commit()
    return {"rotated": True, "next_review_at": tomorrow.isoformat()}


@router.get("/today-progress", response_model=TodayProgressResponse)
def get_today_progress(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TodayProgressResponse:
    return build_today_progress(db, current_user.id)


@router.get("/review-forecast", response_model=ReviewForecastResponse)
def get_review_forecast(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ReviewForecastResponse:
    return build_review_forecast(db, current_user.id)


@router.get("/effectiveness")
def get_effectiveness_metrics(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """P12: effectiveness metrics for the parent dashboard.

    Answers "is the reform working?" with five numbers that must trend the
    right way:
      1. weekly_accuracy   — session success rate, target 70-85% (was 52%)
      2. mastered_time_share — % of word reviews spent on mastered words,
         target < 20% (was 54% before the Phase-1 reform)
      3. status_counts     — vocabulary status snapshot
      4. queue_health      — due-now / overdue / avg interval (intervals
         should spread beyond 1 day as P7 takes effect)
      5. intervention_words — worst words by 14-day accuracy (the P1
         breakthrough list) with their trend
    """
    now = datetime.now(UTC)
    user_id = current_user.id
    # P16: all accuracy metrics count REAL tests only — assisted phases (answer
    # shown before responding) were 100% "correct" by construction and made
    # up 63% of logs, so every accuracy number used to be inflated.
    real_tests_only = ReviewLog.review_mode.notin_(sorted(ASSISTED_REVIEW_MODES))

    weekly_rows = db.execute(
        select(
            func.date_trunc("week", func.timezone("Asia/Shanghai", ReviewLog.reviewed_at)).label("week"),
            func.count(ReviewLog.id),
            func.avg(case((ReviewLog.is_correct, 1), else_=0)),
        )
        .where(ReviewLog.user_id == user_id, ReviewLog.reviewed_at >= now - timedelta(weeks=8), real_tests_only)
        .group_by("week")
        .order_by("week")
    ).all()
    weekly_accuracy = [
        {"week": str(week.date()), "reviews": int(count), "accuracy": round(float(acc or 0), 3)}
        for week, count, acc in weekly_rows
    ]

    share_total, share_mastered = db.execute(
        select(
            func.count(ReviewLog.id),
            func.coalesce(func.sum(case((WordMemoryState.status == "mastered", 1), else_=0)), 0),
        )
        .select_from(ReviewLog)
        .join(LearningItem, and_(LearningItem.id == ReviewLog.learning_item_id, LearningItem.item_type == "word"))
        .outerjoin(WordMemoryState, and_(
            WordMemoryState.word == func.lower(LearningItem.english_text),
            WordMemoryState.user_id == user_id,
        ))
        .where(ReviewLog.user_id == user_id, ReviewLog.reviewed_at >= now - timedelta(days=14), real_tests_only)
    ).one()
    mastered_time_share = round(share_mastered / share_total, 3) if share_total else 0.0

    status_counts = {
        (status_value or "unknown"): int(count)
        for status_value, count in db.execute(
            select(WordMemoryState.status, func.count())
            .where(WordMemoryState.user_id == user_id)
            .group_by(WordMemoryState.status)
        ).all()
    }

    due_now = int(db.scalar(
        select(func.count(MemoryState.id))
        .join(LearningItem, LearningItem.id == MemoryState.learning_item_id)
        .where(LearningItem.user_id == user_id, MemoryState.next_review_at <= now)
    ) or 0)
    overdue_1d = int(db.scalar(
        select(func.count(MemoryState.id))
        .join(LearningItem, LearningItem.id == MemoryState.learning_item_id)
        .where(LearningItem.user_id == user_id, MemoryState.next_review_at <= now - timedelta(days=1))
    ) or 0)
    avg_interval = float(db.scalar(
        select(func.avg(MemoryState.interval_days))
        .join(LearningItem, LearningItem.id == MemoryState.learning_item_id)
        .where(LearningItem.user_id == user_id, LearningItem.item_type == "word")
    ) or 0.0)

    word_status_map = {
        ws.word: (ws.status or "")
        for ws in db.scalars(select(WordMemoryState).where(WordMemoryState.user_id == user_id)).all()
    }
    worst_rows = db.execute(
        select(
            LearningItem.english_text,
            func.count(ReviewLog.id),
            func.avg(case((ReviewLog.is_correct, 1), else_=0)),
        )
        .join(ReviewLog, ReviewLog.learning_item_id == LearningItem.id)
        .where(
            LearningItem.user_id == user_id,
            LearningItem.item_type == "word",
            ReviewLog.reviewed_at >= now - timedelta(days=14),
            real_tests_only,
        )
        .group_by(LearningItem.english_text)
        .having(func.count(ReviewLog.id) >= 8)
        .order_by(func.avg(case((ReviewLog.is_correct, 1), else_=0)).asc())
        .limit(10)
    ).all()
    intervention_words = [
        {
            "word": word,
            "attempts_14d": int(attempts),
            "accuracy_14d": round(float(acc or 0), 3),
            "status": word_status_map.get(word.strip().lower(), ""),
        }
        for word, attempts, acc in worst_rows
    ]

    return {
        "weekly_accuracy": weekly_accuracy,
        "mastered_time_share": mastered_time_share,
        "mastered_time_share_target": 0.2,
        "status_counts": status_counts,
        "queue_health": {
            "due_now": due_now,
            "overdue_1d": overdue_1d,
            "avg_word_interval_days": round(avg_interval, 2),
        },
        "intervention_words": intervention_words,
    }


@router.post("/fsrs/fit", response_model=FsrsFitResponse)
def fit_fsrs_parameters(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> FsrsFitResponse:
    result = fit_user_fsrs_parameters(db, current_user.id)
    return FsrsFitResponse(
        fitted_at=result.fitted_at,
        training_review_count=result.training_review_count,
        training_pair_count=result.training_pair_count,
        accuracy_rate=result.accuracy_rate,
        weights=result.weights,
    )


@router.get("/states/{learning_item_id}", response_model=MemoryStateRead)
def get_memory_state(
    learning_item_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MemoryStateRead:
    learning_item = db.scalar(select(LearningItem).where(LearningItem.id == learning_item_id, LearningItem.user_id == current_user.id))
    if learning_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning item not found")

    memory_state = db.scalar(select(MemoryState).where(MemoryState.learning_item_id == learning_item_id))
    if memory_state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory state not found")

    return MemoryStateRead.model_validate(memory_state)


@router.post("/schedule", response_model=MemoryScheduleResponse)
def schedule_next_review(
    payload: ReviewScoreRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MemoryScheduleResponse:
    learning_item = db.scalar(select(LearningItem).where(LearningItem.id == payload.learning_item_id, LearningItem.user_id == current_user.id))
    if learning_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning item not found")

    result = schedule_memory_review(
        db=db,
        user_id=current_user.id,
        learning_item_id=payload.learning_item_id,
        score=payload.score,
        review_mode=payload.review_mode,
        response_text=payload.response_text,
        duration_seconds=payload.duration_seconds,
    )
    record_learning_event(db, current_user.id, result.review_log, learning_item)
    db.commit()

    return MemoryScheduleResponse(
        memory_state=MemoryStateRead.model_validate(result.memory_state),
        review_log=ReviewLogRead.model_validate(result.review_log),
        mistake_log=MistakeLogRead.model_validate(result.mistake_log) if result.mistake_log is not None else None,
    )


@router.post("/study-time", status_code=status.HTTP_204_NO_CONTENT)
def record_study_time(
    payload: StudyTimeLogRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    if payload.course_id is not None:
        course = db.scalar(select(Course).where(Course.id == payload.course_id, Course.user_id == current_user.id))
        if course is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    db.add(StudyTimeLog(user_id=current_user.id, course_id=payload.course_id, duration_seconds=payload.duration_seconds))
    db.commit()
    # Daily study reward (once per local day) — the heartbeat is the only
    # reliable "child studied today" signal, so the award is wired here.
    try:
        from app.services.points_service import award_daily_study_points
        award_daily_study_points(db, current_user.id)
        db.commit()
    except Exception:
        db.rollback()  # points must never break study-time recording
    check_and_generate_daily_report(db, current_user.id)


@router.post("/course-completions", status_code=status.HTTP_204_NO_CONTENT)
def record_course_completion(
    payload: CourseCompletionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    course = db.scalar(select(Course).where(Course.id == payload.course_id, Course.user_id == current_user.id))
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    db.add(
        CourseCompletionLog(
            user_id=current_user.id,
            course_id=payload.course_id,
            duration_seconds=payload.duration_seconds,
            correct_word_count=payload.correct_word_count,
        )
    )
    db.commit()


@router.get("/course-stats", response_model=list[CourseProgressStats])
def get_course_progress_stats(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    package_id: UUID | None = None,
) -> list[CourseProgressStats]:
    course_statement = select(Course.id).where(Course.user_id == current_user.id)
    if package_id is not None:
        course_statement = course_statement.where(Course.package_id == package_id)
    course_ids = list(db.scalars(course_statement).all())
    if not course_ids:
        return []

    duration_by_course = {
        course_id: int(total_seconds or 0)
        for course_id, total_seconds in db.execute(
            select(StudyTimeLog.course_id, func.coalesce(func.sum(StudyTimeLog.duration_seconds), 0))
            .where(StudyTimeLog.user_id == current_user.id, StudyTimeLog.course_id.in_(course_ids))
            .group_by(StudyTimeLog.course_id)
        ).all()
    }
    completion_by_course = {
        course_id: (int(completed_count or 0), int(correct_word_count or 0), last_completed_at)
        for course_id, completed_count, correct_word_count, last_completed_at in db.execute(
            select(
                CourseCompletionLog.course_id,
                func.count(CourseCompletionLog.id),
                func.coalesce(func.sum(CourseCompletionLog.correct_word_count), 0),
                func.max(CourseCompletionLog.completed_at),
            )
            .where(CourseCompletionLog.user_id == current_user.id, CourseCompletionLog.course_id.in_(course_ids))
            .group_by(CourseCompletionLog.course_id)
        ).all()
    }

    return [
        CourseProgressStats(
            course_id=course_id,
            completed_count=completion_by_course.get(course_id, (0, 0, None))[0],
            total_duration_seconds=duration_by_course.get(course_id, 0),
            total_correct_word_count=completion_by_course.get(course_id, (0, 0, None))[1],
            last_completed_at=completion_by_course.get(course_id, (0, 0, None))[2],
        )
        for course_id in course_ids
    ]


# ── Points & Rewards ──

@router.get("/points/heatmap")
def get_points_heatmap(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    year: int | None = None,
) -> dict:
    """Daily points heatmap data for the given year."""
    from datetime import date, timedelta

    if year is not None and (year < 2000 or year > 2100):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="year must be 2000-2100")
    target_year = year or datetime.now(UTC).year
    start_dt = datetime(target_year, 1, 1, tzinfo=UTC)
    end_dt = datetime(target_year + 1, 1, 1, tzinfo=UTC)

    # Bucket by Asia/Shanghai — func.date() alone uses UTC and attributes
    # evening points (local next day) to the previous day.
    local_date_expr = func.date(func.timezone("Asia/Shanghai", PointsLog.created_at))
    rows = db.execute(
        select(
            local_date_expr.label("d"),
            func.sum(PointsLog.points_changed).label("pts"),
        )
        .where(
            PointsLog.user_id == current_user.id,
            PointsLog.created_at >= start_dt,
            PointsLog.created_at < end_dt,
        )
        .group_by(local_date_expr)
    ).all()

    by_date: dict[str, int] = {str(d): int(p or 0) for d, p in rows}
    total = sum(by_date.values())
    active = sum(1 for v in by_date.values() if v > 0)

    first_day = date(target_year, 1, 1)
    year_days = 366 if (target_year % 4 == 0 and (target_year % 100 != 0 or target_year % 400 == 0)) else 365
    days = []
    for i in range(year_days):
        d = (first_day + timedelta(days=i)).isoformat()
        pts = by_date.get(d, 0)
        absPts = abs(pts)
        color = (
            "#ebedf0" if absPts == 0
            else "#dbeafe" if absPts <= 10
            else "#93c5fd" if absPts <= 30
            else "#3b82f6" if absPts <= 80
            else "#1d4ed8"
        )
        days.append({"date": d, "points": pts, "color": color})

    return {"year": target_year, "days": days, "total_points": total, "active_days": active}


@router.get("/points/summary", response_model=PointsSummaryResponse)
def get_points_summary_endpoint(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PointsSummaryResponse:
    from app.services.points_service import get_points_summary
    return get_points_summary(db, current_user.id)


@router.post("/points/award")
def award_points_endpoint(
    payload: PointsAwardRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    from app.services.points_service import award_points
    result = award_points(db, current_user.id, payload.points_change, payload.reason, payload.detail, payload.learning_item_id)
    db.commit()  # award_points only flushes; without this the award rolls back
    return result


@router.get("/review-advice")
def get_review_advice(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Return today's AI review recommendations, or None if not yet generated."""
    recommendations = get_todays_recommendations(db, current_user.id)
    if recommendations is None:
        return {"has_recommendations": False, "recommended_words": [], "reasoning": "", "suggested_mode": ""}
    return {"has_recommendations": True, **recommendations}


@router.post("/review-advice")
def generate_review_advice_endpoint(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Generate new AI review recommendations now (force refresh).

    Calls the LLM with 7-day review data. May take 5-15 seconds.
    Returns the generated recommendations.
    """
    from app.core.config import settings as app_settings
    from app.services.llm_translation import DEFAULT_LLM_TRANSLATION_SETTINGS, LlmTranslationSettings
    from app.services.secure_model_settings import get_private_model_settings
    from app.utils import string_setting

    stored = get_private_model_settings(db, current_user.id)
    llm_settings = LlmTranslationSettings(
        provider=str(string_setting(stored, "llmProvider") or app_settings.ai_provider or DEFAULT_LLM_TRANSLATION_SETTINGS.provider),
        base_url=str(string_setting(stored, "llmBaseUrl") or app_settings.ai_base_url or DEFAULT_LLM_TRANSLATION_SETTINGS.base_url),
        model=str(string_setting(stored, "llmModel") or app_settings.ai_model or DEFAULT_LLM_TRANSLATION_SETTINGS.model),
        api_key=string_setting(stored, "llmApiKey") or app_settings.ai_api_key,
    )

    try:
        recommendations = generate_review_advice(db, current_user.id, llm_settings, force=True)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 分析失败: {exc}",
        ) from exc

    return {"has_recommendations": True, **recommendations}
