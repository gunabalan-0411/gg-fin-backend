from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class EdiCustomerCreate(BaseModel):
    customer_id: int
    month: Optional[str] = None
    loan_start_date: Optional[date] = None
    customer_segment_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_name_ta: Optional[str] = None
    customer_address: Optional[str] = None
    proof_aadhaar: Optional[str] = None
    contact_number: Optional[str] = None
    loan_amount: Optional[Decimal] = None
    disbursed_amount: Optional[Decimal] = None
    interest: Optional[Decimal] = None
    outstanding_balance: Optional[Decimal] = None
    remarks: Optional[str] = None


class EdiCustomerUpdate(BaseModel):
    month: Optional[str] = None
    loan_start_date: Optional[date] = None
    customer_segment_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_name_ta: Optional[str] = None
    customer_address: Optional[str] = None
    proof_aadhaar: Optional[str] = None
    contact_number: Optional[str] = None
    loan_amount: Optional[Decimal] = None
    disbursed_amount: Optional[Decimal] = None
    interest: Optional[Decimal] = None
    outstanding_balance: Optional[Decimal] = None
    remarks: Optional[str] = None
    ignore: Optional[bool] = None


class EdiCustomerRead(EdiCustomerCreate):
    pass


class IopCustomerCreate(BaseModel):
    customer_id: int
    month: Optional[str] = None
    loan_start_date: Optional[date] = None
    customer_segment_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_name_ta: Optional[str] = None
    customer_address: Optional[str] = None
    proof_aadhaar: Optional[str] = None
    contact_number: Optional[str] = None
    interest_payment_frequency: Optional[Decimal] = None
    loan_amount: Optional[Decimal] = None
    disbursed_amount: Optional[Decimal] = None
    interest: Optional[Decimal] = None
    loan_closure: Optional[int] = None
    remarks: Optional[str] = None


class IopCustomerUpdate(BaseModel):
    month: Optional[str] = None
    loan_start_date: Optional[date] = None
    customer_segment_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_name_ta: Optional[str] = None
    customer_address: Optional[str] = None
    proof_aadhaar: Optional[str] = None
    contact_number: Optional[str] = None
    interest_payment_frequency: Optional[Decimal] = None
    loan_amount: Optional[Decimal] = None
    disbursed_amount: Optional[Decimal] = None
    interest: Optional[Decimal] = None
    loan_closure: Optional[int] = None
    remarks: Optional[str] = None
    ignore: Optional[bool] = None


class IopCustomerRead(IopCustomerCreate):
    pass
