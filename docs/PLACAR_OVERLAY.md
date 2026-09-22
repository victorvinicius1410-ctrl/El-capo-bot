# Placar do overlay (WIN / LOSS / resultado)

Como o frontend mostra o placar da sessão no robô flutuante, e por que ele
às vezes **piscava zerado**, **não zerava no Reiniciar placar**, **caía
um ponto ao parar** (ex.: 10x12 → 9x12), **regredia no start/stop**
(ex.: 5x3 → 2x0) ou **não baixava ao excluir** operação marketing do
histórico.

Atualizado em **2026-09-22**.

Espelho detalhado: [`Frontend/docs/PLACAR_OVERLAY.md`](../frontend/docs/PLACAR_OVERLAY.md).

## Correção (2026-09-22) — placar zerado até apertar F5

### Sintoma

Entrar no painel e ver **0 x 0** com o robô tendo resultado do dia; o El Capo
até narra o resultado, mas o placar não anda. **Recarregar a tela mostra o
placar certo** — ou seja, o servidor sempre teve o número: quem estava preso
era o painel.

### Causas (as duas no cliente)

| # | Onde | O que acontecia |
|---|------|-----------------|
| 1 | `robotStateRefetchInterval` | Com o WS conectado o poll HTTP era **desligado** (`return false`). Se o socket vira zumbi (fica `OPEN` e para de entregar, sem evento de `close` — rede móvel, proxy, suspensão), nada mais atualiza o painel. Pior: `robotStateWsLive` é variável de módulo, então a queda do WS **não** reprogramava o intervalo do React Query — o poll seguia parado mesmo depois do `onclose`. |
| 2 | `preserveRobotSessionScore` | A trava do "Reiniciar placar" (placar em branco + `stop_reset_at` mais novo que o do snapshot) **não tinha prazo** e ainda regravava o `stop_reset_at` novo no estado exibido: a condição se auto-alimentava e TODO placar recebido depois era descartado até o F5. É o mesmo defeito que o backend tirou do `reconcile_session_score_on_gateway` em 15/09 (`PLACAR_DIAGNOSTICO_2026-09-15.md` §F1) — a cópia do painel tinha ficado para trás. |

### Correção

| Camada | Mudança |
|--------|---------|
| `robotStateWsUrl.ts` | `ROBOT_WS_STALE_AFTER_MS` (2 pings + folga = 45s) e `robotStateWsIsStale()` |
| `robotStateWs.ts` | Watchdog: toda mensagem (inclusive o `pong` do ping) marca sinal de vida; socket calado além da janela é derrubado, avisa `onClose` e reconecta |
| `robotState.ts` | Com WS vivo o HTTP não para mais: reconciliação a cada `ROBOT_STATE_WS_RECONCILE_POLL_MS` (60s). Backoff de erro continua tendo precedência |
| `robotState.ts` | Trava do reset passa a valer só pela janela `SESSION_SCORE_RESET_GUARD_MS` (120s, mesmo TTL da marca `robot:score_authority` do backend) |
| `useLiveTradingData.tsx` | `wsLive` também em estado React: a queda do WS reprograma o `refetchInterval` na hora |

Testes: `robotState.poll.test.ts`, `robotStateWs.test.ts`, `robotSessionScore.test.ts`
(260 testes do front, 0 falhas).

### O que NÃO foi tocado

O backend. O caminho de leitura do gateway (`enrich_robot_snapshot_session_score`
→ maior placar entre Redis, memória e DB) está correto e foi conferido contra
produção em 22/09: Redis e `robot_states` batem para todos os clientes com
sessão viva. Fica pendente a visibilidade: o gateway roda em nível **WARNING**,
e as linhas que explicam a decisão do placar (`[SCORE_SNAPSHOT_ENRICHED]`,
`[SCORE_RECONCILED_ON_GATEWAY]`, `[ROBOT_SCORE_RESET]`) são `logger.info` — não
aparecem em produção, o que impede provar o caminho de um incidente pelo log.

## Correção (2026-08-26) — excluir histórico marketing não baixava o placar

### Sintoma

Conta marketing: excluir uma operação no Histórico / Shift+O removia a linha,
mas o overlay (WIN×LOSS) **permanecia igual**.

### Causa

A correção de 2026-08-24 (`reconcile_session_score_on_gateway` — **nunca
rebaixa**) conflitava com a exclusão:

1. `apply_marketing_score_removal` adotava o Redis (ex.: 5x3) e decrementava
   (4x3).
2. `publish_marketing_score_to_overlay` → `publish_robot_control_snapshot`
   chamava de novo o reconcile.
3. O Redis **ainda** tinha 5x3 → memória voltava a 5x3 → overlay intacto.
4. No runtime, `apply_score` republicava o snapshot sem `trust_local_score` e
   podia repetir o mesmo ciclo.

### Correção

| Camada | Mudança |
|--------|---------|
| Backend | `publish_robot_control_snapshot(..., trust_local_score=True)` pula o adopt/reconcile |
| Backend | `apply_marketing_score_removal` persiste e publica com `trust_local_score=True` |
| Backend | `sync_marketing_display_to_robot` idem (placar local intencional) |
| Runtime | `apply_score` publica com `trust_local_score=True` |

Teste: `test_apply_removal_publish_does_not_restore_redis_score` em
`test_marketing_history_deletion_persistence.py`.

## Correção (2026-08-24) — placar regredindo no start/stop (5x3 → 2x0)

### Sintoma

Conta com placar **5x3**; ao **Iniciar** ou **Parar** operação o overlay
caía para **2x0** (ou outro valor parcial errado), não só 0-0.

### Causa

A correção de 2026-08-21 (`adopt_live_session_score_if_blank` +
`preserveRobotSessionScore`) só bloqueava snapshot **zerado**. Snapshots de
controle **atrasados mas não-zero** (Redis/DB com menos WIN+LOSS que a
sessão viva) passavam:

1. **Gateway** publicava 2x0 no start/stop (`publish_robot_control_snapshot`).
2. **GET /robot/state** devolvia o Redis cru, sem comparar com persistência.
3. **`reconcile_gateway_enabled_from_runtime_snapshot`** copiava wins/losses
   do runtime mesmo quando **abaixo** do placar local.
4. **Frontend** aceitava qualquer snapshot “com números reais”, inclusive 2x0
   menor que 5x3.

### Correção

| Camada | Função | Comportamento |
|--------|--------|---------------|
| Backend | `reconcile_session_score_on_gateway` | Escolhe o placar com **mais** WIN+LOSS entre memória, Redis e DB — **nunca rebaixa** |
| Backend | `enrich_robot_snapshot_session_score` | Antes de servir Redis no HTTP/WS, eleva wins/losses/profit ao máximo das fontes |
| Backend | `adopt_live_session_score_if_blank` | Delega ao reconcile (não só 0-0) |
| Backend | `reconcile_gateway_enabled_from_runtime_snapshot` | Usa `_pick_preferred_session_score` em vez de sobrescrever cegamente |
| Frontend | `preserveRobotSessionScore` | Preserva placar exibido se incoming tem **menos** operações, exceto queda de **1** WIN/LOSS (exclusão legítima) |

Logs: `[SCORE_RECONCILED_ON_GATEWAY]`, `[SCORE_SNAPSHOT_ENRICHED]`.

## Correção (2026-08-21) — placar some no start / exclusão marketing

### Placar some ao iniciar (e volta / some de novo)

Em `ROBOT_RUNTIME_MODE=external` o placar vivo fica no `robot-runtime`.
O gateway local muitas vezes tem `wins/losses=0`. `publish_robot_control_snapshot`
(no **Iniciar** / **Parar**) republicava esse 0-0 no Redis e o overlay
“sumia”. O front até preservava o cache, mas o próximo poll/WS lia o Redis
zerado.

**Correção:** `adopt_live_session_score_if_blank` — antes de publicar o
snapshot de controle, o gateway copia WIN/LOSS/profit do Redis (ou da
persistência) se a memória local estiver em branco e não houver
`stop_reset_at`.

### Excluir histórico marketing não baixava o placar

`DELETE /marketing-simulation/trades/{id}` limpa o histórico, mas o placar
só era decrementado se a linha existisse em `robot_trade_history` **com o
mesmo id**. Excluir pelo UUID do Shift+O (quando o robô guardava o
`broker_order_id`) ou só na memória **não** alterava o overlay.

**Correção:**

1. Exclusão devolve a operação e limpa UUID **e** `broker_order_id`.
2. `apply_marketing_score_removal` decrementa **uma vez**, adotando o placar
   vivo do Redis antes (evita publicar 0-0 por cima).
3. Front: `mergeRobotSessionScore(..., { subtract: true })` no Shift+O e em
   `/history`.

Ver `HISTORICO.md` e `MARKETING_SIMULATION.md`.

## Correção (2026-08-18) — Shift+O não gerava o placar visual

Conta marketing: **Gerar histórico do placar** (Shift+O) gravava WIN/LOSS no
gateway, mas o overlay do El Capo continuava 0-0. O painel lê Redis do
`robot-runtime`; o gateway não publicava o placar sintético.

Agora `publish_marketing_score_to_overlay` grava Redis + comando `apply_score`,
e o front aplica o placar no cache na hora. Ver `MARKETING_SIMULATION.md`.

## Correção (2026-08-15)

| Sintoma | Causa | Correção |
|---------|--------|----------|
| 10x12 com operação ligada → 9x12 ao **Parar** | `stop` no runtime fazia `force` hydrate da DB atrasada (último WIN ainda não persistido) | `stop` não re-hidrata memória viva; no `start` o placar vivo ganha se tiver mais operações |
| **Reiniciar placar** às vezes não zera | Cache ia a 0-0, mas WS/refetch reaplicava snapshot Redis antigo (números reais) | `preserveRobotSessionScore` ignora placar antigo quando `stop_reset_at` do painel é mais novo |

## Correção (2026-08-14)

`preserveRobotSessionScore`: start/stop/WS/HTTP não trocam um placar vivo por
0-0. Só **Reiniciar placar** zera (`allowBlankOverwrite`).
