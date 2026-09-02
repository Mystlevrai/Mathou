from __future__ import annotations

import contextlib
import re
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, field_validator

import jobsdb
from config import Config
from worker import JobRunner

cfg = Config.load()
runner = JobRunner(cfg)

_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def valid_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw or len(raw) > 2000 or _CTRL.search(raw):
        raise ValueError("URL invalide")
    p = urlparse(raw)
    if p.scheme not in {"http", "https"} or not p.netloc:
        raise ValueError("URL invalide (http/https uniquement)")
    if cfg.url_allowlist:
        host = (p.hostname or "").lower()
        if not any(host == d or host.endswith("." + d) for d in cfg.url_allowlist):
            raise ValueError(f"Domaine non autorise : {host}")
    return raw


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    jobsdb.init()
    jobsdb.requeue_orphans()
    runner.start()
    try:
        yield
    finally:
        await runner.stop()


app = FastAPI(title="mathou VM worker", lifespan=lifespan)


def auth(
    request: Request,
    authorization: str = Header(default=""),
    x_auth_token: str = Header(default=""),
) -> None:
    if cfg.allowed_ips:
        client_ip = request.client.host if request.client else ""
        if client_ip not in cfg.allowed_ips:
            raise HTTPException(status_code=403, detail="IP non autorisee")
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    token = token or x_auth_token.strip()
    if token != cfg.api_token:
        raise HTTPException(status_code=401, detail="Jeton invalide")


class JobIn(BaseModel):
    url: str
    num: int | None = None

    @field_validator("url")
    @classmethod
    def _v(cls, v: str) -> str:
        try:
            return valid_url(v)
        except ValueError as e:
            raise ValueError(str(e)) from e


@app.get("/healthz")
async def healthz():
    return {"ok": True, "active": jobsdb.count_active()}


@app.post("/jobs", dependencies=[Depends(auth)])
async def create_job(body: JobIn):
    if jobsdb.count_active() >= cfg.max_queue:
        raise HTTPException(status_code=429, detail="File pleine, reessaie plus tard")
    job_id = await runner.submit(body.url, body.num)
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
async def get_job(job_id: str):
    job = jobsdb.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job inconnu")
    log = job.get("log") or ""
    return {
        "job_id": job["id"],
        "status": job["status"],
        "url": job["url"],
        "num": job.get("num"),
        "drive_link": job.get("drive_link"),
        "error": job.get("error"),
        "folder": job.get("folder"),
        "size_bytes": job.get("size_bytes"),
        "dl_seconds": job.get("dl_seconds"),
        "up_seconds": job.get("up_seconds"),
        "log_tail": log[-1500:],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }
