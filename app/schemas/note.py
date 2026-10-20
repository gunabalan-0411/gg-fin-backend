from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class NotePageSummary(BaseModel):
    """List entry: no text, so the list stays light however long the pages are."""
    id: int
    title: str
    version: int
    size: int                      # bytes of Markdown
    updated_at: datetime


class NotePageRead(BaseModel):
    id: int
    title: str
    content: str
    version: int
    created_at: datetime
    updated_at: datetime


class NotePageCreate(BaseModel):
    title: Optional[str] = None    # blank -> "Untitled"
    content: str = ""


class NotePageUpdate(BaseModel):
    version: int                   # the version the editor was based on; a different one means someone saved in between
    title: Optional[str] = None
    content: Optional[str] = None
