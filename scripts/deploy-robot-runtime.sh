#!/usr/bin/env bash
# Deploy seguro só do robot-runtime (hotfix de lógica do robô), sem tocar
# no bullex-service (preserva sessões da corretora).
#
# Existe por causa do incidente 2026-08-18 ~21h: `docker compose build`
# (sem -p) + `up -d --force-recreate -p elcapooneline` (sem --build)
# recriou o container com a imagem ANTIGA, silenciosamente, e o fix ficou
# ~1h40 sem rodar em produção. Ver docs/DEPLOY_VPS.md e
# docs/PERFORMANCE_SISTEMA.md ("fix das 19:20 nunca aplicado").
#
# Uso:
#   /opt/elcapo/scripts/deploy-robot-runtime.sh
set -euo pipefail

ROOT=/opt/elcapo/backend
PROJECT=elcapooneline
cd "$ROOT"

echo "[elcapo] build robot-runtime (project=$PROJECT)..."
docker compose -p "$PROJECT" --env-file .env build robot-runtime

echo "[elcapo] up --no-deps robot-runtime (bullex-service intocado)..."
docker compose -p "$PROJECT" --env-file .env up -d --no-deps robot-runtime

echo "[elcapo] confirmando que a imagem nova está de fato em uso..."
sleep 2
running_image=$(docker inspect robot-runtime --format '{{.Config.Image}}')
expected_image="${PROJECT}-robot-runtime"
if [[ "$running_image" != "$expected_image" ]]; then
  echo "[elcapo] ERRO: container usa imagem '$running_image', esperado '$expected_image'" >&2
  exit 1
fi
echo "[elcapo] imagem OK: $running_image"

echo "[elcapo] health:"
ok=0
for i in 1 2 3 4 5 6 7 8; do
  if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 3
done
if [[ "$ok" -ne 1 ]]; then
  echo "[elcapo] AVISO: health check do gateway não respondeu (robot-runtime não expõe /health próprio)" >&2
fi

# Incidente 2026-08-18 ~21h10 e recorrência ~23h09: PUBLISH em rajada logo
# após o `up` some sem log nem erro se o processo ainda não terminou de
# assinar `robot:cmd` (Redis pub/sub não é fila). Espera o log de subscribe
# ANTES de publicar em vez de um sleep fixo — sleep fixo já se mostrou
# insuficiente quando há muitos usuários restaurados no boot (286 states
# levaram ~36s até o subscribe em 18/08 23h09, mais que os 24s do health
# check acima).
echo "[elcapo] aguardando robot-runtime assinar o canal robot:cmd..."
subscribed=0
for i in $(seq 1 30); do
  if docker logs robot-runtime --since 2m 2>&1 | grep -q "\[ROBOT_RUNTIME\] subscribed channel=robot:cmd"; then
    subscribed=1
    break
  fi
  sleep 2
done
if [[ "$subscribed" -ne 1 ]]; then
  echo "[elcapo] AVISO: não vi 'subscribed channel=robot:cmd' em 60s; publicando mesmo assim (risco de perder mensagens)." >&2
else
  echo "[elcapo] runtime assinado ao robot:cmd, seguro publicar."
fi

echo
echo "[elcapo] religando workers a partir do Supabase (enabled=true)..."
SUPABASE_URL_VALUE=$(grep -E '^SUPABASE_URL=' .env | cut -d= -f2-)
SUPABASE_KEY_VALUE=$(grep -E '^SUPABASE_SERVICE_ROLE_KEY=' .env | cut -d= -f2-)
if [[ -z "$SUPABASE_URL_VALUE" || -z "$SUPABASE_KEY_VALUE" ]]; then
  echo "[elcapo] AVISO: não achei SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY no .env; pulei o religamento automático." >&2
  echo "[elcapo] Religue manualmente: PUBLISH robot:cmd '{\"user_id\":\"<UUID>\",\"action\":\"start\"}'" >&2
else
  # Filtra para UUID (defesa em profundidade, espelha o guard em
  # backend/robot_runtime_main.py::_is_valid_account_user_id). Fixtures de
  # teste com enabled=true no Supabase de produção — ver incidente
  # 2026-08-18 ~22h18 em docs/ROBO_E_SUPORTE.md — são avisadas aqui em vez
  # de silenciosamente religadas.
  all_enabled=$(curl -sS "${SUPABASE_URL_VALUE}/rest/v1/robot_states?select=user_id&enabled=eq.true" \
    -H "apikey: ${SUPABASE_KEY_VALUE}" -H "Authorization: Bearer ${SUPABASE_KEY_VALUE}")
  user_ids=$(echo "$all_enabled" | python3 -c "
import json, sys, uuid
data = json.load(sys.stdin)
valid, invalid = [], []
for r in data:
    uid = r.get('user_id') or ''
    try:
        uuid.UUID(uid)
        valid.append(uid)
    except ValueError:
        invalid.append(uid)
print('\n'.join(valid))
if invalid:
    print('INVALID:' + ','.join(invalid), file=sys.stderr)
")
  invalid_ids=$(echo "$all_enabled" | python3 -c "
import json, sys, uuid
data = json.load(sys.stdin)
for r in data:
    uid = r.get('user_id') or ''
    try:
        uuid.UUID(uid)
    except ValueError:
        print(uid)
")
  if [[ -n "$invalid_ids" ]]; then
    echo "[elcapo] AVISO: user_id não-UUID com enabled=true no Supabase de PRODUÇÃO (fixture de teste?), NÃO religado (o guard do runtime bloquearia mesmo assim, mas corrija a fonte):" >&2
    echo "$invalid_ids" | sed 's/^/  - /' >&2
    echo "[elcapo] Corrija com: PATCH robot_states?user_id=eq.<id> {\"enabled\": false}" >&2
  fi

  count=0
  while IFS= read -r uid; do
    [[ -z "$uid" ]] && continue
    docker exec webhook-redis redis-cli -n 1 PUBLISH robot:cmd "{\"user_id\":\"$uid\",\"action\":\"start\"}" >/dev/null
    count=$((count + 1))
  done <<< "$user_ids"
  echo "[elcapo] $count comando(s) start publicado(s) (só user_id em formato UUID)."

  echo "[elcapo] aguardando 20s para confirmar criação dos workers pelo log..."
  sleep 20
  created=$(docker logs robot-runtime --since 25s 2>&1 | grep -cE "WORKER_CREATED|WORKER_ALREADY_RUNNING" || true)
  echo "[elcapo] workers confirmados no log: $created / $count esperados"
  if [[ "$created" -lt "$count" ]]; then
    echo "[elcapo] menos que o esperado confirmou — republicando os que faltaram..." >&2
    while IFS= read -r uid; do
      [[ -z "$uid" ]] && continue
      if ! docker logs robot-runtime --since 30s 2>&1 | grep -qE "(WORKER_CREATED|WORKER_ALREADY_RUNNING)\] user_id=$uid"; then
        docker exec webhook-redis redis-cli -n 1 PUBLISH robot:cmd "{\"user_id\":\"$uid\",\"action\":\"start\"}" >/dev/null
      fi
    done <<< "$user_ids"
    sleep 10
    created=$(docker logs robot-runtime --since 40s 2>&1 | grep -cE "WORKER_CREATED|WORKER_ALREADY_RUNNING" || true)
    echo "[elcapo] após republicar: $created / $count confirmados no log."
  fi
  if [[ "$created" -lt $((count / 2)) ]]; then
    echo "[elcapo] AVISO: menos da metade dos workers confirmou start. Investigue manualmente." >&2
  fi
fi

echo
echo "[elcapo] OK — valide também a constante alterada, se aplicável:"
echo "  docker exec robot-runtime grep -n 'NOME_DA_CONSTANTE' /app/backend/main.py"
