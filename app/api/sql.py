import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import get_current_user
from app.core.database import engine

router = APIRouter()


class QueryRequest(BaseModel):
    sql: str


@router.post("/query")
def run_query(body: QueryRequest, _=Depends(get_current_user)):
    sql = body.sql.strip()
    if not sql:
        raise HTTPException(400, "Query is empty")

    start = time.perf_counter()
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql))
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

            if result.returns_rows:
                columns = list(result.keys())
                rows = [[str(v) if v is not None else None for v in row] for row in result.fetchall()]
                return {
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "elapsed_ms": elapsed_ms,
                    "affected": None,
                }
            else:
                return {
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "elapsed_ms": elapsed_ms,
                    "affected": result.rowcount,
                }
    except Exception as exc:
        raise HTTPException(400, str(exc))


@router.get("/tables")
def list_tables(_=Depends(get_current_user)):
    with engine.connect() as conn:
        tables_result = conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        ))
        tables = [row[0] for row in tables_result]

        schema: dict[str, list[dict[str, str]]] = {}
        for table in tables:
            cols = conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :t AND table_schema = 'public' ORDER BY ordinal_position"
            ), {"t": table})
            schema[table] = [{"name": r[0], "type": r[1]} for r in cols]

        return {"tables": schema}
