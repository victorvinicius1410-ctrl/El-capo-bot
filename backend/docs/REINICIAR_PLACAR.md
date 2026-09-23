# Reiniciar placar (`POST /robot/reset-score`)

Como o botão **Reiniciar placar** do overlay funciona, o que ele zera, e a
correção de **2026-08-13** (botão “não fazia nada” em produção).

## O que o botão faz

| Campo | Após o clique |
|-------|----------------|
| `wins` / `losses` / `profit` | `0` |
| Histórico em memória do placar | limpo |
| Status `STOP_WIN_HIT` / `STOP_LOSS_HIT` | vira `STOPPED` |
| `stop_reset_at` | timestamp UTC do reset |
| `robot_trade_history` / `robot_trades` | **não** apaga |
| Tela `/history` | **não** muda |

Para zerar placar **e** histórico persistido, use
`POST /robot/reset-cycle` (não o botão do overlay).

## Arquitetura (produção)

`ROBOT_RUNTIME_MODE=external`:

| Processo | Papel |
|----------|--------|
| `backend-gateway` | Atende `POST /robot/reset-score`; serve HTTP/WS |
| `robot-runtime` | Memória viva do placar; publica `robot:snapshot:{user_id}` |
| Painel | Lê estado via WS / `GET /robot/state` (**prefere Redis**) |

## Bug (antes de 2026-08-13)

Sintoma: clicar em **Reiniciar placar** mostrava toast de sucesso (ou
“Reiniciando…”) e o placar **voltava** aos wins/losses antigos.

Cadeia:

1. Gateway zerava só o `auto_trader` **local** + `persist_robot` (async).
2. Em mode=`external`, `persist_robot` **não** grava Redis.
3. Front fazia `await robotState.refetch()` → `GET /robot/state` lia o
   snapshot Redis antigo (TTL 600s) com placar cheio.
4. O `_snapshot_publisher` do runtime republicava o placar vivo antigo por
   cima de qualquer correção pontual.
5. `rehydrate_score_from_persistence_if_blank` podia ainda restaurar o
   placar da DB se a memória já estivesse `0` antes do persist terminar.

Mesmo padrão do bug de Iniciar/Parar operação (ver
`INICIAR_PARAR_OPERACAO.md`).

## Correção (2026-08-13)

### Backend

| Peça | Comportamento |
|------|----------------|
| `publish_robot_control_snapshot` | Grava Redis + agenda WS; no stop força `worker_running=false` e corrige `status=STOPPED` |
| `POST /robot/reset-score` | Aguarda persist (até 3s), chama `publish_robot_control_snapshot`, em external publica cmd `reset_score` |
| `robot_runtime` `_handle_command` | Ação `reset_score`: zera memória do runtime + republica snapshot |
| `rehydrate_score_from_persistence_if_blank` | Se `stop_reset_at` está setado, **não** reidrata placar antigo |

Logs: `[ROBOT_SCORE_RESET]`, `[ROBOT_SCORE_RESET_DELEGATED]`,
`[ROBOT_RUNTIME_CMD] action=reset_score`.

### Frontend

| Peça | Comportamento |
|------|----------------|
| `AppShell.resetScore` | `applyRobotMutationToCache` com o `data` da mutação; refetch em background (`void`) |
| `applyRobotMutationToCache` | Também cobre `reset-score` (além de start/stop) |

## Arquivos

- `backend/main.py` — endpoint + rehydrate
- `backend/robot_runtime_main.py` — cmd `reset_score`
- `backend/robot_bus.py` — canal de comandos
- `backend/auto_trader.py` — `reset_score()`
- `frontend/src/components/AppShell.tsx` — botão do overlay
- `frontend/src/hooks/useLiveTradingData.tsx` — cache React Query

## Validação

```bash
# Backend
cd /root/Backend
PYTHONPATH=. python3 -m unittest \
  tests.test_robot_control_snapshot \
  tests.test_robot_reset_cycle \
  tests.test_insufficient_funds_and_score -v

# Frontend (regressão start/stop / placar)
cd /root/Frontend
node --import tsx --test src/lib/robotCountdown.test.ts
```

No painel (conta de teste):

1. Operar até ter wins/losses no overlay.
2. Clicar **Reiniciar placar** → badges devem ir a **0** na hora.
3. Redis (DB1):

```bash
docker exec webhook-redis redis-cli -n 1 GET 'robot:snapshot:<USER_ID>' \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; print(d['wins'], d['losses'], d['profit'])"
```

Esperado: `0 0 0.0` (ou profit `0`).

4. Confirmar que `/history` ainda lista as operações anteriores.

## Investigação: “placar resetou sozinho” (2026-08-13)

Relato de usuário. Revisão de código + logs de produção (`backend-gateway`,
`robot-runtime`, nginx).

### Conclusão

O sistema **não** zera o placar sozinho para clientes normais. Não há cron,
job de meia-noite nem ciclo do robô que chame `reset_score` sem o cliente.

### O que zera o placar

| Caminho | Quem dispara | Conta |
|---------|--------------|-------|
| `POST /robot/reset-score` | Botão **Reiniciar placar** no overlay | Todas |
| `POST /robot/reset-cycle` com `reset_score` | Fluxo explícito de ciclo | Todas |
| ~~`MARKETING_AUTO_RESET_SCORE_ON_START`~~ | `POST /robot/start` com o stop batendo | **Removido em 2026-09-23** — nenhuma conta zera sozinha |

### Evidência (produção, 2026-08-13)

- Todos os `action=reset_score` no `robot-runtime` bateram com
  `POST /robot/reset-score` no nginx, referer `app.elcapobot.online`,
  User-Agent mobile Chrome (clique no painel).
- Vários double-taps (ex.: mesmo IP com 2 POSTs em ~4s) — o botão **não**
  pede confirmação.
- Zero ocorrências de `MARKETING_AUTO_RESET_SCORE_ON_START` nas últimas 72h.
- Stop Win/Loss **não** zera o placar; só pausa. Para religar, o fluxo
  pede **Reiniciar placar** + **Iniciar** (`STOP_WIN_LOSS.md`).

### Confusão comum

1. Clique acidental no botão (sem diálogo de confirmação, frequente no mobile).
2. Após Stop Win/Loss o usuário reinicia o placar para poder operar de novo.
3. `/history` continua com as operações — só o placar da sessão zera.
4. Bug antigo (08/08): `ensure` re-hidratava placar atrasado da DB — **já
   corrigido** (`force` só em `start`/`stop`; memória viva no `ensure`).

### Como auditar um relato

```bash
# Runtime
docker logs robot-runtime --since 24h 2>&1 | grep "action=reset_score"

# Nginx (prova de clique no browser)
grep reset-score /var/log/nginx/access.log | tail -30

# Auto-reset marketing
docker logs backend-gateway --since 24h 2>&1 | grep MARKETING_AUTO_RESET
```

## Relacionados

- [`HISTORICO.md`](./HISTORICO.md) — placar vs histórico persistido
- [`STOP_WIN_LOSS.md`](./STOP_WIN_LOSS.md) — liberar start após stop
- [`INICIAR_PARAR_OPERACAO.md`](./INICIAR_PARAR_OPERACAO.md) — mesmo padrão Redis/external
- [`ROBO_E_SUPORTE.md`](./ROBO_E_SUPORTE.md) — visão geral do robô
- [`OVERLAY_ROBO.md`](./OVERLAY_ROBO.md) — UI do overlay
