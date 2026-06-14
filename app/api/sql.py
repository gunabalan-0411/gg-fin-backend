import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import get_current_user
from app.core.database import engine

router = APIRouter()

_ALLOWED_PREFIXES = frozenset({"select", "with", "explain"})
_MAX_ROWS = 500


class QueryRequest(BaseModel):
    sql: str


@router.post("/query")
def run_query(body: QueryRequest, _=Depends(get_current_user)):
    sql = body.sql.strip()
    if not sql:
        raise HTTPException(400, "Query is empty")

    first_word = sql.split()[0].lower()
    if first_word not in _ALLOWED_PREFIXES:
        raise HTTPException(403, "Only SELECT / WITH / EXPLAIN queries are permitted")

    start = time.perf_counter()
    try:
        with engine.connect() as conn:          # read-only (no BEGIN/COMMIT)
            result = conn.execute(text(sql))
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

            if result.returns_rows:
                columns = list(result.keys())
                rows = result.fetchmany(_MAX_ROWS)
                return {
                    "columns": columns,
                    "rows": [[str(v) if v is not None else None for v in row] for row in rows],
                    "row_count": len(rows),
                    "elapsed_ms": elapsed_ms,
                    "truncated": len(rows) == _MAX_ROWS,
                }
            return {"columns": [], "rows": [], "row_count": 0, "elapsed_ms": elapsed_ms}
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
