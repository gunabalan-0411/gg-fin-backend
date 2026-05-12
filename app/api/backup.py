import os
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from app.api.deps import get_current_user
from app.core.config import settings as app_settings

router = APIRouter()

# In-memory job store (per-process, fine for single-user app)
_jobs: dict[str, dict] = {}


def _db_parts() -> dict:
    parsed = urlparse(app_settings.DATABASE_URL)
    return {
        "host": parsed.hostname or "postgres",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "ggfin",
        "password": parsed.password or "",
        "dbname": (parsed.path or "/gg_fin_db").lstrip("/"),
    }


def _psql(db: dict, sql: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PGPASSWORD": db["password"]}
    return subprocess.run(
        ["psql", "-h", db["host"], "-p", db["port"], "-U", db["user"],
         "-d", db["dbname"], "-c", sql],
        env=env, capture_output=True, text=True,
    )


# ── Export ────────────────────────────────────────────────────────────────

@router.get("/export")
def export_backup(_=Depends(get_current_user)):
    """Run pg_dump and return the SQL file as a download."""
    db = _db_parts()
    filename = f"{datetime.now().strftime('%Y-%m-%d')}_gg_fin_backup.sql"
    tmp_path = f"/tmp/{filename}"
    env = {**os.environ, "PGPASSWORD": db["password"]}
    result = subprocess.run(
        ["pg_dump", "-h", db["host"], "-p", db["port"], "-U", db["user"],
         "-d", db["dbname"], "--clean", "--if-exists", "-f", tmp_path],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"pg_dump failed: {result.stderr}")
    return FileResponse(tmp_path, media_type="application/octet-stream", filename=filename)


# ── Import (background job) ───────────────────────────────────────────────

@router.post("/import")
async def import_backup(
    file: UploadFile = File(...),
    _=Depends(get_current_user),
):
    content = await file.read()
    filename = file.filename or "backup.sql"
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"progress": 5, "status": "running", "message": "Saving file…"}

    t = threading.Thread(target=_do_restore, args=(content, filename, job_id), daemon=True)
    t.start()
    return {"job_id": job_id}


@router.get("/import/status/{job_id}")
def import_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── Restore worker ────────────────────────────────────────────────────────

def _do_restore(content: bytes, filename: str, job_id: str) -> None:
    db = _db_parts()
    tmp_path = None
    try:
        # 1. Write temp file
        with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        _jobs[job_id] = {"progress": 15, "status": "running", "message": "Restoring database…"}

        # 2. Run psql restore
        env = {**os.environ, "PGPASSWORD": db["password"]}
        result = subprocess.run(
            ["psql", "-h", db["host"], "-p", db["port"], "-U", db["user"],
             "-d", db["dbname"], "-f", tmp_path],
            env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            _jobs[job_id] = {
                "progress": 0, "status": "error",
                "message": f"psql failed: {result.stderr[:300]}",
            }
            return

        _jobs[job_id] = {"progress": 75, "status": "running", "message": "Resetting sequences…"}

        # 3. Reset all sequences to match current max IDs (safety net)
        seq_sql = """
DO $$
DECLARE
    r RECORD;
    v_max BIGINT;
BEGIN
    FOR r IN (
        SELECT s.relname AS seq_name,
               t.relname AS tbl_name,
               a.attname AS col_name
        FROM pg_class s
        JOIN pg_depend d ON d.objid = s.oid
                        AND d.classid = 'pg_class'::regclass
                        AND d.deptype = 'a'
        JOIN pg_class t ON t.oid = d.refobjid
        JOIN pg_attribute a ON a.attrelid = t.oid
                           AND a.attnum = d.refobjsubid
        WHERE s.relkind = 'S'
    )
    LOOP
        EXECUTE format(
            'SELECT COALESCE(MAX(%I), 0) FROM %I',
            r.col_name, r.tbl_name
        ) INTO v_max;
        PERFORM setval(r.seq_name::regclass, GREATEST(v_max + 1, 1), false);
    END LOOP;
END $$;
"""
        _psql(db, seq_sql)

        _jobs[job_id] = {"progress": 90, "status": "running", "message": "Verifying foreign keys…"}

        # 4. Validate any unvalidated FK constraints
        validate_sql = """
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN (
        SELECT quote_ident(n.nspname) || '.' || quote_ident(t.relname) AS tbl,
               quote_ident(c.conname) AS con
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE c.contype = 'f' AND NOT c.convalidated
    )
    LOOP
        EXECUTE 'ALTER TABLE ' || r.tbl || ' VALIDATE CONSTRAINT ' || r.con;
    END LOOP;
END $$;
"""
        _psql(db, validate_sql)

        _jobs[job_id] = {
            "progress": 100, "status": "done",
            "message": f"Restored from {filename}",
        }

    except Exception as e:
        _jobs[job_id] = {"progress": 0, "status": "error", "message": str(e)[:300]}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
