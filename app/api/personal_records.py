"""
Personal Finance: savings accounts, friendly lends, friendly borrows (what the person owes), gifts received and investments of the LOGGED-IN person. Every row belongs to one user: another login
sees none of them and cannot change or delete them (they get "not found"). Mounted under /api/personal (see app/main.py).
"""
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator, model_validator
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.personal_records import PersonalBorrow, PersonalGift, PersonalInvestment, PersonalLend, PersonalSaving
from app.models.user import User

router = APIRouter()


def _clean(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    return v or None


Text = Annotated[Optional[str], BeforeValidator(_clean)]       # trimmed; empty = none


# ── schemas ───────────────────────────────────────────────────────────────────────────────────────────────
class Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SavingIn(BaseModel):
    amount: Decimal
    bank: str
    account_name: str
    balance_date: date
    notes: Text = None

    @field_validator("bank", "account_name")
    @classmethod
    def _required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("required")
        return v

    @field_validator("amount")
    @classmethod
    def _not_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("the amount cannot be negative")
        return v


class SavingRead(Read, SavingIn):
    id: int


class LendIn(BaseModel):
    to_whom: str
    amount: Decimal
    follow_up_date: Optional[date] = None
    is_settled: bool = False
    notes: Text = None

    @field_validator("to_whom")
    @classmethod
    def _required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("required")
        return v

    @field_validator("amount")
    @classmethod
    def _positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("the amount must be more than zero")
        return v


class LendRead(Read, LendIn):
    id: int


class BorrowIn(BaseModel):
    from_whom: str
    amount: Decimal
    pay_back_date: Optional[date] = None
    is_settled: bool = False
    notes: Text = None

    @field_validator("from_whom")
    @classmethod
    def _required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("required")
        return v

    @field_validator("amount")
    @classmethod
    def _positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("the amount must be more than zero")
        return v


class BorrowRead(Read, BorrowIn):
    id: int


InvestmentType = Literal["stocks", "mutual_fund", "fixed_deposit", "gold", "chit", "other"]


class InvestmentIn(BaseModel):
    name: str
    inv_type: InvestmentType
    amount: Decimal                                 # invested
    current_value: Optional[Decimal] = None         # worth now, when the person knows it
    invested_date: Optional[date] = None
    notes: Text = None

    @field_validator("name")
    @classmethod
    def _required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("required")
        return v

    @field_validator("amount")
    @classmethod
    def _positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("the amount invested must be more than zero")
        return v

    @field_validator("current_value")
    @classmethod
    def _not_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("the current value cannot be negative")
        return v


class InvestmentRead(Read, InvestmentIn):
    id: int


class GiftIn(BaseModel):
    gift_type: Literal["money", "gold", "other"]
    amount: Optional[Decimal] = None            # money: how much
    gold_grams: Optional[Decimal] = None        # gold: how much (grams)
    item_name: Text = None                      # other: what it is
    occasion: Text = None
    gift_from: Text = None
    received_date: Optional[date] = None
    notes: Text = None

    @model_validator(mode="after")
    def _by_type(self):
        """Only the field of the chosen type is kept; the one that type needs must be given."""
        if self.gift_type == "money":
            if self.amount is None or self.amount <= 0:
                raise ValueError("money gift: how much is required")
            self.gold_grams, self.item_name = None, None
        elif self.gift_type == "gold":
            if self.gold_grams is None or self.gold_grams <= 0:
                raise ValueError("gold gift: how many grams is required")
            self.amount, self.item_name = None, None
        else:
            if not self.item_name:
                raise ValueError("other gift: the name of the gift is required")
            self.amount, self.gold_grams = None, None
        return self


class GiftRead(Read, GiftIn):
    id: int


class SavingPatch(BaseModel):
    amount: Optional[Decimal] = None
    bank: Optional[str] = None
    account_name: Optional[str] = None
    balance_date: Optional[date] = None
    notes: Optional[str] = None


class LendPatch(BaseModel):
    to_whom: Optional[str] = None
    amount: Optional[Decimal] = None
    follow_up_date: Optional[date] = None
    is_settled: Optional[bool] = None
    notes: Optional[str] = None


class BorrowPatch(BaseModel):
    from_whom: Optional[str] = None
    amount: Optional[Decimal] = None
    pay_back_date: Optional[date] = None
    is_settled: Optional[bool] = None
    notes: Optional[str] = None


class InvestmentPatch(BaseModel):
    name: Optional[str] = None
    inv_type: Optional[InvestmentType] = None
    amount: Optional[Decimal] = None
    current_value: Optional[Decimal] = None
    invested_date: Optional[date] = None
    notes: Optional[str] = None


class GiftPatch(BaseModel):
    gift_type: Optional[Literal["money", "gold", "other"]] = None
    amount: Optional[Decimal] = None
    gold_grams: Optional[Decimal] = None
    item_name: Optional[str] = None
    occasion: Optional[str] = None
    gift_from: Optional[str] = None
    received_date: Optional[date] = None
    notes: Optional[str] = None


# ── what the person holds (for the Personal dashboard) ───────────────────────────────────────────────────────
def holdings(session: Session, user_id: int, today: date) -> dict:
    """
    Total in savings (the last recorded balances added up), what is still to be collected from friends (open lends only; a repaid
    lend is not counted) and what the person still owes (open borrows only), each with how many are overdue and the nearest date.
    The person's own rows only.
    """
    savings = session.exec(select(PersonalSaving).where(PersonalSaving.user_id == user_id)).all()
    open_lends = [r for r in session.exec(select(PersonalLend).where(PersonalLend.user_id == user_id)).all() if not r.is_settled]
    dated = sorted((r for r in open_lends if r.follow_up_date), key=lambda r: (r.follow_up_date, r.id))
    upcoming = next((r for r in dated if r.follow_up_date >= today), None)
    open_borrows = [r for r in session.exec(select(PersonalBorrow).where(PersonalBorrow.user_id == user_id)).all() if not r.is_settled]
    due = sorted((r for r in open_borrows if r.pay_back_date), key=lambda r: (r.pay_back_date, r.id))
    next_due = next((r for r in due if r.pay_back_date >= today), None)
    return {
        "savings": {"total": sum((r.amount for r in savings), Decimal(0)), "accounts": len(savings)},
        "lends": {
            "to_collect": sum((r.amount for r in open_lends), Decimal(0)), "people": len(open_lends),
            "overdue": sum(1 for r in dated if r.follow_up_date < today),
            "overdue_amount": sum((r.amount for r in dated if r.follow_up_date < today), Decimal(0)),
            "next_follow_up": upcoming.follow_up_date if upcoming else None, "next_follow_up_to": upcoming.to_whom if upcoming else None,
        },
        "borrows": {
            "to_pay": sum((r.amount for r in open_borrows), Decimal(0)), "people": len(open_borrows),
            "overdue": sum(1 for r in due if r.pay_back_date < today),
            "overdue_amount": sum((r.amount for r in due if r.pay_back_date < today), Decimal(0)),
            "next_pay_back": next_due.pay_back_date if next_due else None, "next_pay_back_to": next_due.from_whom if next_due else None,
        },
    }


# ── shared helpers ────────────────────────────────────────────────────────────────────────────────────────
def _own(session: Session, model, rid: int, user: User):
    row = session.get(model, rid)
    if row is None or row.user_id != user.id:          # somebody else's row is "not found", never "forbidden"
        raise HTTPException(status_code=404, detail="Not found")
    return row


def _validate(schema, data: dict):
    try:
        return schema(**data)
    except ValueError as e:
        errors = getattr(e, "errors", None)
        msg = "; ".join(str(x.get("msg", "")).removeprefix("Value error, ") for x in errors()) if errors else str(e)
        raise HTTPException(status_code=422, detail=msg)


def _save(session: Session, row):
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _merged(row, patch: BaseModel, fields: list[str]) -> dict:
    """The row as it is now with the patch on top (only fields that were sent; an empty text clears it)."""
    sent = patch.model_dump(exclude_unset=True)
    data = {f: getattr(row, f) for f in fields}
    data.update(sent)
    return data


# ── savings ───────────────────────────────────────────────────────────────────────────────────────────────
SAVING_FIELDS = ["amount", "bank", "account_name", "balance_date", "notes"]


@router.get("/savings", response_model=list[SavingRead])
def list_savings(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    q = select(PersonalSaving).where(PersonalSaving.user_id == user.id).order_by(PersonalSaving.bank, PersonalSaving.account_name, PersonalSaving.id)
    return session.exec(q).all()


@router.post("/savings", response_model=SavingRead, status_code=201)
def create_saving(payload: SavingIn, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return _save(session, PersonalSaving(user_id=user.id, **payload.model_dump()))


@router.patch("/savings/{rid}", response_model=SavingRead)
def update_saving(rid: int, patch: SavingPatch, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    row = _own(session, PersonalSaving, rid, user)
    new = _validate(SavingIn, _merged(row, patch, SAVING_FIELDS))
    for k, v in new.model_dump().items():
        setattr(row, k, v)
    return _save(session, row)


@router.delete("/savings/{rid}", status_code=204)
def delete_saving(rid: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    session.delete(_own(session, PersonalSaving, rid, user))
    session.commit()


# ── friendly lends ────────────────────────────────────────────────────────────────────────────────────────
LEND_FIELDS = ["to_whom", "amount", "follow_up_date", "is_settled", "notes"]


@router.get("/lends", response_model=list[LendRead])
def list_lends(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    """Money still to come back first (the earliest follow-up first, no date last), then the settled ones."""
    rows = session.exec(select(PersonalLend).where(PersonalLend.user_id == user.id)).all()
    return sorted(rows, key=lambda r: (r.is_settled, r.follow_up_date is None, r.follow_up_date or date.max, r.id))


@router.post("/lends", response_model=LendRead, status_code=201)
def create_lend(payload: LendIn, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return _save(session, PersonalLend(user_id=user.id, **payload.model_dump()))


@router.patch("/lends/{rid}", response_model=LendRead)
def update_lend(rid: int, patch: LendPatch, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    row = _own(session, PersonalLend, rid, user)
    new = _validate(LendIn, _merged(row, patch, LEND_FIELDS))
    for k, v in new.model_dump().items():
        setattr(row, k, v)
    return _save(session, row)


@router.delete("/lends/{rid}", status_code=204)
def delete_lend(rid: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    session.delete(_own(session, PersonalLend, rid, user))
    session.commit()


# ── friendly borrows (what the person owes) ───────────────────────────────────────────────────────────────
BORROW_FIELDS = ["from_whom", "amount", "pay_back_date", "is_settled", "notes"]


@router.get("/borrows", response_model=list[BorrowRead])
def list_borrows(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    """Money still to be paid back first (the earliest pay-back date first, no date last), then the paid ones."""
    rows = session.exec(select(PersonalBorrow).where(PersonalBorrow.user_id == user.id)).all()
    return sorted(rows, key=lambda r: (r.is_settled, r.pay_back_date is None, r.pay_back_date or date.max, r.id))


@router.post("/borrows", response_model=BorrowRead, status_code=201)
def create_borrow(payload: BorrowIn, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return _save(session, PersonalBorrow(user_id=user.id, **payload.model_dump()))


@router.patch("/borrows/{rid}", response_model=BorrowRead)
def update_borrow(rid: int, patch: BorrowPatch, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    row = _own(session, PersonalBorrow, rid, user)
    new = _validate(BorrowIn, _merged(row, patch, BORROW_FIELDS))
    for k, v in new.model_dump().items():
        setattr(row, k, v)
    return _save(session, row)


@router.delete("/borrows/{rid}", status_code=204)
def delete_borrow(rid: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    session.delete(_own(session, PersonalBorrow, rid, user))
    session.commit()


# ── investments ───────────────────────────────────────────────────────────────────────────────────────────
INVESTMENT_FIELDS = ["name", "inv_type", "amount", "current_value", "invested_date", "notes"]


@router.get("/investments", response_model=list[InvestmentRead])
def list_investments(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    """Newest first (no date last)."""
    rows = session.exec(select(PersonalInvestment).where(PersonalInvestment.user_id == user.id)).all()
    return sorted(rows, key=lambda r: (r.invested_date is None, -(r.invested_date.toordinal() if r.invested_date else 0), -r.id))


@router.post("/investments", response_model=InvestmentRead, status_code=201)
def create_investment(payload: InvestmentIn, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return _save(session, PersonalInvestment(user_id=user.id, **payload.model_dump()))


@router.patch("/investments/{rid}", response_model=InvestmentRead)
def update_investment(rid: int, patch: InvestmentPatch, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    row = _own(session, PersonalInvestment, rid, user)
    new = _validate(InvestmentIn, _merged(row, patch, INVESTMENT_FIELDS))
    for k, v in new.model_dump().items():
        setattr(row, k, v)
    return _save(session, row)


@router.delete("/investments/{rid}", status_code=204)
def delete_investment(rid: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    session.delete(_own(session, PersonalInvestment, rid, user))
    session.commit()


# ── gifts received ────────────────────────────────────────────────────────────────────────────────────────
GIFT_FIELDS = ["gift_type", "amount", "gold_grams", "item_name", "occasion", "gift_from", "received_date", "notes"]


@router.get("/gifts", response_model=list[GiftRead])
def list_gifts(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    rows = session.exec(select(PersonalGift).where(PersonalGift.user_id == user.id)).all()
    return sorted(rows, key=lambda r: (r.received_date is None, -(r.received_date.toordinal() if r.received_date else 0), -r.id))


@router.post("/gifts", response_model=GiftRead, status_code=201)
def create_gift(payload: GiftIn, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return _save(session, PersonalGift(user_id=user.id, **payload.model_dump()))


@router.patch("/gifts/{rid}", response_model=GiftRead)
def update_gift(rid: int, patch: GiftPatch, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    row = _own(session, PersonalGift, rid, user)
    data = _merged(row, patch, GIFT_FIELDS)
    if "gift_type" in patch.model_dump(exclude_unset=True):          # changing the kind: the old kind's detail must not carry over
        keep = {"money": "amount", "gold": "gold_grams", "other": "item_name"}[data["gift_type"]]
        for f in ("amount", "gold_grams", "item_name"):
            if f != keep and f not in patch.model_dump(exclude_unset=True):
                data[f] = None
    new = _validate(GiftIn, data)
    for k, v in new.model_dump().items():
        setattr(row, k, v)
    return _save(session, row)


@router.delete("/gifts/{rid}", status_code=204)
def delete_gift(rid: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    session.delete(_own(session, PersonalGift, rid, user))
    session.commit()
