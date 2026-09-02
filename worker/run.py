"""Point d'entree du worker. Lit worker/.env puis lance uvicorn."""
from __future__ import annotations

import uvicorn

from config import Config

if __name__ == "__main__":
    c = Config.load()
    # access_log=False : sinon le log est noye sous les GET /jobs du bot (poll toutes les 3s)
    uvicorn.run("app:app", host=c.host, port=c.port, log_level="info", access_log=False)
