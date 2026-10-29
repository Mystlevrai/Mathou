"""File serielle : un job a la fois. (nom jobqueue pour ne pas masquer le module stdlib `queue`)"""
from __future__ import annotations

import asyncio
import uuid

import db
import pipeline
from config import Config
from vmlock import download_slot


class Runner:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.current: str | None = None
        # job sorti de la file mais qui attend que l'autre worker libere la VM
        self.waiting: str | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def submit(self, url: str, season: int | None, vpn_country: str | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        db.job_create(job_id, url, season, None, vpn_country)
        await self.queue.put(job_id)
        return job_id

    def cancel(self, job_id: str) -> str:
        job = db.job_get(job_id)
        if not job:
            return "inconnu"
        if job["status"] == "queued":
            db.job_update(job_id, status="error", error="Annule avant demarrage")
            return "annule"
        if job["status"] in {"running", "zipping", "uploading"}:
            return "en cours"  # tuer cdlr.exe = manuel sur la VM, cf README
        return job["status"]

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await asyncio.to_thread(self._run_job, job_id)
            finally:
                self.queue.task_done()

    def _run_job(self, job_id: str) -> None:
        """Thread du job : attend le creneau de telechargement de la VM (partage avec
        l'autre worker, cf vmlock.py), puis lance le pipeline."""
        def _on_wait() -> None:
            print(f"[{job_id}] en attente : l'autre worker telecharge deja", flush=True)

        job = db.job_get(job_id)
        if not job or job["status"] != "queued":  # annule pendant qu'il etait en file
            return
        self.waiting = job_id
        try:
            with download_slot(_on_wait):
                self.waiting = None
                # statut relu APRES l'attente : le job a pu etre annule entre-temps
                job = db.job_get(job_id)
                if not job or job["status"] != "queued":
                    return
                self.current = job_id
                try:
                    pipeline.process(job_id, job["url"], job["season"], self.cfg, job.get("vpn_country"))
                finally:
                    self.current = None
        finally:
            self.waiting = None
