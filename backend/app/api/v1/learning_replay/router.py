"""REST API for Learning Replay System: heatmap, day, hour, minute drill-down."""

from datetime import date as date_type, datetime, timedelta
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.learning_replay import (
    DayDetailResponse,
    HeatmapResponse,
    HourDetailResponse,
    MinuteEventsResponse,
)
from app.services.learning_replay import (
    backfill_events_from_review_logs,
    build_day_detail,
    build_heatmap,
    build_hour_detail,
    build_minute_events,
)
from app.utils import parse_date_param

router = APIRouter()


@router.get("/learning/heatmap", response_model=HeatmapResponse)
def get_heatmap(
    year: Optional[int] = Query(None),
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> HeatmapResponse:
    """Annual heatmap of study minutes per day."""
    # First-visit auto-backfill from existing review_logs
    backfill_events_from_review_logs(db, current_user.id)
    return HeatmapResponse(**build_heatmap(db, current_user.id, year))


@router.get("/learning/day/{date}", response_model=DayDetailResponse)
def get_day_detail(
    date: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DayDetailResponse:
    """One day: hourly breakdown with minute-level stats."""
    target = parse_date_param(date)
    if target is None:
        raise HTTPException(status_code=400, detail="Invalid date format (YYYY-MM-DD)")
    return DayDetailResponse(**build_day_detail(db, current_user.id, target))


@router.get("/learning/hour/{date}/{hour}", response_model=HourDetailResponse)
def get_hour_detail(
    date: str,
    hour: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> HourDetailResponse:
    """One hour: minute-level breakdown."""
    if hour < 0 or hour > 23:
        raise HTTPException(status_code=400, detail="hour must be 0-23")
    target = parse_date_param(date)
    if target is None:
        raise HTTPException(status_code=400, detail="Invalid date format (YYYY-MM-DD)")
    return HourDetailResponse(**build_hour_detail(db, current_user.id, target, hour))


@router.get("/learning/minute/{date}/{minute}", response_model=MinuteEventsResponse)
def get_minute_events(
    date: str,
    minute: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    hour: int = Query(0, ge=0, le=23),
) -> MinuteEventsResponse:
    """One minute: full event log."""
    if minute < 0 or minute > 59:
        raise HTTPException(status_code=400, detail="minute must be 0-59")
    target = parse_date_param(date)
    if target is None:
        raise HTTPException(status_code=400, detail="Invalid date format (YYYY-MM-DD)")
    events = build_minute_events(db, current_user.id, target, hour, minute)
    return MinuteEventsResponse(
        date=target.isoformat(),
        hour=hour,
        minute=minute,
        events=events,
    )
