from datetime import date

from fastapi import APIRouter

from app.schemas.common import MessageResponse

router = APIRouter()


@router.get("/daily", response_model=MessageResponse)
def get_daily_report(report_date: date | None = None) -> MessageResponse:
    date_label = report_date.isoformat() if report_date else "latest"
    return MessageResponse(message=f"Daily report endpoint ready for {date_label}")


@router.get("/plans/today", response_model=MessageResponse)
def get_today_plan() -> MessageResponse:
    return MessageResponse(message="Today plan endpoint ready")
