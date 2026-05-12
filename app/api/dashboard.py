from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.schemas.dashboard import DashboardSummary, DailyActivity
from app.services.dashboard_service import DashboardService

router = APIRouter()


@router.get("/summary", response_model=DashboardSummary)
def get_summary(session: Session = Depends(get_session), _=Depends(get_current_user)):
    return DashboardService(session).get_summary()


@router.get("/daily-activity", response_model=list[DailyActivity])
def get_daily_activity(
    days: int = 30,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return DashboardService(session).get_daily_activity(days)
