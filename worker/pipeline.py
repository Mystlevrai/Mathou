"""Coeur d'un job : cdlr -> zip -> rclone(B2) -> upsert catalogue -> sync catalogue."""
from __future__ import annotations

import subprocess
import time
import urllib.parse
import zipfile
from pathlib import Path

import catalog
import db
import library
from config import Config

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".ts", ".m4v", ".webm", ".mov"}
_BAD = str.maketrans({c: "_" for c in '\\/:*?"<>|'})


def _run(cmd: list[str], timeout: int | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=str(cwd) if cwd else None, check=False,
    )


def _tail(*chunks: str, limit: int = 6000) -> str:
    return "\n".join(c.strip() for c in chunks if c and c.strip())[-limit:]


def _snapshot(d: Path) -> set[str]:
    try:
        return {p.name for p in d.iterdir()}
    except FileNotFoundError:
        return set()


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _zip_stored(src_dir: Path, dest_zip: Path) -> None:
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for f in sorted(src_dir.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(src_dir.parent))


def _dur(s: float) -> str:
    m, sec = divmod(int(s), 60)
    return f"{m}m{sec:02d}s" if m else f"{sec}s"


def process(job_id: str, url: str, season: int | None, cfg: Config) -> None:
    zips_dir = cfg.tool_output_dir.parent / "_mathou_zips"
    local_folder: Path | None = None
    local_zip: Path | None = None
    try:
        cfg.tool_output_dir.mkdir(parents=True, exist_ok=True)
        before = _snapshot(cfg.tool_output_dir)

        # 1. cdlr
        cmd = [cfg.tool_path, cfg.tool_url_flag, url]
        if season is not None:
            cmd += [cfg.tool_season_flag, str(season)]
        cmd += list(cfg.tool_extra)
        cmd_line = subprocess.list2cmdline(cmd)
        print(f"[{job_id}] cmd> {cmd_line}", flush=True)
        db.job_update(job_id, status="running", log=f"cmd> {cmd_line}")

        t0 = time.monotonic()
        try:
            proc = _run(cmd, cfg.tool_timeout, cfg.tool_cwd)
        except subprocess.TimeoutExpired:
            db.job_update(job_id, status="error", error=f"cdlr a depasse {cfg.tool_timeout}s")
            return
        except FileNotFoundError:
            db.job_update(job_id, status="error", error=f"Executable introuvable : {cfg.tool_path!r}")
            return
        dl_secs = time.monotonic() - t0
        log = _tail(f"cmd> {cmd_line}", proc.stdout, proc.stderr)
        if proc.returncode != 0:
            db.job_update(job_id, status="error", error=f"cdlr code {proc.returncode}", log=log)
            return

        # 2. dossier cree
        new = [cfg.tool_output_dir / n for n in (_snapshot(cfg.tool_output_dir) - before)]
        dirs = [p for p in new if p.is_dir()]
        if not dirs:
            db.job_update(job_id, status="error",
                          error="cdlr s'est termine mais aucun nouveau dossier", log=log)
            return
        local_folder = max(dirs, key=lambda p: _dir_size(p))
        size = _dir_size(local_folder)
        episodes = sum(1 for f in local_folder.rglob("*") if f.suffix.lower() in VIDEO_EXT) or \
            sum(1 for f in local_folder.rglob("*") if f.is_file())
        title, slug = library.parse_series(local_folder.name, cfg.series_regex)
        print(f"[{job_id}] serie={title!r} slug={slug} saison={season} "
              f"{episodes} ep. {size/1024**3:.1f} Go en {_dur(dl_secs)}", flush=True)

        # 3. zip
        db.job_update(job_id, status="zipping", series_slug=slug, size_bytes=size, dl_seconds=dl_secs)
        stem = f"{title} - Saison {season:02d}" if season is not None else title
        zip_name = f"{stem}.zip".translate(_BAD)
        local_zip = zips_dir / zip_name
        t1 = time.monotonic()
        _zip_stored(local_folder, local_zip)
        zip_secs = time.monotonic() - t1

        # 4. upload B2
        db.job_update(job_id, status="uploading", zip_name=zip_name, zip_seconds=zip_secs)
        dest = f"{cfg.rclone_remote}:{cfg.b2_bucket}/{slug}/"
        t2 = time.monotonic()
        rc = _run([cfg.rclone_path, "copy", str(local_zip), dest,
                   "--transfers", "4", "--b2-upload-concurrency", "8",
                   "--retries", "3", "--low-level-retries", "10",
                   "--stats-one-line", "-v"], timeout=None)
        up_secs = time.monotonic() - t2
        log = _tail(log, rc.stdout, rc.stderr)
        if rc.returncode != 0:
            db.job_update(job_id, status="error", error=f"rclone (B2) code {rc.returncode}", log=log)
            return
        download_url = f"{cfg.b2_public_base}/{slug}/{urllib.parse.quote(zip_name)}"

        # 5. catalogue
        tmdb = library.tmdb_lookup(title, cfg.tmdb_api_key)
        db.series_upsert(slug, title, tmdb.get("tmdb_id"), tmdb.get("poster_url"), tmdb.get("overview"))
        db.season_upsert(slug, season if season is not None else 0, zip_name, download_url, size, episodes)
        catalog.build(cfg.catalog_local)
        cat = _run([cfg.rclone_path, "sync", str(cfg.catalog_local),
                    f"{cfg.rclone_remote}:{cfg.b2_bucket}/catalog",
                    "--transfers", "8", "--stats-one-line"], timeout=600)
        log = _tail(log, cat.stdout, cat.stderr)

        timing = (f"dl {_dur(dl_secs)} | zip {_dur(zip_secs)} | "
                  f"upload {_dur(up_secs)} | {size/1024**3:.1f} Go")
        print(f"[{job_id}] {timing}", flush=True)
        db.job_update(job_id, status="done", download_url=download_url,
                      up_seconds=up_secs, log=_tail(log, timing))

    except Exception as exc:  # noqa: BLE001
        db.job_update(job_id, status="error", error=f"Erreur interne : {exc}")
    finally:
        if not cfg.keep_local:
            for p in (local_zip, local_folder):
                if p and p.exists():
                    if p.is_dir():
                        _rmtree(p)
                    else:
                        p.unlink(missing_ok=True)


def _rmtree(p: Path) -> None:
    import shutil

    shutil.rmtree(p, ignore_errors=True)
