# Lance le worker sur la VM. A executer depuis le dossier vm\ (ou en double-clic).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".\.venv")) {
    python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path ".\.env")) {
    Write-Warning ".env manquant : copie .env.example en .env et remplis-le avant de relancer."
    exit 1
}

.\.venv\Scripts\python.exe run.py
