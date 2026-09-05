@echo off
REM Lance le worker en boucle (redemarre s'il crashe).
REM Appele par la tache planifiee "mathou-worker" a l'ouverture de session.
set PYTHONUNBUFFERED=1
cd /d C:\mathou\worker
:loop
.venv\Scripts\python.exe run.py >> worker.log 2>&1
echo [%date% %time%] worker arrete (code %errorlevel%), redemarrage dans 5s >> worker.log
timeout /t 5 /nobreak >nul
goto loop
