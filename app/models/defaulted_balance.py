import datetime
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class DefaultedBalance(SQLModel, table=True):
    __tablename__ = "tbl_defaulted_balance"

    id: int | None = Field(default=None, primary_key=True)
    date: datetime.date = Field(index=True)
    product: str  # "edi" or "iop"
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    notes: Optional[str] = None
