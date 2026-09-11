#!/usr/bin/env bash
# Publica o frontend estático em /var/www/elcapobot (app.elcapobot.online)
set -euo pipefail

# Fonte de verdade: /opt/elcapo/frontend (symlink /root/elcapo).
# Fallback legado: /root/Frontend (espelho de upload).
if [[ -d /opt/elcapo/frontend/.vercel/output/static/assets ]]; then
  SRC_ROOT=/opt/elcapo/frontend
elif [[ -d /root/elcapo/frontend/.vercel/output/static/assets ]]; then
  SRC_ROOT=/root/elcapo/frontend
else
  SRC_ROOT=/root/Frontend
fi

SRC_STATIC="$SRC_ROOT/.vercel/output/static"
SRC_DIST="$SRC_ROOT/dist/client"
SRC_PUBLIC="$SRC_ROOT/public"
DEST=/var/www/elcapobot

if [[ ! -d "$SRC_STATIC/assets" ]]; then
  echo "Build estático não encontrado em $SRC_STATIC" >&2
  exit 1
fi

# `protect assets/***`: NÃO apagar os bundles da versão anterior. Quem estiver
# com o HTML antigo em cache pede chunk com hash antigo; se ele sumiu, o painel
# quebra e o cliente não consegue chegar na versão nova sozinho. Como o HTML
# agora é no-cache (ver nginx), na próxima navegação ele já pega a versão nova.
rsync -a --delete -f 'P assets/***' "$SRC_STATIC/" "$DEST/"

# Assets órfãos de mais de 7 dias podem sair — nenhum HTML cacheado sobrevive tanto.
find "$DEST/assets" -type f -mtime +7 -delete 2>/dev/null || true

if [[ -d "$SRC_DIST" ]]; then
  [[ -f "$SRC_DIST/index.html" ]] && cp -a "$SRC_DIST/index.html" "$DEST/"
  [[ -f "$SRC_DIST/favicon.svg" ]] && cp -a "$SRC_DIST/favicon.svg" "$DEST/"
  cp -a "$SRC_DIST"/*.mp3 "$DEST/" 2>/dev/null || true
  cp -a "$SRC_DIST"/*.webm "$DEST/" 2>/dev/null || true
  for d in login register reset-password dashboard settings branding chart configuracoes feedbacks history payments welcome-trial; do
    if [[ -f "$SRC_DIST/$d/index.html" ]]; then
      mkdir -p "$DEST/$d"
      cp -a "$SRC_DIST/$d/index.html" "$DEST/$d/"
    fi
  done
  [[ -d "$SRC_DIST/admin" ]] && rsync -a "$SRC_DIST/admin/" "$DEST/admin/"
fi

# Assets públicos que o build às vezes não inclui (robô, áudios, branding)
if [[ -d "$SRC_PUBLIC" ]]; then
  cp -a "$SRC_PUBLIC"/*.webm "$DEST/" 2>/dev/null || true
  cp -a "$SRC_PUBLIC"/*.mp3 "$DEST/" 2>/dev/null || true
  [[ -f "$SRC_PUBLIC/favicon.svg" ]] && cp -a "$SRC_PUBLIC/favicon.svg" "$DEST/"
  if [[ -d "$SRC_PUBLIC/branding" ]]; then
    mkdir -p "$DEST/branding"
    rsync -a "$SRC_PUBLIC/branding/" "$DEST/branding/"
  fi
fi

python3 - <<'PY'
from pathlib import Path
root = Path("/var/www/elcapobot/assets")
for path in root.glob("*.js"):
    text = path.read_text()
    new = text.replace('mx("http://127.0.0.1:8080")', 'mx("https://api.elcapobot.online")')
    new = new.replace("mx('http://127.0.0.1:8080')", "mx('https://api.elcapobot.online')")
    if new != text:
        path.write_text(new)
        print(f"patched {path.name}")
PY

echo "Publicado em $DEST (fonte: $SRC_ROOT)"
curl -fsSI https://app.elcapobot.online/ | head -5
curl -fsSI https://app.elcapobot.online/login/ | head -5
