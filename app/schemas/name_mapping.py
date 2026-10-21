from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["alias", "ai_mismatch"]


class WordItem(BaseModel):
    kind: Kind
    word: str


class CheckRequest(BaseModel):
    finance_type: str
    customer_id: int
    items: list[WordItem]


class Resolution(BaseModel):
    kind: Kind
    word: str
    action: Literal["keep_both", "move"]


class SaveRequest(BaseModel):
    finance_type: str
    customer_id: int
    aliases: list[str] = Field(default_factory=list)
    mismatches: list[str] = Field(default_factory=list)
    resolutions: list[Resolution] = Field(default_factory=list)
    confirmed_similar: list[WordItem] = Field(default_factory=list)    # new words the user chose to "Add anyway"


class RememberRequest(BaseModel):
    finance_type: str
    customer_id: int
    word: str


class SuggestRequest(BaseModel):
    finance_type: str
    query: str
    limit: int = Field(default=5, ge=1, le=20)


class MappingRef(BaseModel):
    """Which alias / AI-mismatch row a saved OCR row was matched through (usage counting)."""
    kind: Kind
    id: int


class CarryForwardRequest(BaseModel):
    """Copy a closed account's alias / AI-mismatch names to the open account that replaces it (a re-loan under a new name)."""
    finance_type: str
    from_customer_id: int
    to_customer_id: int
    aliases: bool = True                    # carry the alias names
    mismatches: bool = True                 # carry the AI-mismatch names


class UndoCarryRequest(BaseModel):
    """Take back what a carry-forward added: exactly these words, under the new account's name."""
    finance_type: str
    customer_id: int
    aliases: list[str] = Field(default_factory=list)
    mismatches: list[str] = Field(default_factory=list)
