from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.course import Course
from app.models.course_completion_log import CourseCompletionLog
from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.study_time_log import StudyTimeLog
from app.models.user import User
from app.models.user_points import PointsLog
from app.schemas.memory import CourseCompletionRequest, CourseProgressStats, FsrsFitResponse, MemoryDashboardResponse, MemoryScheduleResponse, MemoryStateRead, PointsAwardRequest, PointsSummaryResponse, ReviewForecastResponse, ReviewScoreRequest, StudyTimeLogRequest, TodayProgressResponse
from app.services.memory_dashboard import build_memory_dashboard, build_review_forecast, build_today_progress, check_and_generate_daily_report
from app.services.ai_review_advisor import generate_review_advice, get_todays_recommendations
from app.schemas.review import MistakeLogRead, ReviewLogRead
from app.services.fsrs_fitting import fit_user_fsrs_parameters
from app.services.learning_replay import record_learning_event
from app.services.memory_scheduler import schedule_memory_review

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
    """Rotate a mastered focus word out — push next_review_at to tomorrow."""
    memory_state = db.scalar(
        select(MemoryState).where(
            MemoryState.learning_item_id == learning_item_id,
            MemoryState.learning_item_id.in_(
                select(LearningItem.id).where(LearningItem.user_id == current_user.id)
            ),
        )
    )
    if memory_state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory state not found")
    memory_state.next_review_at = datetime.now(UTC) + timedelta(days=1)
    db.add(memory_state)
    db.commit()
    return {"rotated": True, "next_review_at": memory_state.next_review_at.isoformat()}


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

    target_year = year or datetime.now(UTC).year
    start_dt = datetime(target_year, 1, 1, tzinfo=UTC)
    end_dt = datetime(target_year + 1, 1, 1, tzinfo=UTC)

    rows = db.execute(
        select(
            func.date(PointsLog.created_at).label("d"),
            func.sum(PointsLog.points_changed).label("pts"),
        )
        .where(
            PointsLog.user_id == current_user.id,
            PointsLog.created_at >= start_dt,
            PointsLog.created_at < end_dt,
        )
        .group_by(func.date(PointsLog.created_at))
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
    return award_points(db, current_user.id, payload.points_change, payload.reason, payload.detail, payload.learning_item_id)


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
