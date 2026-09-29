#!/usr/bin/env bash
# Auditoria do placar a cada 15 min, com e-mail quando um problema persiste.
# Chamado por /etc/cron.d/elcapo-auditoria-placar (modelo em scripts/cron/).
# Ver backend/docs/PLACAR_OVERLAY.md §2026-09-29.
set -uo pipefail
PARA="${1:?uso: auditoria-placar.sh <email>}"
LOG=/root/deploy-elcapo/logs/auditoria-placar.log
mkdir -p "$(dirname "$LOG")"
cd /opt/elcapo/backend || exit 1
set -a
# shellcheck disable=SC1091
. ./.env
set +a
{
  echo "=== $(date -u +%FT%TZ)"
  python3 scripts/alerta_auditoria_placar.py --para "$PARA"
  echo "exit=$?"
} >>"$LOG" 2>&1
# Log não cresce sem fim: guarda as últimas ~5000 linhas.
tail -n 5000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
