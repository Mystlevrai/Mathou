@echo off
REM verif-bot : double-clic pour lancer. Installe ce qu'il faut au premier lancement,
REM puis relance le bot tout seul s'il s'arrete (coupure internet, crash...).
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
cd /d "%~dp0"
title verif-bot

if not exist .env (
  copy .env.example .env >nul
  echo [verif-bot] Fichier .env cree : remplis-le dans le Bloc-notes, enregistre, puis relance start.bat
  notepad .env
  exit /b 0
)
if not exist liste.txt copy liste.example.txt liste.txt >nul

if not exist .venv\Scripts\python.exe (
  echo [verif-bot] Premiere installation...
  python -m venv .venv || goto nopython
)
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt

:loop
.venv\Scripts\python.exe bot.py
set CODE=%errorlevel%
if "%CODE%"=="2" goto config
echo [%date% %time%] verif-bot arrete (code %CODE%), redemarrage dans 10s...
timeout /t 10 /nobreak >nul
goto loop

:config
echo.
echo [verif-bot] Corrige .env (voir le message ci-dessus) puis relance start.bat
pause
exit /b 2

:nopython
echo [verif-bot] Python introuvable : installe-le depuis https://www.python.org/downloads/
echo             en cochant "Add python.exe to PATH", puis relance start.bat
pause
exit /b 1
