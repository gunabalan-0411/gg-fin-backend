from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Text, func
from sqlmodel import Field, SQLModel


class NotePage(SQLModel, table=True):
    """One page of the Note Pad: a title and Markdown text. `version` goes up on every save (stale-save detection)."""

    __tablename__ = "tbl_note_page"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(default="Untitled", max_length=120)
    content: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    version: int = Field(default=1)
    created_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    updated_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
