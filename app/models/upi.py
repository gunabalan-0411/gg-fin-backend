from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class UpiTransaction(SQLModel, table=True):
    __tablename__ = "tbl_upi_transactions"

    id: int | None = Field(default=None, primary_key=True)
    upi_ref_no: str = Field(unique=True, index=True)
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    transaction_type: str = Field(default="credit")  # "credit" | "debit"
    sender_vpa: Optional[str] = None
    sender_name: Optional[str] = None
    notes: Optional[str] = None
    transaction_date: date = Field(index=True)
    source: str = Field(default="csv")  # "gmail" | "csv"
    mapped_customer_id: Optional[int] = None
    mapped_customer_type: Optional[str] = None  # "edi" | "iop"


class UpiVpaMapping(SQLModel, table=True):
    __tablename__ = "tbl_upi_vpa_mappings"
    __table_args__ = (UniqueConstraint("upi_vpa", "customer_type", name="uq_vpa_type"),)

    id: int | None = Field(default=None, primary_key=True)
    upi_vpa: str = Field(index=True)
    customer_id: int
    customer_type: str  # "edi" | "iop"
    customer_name: Optional[str] = None


class GmailSettings(SQLModel, table=True):
    __tablename__ = "tbl_gmail_settings"

    id: int = Field(default=1, primary_key=True)
    email: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_expiry: Optional[datetime] = None


class DriveSettings(SQLModel, table=True):
    __tablename__ = "tbl_drive_settings"

    id: int = Field(default=1, primary_key=True)
    email: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_expiry: Optional[datetime] = None
