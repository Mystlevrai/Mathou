"""Point d'entree du worker. Lit worker/.env puis lance uvicorn.

Garde-fou : refuse de demarrer si un worker repond deja sur le port (sur Windows
plusieurs uvicorn peuvent se lier au meme port -> jobs traites en parallele)."""
from __future__ import annotations

import socket

import uvicorn

from config import Config


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


if __name__ == "__main__":
    c = Config.load()
    if _port_busy(c.port):
        print(f"[run.py] un worker ecoute deja sur :{c.port} -> on n'en lance pas un 2e.", flush=True)
        raise SystemExit(0)
    # access_log=False : sinon le log est noye sous les GET /jobs du bot (poll toutes les 3s)
    uvicorn.run("app:app", host=c.host, port=c.port, log_level="info", access_log=False)
