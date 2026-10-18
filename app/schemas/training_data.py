"""Rows of the training-data CSV. OCR and Voice share exactly one format (Documentation.md, Part 2)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# One header for both datasets — the OCR page image and the Voice recording are both just a `source_file`.
CSV_COLUMNS = ["customer", "date", "amount", "type", "mode", "raw_text", "source_file", "origin"]


def to_iso_date(value: str) -> str:
    """dd-mm-yyyy (the OCR page's format) or yyyy-mm-dd -> yyyy-mm-dd."""
    text = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date {text!r} (use dd-mm-yyyy or yyyy-mm-dd)")


class TrainingRow(BaseModel):
    customer: str = Field(min_length=1, max_length=200)    # the name as written: real name, or the alias — never the misread word
    date: str
    amount: float = Field(ge=0)
    type: Literal["EDI", "IOP"]
    mode: Literal["CASH", "ONLINE"]
    raw_text: str = Field(default="", max_length=500)       # OCR: raw OCR text · Voice: the name as transcribed
    source_file: str = Field(default="", max_length=200)    # OCR: page image · Voice: recording (the server fills in the real file name)
    origin: Literal["matched", "picked", "manual"] = "matched"

    @field_validator("customer", mode="before")
    @classmethod
    def _strip_customer(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("type", "mode", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("date")
    @classmethod
    def _iso(cls, v: str) -> str:
        return to_iso_date(v)
