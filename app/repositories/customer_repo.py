from __future__ import annotations
from typing import Optional, List, Dict
from sqlmodel import Session, select, col, func
from sqlalchemy import text

from app.models.customer import EdiCustomer, IopCustomer
from app.models.mapping import EdiNameMap, IopNameMap

# Whitelisted columns for sorting — prevents arbitrary attribute access via sort_by param.
_EDI_SORT_COLS = frozenset({
    "customer_id", "customer_name", "loan_amount",
    "outstanding_balance", "loan_start_date", "interest",
})
_IOP_SORT_COLS = frozenset({
    "customer_id", "customer_name", "loan_amount",
    "outstanding_balance", "loan_start_date", "interest",
})


class EdiCustomerRepo:
    def __init__(self, session: Session):
        self.session = session

    def _base_query(self, search: str = "", segment_id: Optional[int] = None, balance_gt_zero: bool = False):
        query = select(EdiCustomer)
        if search:
            query = query.where(col(EdiCustomer.customer_name).ilike(f"%{search}%"))
        if segment_id is not None:
            query = query.where(EdiCustomer.customer_segment_id == segment_id)
        if balance_gt_zero:
            query = query.where(col(EdiCustomer.outstanding_balance) > 0)
        return query

    def get_all(self, skip: int = 0, limit: int = 100, search: str = "",
                segment_id: Optional[int] = None, sort_by: str = "customer_id",
                sort_dir: str = "asc", balance_gt_zero: bool = False):
        if sort_by not in _EDI_SORT_COLS:
            sort_by = "customer_id"
        query = self._base_query(search, segment_id, balance_gt_zero)
        column = getattr(EdiCustomer, sort_by, EdiCustomer.customer_id)
        query = query.order_by(column.desc() if sort_dir == "desc" else column.asc())
        return self.session.exec(query.offset(skip).limit(limit)).all()

    def count(self, search: str = "", segment_id: Optional[int] = None, balance_gt_zero: bool = False) -> int:
        # Use COUNT(*) — avoids loading all rows into memory (O(1) vs O(N))
        count_q = select(func.count()).select_from(
            self._base_query(search, segment_id, balance_gt_zero).subquery()
        )
        return self.session.exec(count_q).one()

    def get_by_id(self, customer_id: int) -> Optional[EdiCustomer]:
        return self.session.get(EdiCustomer, customer_id)

    def max_id(self) -> int:
        result = self.session.exec(select(func.max(EdiCustomer.customer_id))).one()
        return result or 0

    def create(self, data: dict) -> EdiCustomer:
        customer = EdiCustomer(**data)
        self.session.add(customer)
        self.session.flush()
        name_en = data.get("customer_name")
        name_ta = data.get("customer_name_ta")
        if name_en or name_ta:
            self.session.execute(
                text(
                    "INSERT INTO tbl_edi_name_map (customer_id, customer_name_en, customer_name_ta) "
                    "VALUES (:cid, :en, :ta) "
                    "ON CONFLICT (customer_id) DO UPDATE SET customer_name_en = EXCLUDED.customer_name_en, customer_name_ta = EXCLUDED.customer_name_ta"
                ),
                {"cid": customer.customer_id, "en": name_en, "ta": name_ta},
            )
        self.session.commit()
        self.session.refresh(customer)
        return customer

    def update(self, customer: EdiCustomer, data: dict) -> EdiCustomer:
        for k, v in data.items():
            if v is not None:
                setattr(customer, k, v)
        self.session.add(customer)
        self.session.flush()
        name_en = data.get("customer_name")
        name_ta = data.get("customer_name_ta") or None
        if name_en:
            self.session.execute(
                text(
                    "INSERT INTO tbl_edi_name_map (customer_id, customer_name_en, customer_name_ta) "
                    "VALUES (:cid, :en, :ta) "
                    "ON CONFLICT (customer_id) DO UPDATE SET "
                    "customer_name_en = COALESCE(EXCLUDED.customer_name_en, tbl_edi_name_map.customer_name_en), "
                    "customer_name_ta = COALESCE(EXCLUDED.customer_name_ta, tbl_edi_name_map.customer_name_ta)"
                ),
                {"cid": customer.customer_id, "en": name_en, "ta": name_ta},
            )
        self.session.commit()
        self.session.refresh(customer)
        return customer

    def delete(self, customer: EdiCustomer) -> None:
        self.session.delete(customer)
        self.session.commit()

    def delete_and_resequence(self, customer_id: int) -> bool:
        customer = self.get_by_id(customer_id)
        if not customer:
            return False
        self.session.delete(customer)
        self.session.flush()
        self.session.execute(
            text("DELETE FROM tbl_edi_name_map WHERE customer_id = :cid"),
            {"cid": customer_id},
        )
        self.session.execute(
            text("UPDATE tbl_edi_customer SET customer_id = -customer_id WHERE customer_id > :cid"),
            {"cid": customer_id},
        )
        self.session.execute(
            text("UPDATE tbl_edi_customer SET customer_id = (-customer_id) - 1 WHERE customer_id < -:cid"),
            {"cid": customer_id},
        )
        self.session.execute(
            text("UPDATE tbl_edi_transactions SET customer_id = customer_id - 1 WHERE customer_id > :cid"),
            {"cid": customer_id},
        )
        self.session.execute(
            text("UPDATE tbl_edi_name_map SET customer_id = -customer_id WHERE customer_id > :cid"),
            {"cid": customer_id},
        )
        self.session.execute(
            text("UPDATE tbl_edi_name_map SET customer_id = (-customer_id) - 1 WHERE customer_id < -:cid"),
            {"cid": customer_id},
        )
        self.session.commit()
        return True

    def get_tamil_names(self, customer_ids: List[int]) -> Dict[int, str]:
        if not customer_ids:
            return {}
        query = select(EdiNameMap).where(col(EdiNameMap.customer_id).in_(customer_ids))
        results = self.session.exec(query).all()
        return {r.customer_id: r.customer_name_ta or "" for r in results}


class IopCustomerRepo:
    def __init__(self, session: Session):
        self.session = session

    def _base_query(self, search: str = "", segment_id: Optional[int] = None, balance_gt_zero: bool = False):
        query = select(IopCustomer)
        if search:
            query = query.where(col(IopCustomer.customer_name).ilike(f"%{search}%"))
        if segment_id is not None:
            query = query.where(IopCustomer.customer_segment_id == segment_id)
        if balance_gt_zero:
            query = query.where(col(IopCustomer.outstanding_balance) > 0)
        return query

    def get_all(self, skip: int = 0, limit: int = 100, search: str = "",
                segment_id: Optional[int] = None, sort_by: str = "customer_id",
                sort_dir: str = "asc", balance_gt_zero: bool = False):
        if sort_by not in _IOP_SORT_COLS:
            sort_by = "customer_id"
        query = self._base_query(search, segment_id, balance_gt_zero)
        column = getattr(IopCustomer, sort_by, IopCustomer.customer_id)
        query = query.order_by(column.desc() if sort_dir == "desc" else column.asc())
        return self.session.exec(query.offset(skip).limit(limit)).all()

    def count(self, search: str = "", segment_id: Optional[int] = None, balance_gt_zero: bool = False) -> int:
        count_q = select(func.count()).select_from(
            self._base_query(search, segment_id, balance_gt_zero).subquery()
        )
        return self.session.exec(count_q).one()

    def get_by_id(self, customer_id: int) -> Optional[IopCustomer]:
        return self.session.get(IopCustomer, customer_id)

    def max_id(self) -> int:
        result = self.session.exec(select(func.max(IopCustomer.customer_id))).one()
        return result or 0

    def create(self, data: dict) -> IopCustomer:
        customer = IopCustomer(**data)
        self.session.add(customer)
        self.session.flush()
        name_en = data.get("customer_name")
        name_ta = data.get("customer_name_ta")
        if name_en or name_ta:
            self.session.execute(
                text(
                    "INSERT INTO tbl_iop_name_map (customer_id, customer_name_en, customer_name_ta) "
                    "VALUES (:cid, :en, :ta) "
                    "ON CONFLICT (customer_id) DO UPDATE SET customer_name_en = EXCLUDED.customer_name_en, customer_name_ta = EXCLUDED.customer_name_ta"
                ),
                {"cid": customer.customer_id, "en": name_en, "ta": name_ta},
            )
        self.session.commit()
        self.session.refresh(customer)
        return customer

    def update(self, customer: IopCustomer, data: dict) -> IopCustomer:
        for k, v in data.items():
            if v is not None:
                setattr(customer, k, v)
        self.session.add(customer)
        self.session.flush()
        name_en = data.get("customer_name")
        name_ta = data.get("customer_name_ta") or None
        if name_en:
            self.session.execute(
                text(
                    "INSERT INTO tbl_iop_name_map (customer_id, customer_name_en, customer_name_ta) "
                    "VALUES (:cid, :en, :ta) "
                    "ON CONFLICT (customer_id) DO UPDATE SET "
                    "customer_name_en = COALESCE(EXCLUDED.customer_name_en, tbl_iop_name_map.customer_name_en), "
                    "customer_name_ta = COALESCE(EXCLUDED.customer_name_ta, tbl_iop_name_map.customer_name_ta)"
                ),
                {"cid": customer.customer_id, "en": name_en, "ta": name_ta},
            )
        self.session.commit()
        self.session.refresh(customer)
        return customer

    def delete(self, customer: IopCustomer) -> None:
        self.session.delete(customer)
        self.session.commit()

    def delete_and_resequence(self, customer_id: int) -> bool:
        customer = self.get_by_id(customer_id)
        if not customer:
            return False
        self.session.delete(customer)
        self.session.flush()
        self.session.execute(
            text("DELETE FROM tbl_iop_name_map WHERE customer_id = :cid"),
            {"cid": customer_id},
        )
        self.session.execute(
            text("UPDATE tbl_iop_customer SET customer_id = -customer_id WHERE customer_id > :cid"),
            {"cid": customer_id},
        )
        self.session.execute(
            text("UPDATE tbl_iop_customer SET customer_id = (-customer_id) - 1 WHERE customer_id < -:cid"),
            {"cid": customer_id},
        )
        self.session.execute(
            text("UPDATE tbl_iop_transactions SET customer_id = customer_id - 1 WHERE customer_id > :cid"),
            {"cid": customer_id},
        )
        self.session.execute(
            text("UPDATE tbl_iop_name_map SET customer_id = -customer_id WHERE customer_id > :cid"),
            {"cid": customer_id},
        )
        self.session.execute(
            text("UPDATE tbl_iop_name_map SET customer_id = (-customer_id) - 1 WHERE customer_id < -:cid"),
            {"cid": customer_id},
        )
        self.session.commit()
        return True

    def get_tamil_names(self, customer_ids: List[int]) -> Dict[int, str]:
        if not customer_ids:
            return {}
        query = select(IopNameMap).where(col(IopNameMap.customer_id).in_(customer_ids))
        results = self.session.exec(query).all()
        return {r.customer_id: r.customer_name_ta or "" for r in results}
