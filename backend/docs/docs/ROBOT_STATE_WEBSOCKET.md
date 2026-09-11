# WebSocket do estado do robô

Canal primário de atualização do painel operacional (`/dashboard`, overlay).
Atualizado em **2026-08-17**.

## Por que existe

O poll de `GET /robot/state` (antes 1–4 s × N usuários) saturava o único
event loop do `backend-gateway` (auth Supabase + reconcile + ensure worker).
O push via WebSocket autentica **uma vez** na conexão e envia deltas.

## Contrato

| Peça | Valor |
|------|--------|
| Ticket | `GET /robot/ws-ticket` → `{ ticket, expires_in: 60, path }` |
| WS | `wss://api…/ws/robot-state?ticket=…` |
| Auth alternativa | Cookie/`Authorization` na handshake (sem ticket) |
| Mensagens server→client | `{ "type": "robot_state", "data": {…} }` |
| Ping client→server | `{ "type": "ping" }` a cada 20 s → `{ "type": "pong" }` |
| Fallback HTTP | `GET /robot/state` com `Retry-After: 30` só se WS cair |

## Backend

| Arquivo | Papel |
|---------|--------|
| `backend/robot_state_ws.py` | Hub, tickets, push loop (1 s), maintenance (5 s) |
| `backend/main.py` | Rotas `/robot/ws-ticket`, `/ws/robot-state`, snapshot, wiring |
| `backend/robot_bus.py` | Redis DB1: cmds + snapshots + `iter_state_messages` |

Side-effects que antes rodavam em **todo** poll HTTP
(`reconcile_timeout_last_trade`, `ensure_robot_worker`, heartbeat) passam pelo
`maintenance_hook` só para usuários com WS conectado.

Publish: `persist_robot` agenda debounce 200 ms; o push loop também compara
digest e só envia se o payload mudou.

### Modo `ROBOT_RUNTIME_MODE=external` (produção)

1. Workers do robô rodam no container `robot-runtime` e publicam em
   `robot:state` + key `robot:snapshot:{user_id}`.
2. O gateway **não** tem `auto_trader` atualizado localmente: o
   `snapshot_builder` lê o snapshot Redis; o relay
   `_relay_robot_state_from_redis` agenda `schedule_publish` a cada mensagem.
3. Boot do hub/warmer/relay: `on_event("startup")` + safety-net no middleware
   HTTP (primeiro request) se o startup falhar em silêncio.
4. Auth cross-subdomínio: preferir `GET /robot/ws-ticket` (ticket na query do WS).
5. **Start/stop do cliente:** o gateway chama
   `publish_robot_control_snapshot` imediatamente (mesmo padrão do
   disconnect). Sem isso o Redis antigo (`worker_running=true`, TTL 600s)
   mantinha o overlay em "Parar Operação". Ver
   [`INICIAR_PARAR_OPERACAO.md`](./INICIAR_PARAR_OPERACAO.md).

Atualizado em **2026-08-13**.

## Frontend

| Arquivo | Papel |
|---------|--------|
| `src/lib/robotStateWs.ts` | Ticket + connect + ping + reconnect |
| `src/hooks/useLiveTradingData.tsx` | Abre WS; `setQueryData` no React Query |
| `src/lib/robotState.ts` | `robotStateWsLive` pausa `refetchInterval` HTTP |

**Admin (`/admin/*`):** `AppShell` continua sem `LiveTradingDataProvider`
(performance da navegação). **Sessão de suporte** (admin dentro da conta do
lead, rotas `/dashboard` `/configuracoes` `/history`): o provider **monta**,
porque essas telas chamam `useLiveTradingData()`. O overlay do robô permanece
oculto. Ver `ROBO_E_SUPORTE.md` §5.

## Dependência obrigatória no gateway

O `uvicorn` puro **não** faz upgrade WebSocket. Sem a lib ASGI, o log do
container mostra:

```text
WARNING: Unsupported upgrade request.
WARNING: No supported WebSocket library detected. Please use "pip install 'uvicorn[standard]'", or install 'websockets' or 'wsproto' manually.
```

e o Nginx registra `GET /ws/robot-state?...` como **404** (não 101).

| Pacote | Papel |
|--------|--------|
| `uvicorn==0.30.6` | servidor ASGI |
| `websockets==14.2` | suporte a upgrade WS (**obrigatório**) |
| `websocket-client==1.8.0` | cliente sync Bullex — **não** substitui o acima |

## Troubleshooting (console do browser)

| Sintoma no DevTools | Causa real | O que fazer |
|---------------------|------------|-------------|
| `Status code: 502` + “Origin … not allowed by Access-Control-Allow-Origin” | Upstream (gateway) caiu/reiniciou; Nginx devolve 502 **sem** headers CORS | Esperar health; evitar deploy com usuários ativos ou usar rolling restart |
| `WebSocket … failed: bad response from the server` + access log **404** | Falta `websockets` no container do gateway | Garantir `websockets==14.2` no `requirements.txt` e rebuild |
| `Fetch … /robot/state` / `/bullex/credentials` falha só durante deploy | Mesmo 502 transitório do recreate dos containers | Recarregar após `curl https://api…/health` = 200 |

**Importante:** o browser costuma rotular 502 de gateway como “erro de CORS”.
Se `Access-Control-Allow-Origin` some **e** o status é 502, o problema não é
lista de origins — é API indisponível.

## Como validar

1. Login cliente → DevTools Network: 1× `ws-ticket`, 1× WS **101**; `/robot/state` quase ausente.
2. Ligar robô → mensagens `robot_state` sem rajada de HTTP.
3. Derrubar WS (offline) → reconnect + poll HTTP 30 s volta.
4. No container: `python -c "import websockets; print(websockets.__version__)"` → `14.2`.
5. Logs do gateway **não** devem conter `No supported WebSocket library detected`.

```bash
cd /opt/elcapo/backend && PYTHONPATH=. python3 -m unittest tests.test_robot_state_ws -v
cd /opt/elcapo/frontend && node --experimental-strip-types --test src/lib/robotStateWs.test.ts src/lib/robotState.poll.test.ts
```

## Histórico

- **2026-08-07** — Fix prod: `websockets==14.2` no image do gateway (WS
  retornava 404 / “Unsupported upgrade”; CORS 502 era efeito colateral de
  deploy `docker compose up --build` derrubando o upstream).
- **2026-08-07** — Relay Redis `robot:state` → hub WS no gateway (modo
  `external`); boot idempotente + safety-net no middleware HTTP.
- **2026-08-07** — Introdução do canal WS + ticket one-shot + fallback 30 s.
