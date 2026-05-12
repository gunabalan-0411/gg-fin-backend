from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.models.mapping import EdiNameMap, IopNameMap
from app.models.transaction import EdiTransaction, IopTransaction


class EdiTransactionRepo:
    def __init__(self, session: Session):
        self.session = session

    def get_by_date(self, collection_date: date):
        rows = self.session.exec(
            select(EdiTransaction, EdiNameMap.customer_name_en, EdiNameMap.customer_name_ta)
            .outerjoin(EdiNameMap, EdiNameMap.customer_id == EdiTransaction.customer_id)
            .where(EdiTransaction.collection_date == collection_date)
        ).all()
        return [self._enrich(txn, en, ta) for txn, en, ta in rows]

    def get_by_id(self, transaction_id: int) -> Optional[EdiTransaction]:
        return self.session.get(EdiTransaction, transaction_id)

    def get_by_customer(self, customer_id: int):
        return self.session.exec(
            select(EdiTransaction).where(EdiTransaction.customer_id == customer_id)
            .order_by(EdiTransaction.collection_date.desc())
        ).all()

    def get_by_customer_and_date(self, customer_id: int, collection_date: date) -> Optional[EdiTransaction]:
        return self.session.exec(
            select(EdiTransaction).where(
                EdiTransaction.customer_id == customer_id,
                EdiTransaction.collection_date == collection_date,
            )
        ).first()

    def create(self, data: dict) -> EdiTransaction:
        txn = EdiTransaction(**data)
        self.session.add(txn)
        self.session.commit()
        self.session.refresh(txn)
        return txn

    def update(self, txn: EdiTransaction, data: dict) -> EdiTransaction:
        for k, v in data.items():
            if v is not None:
                setattr(txn, k, v)
        self.session.add(txn)
        self.session.commit()
        self.session.refresh(txn)
        return txn

    def delete(self, txn: EdiTransaction) -> None:
        self.session.delete(txn)
        self.session.commit()

    @staticmethod
    def _enrich(txn: EdiTransaction, customer_name_en: str | None, customer_name_ta: str | None) -> dict:
        d = txn.model_dump()
        d["customer_name"] = customer_name_en
        d["customer_name_ta"] = customer_name_ta
        return d


class IopTransactionRepo:
    def __init__(self, session: Session):
        self.session = session

    def get_by_date(self, collection_date: date):
        rows = self.session.exec(
            select(IopTransaction, IopNameMap.customer_name_en, IopNameMap.customer_name_ta)
            .outerjoin(IopNameMap, IopNameMap.customer_id == IopTransaction.customer_id)
            .where(IopTransaction.collection_date == collection_date)
        ).all()
        return [self._enrich(txn, en, ta) for txn, en, ta in rows]

    def get_by_id(self, transaction_id: int) -> Optional[IopTransaction]:
        return self.session.get(IopTransaction, transaction_id)

    def get_by_customer(self, customer_id: int):
        return self.session.exec(
            select(IopTransaction).where(IopTransaction.customer_id == customer_id)
            .order_by(IopTransaction.collection_date.desc())
        ).all()

    def get_by_customer_and_date(self, customer_id: int, collection_date: date) -> Optional[IopTransaction]:
        return self.session.exec(
            select(IopTransaction).where(
                IopTransaction.customer_id == customer_id,
                IopTransaction.collection_date == collection_date,
            )
        ).first()

    def create(self, data: dict) -> IopTransaction:
        txn = IopTransaction(**data)
        self.session.add(txn)
        self.session.commit()
        self.session.refresh(txn)
        return txn

    def update(self, txn: IopTransaction, data: dict) -> IopTransaction:
        for k, v in data.items():
            if v is not None:
                setattr(txn, k, v)
        self.session.add(txn)
        self.session.commit()
        self.session.refresh(txn)
        return txn

    def delete(self, txn: IopTransaction) -> None:
        self.session.delete(txn)
        self.session.commit()

    @staticmethod
    def _enrich(txn: IopTransaction, customer_name_en: str | None, customer_name_ta: str | None) -> dict:
        d = txn.model_dump()
        d["customer_name"] = customer_name_en
        d["customer_name_ta"] = customer_name_ta
        return d
