# VM Windows : met a jour anime-worker depuis git et le redemarre.
# Tourne dans la session interactive comme le worker mathou (cf worker-session.md),
# sur un port different (8757) avec son propre mutex -> les deux coexistent.
# Usage : powershell -File deploy\deploy-anime-worker.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")

git pull --ff-only

Set-Location anime-worker
if (-not (Test-Path .\.venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -q --upgrade pip
.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt

# tuer l'anime-worker en cours ; run-session.cmd le relance seul dans les 5s
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*anime-worker*run.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Start-Sleep 8
curl.exe -s http://localhost:8757/healthz
Write-Host ""
