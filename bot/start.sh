#!/usr/bin/env bash
# Lance le bot sur le VPS (Linux). chmod +x start.sh avant le premier lancement.
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
    echo "Il manque .env : copie .env.example en .env et remplis-le avant de relancer." >&2
    exit 1
fi

exec .venv/bin/python bot.py
