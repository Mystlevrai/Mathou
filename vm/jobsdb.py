from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).with_name("jobs.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    num          INTEGER,
    status       TEXT NOT NULL,
    drive_link   TEXT,
    error        TEXT,
    log          TEXT,
    folder       TEXT,
    size_bytes   INTEGER,
    dl_seconds   REAL,
    up_seconds   REAL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


# Colonnes ajoutees apres coup : ajoutees a la volee si une vieille base existe deja.
_MIGRATIONS = {
    "num": "INTEGER",
    "folder": "TEXT",
    "size_bytes": "INTEGER",
    "dl_seconds": "REAL",
    "up_seconds": "REAL",
}


def init() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)
        existing = {row["name"] for row in c.execute("PRAGMA table_info(jobs)")}
        for col, coltype in _MIGRATIONS.items():
            if col not in existing:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {coltype}")


def create(job_id: str, url: str, num: int | None = None) -> None:
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO jobs (id, url, num, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (job_id, url, num, "queued", now, now),
        )


def update(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _conn() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def get(job_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def count_active() -> int:
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running','uploading')"
        ).fetchone()
    return int(row["n"])


def requeue_orphans() -> None:
    """Au redemarrage, la file en memoire est vide : les jobs non termines ne peuvent
    pas reprendre. On les marque en erreur plutot que de les laisser bloques."""
    with _conn() as c:
        c.execute(
            "UPDATE jobs SET status='error', error='Serveur redemarre pendant le job', updated_at=? "
            "WHERE status IN ('running','uploading','queued')",
            (time.time(),),
        )
