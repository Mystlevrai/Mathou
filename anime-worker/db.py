"""SQLite : jobs, series, seasons, cache TMDB.

Le catalogue (series/seasons/tmdb_cache) est PARTAGE avec le worker mathou
(cdlr) : DB_PATH pointe par defaut sur mathou/worker/mathou.db, pour que les
deux outils alimentent le meme catalogue. Voir anime-worker/.env DB_PATH.
Consequence acceptee : jobs_reset_orphans() au demarrage de l'un peut, en cas
de collision de timing tres rare, marquer en erreur un job en cours de l'autre
outil (rien de destructif : juste a relancer)."""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv

    load_dotenv(encoding="utf-8-sig")  # idempotent : au cas ou ce module soit importe avant config.py
except ImportError:
    pass

DB_PATH = Path(os.getenv("DB_PATH", "").strip() or str(Path(__file__).with_name("mathou.db")))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    season        INTEGER,
    extra         TEXT,
    vpn_country   TEXT,
    player        TEXT,
    status        TEXT NOT NULL,
    error         TEXT,
    log           TEXT,
    series_slug   TEXT,
    zip_name      TEXT,
    size_bytes    INTEGER,
    dl_seconds    REAL,
    zip_seconds   REAL,
    up_seconds    REAL,
    download_url  TEXT,
    progress_bytes INTEGER,
    progress_total INTEGER,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS series (
    slug        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    tmdb_id     INTEGER,
    poster_url  TEXT,
    overview    TEXT,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS seasons (
    series_slug   TEXT NOT NULL,
    season        INTEGER NOT NULL,
    label         TEXT,
    zip_name      TEXT,
    download_url  TEXT,
    size_bytes    INTEGER,
    episodes      INTEGER,
    added_at      REAL NOT NULL,
    PRIMARY KEY (series_slug, season)
);

CREATE TABLE IF NOT EXISTS tmdb_cache (
    query       TEXT PRIMARY KEY,
    tmdb_id     INTEGER,
    poster_url  TEXT,
    overview    TEXT,
    fetched_at  REAL NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


# colonnes ajoutees apres coup (ALTER si une vieille base existe deja)
_MIGRATIONS = {
    "jobs": {"progress_bytes": "INTEGER", "progress_total": "INTEGER", "vpn_country": "TEXT", "player": "TEXT"},
    "seasons": {"label": "TEXT"},
}


def init() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)
        for table, cols in _MIGRATIONS.items():
            existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, coltype in cols.items():
                if col not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


# --- jobs ---------------------------------------------------------------------

def job_create(job_id: str, url: str, extra: str | None, vpn_country: str | None = None,
               player: str | None = None) -> None:
    """season reste NULL a la creation : Anime-Downloader le derive de l'URL, le pipeline
    le renseigne via job_update() une fois parse (avant meme de lancer le telechargement).
    player=None -> le pipeline utilise TOOL_PLAYER (config) par defaut."""
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO jobs (id, url, extra, vpn_country, player, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (job_id, url, extra, vpn_country, player, "queued", now, now),
        )


def job_update(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _conn() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def job_get(job_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def jobs_active() -> int:
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) n FROM jobs WHERE status IN ('queued','running','zipping','uploading')"
        ).fetchone()
    return int(row["n"])


_ACTIVE = ("queued", "running", "zipping", "uploading")


def _norm_url(u: str) -> str:
    """URL comparable : sans query/fragment, sans slash final, en minuscules."""
    p = urlparse((u or "").strip())
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}".lower()


def job_find_duplicate(url: str) -> dict | None:
    """Meme URL (normalisee - saison/langue deja dans le chemin) qu'un job deja actif ou
    deja termine avec succes. -> {'kind': 'active'|'done', ...} ou None. Actif = prioritaire."""
    target = _norm_url(url)
    with _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, url, season, status, download_url, series_slug, zip_name, size_bytes "
            "FROM jobs ORDER BY updated_at DESC LIMIT 500"
        )]
    same = [r for r in rows if _norm_url(r["url"]) == target]
    for r in same:
        if r["status"] in _ACTIVE:
            return {"kind": "active", **r}
    for r in same:
        if r["status"] == "done" and r["download_url"]:
            return {"kind": "done", **r}
    return None


def jobs_reset_orphans() -> None:
    """Au demarrage : les jobs non termines ne peuvent pas reprendre -> erreur."""
    with _conn() as c:
        c.execute(
            "UPDATE jobs SET status='error', error='Worker redemarre pendant le job', updated_at=? "
            "WHERE status IN ('queued','running','zipping','uploading')",
            (time.time(),),
        )


# --- series / seasons -------------------------------------------------------

def series_upsert(slug: str, title: str, tmdb_id: int | None,
                  poster_url: str | None, overview: str | None) -> None:
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO series (slug, title, tmdb_id, poster_url, overview, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(slug) DO UPDATE SET title=excluded.title, tmdb_id=excluded.tmdb_id, "
            "poster_url=excluded.poster_url, overview=excluded.overview, updated_at=excluded.updated_at",
            (slug, title, tmdb_id, poster_url, overview, now),
        )


def season_upsert(series_slug: str, season: int, zip_name: str, download_url: str,
                  size_bytes: int, episodes: int) -> None:
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO seasons (series_slug, season, zip_name, download_url, size_bytes, episodes, added_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(series_slug, season) DO UPDATE SET zip_name=excluded.zip_name, "
            "download_url=excluded.download_url, size_bytes=excluded.size_bytes, "
            "episodes=excluded.episodes, added_at=excluded.added_at",
            (series_slug, season, zip_name, download_url, size_bytes, episodes, now),
        )


def series_get(slug: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM series WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def season_get(slug: str, season: int) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM seasons WHERE series_slug=? AND season=?", (slug, season)
        ).fetchone()
    return dict(row) if row else None


def series_update(slug: str, **fields: Any) -> bool:
    """Edition manuelle (titre/affiche/resume) depuis le panel admin. Ignore les champs
    non reconnus et les valeurs None (pas touche)."""
    allowed = {"title", "poster_url", "overview"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not fields:
        return False
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _conn() as c:
        cur = c.execute(f"UPDATE series SET {cols} WHERE slug=?", (*fields.values(), slug))
    return cur.rowcount > 0


def season_update(slug: str, season: int, **fields: Any) -> bool:
    allowed = {"zip_name", "download_url", "size_bytes", "episodes", "label"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not fields:
        return False
    cols = ", ".join(f"{k}=?" for k in fields)
    with _conn() as c:
        cur = c.execute(
            f"UPDATE seasons SET {cols} WHERE series_slug=? AND season=?",
            (*fields.values(), slug, season),
        )
    return cur.rowcount > 0


def seasons_merge(src_slug: str, dst_slug: str) -> None:
    """Deplace toutes les saisons de src vers dst (renumerote en cas de collision,
    en gardant le numero d'origine dans le label si aucun label perso n'existait),
    puis supprime la serie source. Ne touche a aucun fichier B2 (download_url reste
    valide, il pointe deja vers le bon .zip peu importe le series_slug en base)."""
    with _conn() as c:
        existing = {r["season"] for r in c.execute(
            "SELECT season FROM seasons WHERE series_slug=?", (dst_slug,)
        )}
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM seasons WHERE series_slug=?", (src_slug,)
        )]
        next_num = (max(existing) + 1) if existing else 1
        now = time.time()
        for row in rows:
            num = row["season"]
            label = row.get("label") or (f"Saison {num:02d}" if num is not None else None)
            if num is None or num in existing:
                num = next_num
                next_num += 1
            existing.add(num)
            c.execute(
                "INSERT INTO seasons (series_slug, season, label, zip_name, download_url, "
                "size_bytes, episodes, added_at) VALUES (?,?,?,?,?,?,?,?)",
                (dst_slug, num, label, row["zip_name"], row["download_url"],
                 row["size_bytes"], row["episodes"], row.get("added_at") or now),
            )
        c.execute("DELETE FROM seasons WHERE series_slug=?", (src_slug,))
        c.execute("DELETE FROM series WHERE slug=?", (src_slug,))


def series_delete(slug: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM seasons WHERE series_slug=?", (slug,))
        c.execute("DELETE FROM series WHERE slug=?", (slug,))


def season_delete(slug: str, season: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM seasons WHERE series_slug=? AND season=?", (slug, season))


def tmdb_delete(query: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM tmdb_cache WHERE query=?", (query,))


def library() -> list[dict]:
    """Toutes les series avec leurs saisons, pret pour le generateur de catalogue."""
    with _conn() as c:
        series = [dict(r) for r in c.execute("SELECT * FROM series ORDER BY title COLLATE NOCASE")]
        seasons = [dict(r) for r in c.execute("SELECT * FROM seasons ORDER BY season")]
    by_slug: dict[str, list[dict]] = {}
    for s in seasons:
        by_slug.setdefault(s["series_slug"], []).append(s)
    for s in series:
        s["seasons"] = by_slug.get(s["slug"], [])
    return series


def search(term: str, limit: int = 25) -> list[dict]:
    like = f"%{term}%"
    with _conn() as c:
        rows = c.execute(
            "SELECT s.slug, s.title, s.poster_url, COUNT(se.season) seasons "
            "FROM series s LEFT JOIN seasons se ON se.series_slug = s.slug "
            "WHERE s.title LIKE ? COLLATE NOCASE "
            "GROUP BY s.slug ORDER BY s.title COLLATE NOCASE LIMIT ?",
            (like, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# --- cache TMDB -----------------------------------------------------------

def tmdb_get(query: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM tmdb_cache WHERE query=?", (query,)).fetchone()
    return dict(row) if row else None


def tmdb_put(query: str, tmdb_id: int | None, poster_url: str | None, overview: str | None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO tmdb_cache (query, tmdb_id, poster_url, overview, fetched_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(query) DO UPDATE SET tmdb_id=excluded.tmdb_id, poster_url=excluded.poster_url, "
            "overview=excluded.overview, fetched_at=excluded.fetched_at",
            (query, tmdb_id, poster_url, overview, time.time()),
        )
