import datetime
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class Expense(SQLModel, table=True):
    __tablename__ = "tbl_expense"

    id: int | None = Field(default=None, primary_key=True)
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    date: datetime.date = Field(index=True)
    notes: Optional[str] = None
