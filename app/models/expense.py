import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class Expense(SQLModel, table=True):
    __tablename__ = "tbl_expense"

    id: int | None = Field(default=None, primary_key=True)
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    date: datetime.date = Field(index=True)
    notes: Optional[str] = None
    # The MOST SPECIFIC category: a sub-category when one was chosen, otherwise the top-level category.
    # NULL = an old expense that has not been grouped yet (shown as "Ungrouped").
    category_id: Optional[int] = Field(default=None, index=True)
    # "For whom" (family / appa / amma / me ...). Required for new expenses; NULL only on old ones.
    person_id: Optional[int] = Field(default=None, index=True)


class PersonalExpense(SQLModel, table=True):
    """A personal expense of ONE logged-in user (never shown to anybody else). Uses the same categories and people as office expenses."""
    __tablename__ = "tbl_personal_expense"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)                       # the owner (tbl_users.id)
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    date: datetime.date = Field(index=True)
    notes: Optional[str] = None
    category_id: Optional[int] = Field(default=None, index=True)   # the most specific category (same ids as tbl_expense_category)
    person_id: Optional[int] = Field(default=None, index=True)     # "for whom": optional here


class ExpenseCategory(SQLModel, table=True):
    """Two levels at most: a top-level category (parent_id NULL) and its sub-categories."""
    __tablename__ = "tbl_expense_category"
    # one name per level, ignoring case (the same index as migration 016, so a database made by create_all has it too)
    __table_args__ = (Index("uq_expense_category_name", text("COALESCE(parent_id, 0)"), text("lower(name)"), unique=True),)

    id: int | None = Field(default=None, primary_key=True)
    name: str
    parent_id: Optional[int] = Field(default=None, index=True)


class ExpensePerson(SQLModel, table=True):
    """Who an expense was for."""
    __tablename__ = "tbl_expense_person"
    __table_args__ = (Index("uq_expense_person_name", text("lower(name)"), unique=True),)

    id: int | None = Field(default=None, primary_key=True)
    name: str
