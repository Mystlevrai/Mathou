from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _require(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(f"Variable d'environnement obligatoire manquante : {name}")
    return val


def _json_str_list(name: str, example: str) -> list[str]:
    """Parse une variable d'env qui doit etre un tableau JSON de chaines. Vide -> []."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
        assert isinstance(value, list) and all(isinstance(x, str) for x in value)
    except (json.JSONDecodeError, AssertionError) as exc:
        raise RuntimeError(f"{name} doit etre un tableau JSON de chaines, ex : {example}") from exc
    return value


@dataclass(frozen=True)
class Config:
    api_token: str
    host: str
    port: int
    work_dir: Path
    tool_args: list[str]
    tool_timeout: int
    tool_cwd: Path | None
    rclone_path: str
    rclone_remote: str
    drive_dest: str
    make_share_link: bool
    delete_local_after: bool
    max_queue: int
    url_allowlist: tuple[str, ...]
    allowed_ips: tuple[str, ...]
    tool_output_dir: Path | None
    tool_args_num: list[str]
    tool_args_end: list[str]

    @staticmethod
    def load() -> "Config":
        tool_args_raw = _require("TOOL_ARGS")
        try:
            tool_args = json.loads(tool_args_raw)
            assert isinstance(tool_args, list) and all(isinstance(x, str) for x in tool_args)
        except (json.JSONDecodeError, AssertionError) as exc:
            raise RuntimeError(
                "TOOL_ARGS doit etre un tableau JSON de chaines, "
                'ex : ["mytool.exe","--url","{url}","--output","{outdir}"]'
            ) from exc
        if not any("{url}" in a for a in tool_args):
            raise RuntimeError("TOOL_ARGS doit contenir le marqueur {url}")

        out_raw = os.getenv("TOOL_OUTPUT_DIR", "").strip()
        tool_output_dir = Path(out_raw).resolve() if out_raw else None

        # {outdir} n'est obligatoire que si l'outil accepte un chemin de sortie.
        # Si TOOL_OUTPUT_DIR est defini, l'outil ecrit dans son propre dossier et
        # le worker detecte les nouveaux fichiers/dossiers qui y apparaissent.
        if tool_output_dir is None and not any("{outdir}" in a for a in tool_args):
            raise RuntimeError(
                "TOOL_ARGS doit contenir {outdir}, ou alors definis TOOL_OUTPUT_DIR "
                "(dossier ou l'outil depose ses fichiers)"
            )

        # Args ajoutes SEULEMENT si /dl fournit un nombre. {num} y est remplace.
        tool_args_num = _json_str_list("TOOL_ARGS_NUM", '["--episodes","{num}"]')
        # Args ajoutes TOUJOURS, tout a la fin de la commande.
        tool_args_end = _json_str_list("TOOL_ARGS_END", '["--no-color"]')

        cwd_raw = os.getenv("TOOL_CWD", "").strip()
        allow_raw = os.getenv("URL_ALLOWLIST", "").replace(" ", "")
        url_allowlist = tuple(h for h in allow_raw.split(",") if h)
        ips_raw = os.getenv("ALLOWED_IPS", "").replace(" ", "")
        allowed_ips = tuple(h for h in ips_raw.split(",") if h)

        return Config(
            api_token=_require("API_TOKEN"),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8756")),
            work_dir=Path(os.getenv("WORK_DIR", r"C:\downloads")).resolve(),
            tool_args=tool_args,
            tool_timeout=int(os.getenv("TOOL_TIMEOUT", "1800")),
            tool_cwd=Path(cwd_raw).resolve() if cwd_raw else None,
            rclone_path=os.getenv("RCLONE_PATH", "rclone"),
            rclone_remote=os.getenv("RCLONE_REMOTE", "gdrive"),
            drive_dest=os.getenv("DRIVE_DEST", "discord-jobs").strip("/"),
            make_share_link=_bool("MAKE_SHARE_LINK", True),
            delete_local_after=_bool("DELETE_LOCAL_AFTER", False),
            max_queue=int(os.getenv("MAX_QUEUE", "50")),
            url_allowlist=url_allowlist,
            allowed_ips=allowed_ips,
            tool_output_dir=tool_output_dir,
            tool_args_num=tool_args_num,
            tool_args_end=tool_args_end,
        )
