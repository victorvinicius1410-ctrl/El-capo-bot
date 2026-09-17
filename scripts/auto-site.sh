#!/usr/bin/env bash
# Publica o SITE automaticamente quando chega novidade na main do GitHub.
#
# O que ele NÃO faz, de propósito: nada de backend. Não builda imagem, não
# reinicia container, não encosta na corretora nem nos robôs. Backend continua
# saindo só por `scripts/deploy-backend.sh`, rodado à mão.
#
# Roda pelo cron /etc/cron.d/elcapo-auto-site a cada 3 min (com flock).
# Diário: /root/deploy-elcapo/logs/auto-site.log
set -uo pipefail

ROOT=/opt/elcapo
LOG=/root/deploy-elcapo/logs/auto-site.log
BK=/root/deploy-elcapo/backups/site-auto
mkdir -p "$(dirname "$LOG")" "$BK"

log() { echo "[$(date '+%d/%m %H:%M:%S')] $*" >>"$LOG"; }

cd "$ROOT" || exit 1
git fetch -q origin main || { log "não consegui falar com o GitHub"; exit 1; }

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
[[ "$LOCAL" == "$REMOTE" ]] && exit 0   # nada novo: sai calado

# Trabalho não commitado na VPS tem prioridade — nunca sobrescrever.
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  log "PAREI: chegou novidade no GitHub ($(git rev-parse --short origin/main)) mas a VPS tem alteração não commitada. Resolva à mão em $ROOT."
  exit 0
fi

# Só aceita continuação da história; se divergiu, quem decide é gente.
if ! git merge-base --is-ancestor HEAD origin/main; then
  log "PAREI: a main do GitHub divergiu do que está na VPS. Resolva à mão."
  exit 0
fi

CHANGED=$(git diff --name-only HEAD origin/main)
git merge --ff-only -q origin/main || { log "PAREI: o avanço para origin/main falhou."; exit 1; }
log "código atualizado para $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"

if ! grep -qE '^frontend/' <<<"$CHANGED"; then
  log "sem mudança de site nessa leva. (Se mudou backend, ele só vai ao ar com deploy manual.)"
  exit 0
fi

cd "$ROOT/frontend" || exit 1

if grep -qE '^frontend/(package\.json|package-lock\.json)$' <<<"$CHANGED"; then
  log "dependências mudaram — npm ci"
  npm ci >>"$LOG" 2>&1 || { log "PAREI: npm ci falhou. Site no ar segue o anterior."; exit 1; }
fi

log "buildando o site..."
npm run build >>"$LOG" 2>&1 || { log "PAREI: o build falhou. Site no ar segue o anterior."; exit 1; }

log "rodando os testes do site..."
npm test >>"$LOG" 2>&1 || { log "PAREI: teste falhou. NÃO publiquei — site no ar segue o anterior."; exit 1; }

tar -C /var/www -czf "$BK/elcapobot-$(date +%Y%m%d-%H%M%S).tar.gz" elcapobot 2>/dev/null
ls -1t "$BK"/*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f

if bash "$ROOT/scripts/publish-frontend.sh" >>"$LOG" 2>&1; then
  log "SITE PUBLICADO ✅ ($(git rev-parse --short HEAD))"
else
  log "PAREI: a publicação falhou — confira o log acima."
  exit 1
fi
