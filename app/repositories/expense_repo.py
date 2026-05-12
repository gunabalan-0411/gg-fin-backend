from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.models.expense import Expense


class ExpenseRepo:
    def __init__(self, session: Session):
        self.session = session

    def get_all(self, skip: int = 0, limit: int = 100):
        return self.session.exec(
            select(Expense).order_by(Expense.date.desc()).offset(skip).limit(limit)
        ).all()

    def get_by_id(self, expense_id: int) -> Optional[Expense]:
        return self.session.get(Expense, expense_id)

    def create(self, data: dict) -> Expense:
        expense = Expense(**data)
        self.session.add(expense)
        self.session.commit()
        self.session.refresh(expense)
        return expense

    def update(self, expense: Expense, data: dict) -> Expense:
        for k, v in data.items():
            if v is not None:
                setattr(expense, k, v)
        self.session.add(expense)
        self.session.commit()
        self.session.refresh(expense)
        return expense

    def delete(self, expense: Expense) -> None:
        self.session.delete(expense)
        self.session.commit()
