"""Helpers rclone/B2 partages entre pipeline.py (fin de job) et admin.py (edition manuelle)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from config import Config


def run(cmd: list[str], timeout: int | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=str(cwd) if cwd else None, check=False,
    )


def publish_catalog(cfg: Config) -> subprocess.CompletedProcess:
    """Regenere le site statique (depuis la base) et le pousse sur B2."""
    import catalog

    catalog.build(cfg.catalog_local, cfg.site_name)
    return run(
        [cfg.rclone_path, "sync", str(cfg.catalog_local),
         f"{cfg.rclone_remote}:{cfg.b2_bucket}/catalog", "--transfers", "8", "--stats-one-line"],
        timeout=600,
    )


def purge_series(cfg: Config, slug: str) -> subprocess.CompletedProcess:
    """Supprime TOUT le dossier B2 d'une serie (toutes ses saisons)."""
    dest = f"{cfg.rclone_remote}:{cfg.b2_bucket}/{slug}"
    return run([cfg.rclone_path, "purge", dest], timeout=300)


def delete_season_zip(cfg: Config, slug: str, zip_name: str) -> subprocess.CompletedProcess:
    """Supprime le .zip d'une seule saison sur B2."""
    dest = f"{cfg.rclone_remote}:{cfg.b2_bucket}/{slug}/{zip_name}"
    return run([cfg.rclone_path, "deletefile", dest], timeout=300)
