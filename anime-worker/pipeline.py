"""Coeur d'un job : Anime-Downloader (anime-sama/nakanime) -> zip -> rclone(B2)
-> upsert catalogue -> sync catalogue."""
from __future__ import annotations

import re
import subprocess
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path

import db
import library
import storage
from config import Config

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".ts", ".m4v", ".webm", ".mov"}
_BAD = str.maketrans({c: "_" for c in '\\/:*?"<>|'})
# pays VPN : lettres/chiffres/espace/-/_ uniquement -> aucun metacaractere shell ne passe
_VPN_COUNTRY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,30}$")


def _parse_url(url: str) -> tuple[str, str] | None:
    """(anime_name, saison_info) tels que l'outil les derive lui-meme de l'URL :
    anime-sama -> .../catalogue/<anime>/<saison>/... ; nakanime -> .../anime/<nom>/[season/N].
    None si l'URL n'a pas une forme exploitable (il en faut une complete, saison incluse)."""
    m = re.search(r"catalogue/([^/]+)/([^/]+)", url)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"nakanime\.(?:tv|fr)/anime/([^/?#]+)", url, re.IGNORECASE)
    if m:
        sm = re.search(r"/season/(\d+)", url)
        saison = f"saison{sm.group(1)}" if sm else "saison1"
        return m.group(1), saison
    return None


def _season_num_and_label(saison_info: str) -> tuple[int, str | None]:
    """Numero de saison pour la base + label perso si le nom ne suit pas le format standard
    "saisonN" (ex: nom de langue colle, variante) - garde l'info visible plutot que la perdre."""
    m = re.match(r"saison(\d+)$", saison_info, re.IGNORECASE)
    if m:
        return int(m.group(1)), None
    m = re.search(r"(\d+)", saison_info)
    return (int(m.group(1)) if m else 0), saison_info


def _run(cmd: list[str], timeout: int | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=str(cwd) if cwd else None, check=False,
    )


def _run_shell(cmd_str: str, timeout: int | None = None) -> subprocess.CompletedProcess:
    """Commande VPN : template .env (peut contenir des && ), donc shell=True.
    Le pays injecte a deja ete valide par _VPN_COUNTRY_RE (pas de metacaractere)."""
    return subprocess.run(
        cmd_str, shell=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )


def _run_tool(cmd: list[str], timeout: int | None, cwd: Path | None) -> subprocess.CompletedProcess:
    """Anime-Downloader : stdio herite (-> worker.log), stdin neutre (--url/--episodes/--player
    sont fournis pour rester non-interactif ; DEVNULL coupe court si jamais il demandait un input)."""
    return subprocess.run(
        cmd, stdin=subprocess.DEVNULL,
        timeout=timeout, cwd=str(cwd) if cwd else None, check=False,
    )


def _tail(*chunks: str, limit: int = 6000) -> str:
    return "\n".join(c.strip() for c in chunks if c and c.strip())[-limit:]


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _kill(names: tuple[str, ...]) -> None:
    """Tue d'eventuels process residuels d'un job precedent."""
    for name in names:
        exe = name if name.lower().endswith(".exe") else f"{name}.exe"
        try:
            subprocess.run(["taskkill", "/F", "/T", "/IM", exe],
                           capture_output=True, timeout=20, check=False)
        except Exception:  # noqa: BLE001
            pass


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


def process(job_id: str, url: str, cfg: Config, vpn_country: str | None = None,
            player: str | None = None) -> None:
    local_folder: Path | None = None
    vpn_on = False
    try:
        parsed = _parse_url(url)
        if not parsed:
            db.job_update(
                job_id, status="error",
                error="URL non reconnue : il faut l'URL complete, saison incluse "
                      "(ex: https://anime-sama.fr/catalogue/<anime>/saison1/vostfr/) ou nakanime",
            )
            return
        anime_name, saison_info = parsed
        season_num, season_label = _season_num_and_label(saison_info)
        title = " ".join(w.capitalize() for w in anime_name.replace("_", "-").split("-"))
        slug = library.slugify(anime_name)
        cfg.tool_output_dir.mkdir(parents=True, exist_ok=True)
        local_folder = cfg.tool_output_dir / anime_name / saison_info

        # 0. repartir propre : tuer d'eventuels residus (ffmpeg...) d'un job precedent
        if cfg.pre_job_kill:
            print(f"[{job_id}] cleanup process : {', '.join(cfg.pre_job_kill)}", flush=True)
            _kill(cfg.pre_job_kill)
            time.sleep(2)

        # 0.5 VPN (optionnel) : si un pays est demande, on se connecte avant le telechargement.
        vpn_country = (vpn_country or "").strip()
        if vpn_country:
            if not cfg.vpn_connect_cmd:
                db.job_update(job_id, status="error",
                              error="Option VPN demandee mais VPN_CONNECT_CMD absent de worker/.env")
                return
            if not _VPN_COUNTRY_RE.match(vpn_country):
                db.job_update(job_id, status="error",
                              error=f"Pays VPN invalide : {vpn_country!r} "
                                    f"(lettres/chiffres/espace/-/_ uniquement)")
                return
            allowed = cfg.vpn_allowed_countries
            if allowed and vpn_country.lower() not in allowed:
                db.job_update(job_id, status="error",
                              error=f"Pays VPN non autorise : {vpn_country}. "
                                    f"Autorises : {', '.join(allowed)}")
                return
            conn_cmd = cfg.vpn_connect_cmd.replace("{country}", vpn_country)
            print(f"[{job_id}] VPN connect -> {vpn_country}", flush=True)
            try:
                r = _run_shell(conn_cmd, cfg.vpn_timeout)
            except subprocess.TimeoutExpired:
                db.job_update(job_id, status="error",
                              error=f"VPN : connexion a {vpn_country} > {cfg.vpn_timeout}s")
                return
            if r.returncode != 0:
                db.job_update(job_id, status="error",
                              error=f"VPN : echec connexion {vpn_country} (code {r.returncode}) "
                                    f"{_tail(r.stdout, r.stderr)[-400:]}")
                return
            vpn_on = True
            time.sleep(3)

        # 1. Anime-Downloader (pas de navigateur : requests + ffmpeg. Non-interactif grace a
        # --url/--episodes/--player ; --no-mal evite sa propre recherche MyAnimeList, on a TMDB).
        cmd = [
            cfg.tool_python, str(cfg.tool_script),
            "--url", url, "--dest", str(cfg.tool_output_dir),
            "--episodes", "all", "--player", (player or cfg.tool_player),
            "--threads", "--fast", "--mp4", "--tool", "ffmpeg", "--no-mal",
        ] + list(cfg.tool_extra)
        cmd_line = subprocess.list2cmdline(cmd)
        print(f"[{job_id}] cmd> {cmd_line}", flush=True)
        db.job_update(job_id, status="running", season=season_num, log=f"cmd> {cmd_line}")

        t0 = time.monotonic()
        _stop = threading.Event()
        est_total = int(cfg.season_est_gb * 1024**3)

        def _watch_dl() -> None:
            while not _stop.wait(5):
                try:
                    if local_folder.is_dir():
                        cur = _dir_size(local_folder)
                        # barre estimee : plafonne a 99% tant que l'outil n'a pas fini
                        db.job_update(job_id, progress_bytes=min(cur, int(est_total * 0.99)),
                                      progress_total=max(est_total, cur))
                except Exception:  # noqa: BLE001
                    pass

        watcher = threading.Thread(target=_watch_dl, daemon=True)
        watcher.start()
        try:
            proc = _run_tool(cmd, cfg.tool_timeout, cfg.tool_cwd)
        except subprocess.TimeoutExpired:
            _stop.set()
            db.job_update(job_id, status="error", error=f"l'outil a depasse {cfg.tool_timeout}s")
            return
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            _stop.set()
            db.job_update(
                job_id, status="error",
                error=f"Impossible de lancer l'outil : {exc}. "
                      f"python={cfg.tool_python!r} script={cfg.tool_script!r} cwd={cfg.tool_cwd!r}",
            )
            return
        finally:
            _stop.set()
        dl_secs = time.monotonic() - t0
        log = _tail(f"cmd> {cmd_line}")
        if proc.returncode != 0:
            db.job_update(job_id, status="error",
                          error=f"l'outil a quitte avec le code {proc.returncode} (sortie dans worker.log)",
                          log=log)
            return

        # 2. dossier attendu = deterministe (derive de l'URL, pas de devinette).
        if not local_folder.is_dir() or _dir_size(local_folder) == 0:
            db.job_update(job_id, status="error",
                          error=f"l'outil a fini mais le dossier attendu est vide ou introuvable : "
                                f"{local_folder}",
                          log=log)
            return
        size = _dir_size(local_folder)
        episodes = sum(1 for f in local_folder.rglob("*") if f.suffix.lower() in VIDEO_EXT) or \
            sum(1 for f in local_folder.rglob("*") if f.is_file())
        print(f"[{job_id}] serie={title!r} slug={slug} saison={season_num} "
              f"{episodes} ep. {size/1024**3:.1f} Go en {_dur(dl_secs)}", flush=True)

        # 3+4. zip streame directement vers B2 via `rclone rcat` -> AUCUN fichier zip
        # local (le disque de la VM ne peut pas contenir source + zip).
        stem = f"{title} - {season_label}" if season_label else f"{title} - Saison {season_num:02d}"
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
        db.season_upsert(slug, season_num, zip_name, download_url, size, episodes)
        if season_label:
            db.season_update(slug, season_num, label=season_label)
        cat = storage.publish_catalog(cfg)
        log = _tail(log, cat.stdout, cat.stderr)

        timing = (f"dl {_dur(dl_secs)} | zip+upload {_dur(up_secs)} | {size / 1024**3:.1f} Go")
        print(f"[{job_id}] {timing}", flush=True)
        db.job_update(job_id, status="done", download_url=download_url,
                      up_seconds=up_secs, log=_tail(log, timing))

    except Exception as exc:  # noqa: BLE001
        db.job_update(job_id, status="error", error=f"Erreur interne : {exc}")
    finally:
        if vpn_on and cfg.vpn_disconnect_cmd:
            try:
                _run_shell(cfg.vpn_disconnect_cmd, cfg.vpn_timeout)
                print(f"[{job_id}] VPN disconnect", flush=True)
            except Exception:  # noqa: BLE001
                pass
        if not cfg.keep_local and local_folder and local_folder.is_dir():
            _rmtree(local_folder)


def _rmtree(p: Path) -> None:
    import shutil

    shutil.rmtree(p, ignore_errors=True)
