#!/usr/bin/env bash
# Atualiza e sobe a stack El Capo na VPS.
set -euo pipefail

ROOT=/opt/elcapo/backend
cd "$ROOT"

echo "[elcapo] build + up..."
docker compose -p elcapooneline --env-file .env up -d --build

echo "[elcapo] status:"
docker compose -p elcapooneline ps

echo "[elcapo] health local:"
# Startup pode levar >5s (hidratação pattern_memory / Supabase).
ok=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 5
done
if [[ "$ok" -ne 1 ]]; then
  echo "health falhou" >&2
  docker logs backend-gateway --tail 50 >&2
  exit 1
fi
echo
echo "[elcapo] OK"
