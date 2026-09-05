# mathou

Bot Discord + worker qui télécharge des saisons (via `cdlr.exe`), les zippe,
les met sur Backblaze B2, et tient un catalogue web (grille + affiches TMDB).

Voir **[ARCHITECTURE.md](ARCHITECTURE.md)** pour la vue d'ensemble et les décisions.

```
/dl <url> nombre:<saison>   →  bot  →  worker (VM Windows)
                                        cdlr → zip → rclone B2 → catalogue
                                ← lien .zip + lien catalogue + récap #logs
```

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

### 4. Worker (sur la VM Windows)

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

### 5. Bot (sur le VPS Linux)

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
| `/dl lien:<url> nombre:<n>` | télécharge la saison `n`, la met en ligne |
| `/chercher nom:<texte>` | cherche une série dans le catalogue |
| `/catalogue` | renvoie le lien du catalogue |
| `/cancel job:<id>` | annule un job **en file d'attente** (id en pied d'encadré) |

### Annuler un job **déjà démarré**

`/cancel` ne stoppe qu'un job en attente. Pour un job en cours, sur la VM :
```powershell
Stop-Process -Name cdlr, ffmpeg, rclone -Force -ErrorAction SilentlyContinue
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
- **"aucun nouveau dossier"** : cdlr n'a rien créé dans `TOOL_OUTPUT_DIR`, ou un
  dossier partiel d'un job précédent le masque → le supprimer.
- **Catalogue sans affiches** : `TMDB_API_KEY` absente/invalide, ou le titre
  parsé ne matche pas TMDB. Ajuste `SERIES_REGEX`.
- **Upload B2 lent** : vérifier `rclone lsd b2:` OK ; augmenter
  `--b2-upload-concurrency` dans `worker/pipeline.py` si la ligne le permet.
