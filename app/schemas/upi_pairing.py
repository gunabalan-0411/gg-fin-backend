"""Request models for saving UPI pairings made on the OCR page and for moving UPI ids to a re-opened account."""
from typing import Optional

from pydantic import BaseModel, Field


class Pairing(BaseModel):
    txn_id: int
    customer_id: int
    customer_type: str                      # "edi" | "iop"
    customer_name: Optional[str] = None
    replace: bool = False                   # the user confirmed re-pointing a UPI id that belongs to another customer


class ApplyPairingsRequest(BaseModel):
    pairings: list[Pairing] = Field(max_length=500)


class UnlinkVpaRequest(BaseModel):
    txn_id: int


class MoveVpaMappingsRequest(BaseModel):
    customer_type: str                      # "edi" | "iop"
    from_customer_id: int
    to_customer_id: int
