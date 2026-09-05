"""Slugification + lookup TMDB (cache). Partage la meme base que le worker
mathou (cf DB_PATH dans .env) -> meme table tmdb_cache, meme catalogue."""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
import urllib.request

import db


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_\-]+", "-", text) or "divers"


TMDB_SEARCH = "https://api.themoviedb.org/3/search/tv"
TMDB_IMG = "https://image.tmdb.org/t/p/w342"


def tmdb_lookup(title: str, api_key: str) -> dict:
    """{tmdb_id, poster_url, overview} ; mis en cache. {} si rien trouve ou pas de cle."""
    cached = db.tmdb_get(title)
    if cached is not None:
        return {
            "tmdb_id": cached["tmdb_id"],
            "poster_url": cached["poster_url"],
            "overview": cached["overview"],
        }
    if not api_key:
        return {}
    params = urllib.parse.urlencode(
        {"api_key": api_key, "query": title, "language": "fr-FR", "include_adult": "false"}
    )
    try:
        with urllib.request.urlopen(f"{TMDB_SEARCH}?{params}", timeout=15) as r:
            data = json.load(r)
    except Exception:  # noqa: BLE001 - pas de reseau / cle invalide : on continue sans affiche
        return {}

    results = data.get("results") or []
    if not results:
        db.tmdb_put(title, None, None, None)
        return {}
    top = results[0]
    poster = f"{TMDB_IMG}{top['poster_path']}" if top.get("poster_path") else None
    info = {
        "tmdb_id": top.get("id"),
        "poster_url": poster,
        "overview": (top.get("overview") or None),
    }
    db.tmdb_put(title, info["tmdb_id"], info["poster_url"], info["overview"])
    return info
