"""
Inserts DataFrames into PostgreSQL via SQLModel session.
"""
import math
from typing import Any

import pandas as pd
from sqlmodel import Session, SQLModel


def _clean_value(v: Any) -> Any:
    """Convert NaN/NaT to None for safe DB insertion."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def bulk_insert(session: Session, model_class: type, df: pd.DataFrame, batch_size: int = 500) -> int:
    """Insert a DataFrame into the given SQLModel table in batches."""
    inserted = 0
    records = df.to_dict(orient="records")

    for i in range(0, len(records), batch_size):
        batch = records[i: i + batch_size]
        objects = [
            model_class(**{k: _clean_value(v) for k, v in row.items()})
            for row in batch
        ]
        session.bulk_save_objects(objects)
        session.commit()
        inserted += len(objects)
        print(f"  Inserted {inserted}/{len(records)} rows into {model_class.__tablename__}")

    return inserted
