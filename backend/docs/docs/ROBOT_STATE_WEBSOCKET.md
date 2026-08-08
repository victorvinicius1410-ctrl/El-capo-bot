# WebSocket do estado do robô

Canal primário de atualização do painel operacional (`/dashboard`, overlay).
Atualizado em **2026-08-07**.

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

Side-effects que antes rodavam em **todo** poll HTTP
(`reconcile_timeout_last_trade`, `ensure_robot_worker`, heartbeat) passam pelo
`maintenance_hook` só para usuários com WS conectado.

Publish: `persist_robot` agenda debounce 200 ms; o push loop também compara
digest e só envia se o payload mudou.

## Frontend

| Arquivo | Papel |
|---------|--------|
| `src/lib/robotStateWs.ts` | Ticket + connect + ping + reconnect |
| `src/hooks/useLiveTradingData.tsx` | Abre WS; `setQueryData` no React Query |
| `src/lib/robotState.ts` | `robotStateWsLive` pausa `refetchInterval` HTTP |

**Admin:** `AppShell` continua sem `LiveTradingDataProvider` em `/admin/*`.

## Como validar

1. Login cliente → DevTools Network: 1× `ws-ticket`, 1× WS; `/robot/state` quase ausente.
2. Ligar robô → mensagens `robot_state` sem rajada de HTTP.
3. Derrubar WS (offline) → reconnect + poll HTTP 30 s volta.

```bash
cd /opt/elcapo/backend && PYTHONPATH=. python3 -m unittest tests.test_robot_state_ws -v
cd /opt/elcapo/frontend && node --experimental-strip-types --test src/lib/robotStateWs.test.ts src/lib/robotState.poll.test.ts
```

## Histórico

- **2026-08-07** — Introdução do canal WS + ticket one-shot + fallback 30 s.
