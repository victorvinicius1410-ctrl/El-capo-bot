#!/usr/bin/env bash
# Sincroniza /opt/elcapo com origin/main e republica backend + frontend.
set -euo pipefail

ROOT="/opt/elcapo"
cd "$ROOT"

echo "[sync] git pull origin main"
git pull origin main

echo "[sync] deploy backend"
"$ROOT/scripts/deploy-backend.sh"

echo "[sync] publish frontend"
"$ROOT/scripts/publish-frontend.sh"

echo "[sync] ok — valide: curl -sS https://api.elcapobot.online/health"
