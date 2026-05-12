from sqlmodel import Session

from app.repositories.expense_repo import ExpenseRepo
from app.schemas.expense import ExpenseCreate, ExpenseUpdate


class ExpenseService:
    def __init__(self, session: Session):
        self.repo = ExpenseRepo(session)

    def list(self, skip: int, limit: int):
        return self.repo.get_all(skip, limit)

    def get(self, expense_id: int):
        return self.repo.get_by_id(expense_id)

    def create(self, payload: ExpenseCreate):
        return self.repo.create(payload.model_dump())

    def update(self, expense_id: int, payload: ExpenseUpdate):
        expense = self.repo.get_by_id(expense_id)
        if not expense:
            return None
        return self.repo.update(expense, payload.model_dump(exclude_none=True))

    def delete(self, expense_id: int) -> bool:
        expense = self.repo.get_by_id(expense_id)
        if not expense:
            return False
        self.repo.delete(expense)
        return True
