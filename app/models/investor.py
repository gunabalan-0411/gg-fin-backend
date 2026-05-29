import datetime
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class Investor(SQLModel, table=True):
    __tablename__ = "tbl_investor"

    id: int | None = Field(default=None, primary_key=True)
    date: datetime.date = Field(index=True)
    investor_name: str
    amount: Decimal = Field(decimal_places=2, max_digits=14)
    return_amount: Decimal = Field(decimal_places=2, max_digits=14, default=0)
    notes: Optional[str] = None
