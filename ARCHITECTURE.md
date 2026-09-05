# mathou — architecture cible (v2)

Refonte propre du système. Garde ce qui marche (discord.py, FastAPI, rclone),
réorganise, ajoute B2 + catalogue, supprime la dette.

## Objectif

```
/dl <url-saison-complete> [vpn]   (Discord)
   -> le worker télécharge la saison avec Anime-Downloader (anime-sama/nakanime)
   -> zippe la saison
   -> upload le .zip sur Backblaze B2
   -> génère/actualise un catalogue web (grille + affiches TMDB, recherche, admin)
   -> le bot poste : lien du .zip + lien du catalogue, et un récap dans #logs
```

(v3 : l'outil de téléchargement est passé de `cdlr.exe` (Crunchyroll) à
[Anime-Downloader](https://github.com/SertraFurr/Anime-Downloader)
(anime-sama/nakanime) — l'URL contient déjà la saison/langue, donc plus de
paramètre `nombre` séparé ; le worker déduit `series_slug`/`season` de l'URL
elle-même, plus de parsing de nom de dossier.)

Une personne veut une saison = **un** `/dl`, **un** lien de téléchargement.

## Décisions

| Sujet | Choix | Pourquoi |
|---|---|---|
| Stockage | **Backblaze B2**, bucket public, région EU | ~6 $/To, egress 3× gratuit, pas de bridage "projet neuf" |
| Format livré | **1 `.zip` par saison** | B2 n'a pas de "télécharger le dossier". Le zip = 1 lien = toute la saison |
| Déploiement | **git pull + restart** | fini le scp / copier-coller RDP |
| Série/Saison | dérivées de l'URL anime-sama/nakanime elle-même | l'outil écrit `<anime>/<saison>/` d'après l'URL ; pas de parsing de nom de dossier, pas de devinette |
| Affiches | **TMDB** (clé API gratuite), résultat mis en cache | grille avec vignettes |
| Worker au boot | **service Windows via NSSM** | survit reboot / fermeture RDP |

## Repo

```
mathou/
├── ARCHITECTURE.md
├── README.md              # install + exploitation
├── .gitignore
├── bot/
│   ├── bot.py             # /dl /catalogue /chercher /cancel + suivi + #logs
│   ├── requirements.txt
│   └── .env.example
├── worker/
│   ├── app.py             # FastAPI : POST /jobs, GET /jobs/{id}, GET /library
│   ├── queue.py           # file sérielle, 1 job à la fois
│   ├── pipeline.py        # Anime-Downloader -> zip -> rclone(B2) -> catalogue
│   ├── library.py         # parsing série, modèle series/seasons, TMDB (cache)
│   ├── catalog.py         # génère le site statique (grille + pages série/saison)
│   ├── db.py              # SQLite : jobs, series, tmdb_cache
│   ├── config.py
│   ├── run.py
│   ├── requirements.txt
│   ├── templates/         # index.html, series.html, season.html + style.css
│   └── .env.example
└── deploy/
    ├── bot.service            # systemd (VPS Linux)
    ├── worker-nssm.md         # pas à pas NSSM (VM Windows)
    ├── deploy-bot.sh          # git pull + pip + restart (VPS)
    └── deploy-worker.ps1      # git pull + venv + restart service (VM)
```

## Modèle de données (SQLite, sur la VM)

```
jobs(id, url, season, extra, status, error, log,
     series_slug, zip_name, size_bytes, dl_seconds, zip_seconds, up_seconds,
     download_url, created_at, updated_at)

series(slug, title, tmdb_id, poster_url, overview, updated_at)

seasons(series_slug, season, zip_name, download_url, size_bytes, episodes, added_at)
   PK (series_slug, season)

tmdb_cache(query, tmdb_id, poster_url, overview, fetched_at)
```

Le catalogue est **dérivé** de `series` + `seasons`. Régénéré après chaque job.

## Config (résumé — détail dans les .env.example)

**worker/.env**
```
API_TOKEN=...                     # partagé avec le bot
HOST=0.0.0.0
PORT=8756
ALLOWED_IPS=<ip-publique-bot>

TOOL_PYTHON=C:\mathou\anime-downloader\.venv\Scripts\python.exe
TOOL_SCRIPT=C:\mathou\anime-downloader\main.py
TOOL_CWD=C:\mathou\anime-downloader
TOOL_OUTPUT_DIR=C:\mathou\anime-downloader\videos   # <anime>/<saison>/ y apparait
TOOL_PLAYER=sendvid
TOOL_EXTRA=                       # args fixes toujours ajoutés (JSON liste)
TOOL_TIMEOUT=7200

RCLONE_PATH=rclone
RCLONE_REMOTE=b2                  # remote rclone configurée sur B2
B2_BUCKET=mathou-media
B2_PUBLIC_BASE=https://f003.backblazeb2.com/file/mathou-media

TMDB_API_KEY=...
CATALOG_LOCAL=C:\mathou\catalog  # dossier de build du site
MAX_QUEUE=20
KEEP_LOCAL=false                 # garder le téléchargement après upload
```

**bot/.env**
```
DISCORD_TOKEN=...
VM_API_BASE=http://<ip-vm>:8756
API_TOKEN=...                     # = worker
GUILD_ID=0
LOG_CHANNEL_ID=...
LIBRARY_CHANNEL_ID=...            # lien du catalogue épinglé + nouveautés
ALLOWED_USER_IDS=
ALLOWED_ROLE_ID=
POLL_SECONDS=3
JOB_MAX_WAIT=14400
```

## Déroulé d'un job

1. `/dl url:<url-complete>` → bot valide → `POST /jobs {url, vpn}` → `job_id`
2. worker (file sérielle) :
   a. déduit `anime_name`/`saison_info` de l'URL (même logique que l'outil), donc
      `series_slug`/`season` connus **avant** de lancer quoi que ce soit
   b. `python main.py --url <url> --dest TOOL_OUTPUT_DIR --episodes all --player ... --mp4 ...` (cwd = TOOL_CWD)
   c. dossier attendu = `TOOL_OUTPUT_DIR/<anime_name>/<saison_info>/` (déterministe, pas de devinette)
   d. `zip` streamé directement vers B2 (`rclone rcat`, pas de fichier zip local)
   e. `download_url = B2_PUBLIC_BASE/<series_slug>/<zip_name>`
   f. upsert `series` (+ TMDB si pas en cache) et `seasons`
   g. `catalog.build()` → écrit le site dans `CATALOG_LOCAL`
   h. `rclone sync CATALOG_LOCAL b2:<bucket>/catalog/`
   i. job `done` avec `download_url`, tailles, chronos
   j. si `KEEP_LOCAL=false` → supprime le dossier local (jamais de zip local, streamé direct)
3. bot : encadré ✅ avec le lien du zip ; récap dans `#logs` ; poste la nouveauté
   dans `#library` ; le message épinglé pointe sur `CATALOG_PUBLIC/index.html`

## Catalogue web (statique, sur B2)

```
catalog/
├── index.html                  grille de toutes les séries (affiche TMDB, nb saisons)
├── <series_slug>/index.html    liste des saisons (affiche, tailles, liens .zip)
└── assets/style.css
```

JS minimal (recherche client-side sur l'accueil). Régénéré entièrement à
chaque job (rapide, quelques Ko). Un panel `/admin` (jeton) permet d'éditer/
fusionner/supprimer des séries en dehors du flux de téléchargement.

## Plan de migration (ordre)

1. **git** : `git init`, pousser sur `github.com/Mystlevrai/Mathou`, ajouter `.gitignore`
2. **B2** : compte + bucket public EU + clé appli → `rclone config` remote `b2` sur la VM
3. **worker v2** : nouveaux fichiers, `config.py`, `db.py` (migration douce depuis l'ancienne base), `pipeline.py` sans le bricolage `Saison0N`
4. **catalogue** : `library.py` + `catalog.py` + templates
5. **bot v2** : `/dl` (avec `nombre`=saison), `/catalogue`, `/chercher`, `/cancel`
6. **déploiement** : scripts `deploy-*.{sh,ps1}` + service NSSM + systemd
7. **bascule** : `RCLONE_REMOTE=b2`, on arrête l'ancien worker, on lance le service v2
8. **finitions** : régénérer le token Discord, purger l'ancien code

## Toi / moi

- **Moi** : tout le code, templates, scripts, README.
- **Toi** : compte B2 + bucket + clé, clé TMDB, `rclone config` B2 sur la VM,
  `git push`, exécuter les scripts de déploiement, tester, me remonter les erreurs.
