# Bancada do placar

Sobe uma cópia isolada da arquitetura de produção (gateway `external` +
`robot-runtime` `worker` + Redis próprio, persistência SQLite) e exercita cada
caminho do placar com o **código real**. Não toca em produção: rede e volume
próprios, sem `SUPABASE_*`.

Criada em 15/09/2026 para a revisão completa do placar
(`docs/PLACAR_DIAGNOSTICO_2026-09-15.md`). Use como teste de aceitação das
correções: hoje 6 cenários falham de propósito.

## Subir

```bash
SCRATCH=/tmp/placar-bench && mkdir -p $SCRATCH/data
docker network create placar-bench
docker run -d --name bench-redis --network placar-bench redis:7.4.2-alpine
docker run -d --name bench-runtime --network placar-bench \
  -v /opt/elcapo/backend:/w:ro -v $SCRATCH/data:/data -w /w \
  -e APP_ENV=dev -e ROBOT_RUNTIME_MODE=worker -e ROBOT_DB_PATH=/data/bench.db \
  -e PROD_REDIS_URL=redis://bench-redis:6379/0 -e BULLEX_SERVICE_URL=http://127.0.0.1:9 \
  -e AUTH_ALLOW_LEGACY_HEADERS=true -e PANEL_API_KEY=bench \
  elcapooneline-backend:latest python -m backend.robot_runtime_main
docker run -d --name bench-gateway --network placar-bench \
  -v /opt/elcapo/backend:/w:ro -v $SCRATCH/data:/data -w /w \
  -e APP_ENV=dev -e ROBOT_RUNTIME_MODE=external -e ROBOT_DB_PATH=/data/bench.db \
  -e PROD_REDIS_URL=redis://bench-redis:6379/0 -e BULLEX_SERVICE_URL=http://127.0.0.1:9 \
  -e AUTH_ALLOW_LEGACY_HEADERS=true -e PANEL_API_KEY=bench \
  elcapooneline-backend:latest uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

## Rodar

```bash
docker run --rm --network placar-bench \
  -v /opt/elcapo/backend:/w:ro -v $SCRATCH/data:/data \
  -v /opt/elcapo/backend/scripts/placar_bench:/bench -w /w -e PYTHONPATH=/w \
  -e APP_ENV=dev -e ROBOT_RUNTIME_MODE=worker -e ROBOT_DB_PATH=/data/bench.db \
  -e PROD_REDIS_URL=redis://bench-redis:6379/0 -e BULLEX_SERVICE_URL=http://127.0.0.1:9 \
  -e AUTH_ALLOW_LEGACY_HEADERS=true -e PANEL_API_KEY=bench \
  elcapooneline-backend:latest python /bench/bench3.py
```

`scenarios.py` (contabilização), `bench2.py` (sequência do painel),
`bench3.py` (o defeito do gateway + Shift+O + WS), `bench4.py` (reiniciar ciclo,
exclusão, isolamento, stop).

## Derrubar

```bash
docker rm -f bench-gateway bench-runtime bench-redis && docker network rm placar-bench
```

## Duas armadilhas da bancada (não são defeitos do produto)

1. **`order_id` não numérico conta como Shift+O.** `is_synthetic_trade` trata id
   sem dígitos como placar de vitrine, e aí o resumo de gestão ignora a operação
   e o Stop Win/Loss nunca dispara no teste. Use id numérico.
2. **`persist_robot` grava em background.** Ler `robot_states` logo depois pode
   pegar o valor anterior. Espere o `future` ou dê ~0,5s.
