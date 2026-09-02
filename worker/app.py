"""API du worker v2."""
from __future__ import annotations

import contextlib
import re
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, field_validator

import db
from config import Config
from jobqueue import Runner

cfg = Config.load()
runner = Runner(cfg)
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def valid_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw or len(raw) > 2000 or _CTRL.search(raw):
        raise ValueError("URL invalide")
    p = urlparse(raw)
    if p.scheme not in {"http", "https"} or not p.netloc:
        raise ValueError("URL invalide (http/https uniquement)")
    return raw


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    db.jobs_reset_orphans()
    runner.start()
    try:
        yield
    finally:
        await runner.stop()


app = FastAPI(title="mathou worker v2", lifespan=lifespan)


def auth(
    request: Request,
    authorization: str = Header(default=""),
    x_auth_token: str = Header(default=""),
) -> None:
    if cfg.allowed_ips:
        ip = request.client.host if request.client else ""
        if ip not in cfg.allowed_ips and ip not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(403, "IP non autorisee")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    token = token or x_auth_token.strip()
    if token != cfg.api_token:
        raise HTTPException(401, "Jeton invalide")


class JobIn(BaseModel):
    url: str
    season: int | None = None

    @field_validator("url")
    @classmethod
    def _v(cls, v: str) -> str:
        try:
            return valid_url(v)
        except ValueError as e:
            raise ValueError(str(e)) from e


@app.get("/healthz")
async def healthz():
    return {"ok": True, "active": db.jobs_active(), "current": runner.current}


@app.post("/jobs", dependencies=[Depends(auth)])
async def create_job(body: JobIn):
    if db.jobs_active() >= cfg.max_queue:
        raise HTTPException(429, "File pleine, reessaie plus tard")
    job_id = await runner.submit(body.url, body.season)
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
async def get_job(job_id: str):
    job = db.job_get(job_id)
    if not job:
        raise HTTPException(404, "Job inconnu")
    log = job.get("log") or ""
    return {
        "job_id": job["id"],
        "status": job["status"],
        "url": job["url"],
        "season": job.get("season"),
        "series_slug": job.get("series_slug"),
        "zip_name": job.get("zip_name"),
        "size_bytes": job.get("size_bytes"),
        "dl_seconds": job.get("dl_seconds"),
        "zip_seconds": job.get("zip_seconds"),
        "up_seconds": job.get("up_seconds"),
        "download_url": job.get("download_url"),
        "error": job.get("error"),
        "log_tail": log[-1500:],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }


@app.post("/jobs/{job_id}/cancel", dependencies=[Depends(auth)])
async def cancel_job(job_id: str):
    return {"result": runner.cancel(job_id)}


@app.get("/library", dependencies=[Depends(auth)])
async def get_library():
    return {"series": db.library()}


@app.get("/search", dependencies=[Depends(auth)])
async def get_search(q: str):
    return {"results": db.search(q)}
