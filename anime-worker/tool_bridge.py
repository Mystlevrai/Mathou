"""Pont NON-INTERACTIF vers les fonctions de recherche d'Anime-Downloader
(search_anime, expand_catalogue_url) - main.py ne les expose qu'en mode
interactif terminal, avec des input(). A executer avec TOOL_PYTHON et
cwd=TOOL_CWD (pour que `from src....` fonctionne comme dans main.py).

Usage :
  python tool_bridge.py search "<query>"   -> JSON [{title,url,site,support}, ...]
  python tool_bridge.py seasons "<url>"    -> JSON [{name,url}, ...]

Ne bloque JAMAIS sur un input() (contrairement a main.py) : si Cloudflare
bloque anime-sama et qu'aucun cookie valide n'est en cache, echoue proprement
avec un message clair plutot que de pendre indefiniment en tache de fond."""
from __future__ import annotations

import contextlib
import io
import json
import sys

# le code de l'outil imprime des emojis (print_status) dans sa progression ; en
# sous-processus sans vrai terminal, la console Windows retombe sur cp1252/charmap
# et ca crashe (UnicodeEncodeError). On force UTF-8 sur stdout/stderr avant tout.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, ".")

from src.utils.check.is_cloudflare_here import check_if_cloudflare_enabled  # noqa: E402
from src.utils.config.config import check_cookies, get_cookies  # noqa: E402
from src.utils.fetch.fetch_episodes import fetch_episodes  # noqa: E402
from src.utils.search.expand_catalogue import expand_catalogue_url  # noqa: E402
from src.utils.search.search_anime import search_anime  # noqa: E402
from src.var import generate_requests_headers, get_domain  # noqa: E402


def _headers() -> dict:
    cloudflare = check_if_cloudflare_enabled(domain=get_domain(), headers={"User-Agent": "Mozilla/5.0"})
    if not cloudflare:
        return generate_requests_headers("None", "Mozilla/5.0")
    stored = get_cookies()
    if stored is False:
        raise RuntimeError("cloudflare_cookies_missing")
    cf_clearance, hdrs = stored
    user_agent = hdrs.get("User-Agent")
    if not check_cookies(domain=get_domain(), headers={"User-Agent": user_agent}):
        raise RuntimeError("cloudflare_cookies_expired")
    return generate_requests_headers(cf_clearance, user_agent)


# on ne propose que ce qui est effectivement telechargeable en anime (le scan/manga
# n'est pas gere par notre pipeline) ; None/"Unknown" passent (verif reseau ratee,
# on laisse une chance plutot que de masquer un resultat valide)
_ANIME_OK = {"Anime Supported", "Anime & Scans Supported", "Unknown", None}


def cmd_search(query: str, headers: dict) -> list:
    results = search_anime(query, headers=headers) or []
    return [r for r in results if r.get("support") in _ANIME_OK]


def cmd_seasons(url: str, headers: dict) -> list:
    options = expand_catalogue_url(url, headers=headers) or []
    return [o for o in options if "/scan" not in (o.get("url") or "").lower()]


# players dont TOUS les episodes viennent de sources mortes/malveillantes (cf README
# de l'outil : vk.com/myvi.tv "Deprecated - Unsupported/Malicious")
_BAD_DOMAINS = ("vk.com", "myvi.tv")


def cmd_players(url: str, headers: dict) -> list:
    episodes = fetch_episodes(url, headers=headers) or {}
    names = []
    for name, urls in episodes.items():
        if urls and all(any(d in (u or "").lower() for d in _BAD_DOMAINS) for u in urls):
            continue
        names.append({"name": name, "episodes": len(urls)})
    return names


def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: tool_bridge.py <search|seasons|players> <arg>"}))
        return 1
    action, arg = sys.argv[1], sys.argv[2]
    if action not in ("search", "seasons", "players"):
        print(json.dumps({"error": f"commande inconnue: {action}"}))
        return 1
    try:
        headers = _headers()
        # l'outil imprime sa propre progression (print_status, colorée) sur stdout ;
        # on l'avale dans un buffer pour que SEUL le JSON final sorte sur le vrai stdout
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            if action == "search":
                data = cmd_search(arg, headers)
            elif action == "seasons":
                data = cmd_seasons(arg, headers)
            else:
                data = cmd_players(arg, headers)
        print(json.dumps(data))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
