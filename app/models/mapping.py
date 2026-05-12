from typing import Optional

from sqlmodel import Field, SQLModel


class EdiNameMap(SQLModel, table=True):
    __tablename__ = "tbl_edi_name_map"

    customer_id: int = Field(primary_key=True)
    customer_name_en: Optional[str] = None
    customer_name_ta: Optional[str] = None


class EdiGroupMap(SQLModel, table=True):
    __tablename__ = "tbl_edi_group_map"

    customer_segment_id: int = Field(primary_key=True)
    customer_segment_name_en: Optional[str] = None
    customer_segment_name_ta: Optional[str] = None


class IopNameMap(SQLModel, table=True):
    __tablename__ = "tbl_iop_name_map"

    customer_id: int = Field(primary_key=True)
    customer_name_en: Optional[str] = None
    customer_name_ta: Optional[str] = None


class IopGroupMap(SQLModel, table=True):
    __tablename__ = "tbl_iop_group_map"

    customer_segment_id: int = Field(primary_key=True)
    customer_segment_name_en: Optional[str] = None
    customer_segment_name_ta: Optional[str] = None
