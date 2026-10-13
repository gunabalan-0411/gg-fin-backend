"""
Database objects that keep the alias / AI-mismatch mapping tables in step with the customer tables.

Why this lives in the database
------------------------------
Customers are created, closed, renamed, deleted and *re-sequenced* from many places: the Customers tab, the OCR
"Customer Management" step, Excel/Drive import, backup restore, or plain SQL. A trigger on the customer tables is the
only way to guarantee that none of those paths is ever missed (requirement R-25).

Objects (all idempotent — safe to run on every start-up and after every restore)
    fn_ggfin_repoint_mappings(type, name)   point mapping rows at the best customer carrying that name
    fn_ggfin_rename_mappings(type, id, old, new)
    fn_ggfin_customer_sync()                trigger function for INSERT / UPDATE / DELETE on the customer tables
    fn_ggfin_customer_is_active(type, id, name)
    fn_ggfin_resync_all_mappings()          one-time backfill / repair
    trg_ggfin_*                             triggers on tbl_edi_customer / tbl_iop_customer
    vw_customer_alias, vw_customer_ai_mismatch   mapping rows + live `is_customer_active`

Important design constraint: **nothing here may create a dependency on the customer tables**. `pg_dump --clean`
emits plain `DROP TABLE tbl_edi_customer`; a view/FK that depends on that table would make restoring any older
backup fail. Therefore the views read customer data through the SQL function `fn_ggfin_customer_is_active`
(function bodies are not dependency-tracked) instead of joining the customer tables directly.

NOTE: statements are sent with `exec_driver_sql` — keep `%` and `:name` out of this SQL.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

FINANCE_TABLES = (("EDI", "tbl_edi_customer"), ("IOP", "tbl_iop_customer"))
MAPPING_TABLES = ("tbl_customer_alias", "tbl_customer_ai_mismatch")

# ── Tables (used by the Alembic migration; the SQLModel models describe the same shape) ─────────────────────
TABLES_SQL: list[str] = []
for _tbl, _word_col, _short in (
    ("tbl_customer_alias", "alias_name", "customer_alias"),
    ("tbl_customer_ai_mismatch", "mismatch_name", "customer_ai_mismatch"),
):
    TABLES_SQL.append(f"""
CREATE TABLE IF NOT EXISTS {_tbl} (
    id                 SERIAL PRIMARY KEY,
    finance_type       VARCHAR(3)  NOT NULL,
    customer_name      VARCHAR     NOT NULL,
    {_word_col:<18} VARCHAR     NOT NULL,
    active_customer_id INTEGER,
    use_count          INTEGER     NOT NULL DEFAULT 0,
    last_used_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_{_short} UNIQUE (finance_type, customer_name, {_word_col}),
    CONSTRAINT ck_{_short}_type CHECK (finance_type IN ('EDI','IOP'))
)""")
    TABLES_SQL.append(f"CREATE INDEX IF NOT EXISTS ix_{_short}_owner ON {_tbl} (finance_type, customer_name)")
    TABLES_SQL.append(f"CREATE INDEX IF NOT EXISTS ix_{_short}_cid ON {_tbl} (finance_type, active_customer_id)")


# ── Functions ──────────────────────────────────────────────────────────────────────────────────────────────
_FN_REPOINT = """
CREATE OR REPLACE FUNCTION fn_ggfin_repoint_mappings(p_type text, p_name text) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    v_best integer;
    v_rows integer := 0;
    v_n    integer;
BEGIN
    IF p_name IS NULL THEN
        RETURN 0;
    END IF;

    -- Best customer carrying this name: an open account before a closed one, then the lowest id
    -- ("first come, first served").
    IF p_type = 'EDI' THEN
        SELECT c.customer_id INTO v_best FROM tbl_edi_customer c
         WHERE c.customer_name = p_name ORDER BY c.is_closed, c.customer_id LIMIT 1;
    ELSIF p_type = 'IOP' THEN
        SELECT c.customer_id INTO v_best FROM tbl_iop_customer c
         WHERE c.customer_name = p_name ORDER BY c.is_closed, c.customer_id LIMIT 1;
    END IF;

    -- No customer with that name (deleted / not created yet): keep the old id. The live
    -- `is_customer_active` check then reports the row as inactive. (R-26)
    IF v_best IS NULL THEN
        RETURN 0;
    END IF;

    UPDATE tbl_customer_alias
       SET active_customer_id = v_best, updated_at = now()
     WHERE finance_type = p_type AND customer_name = p_name
       AND active_customer_id IS DISTINCT FROM v_best;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    v_rows := v_rows + v_n;

    UPDATE tbl_customer_ai_mismatch
       SET active_customer_id = v_best, updated_at = now()
     WHERE finance_type = p_type AND customer_name = p_name
       AND active_customer_id IS DISTINCT FROM v_best;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    v_rows := v_rows + v_n;

    RETURN v_rows;
END $$"""

_FN_RENAME = """
CREATE OR REPLACE FUNCTION fn_ggfin_rename_mappings(p_type text, p_id integer, p_old text, p_new text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF p_old IS NULL OR p_new IS NULL OR p_old = p_new THEN
        RETURN;
    END IF;

    -- Only rows that follow THIS customer: same finance type, same active id, and still carrying the old name.
    -- Rows that would collide with an existing (type, new name, word) are identical words -> drop the duplicate.
    DELETE FROM tbl_customer_alias a
     WHERE a.finance_type = p_type AND a.active_customer_id = p_id AND a.customer_name = p_old
       AND EXISTS (SELECT 1 FROM tbl_customer_alias b
                    WHERE b.finance_type = p_type AND b.customer_name = p_new AND b.alias_name = a.alias_name);
    UPDATE tbl_customer_alias SET customer_name = p_new, updated_at = now()
     WHERE finance_type = p_type AND active_customer_id = p_id AND customer_name = p_old;

    DELETE FROM tbl_customer_ai_mismatch a
     WHERE a.finance_type = p_type AND a.active_customer_id = p_id AND a.customer_name = p_old
       AND EXISTS (SELECT 1 FROM tbl_customer_ai_mismatch b
                    WHERE b.finance_type = p_type AND b.customer_name = p_new AND b.mismatch_name = a.mismatch_name);
    UPDATE tbl_customer_ai_mismatch SET customer_name = p_new, updated_at = now()
     WHERE finance_type = p_type AND active_customer_id = p_id AND customer_name = p_old;
END $$"""

_FN_SYNC = """
CREATE OR REPLACE FUNCTION fn_ggfin_customer_sync() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_type text := TG_ARGV[0];
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM fn_ggfin_repoint_mappings(v_type, NEW.customer_name);
    ELSIF TG_OP = 'DELETE' THEN
        PERFORM fn_ggfin_repoint_mappings(v_type, OLD.customer_name);
    ELSE
        IF OLD.customer_name IS DISTINCT FROM NEW.customer_name THEN
            PERFORM fn_ggfin_rename_mappings(v_type, NEW.customer_id, OLD.customer_name, NEW.customer_name);
            PERFORM fn_ggfin_repoint_mappings(v_type, OLD.customer_name);
        END IF;
        PERFORM fn_ggfin_repoint_mappings(v_type, NEW.customer_name);
    END IF;
    RETURN NULL;
END $$"""

_FN_IS_ACTIVE = """
CREATE OR REPLACE FUNCTION fn_ggfin_customer_is_active(p_type text, p_id integer, p_name text) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT CASE p_type
        WHEN 'EDI' THEN EXISTS (SELECT 1 FROM tbl_edi_customer c
                                 WHERE c.customer_id = p_id AND c.customer_name = p_name AND NOT c.is_closed)
        WHEN 'IOP' THEN EXISTS (SELECT 1 FROM tbl_iop_customer c
                                 WHERE c.customer_id = p_id AND c.customer_name = p_name AND NOT c.is_closed)
        ELSE false
    END
$$"""

_FN_RESYNC_ALL = """
CREATE OR REPLACE FUNCTION fn_ggfin_resync_all_mappings() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    r       record;
    v_total integer := 0;
BEGIN
    FOR r IN
        SELECT finance_type, customer_name FROM tbl_customer_alias
        UNION
        SELECT finance_type, customer_name FROM tbl_customer_ai_mismatch
    LOOP
        v_total := v_total + fn_ggfin_repoint_mappings(r.finance_type, r.customer_name);
    END LOOP;
    RETURN v_total;
END $$"""

FUNCTIONS_SQL = [_FN_REPOINT, _FN_RENAME, _FN_SYNC, _FN_IS_ACTIVE, _FN_RESYNC_ALL]


# ── Triggers ───────────────────────────────────────────────────────────────────────────────────────────────
def _trigger_sql() -> list[str]:
    out: list[str] = []
    for ftype, table in FINANCE_TABLES:
        short = ftype.lower()
        out += [
            f"DROP TRIGGER IF EXISTS trg_ggfin_{short}_cust_ins ON {table}",
            f"""CREATE TRIGGER trg_ggfin_{short}_cust_ins AFTER INSERT ON {table}
                FOR EACH ROW EXECUTE FUNCTION fn_ggfin_customer_sync('{ftype}')""",
            f"DROP TRIGGER IF EXISTS trg_ggfin_{short}_cust_del ON {table}",
            f"""CREATE TRIGGER trg_ggfin_{short}_cust_del AFTER DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION fn_ggfin_customer_sync('{ftype}')""",
            f"DROP TRIGGER IF EXISTS trg_ggfin_{short}_cust_upd ON {table}",
            # Fires only when id / name / closed-flag change — balance-only updates (every collection save) cost nothing.
            f"""CREATE TRIGGER trg_ggfin_{short}_cust_upd AFTER UPDATE ON {table}
                FOR EACH ROW
                WHEN (OLD.customer_id   IS DISTINCT FROM NEW.customer_id
                   OR OLD.customer_name IS DISTINCT FROM NEW.customer_name
                   OR OLD.is_closed     IS DISTINCT FROM NEW.is_closed)
                EXECUTE FUNCTION fn_ggfin_customer_sync('{ftype}')""",
        ]
    return out


TRIGGERS_SQL = _trigger_sql()


# ── Views ──────────────────────────────────────────────────────────────────────────────────────────────────
def _view_sql(view: str, table: str, word_col: str) -> list[str]:
    return [
        f"DROP VIEW IF EXISTS {view}",
        f"""CREATE VIEW {view} AS
SELECT m.id, m.finance_type, m.customer_name, m.{word_col}, m.active_customer_id,
       fn_ggfin_customer_is_active(m.finance_type, m.active_customer_id, m.customer_name) AS is_customer_active,
       m.use_count, m.last_used_at, m.created_at, m.updated_at
  FROM {table} m""",
    ]


VIEWS_SQL = _view_sql("vw_customer_alias", "tbl_customer_alias", "alias_name") + _view_sql(
    "vw_customer_ai_mismatch", "tbl_customer_ai_mismatch", "mismatch_name"
)

DROP_SQL = (
    ["DROP VIEW IF EXISTS vw_customer_alias", "DROP VIEW IF EXISTS vw_customer_ai_mismatch"]
    + [f"DROP TRIGGER IF EXISTS trg_ggfin_{t.lower()}_cust_{e} ON {tbl}"
       for t, tbl in FINANCE_TABLES for e in ("ins", "del", "upd")]
    + [
        "DROP FUNCTION IF EXISTS fn_ggfin_resync_all_mappings()",
        "DROP FUNCTION IF EXISTS fn_ggfin_customer_sync()",
        "DROP FUNCTION IF EXISTS fn_ggfin_rename_mappings(text, integer, text, text)",
        "DROP FUNCTION IF EXISTS fn_ggfin_repoint_mappings(text, text)",
        "DROP FUNCTION IF EXISTS fn_ggfin_customer_is_active(text, integer, text)",
    ]
)


# ── Public helpers ─────────────────────────────────────────────────────────────────────────────────────────
def _run(conn, statements: list[str]) -> None:
    for stmt in statements:
        conn.exec_driver_sql(stmt)


def create_tables(conn) -> None:
    """Create the two mapping tables (raw SQL, mirrors the SQLModel definitions)."""
    _run(conn, TABLES_SQL)


def ensure_trigram(conn) -> bool:
    """
    Install the optional `pg_trgm` extension used by the large-table matching path (name_mapping_service). Never fatal:
    if the database role may not create extensions the matcher falls back to a token/substring pre-filter.
    """
    try:
        with conn.begin_nested():
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        return True
    except Exception as exc:
        log.warning("pg_trgm not available (%s) — large-table name matching will use the token pre-filter", exc.__class__.__name__)
        return False


def apply(conn) -> None:
    """Create/refresh functions, triggers and views. Idempotent. `conn` is a SQLAlchemy Connection."""
    _run(conn, FUNCTIONS_SQL)
    _run(conn, TRIGGERS_SQL)
    _run(conn, VIEWS_SQL)
    ensure_trigram(conn)


def drop_all(conn) -> None:
    """Remove views, triggers and functions (tables are dropped by the migration)."""
    _run(conn, DROP_SQL)


def resync_all(conn) -> int:
    """Re-point every mapping row. Returns how many rows changed."""
    return int(conn.exec_driver_sql("SELECT fn_ggfin_resync_all_mappings()").scalar() or 0)


def ensure(engine) -> int:
    """
    Make sure tables, functions, triggers and views exist and every pointer is current.

    Called at start-up, after every backup restore and after the Excel migration. Restoring an *older* dump
    re-creates the customer tables without our triggers, so this must be safe to call at any time.
    Returns the number of mapping rows that were re-pointed.
    """
    with engine.begin() as conn:
        create_tables(conn)
        apply(conn)
        return resync_all(conn)
