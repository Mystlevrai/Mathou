from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import jobsdb
from config import Config


def _run(cmd: list[str], timeout: int | None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
        check=False,
    )


def _tail(*chunks: str, limit: int = 6000) -> str:
    text = "\n".join(c.strip() for c in chunks if c and c.strip())
    return text[-limit:]


def _pick_upload_dir(outdir: Path) -> Path | None:
    """Le dossier passe en {outdir} est cree vide avant l'appel a l'outil.
    Apres coup : s'il ne contient qu'un seul sous-dossier, c'est lui le
    'dossier cree' par l'outil -> on l'upload directement (evite un
    niveau d'imbrication en trop sur Drive). Sinon on upload outdir tel quel."""
    entries = list(outdir.iterdir())
    if not entries:
        return None
    visible = [e for e in entries if not e.name.startswith(".")]
    if len(visible) == 1 and visible[0].is_dir():
        return visible[0]
    return outdir


def _safe_name(name: str) -> str:
    """Nettoie un nom de dossier pour un chemin rclone (enleve / \\ et caracteres de controle)."""
    cleaned = re.sub(r"[\\/\x00-\x1f]", "_", name).strip().strip(".")
    return cleaned[:150]


def _exists_on_drive(cfg: Config, rel_path: str) -> bool:
    r = _run([cfg.rclone_path, "lsf", f"{cfg.rclone_remote}:{rel_path}"], timeout=60)
    return r.returncode == 0


def _dir_size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _human(nbytes: int) -> str:
    x = float(nbytes)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if x < 1024 or unit == "To":
            return f"{x:.1f} {unit}"
        x /= 1024


def _dur(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def _speed(nbytes: int, secs: float) -> str:
    if secs <= 0:
        return "?"
    mbps = (nbytes * 8) / secs / 1_000_000
    return f"{mbps:.0f} Mbps"


def _snapshot(d: Path) -> set[str]:
    try:
        return {p.name for p in d.iterdir()}
    except FileNotFoundError:
        return set()


def _collect_new(watch: Path, before: set[str], job_id: str, work_dir: Path) -> tuple[Path | None, list[Path]]:
    """Apres un outil qui ecrit dans son propre dossier `watch` : repere ce qui
    vient d'y apparaitre. Retourne (dossier_a_uploader, chemins_a_nettoyer)."""
    new = [watch / n for n in (_snapshot(watch) - before)]
    if not new:
        return None, []
    if len(new) == 1 and new[0].is_dir():
        return new[0], [new[0]]
    staging = work_dir / job_id
    staging.mkdir(parents=True, exist_ok=True)
    for p in new:
        shutil.move(str(p), str(staging / p.name))
    return staging, [staging]


def _subst(args: list[str], url: str, outdir: Path | None, num: int | None) -> list[str]:
    out: list[str] = []
    for a in args:
        a = a.replace("{url}", url)
        if outdir is not None:
            a = a.replace("{outdir}", str(outdir))
        if num is not None:
            a = a.replace("{num}", str(num))
        out.append(a)
    return out


def process_job(job_id: str, url: str, num: int | None, cfg: Config) -> None:
    watch_mode = cfg.tool_output_dir is not None
    outdir = cfg.work_dir / job_id
    cleanup: list[Path] = []
    try:
        if watch_mode:
            cfg.tool_output_dir.mkdir(parents=True, exist_ok=True)
            before = _snapshot(cfg.tool_output_dir)
        else:
            outdir.mkdir(parents=True, exist_ok=True)

        jobsdb.update(job_id, status="running")
        sub_outdir = None if watch_mode else outdir
        cmd = _subst(cfg.tool_args, url, sub_outdir, num)
        if num is not None and cfg.tool_args_num:
            cmd += _subst(cfg.tool_args_num, url, sub_outdir, num)
        if cfg.tool_args_end:
            cmd += _subst(cfg.tool_args_end, url, sub_outdir, num)

        cmd_line = subprocess.list2cmdline(cmd)
        cwd_line = str(cfg.tool_cwd) if cfg.tool_cwd else str(Path.cwd())
        print(f"[{job_id}] cwd> {cwd_line}", flush=True)
        print(f"[{job_id}] cmd> {cmd_line}", flush=True)
        run_log = f"cwd> {cwd_line}\ncmd> {cmd_line}"

        t_tool = time.monotonic()
        try:
            proc = _run(cmd, cfg.tool_timeout, cfg.tool_cwd)
        except subprocess.TimeoutExpired:
            jobsdb.update(
                job_id, status="error", error=f"L'outil a depasse {cfg.tool_timeout}s", log=run_log
            )
            return
        except FileNotFoundError:
            jobsdb.update(
                job_id, status="error", error=f"Executable introuvable : {cmd[0]!r}", log=run_log
            )
            return

        dl_secs = time.monotonic() - t_tool
        log = _tail(run_log, proc.stdout, proc.stderr)
        if proc.returncode != 0:
            jobsdb.update(
                job_id, status="error", error=f"L'outil a renvoye le code {proc.returncode}", log=log
            )
            return

        if watch_mode:
            upload_dir, cleanup = _collect_new(cfg.tool_output_dir, before, job_id, cfg.work_dir)
        else:
            upload_dir = _pick_upload_dir(outdir)
            cleanup = [outdir]

        if upload_dir is None:
            jobsdb.update(
                job_id,
                status="error",
                error="L'outil s'est termine mais aucun nouveau fichier n'a ete produit",
                log=log,
            )
            return

        size = _dir_size(upload_dir)
        print(f"[{job_id}] telecharge en {_dur(dl_secs)} ({_human(size)}, {_speed(size, dl_secs)})", flush=True)

        # Nom du dossier Drive = nom du dossier cree par l'outil.
        # S'il existe deja sur Drive : "<nom> Saison0N" si un nombre a ete fourni
        # (les fichiers s'ajoutent dedans), sinon "<nom>-<id>" en secours.
        base = _safe_name(upload_dir.name) or job_id
        folder = base
        if _exists_on_drive(cfg, f"{cfg.drive_dest}/{base}"):
            folder = f"{base} Saison{num:02d}" if num is not None else f"{base}-{job_id}"
        rel = f"{cfg.drive_dest}/{folder}"
        dest = f"{cfg.rclone_remote}:{rel}"

        jobsdb.update(
            job_id,
            status="uploading",
            log=log,
            folder=folder,
            size_bytes=size,
            dl_seconds=dl_secs,
        )
        t_up = time.monotonic()
        rc = _run(
            [
                cfg.rclone_path,
                "copy",
                str(upload_dir),
                dest,
                "--transfers",
                "4",
                "--drive-chunk-size",
                "128M",
                "--drive-upload-cutoff",
                "128M",
                "--retries",
                "3",
                "--low-level-retries",
                "10",
                "--contimeout",
                "30s",
                "--stats-one-line",
                "-v",
            ],
            timeout=None,
        )
        up_secs = time.monotonic() - t_up
        log = _tail(log, rc.stdout, rc.stderr)
        if rc.returncode != 0:
            jobsdb.update(job_id, status="error", error=f"rclone a echoue (code {rc.returncode})", log=log)
            return

        timing = (
            f"telechargement {_dur(dl_secs)} ({_speed(size, dl_secs)}) | "
            f"upload Drive {_dur(up_secs)} ({_speed(size, up_secs)}) | "
            f"taille {_human(size)}"
        )
        print(f"[{job_id}] {timing}", flush=True)
        log = _tail(log, timing)

        drive_link = None
        if cfg.make_share_link:
            lk = _run([cfg.rclone_path, "link", dest], timeout=120)
            if lk.returncode == 0 and lk.stdout.strip():
                drive_link = lk.stdout.strip().splitlines()[-1].strip()
            else:
                log = _tail(log, lk.stdout, lk.stderr)

        jobsdb.update(
            job_id,
            status="done",
            drive_link=drive_link or f"(Drive, pas de lien public) {rel}",
            log=log,
            up_seconds=up_secs,
        )

        if cfg.delete_local_after:
            for p in cleanup:
                shutil.rmtree(p, ignore_errors=True)

    except Exception as exc:  # noqa: BLE001 - on renvoie toute erreur au bot plutot que de crasher le worker
        jobsdb.update(job_id, status="error", error=f"Erreur interne : {exc}")


class JobRunner:
    """File serielle : un job a la fois. Simple et evite de saturer le disque/la bande
    passante de la VM. Augmente MAX_QUEUE cote config si besoin de paralleliser plus tard."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def submit(self, url: str, num: int | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        jobsdb.create(job_id, url, num)
        await self.queue.put(job_id)
        return job_id

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            job = jobsdb.get(job_id)
            if not job or job["status"] != "queued":
                self.queue.task_done()
                continue
            try:
                await asyncio.to_thread(process_job, job_id, job["url"], job["num"], self.cfg)
            finally:
                self.queue.task_done()
