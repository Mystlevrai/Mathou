"""File serielle : un job a la fois. (nom jobqueue pour ne pas masquer le module stdlib `queue`)"""
from __future__ import annotations

import asyncio
import uuid

import db
import pipeline
from config import Config


class Runner:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.current: str | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def submit(self, url: str, vpn_country: str | None = None, player: str | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        db.job_create(job_id, url, None, vpn_country, player)
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
            return "en cours"  # tuer l'outil = manuel sur la VM, cf README
        return job["status"]

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            job = db.job_get(job_id)
            if not job or job["status"] != "queued":
                self.queue.task_done()
                continue
            self.current = job_id
            try:
                await asyncio.to_thread(pipeline.process, job_id, job["url"],
                                        self.cfg, job.get("vpn_country"), job.get("player"))
            finally:
                self.current = None
                self.queue.task_done()
