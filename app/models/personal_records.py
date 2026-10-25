"""
Personal Finance records that belong to ONE login (user_id) and are never shown to another: savings accounts, friendly lends, gifts received.
"""
import datetime
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class PersonalSaving(SQLModel, table=True):
    __tablename__ = "tbl_personal_saving"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    amount: Decimal = Field(decimal_places=2, max_digits=14)          # the balance last recorded
    bank: str
    account_name: str
    balance_date: datetime.date                                       # the day that balance was recorded
    notes: Optional[str] = None


class PersonalLend(SQLModel, table=True):
    __tablename__ = "tbl_personal_lend"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    to_whom: str
    amount: Decimal = Field(decimal_places=2, max_digits=14)
    follow_up_date: Optional[datetime.date] = Field(default=None, index=True)    # when to ask for it back
    is_settled: bool = Field(default=False)
    notes: Optional[str] = None


class PersonalGift(SQLModel, table=True):
    __tablename__ = "tbl_personal_gift"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    gift_type: str                                                    # money | gold | other
    amount: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=14)      # money: how much (rupees)
    gold_grams: Optional[Decimal] = Field(default=None, decimal_places=3, max_digits=10)  # gold: how much (grams)
    item_name: Optional[str] = None                                   # other: what the gift is
    occasion: Optional[str] = None
    gift_from: Optional[str] = None
    received_date: Optional[datetime.date] = Field(default=None, index=True)
    notes: Optional[str] = None
