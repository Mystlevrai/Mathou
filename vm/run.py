"""Point d'entree du worker VM. Lit vm/.env puis lance uvicorn."""
from __future__ import annotations

import uvicorn

from config import Config

if __name__ == "__main__":
    cfg = Config.load()
    uvicorn.run("server:app", host=cfg.host, port=cfg.port, log_level="info")
