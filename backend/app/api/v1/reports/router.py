from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.ai_daily_report import AiDailyReport
from app.models.course import Course
from app.models.course_completion_log import CourseCompletionLog
from app.models.course_package import CoursePackage
from app.models.daily_plan import DailyPlan
from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.review_log import ReviewLog
from app.models.study_time_log import StudyTimeLog
from app.models.user import User
from app.models.user_model_settings import UserModelSettings
from app.models.word_memory_state import WordMemoryState
from app.models.word_review_task import WordReviewTask
from app.schemas.common import MessageResponse
from app.services.secure_model_settings import encrypt_model_settings, public_model_settings

router = APIRouter()


@router.get("/daily", response_model=MessageResponse)
def get_daily_report(
    current_user: Annotated[User, Depends(get_current_user)],
    report_date: date | None = None,
) -> MessageResponse:
    del current_user
    date_label = report_date.isoformat() if report_date else "latest"
    return MessageResponse(message=f"Daily report endpoint ready for {date_label}")


@router.get("/plans/today", response_model=MessageResponse)
def get_today_plan(current_user: Annotated[User, Depends(get_current_user)]) -> MessageResponse:
    del current_user
    return MessageResponse(message="Today plan endpoint ready")


@router.get("/export")
def export_user_data(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    learning_item_ids = select(LearningItem.id).where(LearningItem.user_id == current_user.id)
    stored_settings = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == current_user.id))

    export_payload = {
        "version": 1,
        "exported_at": datetime.now(UTC),
        "secrets_exported": False,
        "user": serialize_model(current_user, exclude={"hashed_password"}),
        "model_settings": public_model_settings(stored_settings.settings) if stored_settings else {},
        "course_packages": serialize_models(
            db.scalars(select(CoursePackage).where(CoursePackage.user_id == current_user.id).order_by(CoursePackage.created_at.asc())).all()
        ),
        "courses": serialize_models(db.scalars(select(Course).where(Course.user_id == current_user.id).order_by(Course.created_at.asc())).all()),
        "learning_items": serialize_models(
            db.scalars(select(LearningItem).where(LearningItem.user_id == current_user.id).order_by(LearningItem.created_at.asc())).all()
        ),
        "memory_states": serialize_models(
            db.scalars(select(MemoryState).where(MemoryState.learning_item_id.in_(learning_item_ids)).order_by(MemoryState.created_at.asc())).all()
        ),
        "review_logs": serialize_models(db.scalars(select(ReviewLog).where(ReviewLog.user_id == current_user.id).order_by(ReviewLog.reviewed_at.asc())).all()),
        "mistake_logs": serialize_models(db.scalars(select(MistakeLog).where(MistakeLog.user_id == current_user.id).order_by(MistakeLog.occurred_at.asc())).all()),
        "word_memory_states": serialize_models(
            db.scalars(select(WordMemoryState).where(WordMemoryState.user_id == current_user.id).order_by(WordMemoryState.created_at.asc())).all()
        ),
        "word_review_tasks": serialize_models(
            db.scalars(select(WordReviewTask).where(WordReviewTask.user_id == current_user.id).order_by(WordReviewTask.due_at.asc())).all()
        ),
        "study_time_logs": serialize_models(
            db.scalars(select(StudyTimeLog).where(StudyTimeLog.user_id == current_user.id).order_by(StudyTimeLog.recorded_at.asc())).all()
        ),
        "course_completion_logs": serialize_models(
            db.scalars(
                select(CourseCompletionLog).where(CourseCompletionLog.user_id == current_user.id).order_by(CourseCompletionLog.completed_at.asc())
            ).all()
        ),
        "daily_plans": serialize_models(db.scalars(select(DailyPlan).where(DailyPlan.user_id == current_user.id).order_by(DailyPlan.plan_date.asc())).all()),
        "ai_daily_reports": serialize_models(
            db.scalars(select(AiDailyReport).where(AiDailyReport.user_id == current_user.id).order_by(AiDailyReport.report_date.asc())).all()
        ),
    }
    return jsonable_encoder(export_payload)


@router.post("/import", status_code=status.HTTP_201_CREATED)
def import_user_data(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    id_maps: dict[str, dict[str, Any]] = {
        "course_packages": {},
        "courses": {},
        "learning_items": {},
        "memory_states": {},
        "word_memory_states": {},
    }
    summary = {
        "course_packages": 0,
        "courses": 0,
        "learning_items": 0,
        "memory_states": 0,
        "review_logs": 0,
        "mistake_logs": 0,
        "word_memory_states": 0,
        "word_review_tasks": 0,
        "study_time_logs": 0,
        "course_completion_logs": 0,
        "daily_plans": 0,
        "ai_daily_reports": 0,
    }

    existing_package_names = set(db.scalars(select(CoursePackage.name).where(CoursePackage.user_id == current_user.id)).all())
    for row in as_rows(payload.get("course_packages")):
        course_package = CoursePackage(
            user_id=current_user.id,
            name=unique_name(str(row.get("name") or "导入课程包"), existing_package_names),
            description=str(row.get("description") or ""),
        )
        db.add(course_package)
        db.flush()
        id_maps["course_packages"][str(row.get("id"))] = course_package.id
        summary["course_packages"] += 1

    existing_course_names: dict[Any, set[str]] = {}
    for row in as_rows(payload.get("courses")):
        package_id = id_maps["course_packages"].get(str(row.get("package_id")))
        if package_id is None:
            continue
        names = existing_course_names.setdefault(
            package_id,
            set(db.scalars(select(Course.name).where(Course.user_id == current_user.id, Course.package_id == package_id)).all()),
        )
        course = Course(
            user_id=current_user.id,
            package_id=package_id,
            name=unique_name(str(row.get("name") or "导入课程"), names),
            description=str(row.get("description") or ""),
        )
        db.add(course)
        db.flush()
        id_maps["courses"][str(row.get("id"))] = course.id
        summary["courses"] += 1

    for row in as_rows(payload.get("learning_items")):
        course_id = id_maps["courses"].get(str(row.get("course_id"))) if row.get("course_id") else None
        learning_item = LearningItem(
            user_id=current_user.id,
            course_id=course_id,
            item_type=str(row.get("item_type") or "sentence"),
            english_text=str(row.get("english_text") or ""),
            chinese_text=str(row.get("chinese_text") or ""),
            phonetic=str(row.get("phonetic")) if row.get("phonetic") is not None else None,
            difficulty_level=as_int(row.get("difficulty_level"), 1),
            source=str(row.get("source")) if row.get("source") is not None else "import",
        )
        if not learning_item.english_text:
            continue
        db.add(learning_item)
        db.flush()
        id_maps["learning_items"][str(row.get("id"))] = learning_item.id
        summary["learning_items"] += 1

    for row in as_rows(payload.get("memory_states")):
        learning_item_id = id_maps["learning_items"].get(str(row.get("learning_item_id")))
        if learning_item_id is None:
            continue
        memory_state = MemoryState(
            learning_item_id=learning_item_id,
            interval_days=as_int(row.get("interval_days"), 0),
            ease_factor=as_float(row.get("ease_factor"), 2.5),
            memory_strength=as_float(row.get("memory_strength"), 0.0),
            forget_risk=as_float(row.get("forget_risk"), 1.0),
            repetition_count=as_int(row.get("repetition_count"), 0),
            lapse_count=as_int(row.get("lapse_count"), 0),
            consecutive_correct_count=as_int(row.get("consecutive_correct_count"), 0),
            consecutive_error_count=as_int(row.get("consecutive_error_count"), 0),
            recall_correct_count=as_int(row.get("recall_correct_count"), 0),
            hinted_correct_count=as_int(row.get("hinted_correct_count"), 0),
            preview_correct_count=as_int(row.get("preview_correct_count"), 0),
            context_correct_count=as_int(row.get("context_correct_count"), 0),
            last_reviewed_at=parse_datetime(row.get("last_reviewed_at")),
            next_review_at=parse_datetime(row.get("next_review_at")) or datetime.now(UTC),
        )
        db.add(memory_state)
        db.flush()
        id_maps["memory_states"][str(row.get("id"))] = memory_state.id
        summary["memory_states"] += 1

    for row in as_rows(payload.get("review_logs")):
        learning_item_id = id_maps["learning_items"].get(str(row.get("learning_item_id")))
        if learning_item_id is None:
            continue
        db.add(
            ReviewLog(
                user_id=current_user.id,
                learning_item_id=learning_item_id,
                review_mode=str(row.get("review_mode") or "import"),
                error_type=str(row.get("error_type")) if row.get("error_type") is not None else None,
                score=as_int(row.get("score"), 0),
                is_correct=as_bool(row.get("is_correct")),
                response_text=str(row.get("response_text")) if row.get("response_text") is not None else None,
                duration_seconds=as_int(row.get("duration_seconds"), 0),
                reviewed_at=parse_datetime(row.get("reviewed_at")) or datetime.now(UTC),
            )
        )
        summary["review_logs"] += 1

    for row in as_rows(payload.get("mistake_logs")):
        learning_item_id = id_maps["learning_items"].get(str(row.get("learning_item_id")))
        if learning_item_id is None:
            continue
        db.add(
            MistakeLog(
                user_id=current_user.id,
                learning_item_id=learning_item_id,
                mistake_type=str(row.get("mistake_type") or "import"),
                error_type=str(row.get("error_type")) if row.get("error_type") is not None else None,
                expected_answer=str(row.get("expected_answer") or ""),
                actual_answer=str(row.get("actual_answer") or ""),
                is_resolved=as_bool(row.get("is_resolved")),
                occurred_at=parse_datetime(row.get("occurred_at")) or datetime.now(UTC),
                resolved_at=parse_datetime(row.get("resolved_at")),
            )
        )
        summary["mistake_logs"] += 1

    for row in as_rows(payload.get("word_memory_states")):
        word = str(row.get("word") or "").strip().lower()
        if not word:
            continue
        word_state = WordMemoryState(
            user_id=current_user.id,
            word=word,
            learning_item_id=id_maps["learning_items"].get(str(row.get("learning_item_id"))) if row.get("learning_item_id") else None,
            memory_state_id=id_maps["memory_states"].get(str(row.get("memory_state_id"))) if row.get("memory_state_id") else None,
            status=str(row.get("status") or "teaching"),
            memory_strength=as_float(row.get("memory_strength"), 0.0),
            forget_risk=as_float(row.get("forget_risk"), 1.0),
            priority_score=as_float(row.get("priority_score"), 1.0),
            consecutive_correct_count=as_int(row.get("consecutive_correct_count"), 0),
            consecutive_error_count=as_int(row.get("consecutive_error_count"), 0),
            recall_correct_count=as_int(row.get("recall_correct_count"), 0),
            hinted_correct_count=as_int(row.get("hinted_correct_count"), 0),
            preview_correct_count=as_int(row.get("preview_correct_count"), 0),
            context_correct_count=as_int(row.get("context_correct_count"), 0),
            hidden_recall_correct_count=as_int(row.get("hidden_recall_correct_count"), 0),
            no_hint_correct_date_count=as_int(row.get("no_hint_correct_date_count"), 0),
            last_no_hint_correct_date=parse_date(row.get("last_no_hint_correct_date")),
            last_answer_seen_at=parse_datetime(row.get("last_answer_seen_at")),
            error_type_counts=as_dict(row.get("error_type_counts")),
            task_type_counts=as_dict(row.get("task_type_counts")),
            next_micro_review_at=parse_datetime(row.get("next_micro_review_at")),
            micro_review_stage=as_int(row.get("micro_review_stage"), 0),
            last_reviewed_at=parse_datetime(row.get("last_reviewed_at")),
        )
        db.add(word_state)
        db.flush()
        id_maps["word_memory_states"][str(row.get("id"))] = word_state.id
        summary["word_memory_states"] += 1

    for row in as_rows(payload.get("word_review_tasks")):
        word = str(row.get("word") or "").strip().lower()
        if not word:
            continue
        db.add(
            WordReviewTask(
                user_id=current_user.id,
                word_memory_state_id=id_maps["word_memory_states"].get(str(row.get("word_memory_state_id"))) if row.get("word_memory_state_id") else None,
                learning_item_id=id_maps["learning_items"].get(str(row.get("learning_item_id"))) if row.get("learning_item_id") else None,
                word=word,
                task_type=str(row.get("task_type") or "chinese_to_english"),
                prompt_text=str(row.get("prompt_text") or word),
                expected_answer=str(row.get("expected_answer") or word),
                choices=row.get("choices") if isinstance(row.get("choices"), list) else [],
                priority_score=as_float(row.get("priority_score"), 1.0),
                status=str(row.get("status") or "pending"),
                source=str(row.get("source") or "import"),
                due_at=parse_datetime(row.get("due_at")) or datetime.now(UTC),
                completed_at=parse_datetime(row.get("completed_at")),
            )
        )
        summary["word_review_tasks"] += 1

    for row in as_rows(payload.get("study_time_logs")):
        db.add(
            StudyTimeLog(
                user_id=current_user.id,
                course_id=id_maps["courses"].get(str(row.get("course_id"))) if row.get("course_id") else None,
                duration_seconds=as_int(row.get("duration_seconds"), 0),
                recorded_at=parse_datetime(row.get("recorded_at")) or datetime.now(UTC),
            )
        )
        summary["study_time_logs"] += 1

    for row in as_rows(payload.get("course_completion_logs")):
        course_id = id_maps["courses"].get(str(row.get("course_id")))
        if course_id is None:
            continue
        db.add(
            CourseCompletionLog(
                user_id=current_user.id,
                course_id=course_id,
                duration_seconds=as_int(row.get("duration_seconds"), 0),
                correct_word_count=as_int(row.get("correct_word_count"), 0),
                completed_at=parse_datetime(row.get("completed_at")) or datetime.now(UTC),
            )
        )
        summary["course_completion_logs"] += 1

    for row in as_rows(payload.get("daily_plans")):
        db.add(
            DailyPlan(
                user_id=current_user.id,
                plan_date=parse_date(row.get("plan_date")) or datetime.now(UTC).date(),
                warmup_review_minutes=as_int(row.get("warmup_review_minutes"), 10),
                new_learning_minutes=as_int(row.get("new_learning_minutes"), 20),
                sentence_training_minutes=as_int(row.get("sentence_training_minutes"), 20),
                mistake_reinforcement_minutes=as_int(row.get("mistake_reinforcement_minutes"), 10),
                new_word_limit=as_int(row.get("new_word_limit"), 0),
                new_phrase_limit=as_int(row.get("new_phrase_limit"), 0),
                strategy=as_dict(row.get("strategy")),
            )
        )
        summary["daily_plans"] += 1

    for row in as_rows(payload.get("ai_daily_reports")):
        db.add(
            AiDailyReport(
                user_id=current_user.id,
                report_date=parse_date(row.get("report_date")) or datetime.now(UTC).date(),
                accuracy_rate=as_float(row.get("accuracy_rate"), 0.0),
                spelling_error_rate=as_float(row.get("spelling_error_rate"), 0.0),
                sentence_error_rate=as_float(row.get("sentence_error_rate"), 0.0),
                study_duration_minutes=as_int(row.get("study_duration_minutes"), 0),
                review_backlog_count=as_int(row.get("review_backlog_count"), 0),
                high_forget_risk_count=as_int(row.get("high_forget_risk_count"), 0),
                summary=str(row.get("summary") or ""),
                next_day_strategy=as_dict(row.get("next_day_strategy")),
            )
        )
        summary["ai_daily_reports"] += 1

    import_public_model_settings(db, current_user.id, payload.get("model_settings"))
    db.commit()
    return {"message": "Import completed", "imported": summary}


def serialize_models(rows: list[Any]) -> list[dict[str, Any]]:
    return [serialize_model(row) for row in rows]


def serialize_model(row: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    excluded_keys = exclude or set()
    return {column.name: getattr(row, column.name) for column in row.__table__.columns if column.name not in excluded_keys}


def import_public_model_settings(db: Session, user_id: Any, raw_settings: Any) -> None:
    if not isinstance(raw_settings, dict):
        return
    settings = {
        key: value
        for key, value in raw_settings.items()
        if isinstance(value, str)
        and key
        not in {
            "llmApiKey",
            "volcengineTtsApiKey",
            "llmApiKeyConfigured",
            "volcengineTtsApiKeyConfigured",
        }
    }
    if not settings:
        return
    stored_settings = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == user_id))
    if stored_settings is None:
        db.add(UserModelSettings(user_id=user_id, settings=encrypt_model_settings(settings)))
    else:
        stored_settings.settings = encrypt_model_settings({**stored_settings.settings, **settings}, stored_settings.settings)


def as_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def unique_name(name: str, existing_names: set[str]) -> str:
    base_name = name.strip()[:100] or "导入数据"
    candidate = base_name
    index = 2
    while candidate in existing_names:
        candidate = f"{base_name} ({index})"[:120]
        index += 1
    existing_names.add(candidate)
    return candidate


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else str(value).lower() == "true"


def as_dict(value: Any) -> dict[str, object]:
    return value if isinstance(value, dict) else {}
