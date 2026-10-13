"""
Standard mapping tables used by OCR name matching.

    tbl_customer_alias        2nd alias names the customer is also known by      (max 3 per customer)
    tbl_customer_ai_mismatch  wrong spellings/readings the OCR/AI produced        (max 10 per customer)

Rows are keyed by (finance_type, customer_name, word) and are deliberately NOT tied to a customer id by a
foreign key: ids are re-sequenced on delete and a closed customer may later come back as a new account.
`active_customer_id` is only a pointer, kept up to date by database triggers (see app/db/mapping_schema.py).
Whether the pointed-to customer is active is never stored — read it from the `vw_customer_*` views.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, UniqueConstraint, func
from sqlmodel import Field, SQLModel


def _ts_column() -> Column:
    return Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _use_count_column() -> Column:
    # server-side default so raw INSERTs (the service layer) work on databases built by create_all too
    return Column(Integer, nullable=False, server_default="0")


class CustomerAlias(SQLModel, table=True):
    __tablename__ = "tbl_customer_alias"
    __table_args__ = (
        UniqueConstraint("finance_type", "customer_name", "alias_name", name="uq_customer_alias"),
        CheckConstraint("finance_type IN ('EDI','IOP')", name="ck_customer_alias_type"),
        Index("ix_customer_alias_owner", "finance_type", "customer_name"),
        Index("ix_customer_alias_cid", "finance_type", "active_customer_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    finance_type: str = Field(max_length=3)
    customer_name: str
    alias_name: str
    active_customer_id: Optional[int] = None
    use_count: int = Field(default=0, sa_column=_use_count_column())
    last_used_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: Optional[datetime] = Field(default=None, sa_column=_ts_column())
    updated_at: Optional[datetime] = Field(default=None, sa_column=_ts_column())


class CustomerAiMismatch(SQLModel, table=True):
    __tablename__ = "tbl_customer_ai_mismatch"
    __table_args__ = (
        UniqueConstraint("finance_type", "customer_name", "mismatch_name", name="uq_customer_ai_mismatch"),
        CheckConstraint("finance_type IN ('EDI','IOP')", name="ck_customer_ai_mismatch_type"),
        Index("ix_customer_ai_mismatch_owner", "finance_type", "customer_name"),
        Index("ix_customer_ai_mismatch_cid", "finance_type", "active_customer_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    finance_type: str = Field(max_length=3)
    customer_name: str
    mismatch_name: str
    active_customer_id: Optional[int] = None
    use_count: int = Field(default=0, sa_column=_use_count_column())
    last_used_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: Optional[datetime] = Field(default=None, sa_column=_ts_column())
    updated_at: Optional[datetime] = Field(default=None, sa_column=_ts_column())
