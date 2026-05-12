from __future__ import annotations
from typing import Optional, List, Dict
from sqlmodel import Session

from app.repositories.customer_repo import EdiCustomerRepo, IopCustomerRepo
from app.schemas.customer import (
    EdiCustomerCreate, EdiCustomerUpdate,
    IopCustomerCreate, IopCustomerUpdate,
)


class EdiCustomerService:
    def __init__(self, session: Session):
        self.repo = EdiCustomerRepo(session)

    def list(self, skip: int, limit: int, search: str, segment_id: Optional[int],
             sort_by: str = "customer_id", sort_dir: str = "asc", balance_gt_zero: bool = False):
        return self.repo.get_all(skip, limit, search, segment_id, sort_by, sort_dir, balance_gt_zero)

    def count(self, search: str, segment_id: Optional[int], balance_gt_zero: bool = False) -> int:
        return self.repo.count(search, segment_id, balance_gt_zero)

    def get(self, customer_id: int):
        return self.repo.get_by_id(customer_id)

    def next_id(self) -> int:
        return self.repo.max_id() + 1

    def create(self, payload: EdiCustomerCreate):
        return self.repo.create(payload.model_dump())

    def update(self, customer_id: int, payload: EdiCustomerUpdate):
        customer = self.repo.get_by_id(customer_id)
        if not customer:
            return None
        return self.repo.update(customer, payload.model_dump(exclude_none=True))

    def delete(self, customer_id: int) -> bool:
        customer = self.repo.get_by_id(customer_id)
        if not customer:
            return False
        self.repo.delete(customer)
        return True

    def delete_and_resequence(self, customer_id: int) -> bool:
        return self.repo.delete_and_resequence(customer_id)

    def get_tamil_names(self, customer_ids: List[int]) -> Dict[int, str]:
        return self.repo.get_tamil_names(customer_ids)


class IopCustomerService:
    def __init__(self, session: Session):
        self.repo = IopCustomerRepo(session)

    def list(self, skip: int, limit: int, search: str, segment_id: Optional[int],
             sort_by: str = "customer_id", sort_dir: str = "asc", balance_gt_zero: bool = False):
        return self.repo.get_all(skip, limit, search, segment_id, sort_by, sort_dir, balance_gt_zero)

    def count(self, search: str, segment_id: Optional[int], balance_gt_zero: bool = False) -> int:
        return self.repo.count(search, segment_id, balance_gt_zero)

    def get(self, customer_id: int):
        return self.repo.get_by_id(customer_id)

    def next_id(self) -> int:
        return self.repo.max_id() + 1

    def create(self, payload: IopCustomerCreate):
        return self.repo.create(payload.model_dump())

    def update(self, customer_id: int, payload: IopCustomerUpdate):
        customer = self.repo.get_by_id(customer_id)
        if not customer:
            return None
        return self.repo.update(customer, payload.model_dump(exclude_none=True))

    def delete(self, customer_id: int) -> bool:
        customer = self.repo.get_by_id(customer_id)
        if not customer:
            return False
        self.repo.delete(customer)
        return True

    def delete_and_resequence(self, customer_id: int) -> bool:
        return self.repo.delete_and_resequence(customer_id)

    def get_tamil_names(self, customer_ids: List[int]) -> Dict[int, str]:
        return self.repo.get_tamil_names(customer_ids)
