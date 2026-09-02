"""Point d'entree du worker. Lit worker/.env puis lance uvicorn."""
from __future__ import annotations

import uvicorn

from config import Config

if __name__ == "__main__":
    c = Config.load()
    uvicorn.run("app:app", host=c.host, port=c.port, log_level="info")
