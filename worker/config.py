"""Config unique du worker v2. Toutes les valeurs viennent de worker/.env."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    # utf-8-sig : tolere un BOM eventuel (PowerShell 5.1 en ajoute un avec -Encoding utf8)
    load_dotenv(encoding="utf-8-sig")
except ImportError:
    pass


def _req(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Variable d'environnement obligatoire manquante : {name}")
    return v


def _int(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    return int(v) if v else default


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


def _json_list(name: str) -> list[str]:
    raw = os.getenv(name, "").strip().lstrip("﻿").strip().strip("'\"").strip()
    if not raw or raw in ("[]", "[ ]"):
        return []
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        # pas du JSON : on accepte une liste separee par des espaces (ex: --vostfr --hd)
        return raw.split()
    if not (isinstance(val, list) and all(isinstance(x, str) for x in val)):
        raise RuntimeError(f"{name} doit etre un tableau JSON de chaines, ex : [\"--vostfr\"]")
    return val


@dataclass(frozen=True)
class Config:
    api_token: str
    host: str
    port: int
    allowed_ips: tuple[str, ...]

    tool_python: str
    tool_script: Path
    tool_cwd: Path
    tool_output_dir: Path
    tool_player: str
    tool_extra: list[str]
    tool_timeout: int

    rclone_path: str
    rclone_remote: str
    b2_bucket: str
    b2_public_base: str

    tmdb_api_key: str
    catalog_local: Path
    site_name: str

    max_queue: int
    keep_local: bool
    season_est_gb: float
    pre_job_kill: tuple[str, ...]

    vpn_connect_cmd: str
    vpn_disconnect_cmd: str
    vpn_allowed_countries: tuple[str, ...]
    vpn_timeout: int

    admin_token: str

    @staticmethod
    def load() -> "Config":
        ips = os.getenv("ALLOWED_IPS", "").replace(" ", "")
        return Config(
            api_token=_req("API_TOKEN"),
            host=os.getenv("HOST", "0.0.0.0"),
            port=_int("PORT", 8756),
            allowed_ips=tuple(x for x in ips.split(",") if x),
            tool_python=_req("TOOL_PYTHON"),
            tool_script=Path(_req("TOOL_SCRIPT")).resolve(),
            tool_cwd=Path(_req("TOOL_CWD")).resolve(),
            tool_output_dir=Path(_req("TOOL_OUTPUT_DIR")).resolve(),
            tool_player=os.getenv("TOOL_PLAYER", "sendvid").strip() or "sendvid",
            tool_extra=_json_list("TOOL_EXTRA"),
            tool_timeout=_int("TOOL_TIMEOUT", 7200),
            rclone_path=os.getenv("RCLONE_PATH", "rclone"),
            rclone_remote=os.getenv("RCLONE_REMOTE", "b2").strip(),
            b2_bucket=_req("B2_BUCKET"),
            b2_public_base=_req("B2_PUBLIC_BASE").rstrip("/"),
            tmdb_api_key=os.getenv("TMDB_API_KEY", "").strip(),
            catalog_local=Path(os.getenv("CATALOG_LOCAL", r"C:\mathou\catalog")).resolve(),
            site_name=os.getenv("SITE_NAME", "aburame").strip() or "aburame",
            max_queue=_int("MAX_QUEUE", 20),
            keep_local=_bool("KEEP_LOCAL", False),
            season_est_gb=float(os.getenv("SEASON_EST_GB", "").strip() or "14"),
            pre_job_kill=tuple(
                x for x in os.getenv("PRE_JOB_KILL", "ffmpeg").replace(" ", "").split(",") if x
            ),
            vpn_connect_cmd=os.getenv("VPN_CONNECT_CMD", "").strip(),
            vpn_disconnect_cmd=os.getenv("VPN_DISCONNECT_CMD", "").strip(),
            vpn_allowed_countries=tuple(
                x.lower() for x in os.getenv("VPN_ALLOWED_COUNTRIES", "").replace(" ", "").split(",") if x
            ),
            vpn_timeout=_int("VPN_TIMEOUT", 120),
            admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
        )
