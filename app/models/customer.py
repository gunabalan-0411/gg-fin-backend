from datetime import date
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class EdiCustomer(SQLModel, table=True):
    __tablename__ = "tbl_edi_customer"

    customer_id: int = Field(primary_key=True)
    month: Optional[str] = None
    loan_start_date: Optional[date] = None
    customer_segment_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_name_ta: Optional[str] = None
    customer_address: Optional[str] = None
    proof_aadhaar: Optional[str] = None
    contact_number: Optional[str] = None
    loan_amount: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    disbursed_amount: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    interest: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    outstanding_balance: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    remarks: Optional[str] = None
    ignore: bool = Field(default=False)


class IopCustomer(SQLModel, table=True):
    __tablename__ = "tbl_iop_customer"

    customer_id: int = Field(primary_key=True)
    month: Optional[str] = None
    loan_start_date: Optional[date] = None
    customer_segment_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_name_ta: Optional[str] = None
    customer_address: Optional[str] = None
    proof_aadhaar: Optional[str] = None
    contact_number: Optional[str] = None
    interest_payment_frequency: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=6)
    loan_amount: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    disbursed_amount: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    interest: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    loan_closure: Optional[int] = None
    remarks: Optional[str] = None
    ignore: bool = Field(default=False)
