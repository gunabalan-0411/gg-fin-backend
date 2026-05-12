from pydantic import BaseModel


class MonthlyProfit(BaseModel):
    month: str
    iop_profit: float
    edi_profit: float
    expense: float
    net_profit: float


class DashboardSummary(BaseModel):
    current_month_iop_profit: float
    current_month_edi_profit: float
    current_month_expense: float
    current_month_net_profit: float
    monthly_trends: list[MonthlyProfit]


class DailyActivity(BaseModel):
    date: str
    edi_count: int
    iop_count: int
    edi_amount: float
    iop_amount: float
