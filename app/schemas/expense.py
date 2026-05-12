from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel

# Alias avoids the field name 'date' shadowing the 'date' type in Optional[date] = None
_Date = date


class ExpenseCreate(BaseModel):
    amount: Decimal
    date: date
    notes: Optional[str] = None


class ExpenseUpdate(BaseModel):
    amount: Optional[Decimal] = None
    date: Optional[_Date] = None
    notes: Optional[str] = None


class ExpenseRead(ExpenseCreate):
    id: int
