# VM Windows : met a jour le worker depuis git et le redemarre.
# Le worker tourne dans la session interactive (cf deploy/worker-session.md).
# Usage : powershell -File deploy\deploy-worker.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")

git pull --ff-only

Set-Location worker
if (-not (Test-Path .\.venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -q --upgrade pip
.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt

# tuer le worker en cours ; run-session.cmd le relance seul dans les 5s
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*mathou*run.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Start-Sleep 8
curl.exe -s http://localhost:8756/healthz
Write-Host ""
