# Worker en service Windows (NSSM)

Pour que le worker tourne 24/7, survive au reboot de la VM et redemarre s'il
plante — sans fenetre `start.ps1` ouverte.

## 1. Installer NSSM

Sur la VM :
```powershell
winget install -e --id NSSM.NSSM --source winget --accept-package-agreements
```
(ou telecharger sur https://nssm.cc/download et mettre `nssm.exe` dans le PATH)

## 2. Preparer le venv une fois

```powershell
cd C:\mathou\worker          # <- chemin du repo cloné, dossier worker\
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
notepad .env                 # remplir toutes les valeurs
```

## 3. Creer le service

```powershell
nssm install mathou-worker "C:\mathou\worker\.venv\Scripts\python.exe" "C:\mathou\worker\run.py"
nssm set mathou-worker AppDirectory "C:\mathou\worker"
nssm set mathou-worker Start SERVICE_AUTO_START
nssm set mathou-worker AppStdout "C:\mathou\worker\worker.log"
nssm set mathou-worker AppStderr "C:\mathou\worker\worker.log"
nssm set mathou-worker AppRotateFiles 1
nssm set mathou-worker AppRotateBytes 10000000
nssm start mathou-worker
```

## 4. Verifier

```powershell
nssm status mathou-worker          # doit afficher SERVICE_RUNNING
curl.exe -s http://localhost:8756/healthz
Get-Content C:\mathou\worker\worker.log -Tail 20
```

## Commandes utiles

```powershell
nssm restart mathou-worker
nssm stop mathou-worker
nssm status mathou-worker
nssm edit mathou-worker            # interface graphique
nssm remove mathou-worker confirm  # desinstaller
```

## Mise a jour

`powershell -File C:\mathou\deploy\deploy-worker.ps1` (git pull + pip + restart).
