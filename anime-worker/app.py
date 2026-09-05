"""API du worker anime-sama/nakanime. Pas de panel /admin ici : la base (et
donc le catalogue) est partagee avec le worker mathou, qui expose deja /admin
sur son propre port pour l'editer (meme donnees, un seul panel a maintenir)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, field_validator

import db
from config import Config
from jobqueue import Runner

cfg = Config.load()
runner = Runner(cfg)
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_BRIDGE = Path(__file__).with_name("tool_bridge.py")


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


app = FastAPI(title="anime-worker", lifespan=lifespan)


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
    vpn: str | None = None
    player: str | None = None

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


def _run_bridge(action: str, arg: str, timeout: int = 40) -> list:
    """Appelle tool_bridge.py avec TOOL_PYTHON/TOOL_CWD (bloquant - a lancer via
    asyncio.to_thread pour ne pas geler le reste de l'API pendant une recherche)."""
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        r = subprocess.run(
            [cfg.tool_python, str(_BRIDGE), action, arg],
            cwd=str(cfg.tool_cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Recherche trop longue, reessaie")
    out = (r.stdout or "").strip()
    try:
        data = json.loads(out) if out else None
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        if err in ("cloudflare_cookies_missing", "cloudflare_cookies_expired"):
            raise HTTPException(
                502, "Cloudflare bloque anime-sama : relance main.py a la main sur la "
                     "VM (dans anime-downloader) pour rafraichir le cookie."
            )
        raise HTTPException(502, f"Recherche echouee : {err}")
    if not isinstance(data, list):
        raise HTTPException(
            502, f"Reponse invalide de l'outil (code {r.returncode}) : {(r.stderr or r.stdout)[-400:]}"
        )
    return data


@app.get("/search-anime", dependencies=[Depends(auth)])
async def search_anime_ep(q: str):
    data = await asyncio.to_thread(_run_bridge, "search", q)
    return {"results": data[:20]}


@app.get("/search-seasons", dependencies=[Depends(auth)])
async def search_seasons_ep(url: str):
    data = await asyncio.to_thread(_run_bridge, "seasons", url)
    return {"results": data[:25]}


@app.get("/search-players", dependencies=[Depends(auth)])
async def search_players_ep(url: str):
    data = await asyncio.to_thread(_run_bridge, "players", url)
    return {"results": data[:25]}


@app.post("/jobs", dependencies=[Depends(auth)])
async def create_job(body: JobIn):
    # deduplication : meme URL (saison/langue deja dedans) deja en ligne ou en cours -> pas de 2e job
    dup = db.job_find_duplicate(body.url)
    if dup and dup["kind"] == "done":
        return {"job_id": dup["id"], "status": "duplicate_done",
                "download_url": dup["download_url"], "series_slug": dup["series_slug"],
                "zip_name": dup["zip_name"], "size_bytes": dup["size_bytes"],
                "season": dup.get("season")}
    if dup and dup["kind"] == "active":
        return {"job_id": dup["id"], "status": "duplicate_active"}
    if db.jobs_active() >= cfg.max_queue:
        raise HTTPException(429, "File pleine, reessaie plus tard")
    job_id = await runner.submit(body.url, (body.vpn or "").strip() or None, (body.player or "").strip() or None)
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
        "vpn_country": job.get("vpn_country"),
        "player": job.get("player"),
        "series_slug": job.get("series_slug"),
        "zip_name": job.get("zip_name"),
        "size_bytes": job.get("size_bytes"),
        "dl_seconds": job.get("dl_seconds"),
        "zip_seconds": job.get("zip_seconds"),
        "up_seconds": job.get("up_seconds"),
        "download_url": job.get("download_url"),
        "progress_bytes": job.get("progress_bytes"),
        "progress_total": job.get("progress_total"),
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
