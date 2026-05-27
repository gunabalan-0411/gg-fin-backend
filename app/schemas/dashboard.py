from pydantic import BaseModel


class MonthlyProfit(BaseModel):
    month: str
    iop_profit: float
    edi_profit: float
    expense: float
    unclaimed: float = 0
    defaulted: float = 0
    net_profit: float


class DashboardSummary(BaseModel):
    current_month_iop_profit: float
    current_month_edi_profit: float
    current_month_expense: float
    current_month_unclaimed: float = 0
    current_month_defaulted: float = 0
    current_month_net_profit: float
    monthly_trends: list[MonthlyProfit]


class DailyActivity(BaseModel):
    date: str
    edi_count: int
    iop_count: int
    edi_amount: float
    iop_amount: float


class LoanSummary(BaseModel):
    edi_total_loan: float = 0
    edi_total_receivable: float = 0
    iop_total_loan: float = 0
    iop_total_receivable: float = 0


class CustomerBrief(BaseModel):
    customer_id: int
    customer_name: str
    tamil_name: str
    loan_amount: float
    frequency: int = 1


class IopRemindersResponse(BaseModel):
    yesterday: list[CustomerBrief] = []
    today: list[CustomerBrief] = []
    tomorrow: list[CustomerBrief] = []


class IopCalendarDay(BaseModel):
    date: str
    customers: list[CustomerBrief]


class EdiInactiveCustomer(BaseModel):
    customer_id: int
    customer_name: str
    tamil_name: str
    loan_amount: float
    outstanding_balance: float
    last_payment_date: str | None
    days_since_payment: int


class EdiDefaulter(BaseModel):
    customer_id: int
    customer_name: str
    tamil_name: str
    loan_amount: float
    outstanding_balance: float
    last_payment_date: str | None
    days_overdue: int


class IopMonthlyDue(BaseModel):
    customer_id: int
    customer_name: str
    tamil_name: str
    loan_amount: float
    monthly_interest: float
    paid_this_month: float
    due_this_month: float
    payments_due_so_far: int
    frequency: int
