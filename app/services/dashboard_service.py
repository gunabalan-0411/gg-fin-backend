import math
import calendar as _cal
from datetime import date, timedelta

from sqlmodel import Session

from app.repositories.dashboard_repo import DashboardRepo
from app.schemas.dashboard import (
    DashboardSummary, MonthlyProfit, DailyActivity,
    LoanSummary, CustomerBrief, IopRemindersResponse,
    IopCalendarDay, EdiInactiveCustomer, EdiDefaulter, IopMonthlyDue,
)


# ── IOP payment-date helpers ──────────────────────────────────────────────────

def _interval_days(frequency: float) -> int:
    if not frequency or frequency <= 0:
        return 30
    return max(1, round(30 / float(frequency)))


def _payment_dates_in_range(start: date, frequency: float, from_d: date, to_d: date) -> list[date]:
    if to_d < start or to_d < from_d:
        return []
    interval = _interval_days(frequency)
    days_to_from = max(0, (from_d - start).days)
    first_i = math.ceil(days_to_from / interval)
    result = []
    d = start + timedelta(days=first_i * interval)
    while d <= to_d:
        if d >= from_d:
            result.append(d)
        d += timedelta(days=interval)
    return result


def _to_brief(c: dict) -> dict:
    return {
        "customer_id": c["customer_id"],
        "customer_name": c["customer_name"] or "",
        "tamil_name": c["tamil_name"] or "",
        "loan_amount": float(c["loan_amount"] or 0),
        "frequency": round(float(c["frequency"] or 1)),
    }


# ── Service ───────────────────────────────────────────────────────────────────

class DashboardService:
    def __init__(self, session: Session):
        self.repo = DashboardRepo(session)

    def get_summary(self) -> DashboardSummary:
        iop_monthly  = {r["month"]: float(r["iop_profit"] or 0) for r in self.repo.get_monthly_iop_profit()}
        edi_monthly  = {r["month"]: float(r["edi_profit"] or 0) for r in self.repo.get_monthly_edi_profit()}
        exp_monthly  = {r["month"]: float(r["expense"] or 0)    for r in self.repo.get_monthly_expense()}
        uncl_monthly = {r["month"]: float(r["unclaimed"] or 0)  for r in self.repo.get_monthly_unclaimed()}
        deft_monthly = {r["month"]: float(r["defaulted"] or 0)  for r in self.repo.get_monthly_defaulted()}

        all_months = sorted(set(list(iop_monthly) + list(edi_monthly) + list(exp_monthly) + list(uncl_monthly) + list(deft_monthly)))
        trends = []
        for m in all_months:
            iop  = iop_monthly.get(m, 0)
            edi  = edi_monthly.get(m, 0)
            exp  = exp_monthly.get(m, 0)
            uncl = uncl_monthly.get(m, 0)
            deft = deft_monthly.get(m, 0)
            trends.append(MonthlyProfit(
                month=m, iop_profit=iop, edi_profit=edi, expense=exp,
                unclaimed=uncl, defaulted=deft,
                net_profit=iop + edi + uncl - exp - deft,
            ))

        iop_cur  = self.repo.get_current_month_iop_profit()
        edi_cur  = self.repo.get_current_month_edi_profit()
        exp_cur  = self.repo.get_current_month_expense()
        uncl_cur = self.repo.get_current_month_unclaimed()
        deft_cur = self.repo.get_current_month_defaulted()

        return DashboardSummary(
            current_month_iop_profit=iop_cur,
            current_month_edi_profit=edi_cur,
            current_month_expense=exp_cur,
            current_month_unclaimed=uncl_cur,
            current_month_defaulted=deft_cur,
            current_month_net_profit=iop_cur + edi_cur + uncl_cur - exp_cur - deft_cur,
            monthly_trends=trends,
        )

    def get_daily_activity(self, days: int = 30) -> list[DailyActivity]:
        rows = self.repo.get_daily_activity(days)
        return [
            DailyActivity(
                date=str(r["date"]),
                edi_count=int(r["edi_count"]),
                iop_count=int(r["iop_count"]),
                edi_amount=float(r["edi_amount"]),
                iop_amount=float(r["iop_amount"]),
            )
            for r in rows
        ]

    def get_loan_summary(self) -> LoanSummary:
        edi = self.repo.get_loan_summary_edi()
        iop = self.repo.get_loan_summary_iop()
        return LoanSummary(
            edi_total_loan=edi["total_loan"],
            edi_total_receivable=edi["total_receivable"],
            iop_total_loan=iop["total_loan"],
            iop_total_receivable=iop["total_receivable"],
        )

    def get_iop_reminders(self) -> IopRemindersResponse:
        customers = self.repo.get_active_iop_customers()
        today = date.today()
        yesterday = today - timedelta(days=1)
        tomorrow = today + timedelta(days=1)
        result: dict[str, list[CustomerBrief]] = {"yesterday": [], "today": [], "tomorrow": []}
        key_map = {yesterday: "yesterday", today: "today", tomorrow: "tomorrow"}

        for c in customers:
            start = c["loan_start_date"]
            freq = float(c["frequency"] or 1)
            if not start:
                continue
            for d, key in key_map.items():
                if _payment_dates_in_range(start, freq, d, d):
                    result[key].append(CustomerBrief(**_to_brief(c)))

        return IopRemindersResponse(**result)

    def get_iop_calendar(self, year: int, month: int) -> list[IopCalendarDay]:
        customers = self.repo.get_active_iop_customers()
        _, days_in_month = _cal.monthrange(year, month)
        month_start = date(year, month, 1)
        month_end = date(year, month, days_in_month)

        cal_map: dict[str, list[CustomerBrief]] = {
            date(year, month, d).isoformat(): [] for d in range(1, days_in_month + 1)
        }

        for c in customers:
            start = c["loan_start_date"]
            freq = float(c["frequency"] or 1)
            if not start:
                continue
            brief = CustomerBrief(**_to_brief(c))
            for pd in _payment_dates_in_range(start, freq, month_start, month_end):
                cal_map[pd.isoformat()].append(brief)

        return [IopCalendarDay(date=d, customers=cust) for d, cust in cal_map.items()]

    def get_edi_inactive(self) -> list[EdiInactiveCustomer]:
        rows = self.repo.get_edi_overdue(min_days=7)
        return [
            EdiInactiveCustomer(
                customer_id=r["customer_id"],
                customer_name=r["customer_name"],
                tamil_name=r["tamil_name"],
                loan_amount=r["loan_amount"],
                outstanding_balance=r["outstanding_balance"],
                last_payment_date=str(r["last_payment_date"]) if r["last_payment_date"] else None,
                days_since_payment=int(r["days_since_payment"]),
            )
            for r in rows
        ]

    def get_edi_defaulters(self) -> list[EdiDefaulter]:
        rows = self.repo.get_edi_overdue(min_days=95)
        return [
            EdiDefaulter(
                customer_id=r["customer_id"],
                customer_name=r["customer_name"],
                tamil_name=r["tamil_name"],
                loan_amount=r["loan_amount"],
                outstanding_balance=r["outstanding_balance"],
                last_payment_date=str(r["last_payment_date"]) if r["last_payment_date"] else None,
                days_overdue=int(r["days_since_payment"]),
            )
            for r in rows
        ]

    def get_iop_monthly_dues(self) -> list[IopMonthlyDue]:
        customers = self.repo.get_active_iop_customers()
        paid_map = self.repo.get_iop_paid_this_month()
        today = date.today()
        month_start = today.replace(day=1)

        dues = []
        for c in customers:
            start = c["loan_start_date"]
            freq = float(c["frequency"] or 1)
            monthly_interest = float(c["monthly_interest"] or 0)
            if not start:
                continue

            passed = _payment_dates_in_range(start, freq, month_start, today)
            n_due = len(passed)
            if n_due == 0:
                continue

            per_payment = monthly_interest / max(1, round(freq))
            expected = n_due * per_payment
            paid = float(paid_map.get(c["customer_id"], 0))
            due = max(0.0, expected - paid)

            dues.append(IopMonthlyDue(
                customer_id=c["customer_id"],
                customer_name=c["customer_name"] or "",
                tamil_name=c["tamil_name"] or "",
                loan_amount=float(c["loan_amount"] or 0),
                monthly_interest=monthly_interest,
                paid_this_month=paid,
                due_this_month=due,
                payments_due_so_far=n_due,
                frequency=round(freq),
            ))

        dues.sort(key=lambda x: x.due_this_month, reverse=True)
        return dues
