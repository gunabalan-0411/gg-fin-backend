from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

# Alias avoids the field name 'date' shadowing the 'date' type in Optional[date] = None
_Date = date


class ExpenseCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    date: date
    notes: Optional[str] = None
    category_id: Optional[int] = None       # a sub-category or a top-level one; Miscellaneous when left out
    person_id: int                          # "for whom" is required


class ExpenseUpdate(BaseModel):
    amount: Optional[Decimal] = None
    date: Optional[_Date] = None
    notes: Optional[str] = None
    category_id: Optional[int] = None
    person_id: Optional[int] = None


class ExpenseRead(BaseModel):
    id: int
    amount: Decimal
    date: _Date
    notes: Optional[str] = None
    category_id: Optional[int] = None       # the most specific category; NULL = old expense, not grouped yet
    top_id: Optional[int] = None            # its top-level category
    top_name: Optional[str] = None
    sub_name: Optional[str] = None          # the sub-category (None when only a top-level one was chosen)
    person_id: Optional[int] = None
    person_name: Optional[str] = None


class ExpenseBulkCreate(BaseModel):
    items: list[ExpenseCreate] = Field(min_length=1, max_length=200)


class CategoryCreate(BaseModel):
    name: str
    parent_id: Optional[int] = None


class NameUpdate(BaseModel):
    name: str


class PersonCreate(BaseModel):
    name: str


class LegacyApplyItem(BaseModel):
    notes: Optional[str] = None             # the exact notes text of the group (None / "" = no notes)
    category_id: int
    person_id: Optional[int] = None


class LegacyApplyRequest(BaseModel):
    items: list[LegacyApplyItem] = Field(max_length=2000)


class PersonalExpenseCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    date: date
    notes: Optional[str] = None
    category_id: Optional[int] = None       # Miscellaneous when left out
    person_id: Optional[int] = None         # "for whom" is optional for personal expenses
