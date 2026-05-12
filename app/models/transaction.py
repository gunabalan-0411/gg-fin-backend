from datetime import date
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class EdiTransaction(SQLModel, table=True):
    __tablename__ = "tbl_edi_transactions"

    transaction_id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(index=True)
    collection_date: date = Field(index=True)
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    payment_mode: str = Field(default="CASH")
    payment_status: str = Field(default="PAID")


class IopTransaction(SQLModel, table=True):
    __tablename__ = "tbl_iop_transactions"

    transaction_id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(index=True)
    collection_date: date = Field(index=True)
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    payment_mode: str = Field(default="CASH")
    payment_status: str = Field(default="PAID")
