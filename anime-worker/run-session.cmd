@echo off
REM Lance le worker anime-sama en boucle (redemarre s'il crashe).
REM Appele par la tache planifiee "mathou-anime-worker" a l'ouverture de session.
set PYTHONUNBUFFERED=1
cd /d C:\mathou\anime-worker
:loop
.venv\Scripts\python.exe run.py >> worker.log 2>&1
echo [%date% %time%] anime-worker arrete (code %errorlevel%), redemarrage dans 5s >> worker.log
timeout /t 5 /nobreak >nul
goto loop
