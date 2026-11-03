"""
Personal Finance > Monthly budget > Expense accounts: accounts used ONLY for expenses (a bank account, a wallet ...). Money is put in as
funds, expenses are paid from an account (the 'Paid from' of a personal expense), and at the end of a month the balance TickBook shows
should equal the bank's - 'matching with the bank' shows the difference and can record it as an adjustment. Every row belongs to one login.

balance(as of D) = opening balance + funds + adjustments - expenses paid from the account, counting only days from opening_date to D.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import text
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.user import User

router = APIRouter()
_Date = date                                        # the field named "date" below would otherwise shadow the type

MAX_AMOUNT = Decimal("99999999999999.99")          # NUMERIC(14, 2)
Kind = Literal["fund", "adjustment"]


def _money(v: Decimal, allow_negative: bool = False) -> Decimal:
    if not allow_negative and v < 0:
        raise ValueError("That amount cannot be negative")
    if abs(v) > MAX_AMOUNT:
        raise ValueError("That amount is too large")
    return v


class AccountIn(BaseModel):
    name: str
    opening_balance: Decimal = Decimal(0)
    opening_date: Optional[date] = None             # the first of this month when left out

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Give the account a name")
        if len(v) > 60:
            raise ValueError("That name is too long (60 characters at most)")
        return v

    @field_validator("opening_balance")
    @classmethod
    def _opening(cls, v: Decimal) -> Decimal:
        return _money(v, allow_negative=True)       # an overdrawn account opens below zero


class AccountPatch(BaseModel):
    name: Optional[str] = None
    opening_balance: Optional[Decimal] = None
    opening_date: Optional[date] = None

    @model_validator(mode="after")
    def _checks(self):
        if self.name is not None:
            self.name = AccountIn._name(self.name)
        if self.opening_balance is not None:
            _money(self.opening_balance, allow_negative=True)
        return self


class FundIn(BaseModel):
    amount: Decimal
    date: Optional[_Date] = None                    # today when left out
    notes: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def _amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("A fund must be more than zero")
        return _money(v)


class ReconcileIn(BaseModel):
    bank_balance: Decimal
    as_of: Optional[date] = None                    # today when left out
    record: bool = False                            # True: also save the difference as an adjustment

    @field_validator("bank_balance")
    @classmethod
    def _bank(cls, v: Decimal) -> Decimal:
        return _money(v, allow_negative=True)


def _own(session: Session, user_id: int, account_id: int) -> dict:
    r = session.execute(text("SELECT id, name, opening_balance, opening_date FROM tbl_personal_account WHERE id = :i AND user_id = :u"),
                        {"i": account_id, "u": user_id}).first()
    if r is None:
        raise HTTPException(status_code=404, detail="That expense account does not exist")
    return {"id": r[0], "name": r[1], "opening_balance": r[2], "opening_date": r[3]}


def _flows(session: Session, user_id: int, account_id: int, d_from: date, d_to: date) -> dict:
    """Funds, adjustments and spending of one account between two days (inclusive)."""
    funds, adjust = session.execute(text("""
        SELECT COALESCE(sum(amount) FILTER (WHERE kind = 'fund'), 0), COALESCE(sum(amount) FILTER (WHERE kind <> 'fund'), 0)
        FROM tbl_personal_account_entry WHERE user_id = :u AND account_id = :a AND "date" >= :f AND "date" <= :t
    """), {"u": user_id, "a": account_id, "f": d_from, "t": d_to}).one()
    spent = session.execute(text("""
        SELECT COALESCE(sum(amount), 0) FROM tbl_personal_expense
        WHERE user_id = :u AND account_id = :a AND "date" >= :f AND "date" <= :t
    """), {"u": user_id, "a": account_id, "f": d_from, "t": d_to}).scalar()
    return {"funds": funds, "adjustments": adjust, "spent": spent, "net": funds + adjust - spent}


def balance_on(session: Session, user_id: int, acct: dict, as_of: date) -> Decimal:
    """What the account holds at the END of `as_of`."""
    if as_of < acct["opening_date"]:
        return Decimal(0)
    return acct["opening_balance"] + _flows(session, user_id, acct["id"], acct["opening_date"], as_of)["net"]


def _month_row(session: Session, user_id: int, acct: dict, first: date, today: date) -> dict:
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    row = {
        "id": acct["id"], "name": acct["name"], "opening_balance": acct["opening_balance"], "opening_date": acct["opening_date"],
        "balance_now": balance_on(session, user_id, acct, today),
        "active": acct["opening_date"] <= last,
        "month_opening": None, "funds": Decimal(0), "adjustments": Decimal(0), "spent": Decimal(0), "closing": None,
    }
    if not row["active"]:
        return row                                                       # the account did not exist yet in that month
    if first > acct["opening_date"]:
        opening = balance_on(session, user_id, acct, first - timedelta(days=1))
        fl = _flows(session, user_id, acct["id"], first, last)
    else:                                                                # the account starts inside this month
        opening = acct["opening_balance"]
        fl = _flows(session, user_id, acct["id"], acct["opening_date"], last)
    row.update(month_opening=opening, funds=fl["funds"], adjustments=fl["adjustments"], spent=fl["spent"], closing=opening + fl["net"])
    return row


def accounts_view(session: Session, user_id: int, first: date, today: date) -> dict:
    accts = session.execute(text("SELECT id, name, opening_balance, opening_date FROM tbl_personal_account WHERE user_id = :u ORDER BY lower(name)"),
                            {"u": user_id}).all()
    rows = [_month_row(session, user_id, {"id": a[0], "name": a[1], "opening_balance": a[2], "opening_date": a[3]}, first, today) for a in accts]
    live = [r for r in rows if r["active"]]
    zero = Decimal(0)
    return {
        "month": f"{first.year:04d}-{first.month:02d}",
        "accounts": rows,
        "totals": {
            "month_opening": sum((r["month_opening"] for r in live), zero), "funds": sum((r["funds"] for r in live), zero),
            "adjustments": sum((r["adjustments"] for r in live), zero), "spent": sum((r["spent"] for r in live), zero),
            "closing": sum((r["closing"] for r in live), zero), "balance_now": sum((r["balance_now"] for r in rows), zero),
        },
    }


def _first_of_month(value: Optional[str], today: date) -> date:
    if not value:
        return today.replace(day=1)
    try:
        y, m = value.split("-")
        return date(int(y), int(m), 1)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="month must look like 2026-11")


def _name_taken(session: Session, user_id: int, name: str, except_id: Optional[int] = None) -> bool:
    return session.execute(text("SELECT 1 FROM tbl_personal_account WHERE user_id = :u AND lower(name) = lower(:n) AND id <> :x"),
                           {"u": user_id, "n": name, "x": except_id or 0}).first() is not None


def _one(session: Session, user_id: int, account_id: int, today: date) -> dict:
    return _month_row(session, user_id, _own(session, user_id, account_id), today.replace(day=1), today)


@router.get("/accounts")
def list_accounts(
    month: Optional[str] = Query(None, description="YYYY-MM; this month when absent"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    today = date.today()
    return accounts_view(session, user.id, _first_of_month(month, today), today)


@router.post("/accounts", status_code=201)
def create_account(payload: AccountIn, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    today = date.today()
    if _name_taken(session, user.id, payload.name):
        raise HTTPException(status_code=409, detail="You already have an expense account with that name")
    new_id = session.execute(
        text("INSERT INTO tbl_personal_account (user_id, name, opening_balance, opening_date) VALUES (:u, :n, :b, :d) RETURNING id"),
        {"u": user.id, "n": payload.name, "b": payload.opening_balance, "d": payload.opening_date or today.replace(day=1)},
    ).scalar()
    session.commit()
    return _one(session, user.id, new_id, today)


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, payload: AccountPatch, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    _own(session, user.id, account_id)
    sets, params = [], {"i": account_id}
    if payload.name is not None:
        if _name_taken(session, user.id, payload.name, account_id):
            raise HTTPException(status_code=409, detail="You already have an expense account with that name")
        sets.append("name = :n"); params["n"] = payload.name
    if payload.opening_balance is not None:
        sets.append("opening_balance = :b"); params["b"] = payload.opening_balance
    if payload.opening_date is not None:
        sets.append("opening_date = :d"); params["d"] = payload.opening_date
    if sets:
        session.execute(text("UPDATE tbl_personal_account SET " + ", ".join(sets) + " WHERE id = :i"), params)
        session.commit()
    return _one(session, user.id, account_id, date.today())


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    """The expenses paid from it stay (they just no longer name an account); its funds and adjustments go with it."""
    _own(session, user.id, account_id)
    freed = session.execute(text("UPDATE tbl_personal_expense SET account_id = NULL WHERE account_id = :a AND user_id = :u"),
                            {"a": account_id, "u": user.id}).rowcount
    gone = session.execute(text("DELETE FROM tbl_personal_account_entry WHERE account_id = :a AND user_id = :u"), {"a": account_id, "u": user.id}).rowcount
    session.execute(text("DELETE FROM tbl_personal_account WHERE id = :a AND user_id = :u"), {"a": account_id, "u": user.id})
    session.commit()
    return {"deleted": account_id, "expenses_kept": freed, "entries_removed": gone}


@router.get("/accounts/{account_id}/entries")
def list_entries(account_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    """Funds and adjustments of one account, newest first."""
    _own(session, user.id, account_id)
    rows = session.execute(text("""
        SELECT id, "date", amount, kind, notes FROM tbl_personal_account_entry WHERE user_id = :u AND account_id = :a
        ORDER BY "date" DESC, id DESC LIMIT 500
    """), {"u": user.id, "a": account_id}).all()
    return [{"id": r[0], "date": r[1], "amount": r[2], "kind": r[3], "notes": r[4]} for r in rows]


@router.post("/accounts/{account_id}/funds", status_code=201)
def add_fund(account_id: int, payload: FundIn, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    _own(session, user.id, account_id)
    session.execute(
        text("INSERT INTO tbl_personal_account_entry (user_id, account_id, \"date\", amount, kind, notes) VALUES (:u, :a, :d, :m, 'fund', :n)"),
        {"u": user.id, "a": account_id, "d": payload.date or date.today(), "m": payload.amount, "n": (payload.notes or "").strip() or None},
    )
    session.commit()
    return _one(session, user.id, account_id, date.today())


@router.delete("/accounts/{account_id}/entries/{entry_id}")
def delete_entry(account_id: int, entry_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    _own(session, user.id, account_id)
    n = session.execute(text("DELETE FROM tbl_personal_account_entry WHERE id = :e AND account_id = :a AND user_id = :u"),
                        {"e": entry_id, "a": account_id, "u": user.id}).rowcount
    if not n:
        raise HTTPException(status_code=404, detail="That entry does not exist")
    session.commit()
    return _one(session, user.id, account_id, date.today())


@router.post("/accounts/{account_id}/reconcile")
def reconcile(account_id: int, payload: ReconcileIn, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    """Compare the bank's balance on a day with TickBook's. The difference (bank - TickBook) can be saved as an adjustment so they match."""
    acct = _own(session, user.id, account_id)
    as_of = payload.as_of or date.today()
    if as_of < acct["opening_date"]:
        raise HTTPException(status_code=422, detail=f"The account opens on {acct['opening_date']:%d %b %Y}; pick that day or later")
    ours = balance_on(session, user.id, acct, as_of)
    diff = payload.bank_balance - ours
    out = {"as_of": as_of, "tickbook_balance": ours, "bank_balance": payload.bank_balance, "difference": diff, "matched": diff == 0, "recorded": False}
    if payload.record and diff != 0:
        session.execute(
            text("INSERT INTO tbl_personal_account_entry (user_id, account_id, \"date\", amount, kind, notes) VALUES (:u, :a, :d, :m, 'adjustment', :n)"),
            {"u": user.id, "a": account_id, "d": as_of, "m": diff, "n": "Matched with the bank"},
        )
        session.commit()
        out["recorded"] = True
    return out
