"""Coeur d'un job : cdlr -> zip -> rclone(B2) -> upsert catalogue -> sync catalogue."""
from __future__ import annotations

import subprocess
import threading
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


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _newest_mtime(p: Path) -> float:
    return max((f.stat().st_mtime for f in p.rglob("*") if f.is_file()), default=0.0)


class _CountingWriter:
    """Compte les octets ecrits vers le pipe rcat, et rapporte la progression."""

    def __init__(self, fp, job_id: str, total: int) -> None:
        self.fp = fp
        self.n = 0
        self._job = job_id
        self._total = total
        self._last = 0.0

    def write(self, b) -> int:
        self.fp.write(b)
        self.n += len(b)
        now = time.monotonic()
        if now - self._last >= 3:
            self._last = now
            db.job_update(self._job, progress_bytes=self.n, progress_total=self._total)
        return len(b)

    def flush(self) -> None:
        self.fp.flush()

    def tell(self) -> int:
        return self.n

    def seekable(self) -> bool:
        return False


def _dur(s: float) -> str:
    m, sec = divmod(int(s), 60)
    return f"{m}m{sec:02d}s" if m else f"{sec}s"


def process(job_id: str, url: str, season: int | None, cfg: Config) -> None:
    local_folder: Path | None = None
    try:
        cfg.tool_output_dir.mkdir(parents=True, exist_ok=True)
        ignore_dirs = {cfg.tool_cwd.name, ".git", ".github", "__pycache__", "_mathou_zips"}

        # 1. cdlr
        cmd = [cfg.tool_path, cfg.tool_url_flag, url]
        if season is not None:
            cmd += [cfg.tool_season_flag, str(season)]
        cmd += list(cfg.tool_extra)
        cmd_line = subprocess.list2cmdline(cmd)
        print(f"[{job_id}] cmd> {cmd_line}", flush=True)
        db.job_update(job_id, status="running", log=f"cmd> {cmd_line}")

        t0 = time.monotonic()
        t_wall = time.time()
        # pendant cdlr : thread qui rapporte la taille du dossier en cours (pas de total connu)
        _stop = threading.Event()

        def _watch_dl() -> None:
            while not _stop.wait(5):
                try:
                    cands = [
                        p for p in cfg.tool_output_dir.iterdir()
                        if p.is_dir() and not p.name.startswith(".") and p.name not in ignore_dirs
                    ]
                    cands = [p for p in cands if _newest_mtime(p) >= t_wall - 60]
                    if cands:
                        db.job_update(job_id, progress_bytes=_dir_size(max(cands, key=_newest_mtime)),
                                      progress_total=None)
                except Exception:  # noqa: BLE001
                    pass

        watcher = threading.Thread(target=_watch_dl, daemon=True)
        watcher.start()
        try:
            proc = _run(cmd, cfg.tool_timeout, cfg.tool_cwd)
        except subprocess.TimeoutExpired:
            _stop.set()
            db.job_update(job_id, status="error", error=f"cdlr a depasse {cfg.tool_timeout}s")
            return
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            _stop.set()
            db.job_update(
                job_id, status="error",
                error=f"Impossible de lancer cdlr : {exc}. "
                      f"exe={cfg.tool_path!r} cwd={cfg.tool_cwd!r}",
            )
            return
        finally:
            _stop.set()
        dl_secs = time.monotonic() - t0
        log = _tail(f"cmd> {cmd_line}", proc.stdout, proc.stderr)
        if proc.returncode != 0:
            db.job_update(job_id, status="error", error=f"cdlr code {proc.returncode}", log=log)
            return

        # 2. le dossier cree/rempli par cdlr = le sous-dossier NON VIDE dont un fichier
        #    a ete ecrit le plus recemment (robuste si un dossier du meme nom existait deja).
        candidates = [
            p for p in cfg.tool_output_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".") and p.name not in ignore_dirs
        ]
        # non vide ET rempli pendant CE job (evite de re-uploader un vieux dossier)
        candidates = [p for p in candidates if _dir_size(p) > 0 and _newest_mtime(p) >= t_wall - 60]
        if not candidates:
            db.job_update(job_id, status="error",
                          error="cdlr s'est termine mais aucun dossier n'a ete rempli pendant le job",
                          log=log)
            return
        local_folder = max(candidates, key=_newest_mtime)
        size = _dir_size(local_folder)
        episodes = sum(1 for f in local_folder.rglob("*") if f.suffix.lower() in VIDEO_EXT) or \
            sum(1 for f in local_folder.rglob("*") if f.is_file())
        title, slug = library.parse_series(local_folder.name, cfg.series_regex)
        print(f"[{job_id}] serie={title!r} slug={slug} saison={season} "
              f"{episodes} ep. {size/1024**3:.1f} Go en {_dur(dl_secs)}", flush=True)

        # 3+4. zip streame directement vers B2 via `rclone rcat` -> AUCUN fichier zip
        # local (le disque de la VM ne peut pas contenir source + zip).
        stem = f"{title} - Saison {season:02d}" if season is not None else title
        zip_name = f"{stem}.zip".translate(_BAD)
        dest = f"{cfg.rclone_remote}:{cfg.b2_bucket}/{slug}/{zip_name}"
        db.job_update(job_id, status="uploading", series_slug=slug, size_bytes=size,
                      dl_seconds=dl_secs, zip_name=zip_name,
                      progress_bytes=0, progress_total=size)
        print(f"[{job_id}] stream zip -> {dest}", flush=True)

        t2 = time.monotonic()
        # stdin BINAIRE (le zip ecrit des bytes) ; stderr capture en bytes puis decode.
        rcat = subprocess.Popen(
            # -q : silencieux sauf erreurs (sinon stderr peut saturer 64K et bloquer le stream)
            [cfg.rclone_path, "rcat", dest, "-q", "--stats", "0",
             "--b2-chunk-size", "100M", "--b2-upload-concurrency", "8",
             "--retries", "3", "--low-level-retries", "10"],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(cfg.tool_cwd),
        )
        zip_err: Exception | None = None
        try:
            sink = _CountingWriter(rcat.stdin, job_id, size)
            with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                for f in sorted(local_folder.rglob("*")):
                    if f.is_file():
                        z.write(f, str(f.relative_to(local_folder.parent)))
        except Exception as exc:  # noqa: BLE001 - souvent = rcat mort, le vrai motif est dans stderr
            zip_err = exc
        finally:
            try:
                rcat.stdin.close()
            except Exception:  # noqa: BLE001
                pass
        rcat_err = (rcat.stderr.read() or b"").decode("utf-8", "replace").strip() if rcat.stderr else ""
        rc_code = rcat.wait()
        up_secs = time.monotonic() - t2
        if rc_code != 0:
            db.job_update(job_id, status="error",
                          error=f"rclone rcat code {rc_code} : {rcat_err[-800:]}",
                          log=_tail(log, rcat_err))
            return
        if zip_err is not None:
            db.job_update(job_id, status="error",
                          error=f"Erreur pendant le zip : {zip_err}", log=_tail(log, rcat_err))
            return
        download_url = f"{cfg.b2_public_base}/{slug}/{urllib.parse.quote(zip_name)}"

        # 5. catalogue
        tmdb = library.tmdb_lookup(title, cfg.tmdb_api_key)
        db.series_upsert(slug, title, tmdb.get("tmdb_id"), tmdb.get("poster_url"), tmdb.get("overview"))
        db.season_upsert(slug, season if season is not None else 0, zip_name, download_url, size, episodes)
        catalog.build(cfg.catalog_local, cfg.site_name)
        cat = _run([cfg.rclone_path, "sync", str(cfg.catalog_local),
                    f"{cfg.rclone_remote}:{cfg.b2_bucket}/catalog",
                    "--transfers", "8", "--stats-one-line"], timeout=600)
        log = _tail(log, cat.stdout, cat.stderr)

        timing = (f"dl {_dur(dl_secs)} | zip+upload {_dur(up_secs)} | {size / 1024**3:.1f} Go")
        print(f"[{job_id}] {timing}", flush=True)
        db.job_update(job_id, status="done", download_url=download_url,
                      up_seconds=up_secs, log=_tail(log, timing))

    except Exception as exc:  # noqa: BLE001
        db.job_update(job_id, status="error", error=f"Erreur interne : {exc}")
    finally:
        if not cfg.keep_local and local_folder and local_folder.is_dir():
            _rmtree(local_folder)


def _rmtree(p: Path) -> None:
    import shutil

    shutil.rmtree(p, ignore_errors=True)
