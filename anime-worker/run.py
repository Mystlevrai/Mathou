"""Point d'entree du worker anime-sama/nakanime. Lit anime-worker/.env puis lance uvicorn.

Garde-fou infaillible : un mutex Windows nomme garantit un SEUL worker de CE type
a la fois (mutex distinct de celui du worker mathou/cdlr - les deux tournent en
parallele, chacun avec son propre singleton)."""
from __future__ import annotations

import ctypes
import socket
import sys

import uvicorn

from config import Config

_MUTEX_NAME = "Global\\mathou-anime-worker-singleton"


def _acquire_singleton() -> bool:
    """True si on a le verrou (on est le seul worker). False si un worker existe deja."""
    if sys.platform != "win32":
        return True
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    already = ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    if already:
        return False
    # on garde le handle vivant pour toute la duree du process
    globals()["_MUTEX_HANDLE"] = handle
    return True


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
    if not _acquire_singleton() or _port_busy(c.port):
        print(f"[run.py] un anime-worker tourne deja -> on n'en lance pas un 2e.", flush=True)
        raise SystemExit(0)
    uvicorn.run("app:app", host=c.host, port=c.port, log_level="info", access_log=False)
