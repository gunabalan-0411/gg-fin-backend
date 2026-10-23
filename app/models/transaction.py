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
    amount: Decimal = Field(decimal_places=2, max_digits=12)          # the INTEREST collected (IOP transactions are interest-only)
    payment_mode: str = Field(default="CASH")
    payment_status: str = Field(default="PAID")
    # Principal that came in the same payment (e.g. 10,000 collected = 500 interest + 9,500 principal). It is NOT part of `amount`
    # (so profit stays interest only); it was added to the customer's principal_paid and is taken back if this transaction goes.
    principal_amount: Decimal = Field(default=Decimal("0"), decimal_places=2, max_digits=12)
