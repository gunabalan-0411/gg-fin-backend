import datetime
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class Debt(SQLModel, table=True):
    __tablename__ = "tbl_debt"

    id: int | None = Field(default=None, primary_key=True)
    date: datetime.date = Field(index=True)
    lender_name: str
    borrower_name: Optional[str] = None
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    emi_amount: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    interest_rate_pa: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=5)
    tenure_months: Optional[int] = None
    end_date: Optional[datetime.date] = None
    paid_by: Optional[str] = None
    notes: Optional[str] = None


class DebtRepayment(SQLModel, table=True):
    __tablename__ = "tbl_debt_repayment"

    id: int | None = Field(default=None, primary_key=True)
    debt_id: int = Field(foreign_key="tbl_debt.id", index=True)
    date: datetime.date
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    notes: Optional[str] = None
