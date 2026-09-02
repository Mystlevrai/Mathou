# Worker en session interactive (pas NSSM)

`cdlr.exe` pilote un navigateur -> il lui faut un **bureau**. Un service Windows
(NSSM) tourne en session 0 sans bureau : cdlr y echoue (`[WinError 2]`).

Solution : la VM se **connecte automatiquement** au demarrage, et une **tache
planifiee "a l'ouverture de session"** lance le worker dans cette session.
Ca survit au reboot ; on peut se deconnecter du RDP (sans se **deconnecter** de
Windows) et le worker continue.

## 1. Retirer le service NSSM

```powershell
nssm stop mathou-worker
nssm remove mathou-worker confirm
```

## 2. Tache planifiee "a l'ouverture de session"

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\Windows\System32\cmd.exe" -Argument "/c C:\mathou\worker\run-session.cmd"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "Administrator"
$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "mathou-worker" -Action $action -Trigger $trigger -Settings $set -RunLevel Highest -Force
```

## 3. Connexion automatique de la VM

Le plus propre : **Sysinternals Autologon** (mot de passe chiffre en LSA, pas en clair).

```powershell
Invoke-WebRequest https://download.sysinternals.com/files/AutoLogon.zip -OutFile $env:TEMP\AutoLogon.zip
Expand-Archive $env:TEMP\AutoLogon.zip $env:TEMP\AutoLogon -Force
& "$env:TEMP\AutoLogon\Autologon64.exe" /accepteula Administrator $env:COMPUTERNAME "<MOT_DE_PASSE_ADMIN>"
```

(Alternative sans outil, mot de passe en clair dans le registre :)
```powershell
$k = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
Set-ItemProperty $k AutoAdminLogon "1"
Set-ItemProperty $k DefaultUserName "Administrator"
Set-ItemProperty $k DefaultPassword "<MOT_DE_PASSE_ADMIN>"
```

## 4. Redemarrer et verifier

```powershell
Restart-Computer
```
Attendre ~1-2 min, se reconnecter en RDP, puis :
```powershell
curl.exe -s http://localhost:8756/healthz
Get-Content C:\mathou\worker\worker.log -Tail 20
```

## Exploitation

- Voir les logs : `Get-Content C:\mathou\worker\worker.log -Wait -Tail 30`
- Redemarrer le worker : fermer la fenetre `cmd` du worker (elle se relance seule
  via `run-session.cmd`), ou :
  ```powershell
  Get-Process python | Where-Object { $_.Path -like "*mathou*" } | Stop-Process -Force
  ```
- Mise a jour : `powershell -File C:\mathou\deploy\deploy-worker.ps1`
- **Ne jamais se "Deconnecter" de Windows** (fermer la session) : se **deconnecter
  du RDP** uniquement (croix de la fenetre), la session reste active.
