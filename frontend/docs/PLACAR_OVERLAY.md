# Placar do overlay (WIN / LOSS / resultado)

Como o frontend mostra o placar da sessão no robô flutuante, e por que ele
às vezes **piscava zerado**, parecia **outro placar** ao iniciar/parar,
**regredia** (ex.: 5x3 → 2x0) ou **não baixava** ao excluir operação
marketing.

Atualizado em **2026-08-26**.

Documentação canônica (backend + causas completas):
[`docs/PLACAR_OVERLAY.md`](../../docs/PLACAR_OVERLAY.md).

## Correção (2026-08-26) — exclusão marketing × reconcile

Excluir no Histórico/Shift+O baixava o cache do front, mas o backend
republicava o Redis antigo (reconcile “nunca rebaixa”). O overlay voltava
ao placar anterior. Correção no gateway/runtime com `trust_local_score`
— detalhes em `docs/PLACAR_OVERLAY.md`.

## Correção (2026-08-24) — placar regredindo no start/stop

Snapshots de controle **não-zero porém atrasados** (ex.: 2x0 com sessão em
5x3) passavam pelas proteções de 2026-08-21 (só bloqueavam 0-0).

**Frontend:** `preserveRobotSessionScore` agora preserva o placar exibido
quando o snapshot traz **menos** WIN+LOSS, exceto queda de **uma** operação
(exclusão legítima no histórico).

**Backend:** `reconcile_session_score_on_gateway` +
`enrich_robot_snapshot_session_score` — nunca rebaixam o placar ao publicar
ou servir Redis (exceto caminho explícito `trust_local_score` na exclusão).

## Correção (2026-08-21) — placar some / exclusão não baixa WIN-LOSS

1. **Start/stop apagava o Redis:** o gateway publicava snapshot com placar
   local 0-0. Agora `adopt_live_session_score_if_blank` copia o placar do
   Redis/persistência antes do publish. Ver docs raiz `PLACAR_OVERLAY.md`.
2. **Excluir no Shift+O / Histórico:** o overlay não baixava. O front usa
   `mergeRobotSessionScore(..., { subtract: true })`; o backend limpa UUID +
   `broker_order_id` e chama `apply_marketing_score_removal` uma vez.

## O que o usuário vê

Há **um** placar: badges WIN e LOSS + resultado financeiro no
`RobotOverlay` (robô flutuante). Não existe um segundo componente de placar
no dashboard — o “outro placar” era o **mesmo overlay** trocando de 0-0
para os números reais (ou o contrário) quando o cache do React Query era
substituído.

## Fluxo no frontend

```text
POST /robot/start|stop|reset-score
        ↓
applyRobotMutationToCache  →  query ["robot-state", userId]
        ↓
WS /ws/robot-state  e  GET /robot/state (refetch em background)
        ↓
RobotOverlay lê robotState.wins / losses / profit
```

Shift+O (conta marketing) **não** passa por `/robot/start|stop`. O generate
grava o placar via `applyRobotSessionScoreToCache` e o backend publica
`apply_score` no runtime para o Redis/WS acompanharem.

Enquanto `robotState` ainda não chegou, o overlay **não** cai para `?? 0`
cego: guarda o último placar conhecido (`lastKnownScoreRef`).

## Causa do bug (start / stop)

Em produção (`ROBOT_RUNTIME_MODE=external`):

1. O overlay já mostra o placar vivo (ex.: 4 WIN / 2 LOSS) via WS do
   `robot-runtime`.
2. O cliente clica **Iniciar Operação** ou **Parar Operação**.
3. A resposta HTTP vem do **gateway**. O `auto_trader` local do gateway
   muitas vezes ainda está com `wins=0`, `losses=0`, `profit=0`.
4. O painel aplicava esse payload **inteiro** no React Query
   (`applyRobotMutationToCache`).
5. O overlay zerava. Logo depois o WS do runtime republicava o placar
   certo — visualmente “apareceu outro placar”.
6. Parar e iniciar de novo repetia o passo 3–5: o placar **reiniciava**
   na UI mesmo sem o botão **Reiniciar placar**.

O mesmo vale para o `void robotState.refetch()` depois da mutação: o
`GET /robot/state` pode ler o snapshot de controle no Redis **antes** do
runtime republicar o placar.

## Correção (2026-08-14)

`preserveRobotSessionScore` (`lib/robotState.ts`):

| Snapshot novo | Placar anterior | Resultado |
|---------------|-----------------|-----------|
| 0 / 0 / R$ 0 | tinha WIN, LOSS ou lucro ≠ 0 | **mantém** o anterior |
| números reais com **menos** WIN+LOSS (ex.: 2x0 vs 5x3) | tinha placar maior | **mantém** o anterior |
| queda de **1** WIN ou **1** LOSS (ex.: 5x3 → 5x2) | tinha placar maior | **aplica** (exclusão) |
| números reais com **mais** WIN+LOSS | qualquer | **aplica** o novo |
| 0 / 0 / R$ 0 com `allowBlankOverwrite` | qualquer | **zera** (só Reiniciar placar) |
| números reais, mas `stop_reset_at` do painel é **mais novo** | 0-0 já aplicado | **mantém 0-0** (snapshot Redis/WS atrasado) |

Usado em:

- `applyRobotMutationToCache` (start/stop preservam; reset-score zera)
- push WebSocket
- `GET /robot/state` (queryFn), inclusive `SESSION_NOT_FOUND`

## O que continua zerando de propósito

Só o botão **Reiniciar placar** (`POST /robot/reset-score`) chama
`applyRobotMutationToCache(..., { allowBlankOverwrite: true })`.

Stop Win/Loss **não** zera o placar. Ver `STOP_WIN_LOSS.md` e
`REINICIAR_PLACAR.md`.

## Como validar

```bash
cd /root/Frontend
node --import tsx --test src/lib/robotSessionScore.test.ts src/lib/robotCountdown.test.ts
```

No painel (conta de teste):

1. Operar até o overlay mostrar WIN/LOSS > 0.
2. **Parar Operação** → badges **não** voltam a 0.
3. **Iniciar Operação** → o mesmo placar permanece; não pisca 0-0 nem
   “salta” para outro número.
4. **Reiniciar placar** → badges vão a 0 na hora.
5. **Parar Operação** com placar 10x12 → continua 10x12 (não cai para 9x12).

## Correção (2026-08-15) — placar cai ao parar / reset “não pega”

### 10x12 → 9x12 ao clicar em Parar

O `robot-runtime` fazia `_hydrate_user_from_persistence(..., force=True)` no
comando `stop`. Isso substituía a memória viva (último WIN já no overlay) pelo
snapshot da DB, que é gravado em background e quase sempre **um resultado
atrasado**. O overlay aplicava o número menor porque não era 0-0.

Agora o `stop` **não** re-hidrata se o runtime já tem estado; só chama
`auto_trader.stop()` (desliga, preserva wins/losses). No `start` (force), se a
memória tiver mais operações que a persistência, o placar vivo prevalece.

### Reiniciar placar às vezes volta o número antigo

O cache ia a 0-0 (`allowBlankOverwrite`), mas o `void refetch()` / WS lia o
Redis ainda com o placar anterior (publisher a cada 1s, cmd `reset_score` ainda
na fila). `preserveRobotSessionScore` só bloqueava 0-0 *entrando*, não números
reais *atrasados*.

Correção: o payload inclui `stop_reset_at`. Se o painel já zerou com um reset
mais novo, snapshot antigo com 10x12 é ignorado.

## Arquivos

- `Frontend/src/lib/robotState.ts` — `preserveRobotSessionScore`, `mergeRobotSessionScore`
- `Frontend/src/hooks/useLiveTradingData.tsx` — cache / WS / HTTP / `applyRobotSessionScoreToCache`
- `Frontend/src/components/AppShell.tsx` — reset com overwrite explícito
- `Frontend/src/components/RobotOverlay.tsx` — último placar conhecido
- `Frontend/src/components/MarketingControlPanel.tsx` — Shift+O aplica o placar no overlay
- `Frontend/src/lib/robotSessionScore.test.ts`

## Relacionados

- [`INICIAR_PARAR_OPERACAO.md`](./INICIAR_PARAR_OPERACAO.md)
- [`REINICIAR_PLACAR.md`](./REINICIAR_PLACAR.md)
- [`OVERLAY_ROBO.md`](./OVERLAY_ROBO.md)
- [`ROBOT_STATE_WEBSOCKET.md`](./ROBOT_STATE_WEBSOCKET.md)
- [`MARKETING_SIMULATION.md`](../MARKETING_SIMULATION.md)
