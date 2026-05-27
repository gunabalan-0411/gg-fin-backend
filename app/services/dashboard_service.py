from sqlmodel import Session

from app.repositories.dashboard_repo import DashboardRepo
from app.schemas.dashboard import DashboardSummary, MonthlyProfit, DailyActivity


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
