# Iniciar / Parar operação — latência do overlay

Por que o botão **Iniciar Operação** / **Parar Operação** demorava a
refletir o clique no painel, e o que foi corrigido em **2026-08-13**.

## Sintoma

No overlay (e no painel Configurações → Robô), após clicar em iniciar ou
parar, a UI ficava vários segundos em "Iniciando..." / "Parando..." ou
continuava mostrando o botão errado (Parar com o robô já parado).

## Arquitetura relevante

Produção usa `ROBOT_RUNTIME_MODE=external`:

| Processo | Papel |
|----------|--------|
| `backend-gateway` | `POST /robot/start` e `/robot/stop`; serve HTTP/WS |
| `robot-runtime` | Workers; publica `robot:snapshot:{user_id}` no Redis |
| Painel | Lê estado via WS (ou `GET /robot/state`), que **prefere o Redis** |

O publisher do runtime só republica usuários presentes em `robot_tasks`.

## Causas da lentidão

### 1. Snapshot Redis stale no stop (principal)

Fluxo antigo no stop:

1. Gateway: `auto_trader.stop` → `enabled=false` + `persist` + cmd Redis `stop`
2. Resposta HTTP já vinha com `enabled=false`
3. Front fazia `await robotState.refetch()` → `GET /robot/state` lia o
   **snapshot Redis antigo** (`enabled=true`, `worker_running=true`, TTL 600s)
4. Runtime recebia `stop`, cancelava o worker (podia levar segundos numa
   chamada Bullex) e **não publicava** snapshot final
5. Como o user saía de `robot_tasks`, o publisher **parava** de atualizar —
   o Redis podia ficar com `worker_running=true` por muito tempo

O overlay usava:

```ts
operationRunning = enabled || worker_running
```

Então `worker_running=true` sozinho mantinha o botão **Parar Operação**.

Evidência em produção (Redis DB1): snapshot com `enabled=false` e
`worker_running=true` ao mesmo tempo.

### 2. Front esperava refetch após a mutação

`StartOperationDialog` / `AppShell` / `RobotControlPanel` faziam:

1. `POST /robot/config` (só no start)
2. `POST /robot/start` ou `/robot/stop`
3. **`await` `GET /robot/state`**

O passo 3 adicionava RTT e, no pior caso, reaplicava o snapshot stale.

### 3. Start ainda valida Bullex (latência real, esperada)

O `POST /robot/start` continua fazendo, quando necessário:

- status/sessão Bullex
- `GET /account` (saldo REAL)
- auto-reconnect (SSID/senha; connect pode ir até ~60s se a corretora
  estiver lenta)

Isso é segurança operacional (não ligar sem saldo/REAL). A correção desta
rodada **não** remove essas checagens — só faz a UI refletir o resultado
assim que a mutação responde, e publica o Redis na hora.

### 4. Persist wait no start (até 3s)

`start_robot_worker` aguarda a gravação em background
(`ROBOT_START_PERSIST_WAIT_SECONDS=3`) antes de publicar o cmd `start` ao
runtime (senão o runtime relia `enabled=false`). O snapshot de controle
agora é publicado **antes** dessa espera, para o painel não ficar preso.

## Correção (2026-08-13)

### Backend

| Peça | Comportamento |
|------|----------------|
| `publish_robot_control_snapshot` | Grava Redis + agenda WS push (como o disconnect) |
| `POST /robot/stop` | Publica `enabled=false`, `worker_running=false` **antes** do cmd |
| `POST /robot/start` | Publica snapshot com `enabled=true` **antes** do persist wait |
| `robot_runtime` stop | Remove de `robot_tasks` → publica snapshot → cancela task (timeout 2s) |

Log: `[ROBOT_CONTROL_SNAPSHOT_PUBLISHED]`.

### Frontend

| Peça | Comportamento |
|------|----------------|
| `isRobotOperationRunning` | Usa só `enabled` |
| `applyRobotMutationToCache` | Aplica o `data` de start/stop no React Query na hora |
| Overlay / painel Robô | Refetch de `/robot/state` em background (`void`), sem bloquear o botão |

## Como validar

```bash
# Backend
cd /opt/elcapo/backend
PYTHONPATH=. python3 -m unittest tests.test_robot_control_snapshot -v
PYTHONPATH=. python3 -m unittest \
  tests.test_robot_reset_cycle.RobotResetCycleTests.test_robot_config_unlocks_when_redis_says_stopped_but_gateway_enabled \
  -v

# Frontend
cd /root/Frontend
node --import tsx --test src/lib/robotCountdown.test.ts
```

No painel (conta de teste):

1. Conectar Bullex → **Iniciar Operação** → botão deve virar **Parar** assim
   que o diálogo fechar (sem vários segundos de atraso visual).
2. **Parar Operação** → botão deve voltar para **Iniciar** na hora; Redis:

```bash
docker exec webhook-redis redis-cli -n 1 GET 'robot:snapshot:<USER_ID>' \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; print(d['enabled'], d['worker_running'])"
# esperado após stop: False False
```

3. Após **Stop Loss / Stop Win**: overlay parado → **Confirmar e iniciar**
   no diálogo **não** deve mostrar "Pare o robô antes de alterar
   configurações." (pode pedir **Reiniciar placar** se o placar ainda
   violar o stop — isso é esperado).

## Incidente 2026-08-13 — "Pare o robô..." com overlay parado

### Sintoma

Vários clientes viam o robô **parado** no overlay, abriam **Iniciar
operação**, clicavam **Confirmar e iniciar** e recebiam:

> Pare o robô antes de alterar configurações.

(`409 ROBOT_RUNNING_CONFIG_LOCKED` no `POST /robot/config`)

### Causa

Em `ROBOT_RUNTIME_MODE=external`:

| Processo | O que acontece no Stop Win/Loss |
|----------|--------------------------------|
| `robot-runtime` | `pause_by_stop` → `enabled=false` + snapshot Redis |
| Painel | Lê Redis → mostra parado / botão Iniciar |
| `backend-gateway` | Memória **continua** `enabled=true` do último start |

O diálogo chama `POST /robot/config` **antes** do start. O lock usava só a
memória do gateway → trava fantasma.

Evidência em produção: log `[ROBOT_CONFIG_LOCKED] ... enabled=True` no
gateway com Redis `enabled=False status=STOPPED` (ou `STOP_LOSS_HIT`).

### Correção

`reconcile_gateway_enabled_from_runtime_snapshot`:

1. Em mode=external, se a memória tem `enabled=true` e Redis (ou
   persistência) tem `enabled=false`, alinha memória (status/placar
   inclusive).
2. Chamado em `robot_config`, `robot_config_locked`, `robot_start` e
   `ensure_robot_worker` (este último para cortar spam de `ensure`).

Log: `[GATEWAY_ENABLED_RECONCILED_FROM_RUNTIME]`.

## Arquivos

- `Backend/backend/main.py` — `publish_robot_control_snapshot`, start/stop,
  `reconcile_gateway_enabled_from_runtime_snapshot`
- `Backend/backend/robot_runtime_main.py` — ordem do cmd `stop`
- `Backend/tests/test_robot_control_snapshot.py`
- `Backend/tests/test_robot_reset_cycle.py` — split-brain config lock
- `Frontend/src/lib/robotState.ts` — `isRobotOperationRunning`
- `Frontend/src/hooks/useLiveTradingData.tsx` — `applyRobotMutationToCache`
- `Frontend/src/components/AppShell.tsx`, `StartOperationDialog.tsx`,
  `RobotControlPanel.tsx`

## Relacionados

- [`ROBOT_STATE_WEBSOCKET.md`](./ROBOT_STATE_WEBSOCKET.md) — canal WS + Redis
- [`ROBO_E_SUPORTE.md`](./ROBO_E_SUPORTE.md) — regras de operação
- [`STOP_WIN_LOSS.md`](./STOP_WIN_LOSS.md) — pause no runtime
- [`PERFORMANCE_SISTEMA.md`](./PERFORMANCE_SISTEMA.md) — modo external
- [`BULLEX_CREDENCIAIS.md`](./BULLEX_CREDENCIAIS.md) — reconnect no start
- [`CONFIGURACOES.md`](./CONFIGURACOES.md) — painel Robô / diálogo início
