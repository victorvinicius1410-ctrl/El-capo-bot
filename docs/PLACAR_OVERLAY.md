# Placar do overlay (WIN / LOSS / resultado)

Como o frontend mostra o placar da sessão no robô flutuante, e por que ele
às vezes **piscava zerado**, **não zerava no Reiniciar placar**, **caía
um ponto ao parar** (ex.: 10x12 → 9x12), **regredia no start/stop**
(ex.: 5x3 → 2x0) ou **não baixava ao excluir** operação marketing do
histórico.

Atualizado em **2026-08-26**.

Espelho detalhado: [`Frontend/docs/PLACAR_OVERLAY.md`](../frontend/docs/PLACAR_OVERLAY.md).

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
