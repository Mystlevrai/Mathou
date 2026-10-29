# VM Windows : met a jour l'anime-worker (port 8757) depuis git et le redemarre.
# Delegue a deploy-vm.ps1 (repere le worker par son port, ne coupe pas un job en cours).
# Usage : powershell -ExecutionPolicy Bypass -File deploy\deploy-anime-worker.ps1 [-Force]
param([switch]$Force)
& (Join-Path $PSScriptRoot "deploy-vm.ps1") -Only anime -Force:$Force
