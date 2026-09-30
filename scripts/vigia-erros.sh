#!/usr/bin/env bash
# Vigia de erros a cada 5 min: todo erro do sistema vira e-mail.
# Chamado por /etc/cron.d/elcapo-vigia-erros (modelo em scripts/cron/).
# Padrão e regras: backend/docs/VIGIA_DE_ERROS.md.
set -uo pipefail
PARA="${1:?uso: vigia-erros.sh <email>}"
LOG=/root/deploy-elcapo/logs/vigia-erros.log
mkdir -p "$(dirname "$LOG")"
cd /opt/elcapo/backend || exit 1
set -a
# shellcheck disable=SC1091
. ./.env
set +a
python3 scripts/vigia_erros.py --para "$PARA" >>"$LOG" 2>&1
tail -n 5000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
