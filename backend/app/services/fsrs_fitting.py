from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.models.review_log import ReviewLog
from app.models.user_model_settings import UserModelSettings
from app.services.memory_scheduler import (
    FSRS_DECAY,
    FSRS_FACTOR,
    FSRS_WEIGHTS,
    FSRS_WEIGHTS_SETTING_KEY,
    constrain_stability,
    score_to_fsrs_rating,
)
from app.utils import clamp

MIN_FSRS_TRAINING_REVIEWS = 3000


@dataclass(frozen=True)
class FsrsFitResult:
    fitted_at: datetime
    training_review_count: int
    training_pair_count: int
    accuracy_rate: float
    weights: list[float]


def fit_user_fsrs_parameters(db: Session, user_id: UUID) -> FsrsFitResult:
    review_count = int(db.scalar(select(func.count(ReviewLog.id)).where(ReviewLog.user_id == user_id)) or 0)
    if review_count < MIN_FSRS_TRAINING_REVIEWS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"FSRS fitting requires at least {MIN_FSRS_TRAINING_REVIEWS} historical review logs. Current count: {review_count}.",
        )

    review_logs = db.scalars(
        select(ReviewLog)
        .where(ReviewLog.user_id == user_id)
        .order_by(ReviewLog.learning_item_id.asc(), ReviewLog.reviewed_at.asc())
    ).all()
    correct_count = len([review_log for review_log in review_logs if review_log.is_correct])
    accuracy_rate = correct_count / review_count if review_count else 0.0
    stability_samples_by_rating: dict[int, list[float]] = defaultdict(list)

    previous_log: ReviewLog | None = None
    for review_log in review_logs:
        if previous_log is not None and previous_log.learning_item_id == review_log.learning_item_id:
            elapsed_days = max((review_log.reviewed_at - previous_log.reviewed_at).total_seconds() / 86400, 1 / 1440)
            target_retrievability = 0.88 if review_log.is_correct else 0.45
            stability_samples_by_rating[score_to_fsrs_rating(previous_log.score)].append(estimate_stability_days(elapsed_days, target_retrievability))
        previous_log = review_log

    fitted_weights = list(FSRS_WEIGHTS)
    for rating in range(1, 5):
        samples = stability_samples_by_rating.get(rating, [])
        if len(samples) >= 50:
            sample_stability = median(samples)
            default_stability = FSRS_WEIGHTS[rating - 1]
            fitted_weights[rating - 1] = constrain_stability(default_stability * 0.35 + sample_stability * 0.65)

    for index in range(1, 4):
        fitted_weights[index] = max(fitted_weights[index], fitted_weights[index - 1] + 0.05)

    difficulty_adjustment = (0.78 - accuracy_rate) * 3
    fitted_weights[4] = clamp(FSRS_WEIGHTS[4] + difficulty_adjustment, 4.0, 9.0)
    fitted_weights[6] = clamp(FSRS_WEIGHTS[6] * (1 + (0.75 - accuracy_rate) * 0.5), 0.8, 2.2)
    fitted_weights[8] = clamp(FSRS_WEIGHTS[8] + (accuracy_rate - 0.78) * 0.4, 0.8, 2.4)
    fitted_weights[10] = clamp(FSRS_WEIGHTS[10] + (0.78 - accuracy_rate) * 0.35, 0.6, 1.6)
    fitted_weights[14] = clamp(FSRS_WEIGHTS[14] + (0.78 - accuracy_rate) * 0.6, 1.3, 3.5)

    rounded_weights = [round(weight, 6) for weight in fitted_weights]
    fitted_at = datetime.now(UTC)
    try:
        stored_settings = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == user_id))
    except ProgrammingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model settings table is missing. Apply database/init/003_user_model_settings.sql to enable user FSRS fitting.",
        ) from exc
    if stored_settings is None:
        stored_settings = UserModelSettings(user_id=user_id, settings={})
        db.add(stored_settings)

    stored_settings.settings = {
        **dict(stored_settings.settings or {}),
        FSRS_WEIGHTS_SETTING_KEY: rounded_weights,
        "fsrsFittedAt": fitted_at.isoformat(),
        "fsrsTrainingReviewCount": review_count,
        "fsrsTrainingPairCount": sum(len(samples) for samples in stability_samples_by_rating.values()),
        "fsrsAccuracyRate": round(accuracy_rate, 4),
    }
    db.commit()

    return FsrsFitResult(
        fitted_at=fitted_at,
        training_review_count=review_count,
        training_pair_count=sum(len(samples) for samples in stability_samples_by_rating.values()),
        accuracy_rate=round(accuracy_rate, 4),
        weights=rounded_weights,
    )


def estimate_stability_days(elapsed_days: float, retrievability: float) -> float:
    denominator = retrievability ** (1 / FSRS_DECAY) - 1
    if denominator <= 0:
        return constrain_stability(elapsed_days)
    return constrain_stability(FSRS_FACTOR * elapsed_days / denominator)
