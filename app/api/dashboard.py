from datetime import date

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.schemas.dashboard import (
    DashboardSummary, DailyActivity, LoanSummary,
    IopRemindersResponse, IopCalendarDay,
    EdiInactiveCustomer, EdiDefaulter, IopMonthlyDue,
)
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


@router.get("/loan-summary", response_model=LoanSummary)
def get_loan_summary(session: Session = Depends(get_session), _=Depends(get_current_user)):
    return DashboardService(session).get_loan_summary()


@router.get("/iop-reminders", response_model=IopRemindersResponse)
def get_iop_reminders(session: Session = Depends(get_session), _=Depends(get_current_user)):
    return DashboardService(session).get_iop_reminders()


@router.get("/iop-calendar", response_model=list[IopCalendarDay])
def get_iop_calendar(
    year: int = date.today().year,
    month: int = date.today().month,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return DashboardService(session).get_iop_calendar(year, month)


@router.get("/edi-inactive", response_model=list[EdiInactiveCustomer])
def get_edi_inactive(session: Session = Depends(get_session), _=Depends(get_current_user)):
    return DashboardService(session).get_edi_inactive()


@router.get("/edi-defaulters", response_model=list[EdiDefaulter])
def get_edi_defaulters(session: Session = Depends(get_session), _=Depends(get_current_user)):
    return DashboardService(session).get_edi_defaulters()


@router.get("/iop-monthly-dues", response_model=list[IopMonthlyDue])
def get_iop_monthly_dues(session: Session = Depends(get_session), _=Depends(get_current_user)):
    return DashboardService(session).get_iop_monthly_dues()
