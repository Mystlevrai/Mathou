# VM Windows : met a jour les workers depuis git et les redemarre proprement.
#   mathou (cdlr)  -> worker\        port 8756
#   anime-worker   -> anime-worker\  port 8757
# Un worker OCCUPE (job en cours, en attente ou en file) n'est PAS redemarre : relancer
# le script quand il a fini (ou -Force pour le couper quand meme).
# Les workers sont reperes par leur PORT (leur ligne de commande est juste
# ".venv\Scripts\python.exe run.py", sans chemin : impossible de les distinguer par la).
# A lancer dans la session RDP (cdlr a besoin du bureau) :
#   powershell -ExecutionPolicy Bypass -File C:\mathou\deploy\deploy-vm.ps1 [-Only mathou|anime] [-Force]
param(
    [ValidateSet("all", "mathou", "anime")] [string]$Only = "all",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$workers = @(@(
    @{ Name = "mathou"; Dir = "worker"; Port = 8756 },
    @{ Name = "anime"; Dir = "anime-worker"; Port = 8757 }
) | Where-Object { $Only -eq "all" -or $_.Name -eq $Only })

function Get-Health([int]$port) {
    $raw = curl.exe -s -m 5 "http://localhost:$port/healthz"
    if (-not $raw) { return $null }
    try { return $raw | ConvertFrom-Json } catch { return $null }
}

function Get-ListenerId([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return [int]$c.OwningProcess }
    return $null
}

function Get-Loops([string]$dir) {
    # fenetres cmd qui font tourner run-session.cmd de CE worker
    @(Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" |
        Where-Object { $_.CommandLine -like "*\$dir\run-session.cmd*" })
}

function Get-OwnerLoopId([int]$listener) {
    # cmd (run-session.cmd) -> .venv\Scripts\python.exe (lanceur) -> python.exe (ecoute le port)
    $launcher = (Get-CimInstance Win32_Process -Filter "ProcessId=$listener").ParentProcessId
    if (-not $launcher) { return $null }
    return (Get-CimInstance Win32_Process -Filter "ProcessId=$launcher").ParentProcessId
}

function Stop-Tool([string]$name) {
    # -Force sur un worker occupe : son outil (sous-process) survivrait au worker
    if ($name -eq "mathou") {
        Get-Process cdlr, ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force
    } else {
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like "*anime-downloader*main.py*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Get-Process ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force
    }
}

git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "git pull --ff-only a echoue (modifs locales dans C:\mathou ?)" }

foreach ($w in $workers) {
    Write-Host ""
    Write-Host "=== $($w.Name) ($($w.Dir), port $($w.Port)) ==="
    $h = Get-Health $w.Port
    $busy = $h -and ($h.current -or $h.waiting -or ($h.queued -gt 0))
    $listener = Get-ListenerId $w.Port
    $loops = Get-Loops $w.Dir

    if ($busy -and -not $Force) {
        Write-Host "OCCUPE (job $($h.current)$($h.waiting)) : pas redemarre. Relance ce script quand il aura fini."
        # on ferme quand meme les fenetres en double, en gardant celle du worker en cours
        $keep = if ($listener) { Get-OwnerLoopId $listener } else { $null }
        if ($keep -and ($loops.ProcessId -contains $keep)) {
            $loops | Where-Object { $_.ProcessId -ne $keep } | ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force
                Write-Host "fenetre en double fermee (cmd $($_.ProcessId))"
            }
        }
        continue
    }

    Push-Location (Join-Path $root $w.Dir)
    if (-not (Test-Path .\.venv)) { python -m venv .venv }
    .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt
    Pop-Location

    # 1. fermer TOUTES ses boucles (sinon l'une relance l'ancien worker), 2. tuer le worker
    $loops | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if ($listener) { Stop-Process -Id $listener -Force -ErrorAction SilentlyContinue }
    if ($busy) { Stop-Tool $w.Name; Write-Host "job en cours coupe (-Force)" }
    for ($i = 0; $i -lt 15 -and (Get-ListenerId $w.Port); $i++) { Start-Sleep 1 }
    if (Get-ListenerId $w.Port) { throw "le port $($w.Port) est toujours occupe, worker $($w.Name) non relance" }

    # 3. une seule fenetre neuve
    Start-Process cmd.exe -ArgumentList "/c `"$root\$($w.Dir)\run-session.cmd`""
    $h = $null
    for ($i = 0; $i -lt 30 -and -not $h; $i++) { Start-Sleep 1; $h = Get-Health $w.Port }
    if ($h) { Write-Host "OK, redemarre : $($h | ConvertTo-Json -Compress)" }
    else { Write-Host "NE REPOND PAS apres 30s : Get-Content $root\$($w.Dir)\worker.log -Tail 30" }
}
