# anime-worker en session interactive

Meme principe que `worker-session.md` (mathou) : une tache planifiee lance
`anime-worker\run-session.cmd` a l'ouverture de session. L'autologon Windows
est deja en place pour mathou, pas besoin de le refaire.

## Tache planifiee dediee

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\Windows\System32\cmd.exe" -Argument "/c C:\mathou\anime-worker\run-session.cmd"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "Administrator"
$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "mathou-anime-worker" -Action $action -Trigger $trigger -Settings $set -RunLevel Highest -Force
```

Redemarrer et verifier (les DEUX workers doivent repondre, sur des ports differents) :
```powershell
curl.exe -s http://localhost:8756/healthz   # mathou (cdlr)
curl.exe -s http://localhost:8757/healthz   # anime-worker
```

## Exploitation

- Logs : `Get-Content C:\mathou\anime-worker\worker.log -Wait -Tail 30`
- Redemarrer : cibler le process qui ecoute le port 8757 (ne jamais tuer tous les
  `python.exe` : ca couperait aussi mathou) ; sa fenetre `cmd` le relance en 5s :
  ```powershell
  Stop-Process -Id (Get-NetTCPConnection -LocalPort 8757 -State Listen).OwningProcess -Force
  ```
- Mise a jour : `powershell -File C:\mathou\deploy\deploy-anime-worker.ps1`
