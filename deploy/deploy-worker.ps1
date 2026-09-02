# VM Windows : met a jour le worker depuis git et redemarre le service.
# Usage : powershell -File deploy\deploy-worker.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")

git pull --ff-only

Set-Location worker
if (-not (Test-Path .\.venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -q --upgrade pip
.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt

# Service installe via NSSM (voir deploy/worker-nssm.md), nom : mathou-worker
try {
    nssm restart mathou-worker
} catch {
    Restart-Service mathou-worker
}

Start-Sleep 3
curl.exe -s http://localhost:8756/healthz
Write-Host ""
