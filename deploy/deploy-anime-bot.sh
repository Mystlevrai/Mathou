#!/usr/bin/env bash
# VPS Linux : met a jour anime-bot depuis git et redemarre le service.
# Usage : bash deploy/deploy-anime-bot.sh
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only

cd anime-bot
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

sudo systemctl restart anime-bot
sleep 3
sudo systemctl --no-pager --lines=20 status anime-bot || true
