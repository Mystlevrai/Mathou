#!/usr/bin/env bash
# VPS Linux : met a jour le bot depuis git et redemarre le service.
# Usage : bash deploy/deploy-bot.sh   (depuis n'importe ou, ou via chemin absolu)
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only

cd bot
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

sudo systemctl restart mathou-bot
sleep 3
sudo systemctl --no-pager --lines=20 status mathou-bot || true
