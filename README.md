# mathou

Bot Discord + worker qui télécharge des saisons (anime-sama/nakanime, via
[Anime-Downloader](https://github.com/SertraFurr/Anime-Downloader)), les
zippe, les met sur Backblaze B2, et tient un catalogue web (grille + affiches
TMDB, recherche, panel admin).

Voir **[ARCHITECTURE.md](ARCHITECTURE.md)** pour la vue d'ensemble et les décisions.

```
/dl <url-complete-avec-saison>   →  bot  →  worker (VM Windows)
                                             Anime-Downloader → zip → rclone B2 → catalogue
                                     ← lien .zip + lien catalogue + récap #logs
```

L'URL doit déjà contenir la saison/langue (ex :
`https://anime-sama.fr/catalogue/<anime>/saison1/vostfr/`) — pas de paramètre
saison séparé, l'outil et le worker le déduisent du chemin.

## Machines

| Rôle | Où | OS |
|---|---|---|
| bot (`bot/`) | VPS BisectHosting `216.201.76.142` | Ubuntu, service systemd |
| worker (`worker/`) | VM OuiHeberg `88.151.197.173` | Windows, service NSSM |

---

## Installation (première fois)

### 1. Git

Le repo vit sur `github.com/Mystlevrai/Mathou`.

```bash
# local (ton PC) : pousser le code v2
cd C:\Users\Myst\Desktop\mathou
git init
git branch -M main
git remote add origin https://github.com/Mystlevrai/Mathou.git
git add .
git commit -m "mathou v2"
git push -u origin main --force        # écrase le repo (il n'a que PRIVACY.md)
```

### 2. Backblaze B2

1. Compte : https://www.backblaze.com/sign-up/cloud-storage
2. **Buckets → Create a Bucket** : nom `mathou-media` (unique au monde), **Public**, région **EU** si proposé.
3. Note l'URL "friendly" du bucket → c'est `B2_PUBLIC_BASE`
   (forme `https://f003.backblazeb2.com/file/mathou-media`).
4. **Application Keys → Add a New Application Key** : restreint au bucket, *Read and Write*.
   Note le **keyID** et l'**applicationKey** (affiché une seule fois).

### 3. TMDB (affiches)

https://www.themoviedb.org/signup → Paramètres → **API** → clé v3.

### 4. Anime-Downloader (l'outil de telechargement, sur la VM Windows)

Clone séparé de mathou — c'est un outil externe, pas vendorisé dans ce repo :
```powershell
cd C:\mathou
git clone https://github.com/SertraFurr/Anime-Downloader.git anime-downloader
cd anime-downloader
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
Puis lance-le **une fois à la main** (`..\.venv\Scripts\python.exe main.py`) pour
passer l'étape Cloudflare si elle se présente (elle demande un cookie `cf_clearance`
+ ton User-Agent, sauvegardés dans `src/utils/config/config.json`) — sinon le
premier job lancé par le worker plantera dessus (pas d'invite possible en tâche
de fond). `ffmpeg` doit être sur le PATH de la VM.

### 5. Worker (sur la VM Windows)

```powershell
cd C:\
git clone https://github.com/Mystlevrai/Mathou.git mathou
cd mathou\worker
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
notepad .env          # remplir : API_TOKEN, ALLOWED_IPS, TOOL_*, B2_*, TMDB_API_KEY
```

Configurer rclone sur B2 :
```powershell
rclone config
#  n  -> nom: b2
#  Storage: b2
#  account: <keyID>
#  key: <applicationKey>
#  hard_delete: false ; Edit advanced: n ; y (keep)
rclone lsd b2:mathou-media        # doit répondre sans erreur
```

Installer le service : suivre **[deploy/worker-nssm.md](deploy/worker-nssm.md)**.
Vérifier : `curl.exe -s http://localhost:8756/healthz`

Pare-feu (port ouvert seulement pour le bot) :
```powershell
New-NetFirewallRule -DisplayName "mathou worker" -Direction Inbound -Action Allow `
  -Protocol TCP -LocalPort 8756 -RemoteAddress 216.201.76.142
```

### 6. Bot (sur le VPS Linux)

```bash
ssh -i <clé> root@216.201.76.142
apt update && apt install -y python3-venv git
git clone https://github.com/Mystlevrai/Mathou.git /opt/mathou
cd /opt/mathou/bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env      # DISCORD_TOKEN, VM_API_BASE, API_TOKEN (=worker), GUILD_ID,
               # LOG_CHANNEL_ID, LIBRARY_CHANNEL_ID, CATALOG_URL

cp /opt/mathou/deploy/mathou-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mathou-bot
journalctl -u mathou-bot -f
```

Discord : app + bot + intent (aucun requis pour les slash), invitation avec
scopes `bot` + `applications.commands`. Créer les salons `#logs` et
`#bibliotheque`, mettre leurs IDs dans `bot/.env`.

`CATALOG_URL` = `<B2_PUBLIC_BASE>/catalog/index.html`.

---

## Exploitation

### Déployer une mise à jour

```bash
# bot (sur le VPS)
bash /opt/mathou/deploy/deploy-bot.sh
```
```powershell
# worker (sur la VM)
powershell -File C:\mathou\deploy\deploy-worker.ps1
```

### Commandes Discord

| Commande | Effet |
|---|---|
| `/dl lien:<url> [vpn:<pays>]` | télécharge la saison (URL complète, saison incluse), la met en ligne |
| `/chercher nom:<texte>` | cherche une série dans le catalogue |
| `/catalogue` | renvoie le lien du catalogue |
| `/cancel job:<id>` | annule un job **en file d'attente** (id en pied d'encadré) |

### Annuler un job **déjà démarré**

`/cancel` ne stoppe qu'un job en attente. Pour un job en cours, sur la VM —
**ne pas tuer tous les `python.exe`** (ça tuerait aussi le worker), cibler par
ligne de commande :
```powershell
Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%main.py%'" |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Stop-Process -Name ffmpeg, rclone -Force -ErrorAction SilentlyContinue
```
Le worker le marquera en échec, la file repart. Supprime le dossier partiel
dans `TOOL_OUTPUT_DIR` s'il en reste un.

### Logs

- Worker : `Get-Content C:\mathou\worker\worker.log -Tail 40`
- Bot : `journalctl -u mathou-bot -n 40 --no-pager`
- Discord : salon `#logs` (récap par job), `#bibliotheque` (nouveautés)

---

## Migration depuis la v1

Une fois la v2 validée :
```bash
git rm -r vm/ bot/ping_bot.py bot/start.sh bot/start.ps1 bot/mathou-bot.service
git commit -m "retrait v1"
git push
```
Sur la VM, arrêter l'ancien worker (fenêtre `start.ps1` → Ctrl+C) avant de
démarrer le service NSSM. Régénérer le **token Discord** (Reset Token) et le
remettre dans `bot/.env`.

---

## Dépannage

- **`/dl` → "Impossible de joindre la VM"** : `curl http://88.151.197.173:8756/healthz`
  depuis le VPS. Sinon : service worker arrêté, mauvais port, règle pare-feu.
- **403 "IP non autorisee"** : `ALLOWED_IPS` (worker) ≠ IP réelle du bot
  (`curl ifconfig.me` sur le VPS).
- **"le dossier attendu est vide ou introuvable"** : l'outil n'a rien créé
  (vérifie `worker.log` pour son erreur réelle — souvent Cloudflare : relance
  `main.py` à la main une fois pour repasser le cookie, cf étape 4).
- **Job plante immédiatement avec une trace Cloudflare/`input()`** : les cookies
  `cf_clearance` de l'outil ont expiré → relance `main.py` à la main sur la VM.
- **Catalogue sans affiches** : `TMDB_API_KEY` absente/invalide, ou le titre
  deviné (depuis le slug de l'URL) ne matche pas TMDB → renomme-le dans `/admin`.
- **Upload B2 lent** : vérifier `rclone lsd b2:` OK ; augmenter
  `--b2-upload-concurrency` dans `worker/pipeline.py` si la ligne le permet.
