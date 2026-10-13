"""Small SQL helpers shared by the tests."""
from sqlalchemy import text

TABLE = {"EDI": "tbl_edi_customer", "IOP": "tbl_iop_customer"}
NAME_MAP = {"EDI": "tbl_edi_name_map", "IOP": "tbl_iop_name_map"}
KIND_TABLE = {"alias": ("tbl_customer_alias", "alias_name"), "ai_mismatch": ("tbl_customer_ai_mismatch", "mismatch_name")}


def add_customer(conn, ftype: str, cid: int, name: str | None, closed: bool = False, name_map: bool = True):
    """Insert a customer straight into the table (the same thing an import / restore does)."""
    extra_cols, extra_vals = (", principal_paid", ", 0") if ftype == "IOP" else ("", "")
    conn.execute(
        text(f"INSERT INTO {TABLE[ftype]} (customer_id, customer_name, is_closed, ignore{extra_cols}) "
             f"VALUES (:id, :name, :closed, false{extra_vals})"),
        {"id": cid, "name": name, "closed": closed},
    )
    if name_map and name:
        conn.execute(
            text(f"INSERT INTO {NAME_MAP[ftype]} (customer_id, customer_name_en) VALUES (:id, :name) "
                 "ON CONFLICT (customer_id) DO UPDATE SET customer_name_en = EXCLUDED.customer_name_en"),
            {"id": cid, "name": name},
        )


def set_closed(conn, ftype: str, cid: int, closed: bool):
    conn.execute(text(f"UPDATE {TABLE[ftype]} SET is_closed = :c WHERE customer_id = :id"), {"c": closed, "id": cid})


def rename(conn, ftype: str, cid: int, name: str):
    conn.execute(text(f"UPDATE {TABLE[ftype]} SET customer_name = :n WHERE customer_id = :id"), {"n": name, "id": cid})


def delete_customer(conn, ftype: str, cid: int):
    conn.execute(text(f"DELETE FROM {TABLE[ftype]} WHERE customer_id = :id"), {"id": cid})


def add_mapping(conn, kind: str, ftype: str, customer_name: str, word: str, cid: int | None = None,
                use_count: int = 0, last_used: str | None = None, created: str | None = None):
    table, col = KIND_TABLE[kind]
    conn.execute(
        text(f"INSERT INTO {table} (finance_type, customer_name, {col}, active_customer_id, use_count, last_used_at, created_at) "
             "VALUES (:t, :n, :w, :cid, :uc, :lu, COALESCE(:cr, now()))"),
        {"t": ftype, "n": customer_name, "w": word, "cid": cid, "uc": use_count, "lu": last_used, "cr": created},
    )


def pointer(conn, kind: str, ftype: str, customer_name: str, word: str):
    """(active_customer_id, is_customer_active) of one mapping row, read through the live view."""
    table, col = KIND_TABLE[kind]
    view = "vw_customer_alias" if kind == "alias" else "vw_customer_ai_mismatch"
    row = conn.execute(
        text(f"SELECT active_customer_id, is_customer_active FROM {view} "
             f"WHERE finance_type = :t AND customer_name = :n AND {col} = :w"),
        {"t": ftype, "n": customer_name, "w": word},
    ).first()
    return None if row is None else (row[0], row[1])


def rows(conn, kind: str, ftype: str | None = None):
    table, col = KIND_TABLE[kind]
    q = f"SELECT customer_name, {col}, active_customer_id FROM {table}" + (" WHERE finance_type = :t" if ftype else "") + " ORDER BY 1, 2"
    return [tuple(r) for r in conn.execute(text(q), {"t": ftype} if ftype else {}).all()]
