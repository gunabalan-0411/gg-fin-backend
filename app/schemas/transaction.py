from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class EdiTransactionCreate(BaseModel):
    customer_id: int
    collection_date: date
    amount: Decimal
    payment_mode: str = "CASH"
    payment_status: str = "PAID"


class EdiTransactionUpdate(BaseModel):
    amount: Optional[Decimal] = None
    payment_mode: Optional[str] = None
    payment_status: Optional[str] = None


class EdiTransactionRead(EdiTransactionCreate):
    transaction_id: int
    customer_name: Optional[str] = None
    customer_name_ta: Optional[str] = None


class IopTransactionCreate(BaseModel):
    customer_id: int
    collection_date: date
    amount: Decimal
    payment_mode: str = "CASH"
    payment_status: str = "PAID"


class IopTransactionUpdate(BaseModel):
    amount: Optional[Decimal] = None
    payment_mode: Optional[str] = None
    payment_status: Optional[str] = None


class IopTransactionRead(IopTransactionCreate):
    transaction_id: int
    customer_name: Optional[str] = None
    customer_name_ta: Optional[str] = None
