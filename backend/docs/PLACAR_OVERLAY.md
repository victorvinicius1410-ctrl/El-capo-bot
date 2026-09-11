# Placar do overlay (WIN / LOSS / resultado)

Como o frontend mostra o placar da sessão no robô flutuante, e por que ele
às vezes **piscava zerado**, **não zerava no Reiniciar placar**, **caía
um ponto ao parar** (ex.: 10x12 → 9x12), **regredia no start/stop**
(ex.: 5x3 → 2x0) ou **não baixava ao excluir** operação marketing do
histórico.

Atualizado em **2026-09-07**.

Espelho detalhado: [`Frontend/docs/PLACAR_OVERLAY.md`](../frontend/docs/PLACAR_OVERLAY.md).

## Correção (2026-09-07) — a exclusão voltava porque tudo "nunca rebaixa"

### Por que este defeito sempre voltava

O placar da sessão vive em **quatro réplicas**: memória do `backend-gateway`,
memória do `robot-runtime`, `robot:snapshot:{user_id}` no Redis e `robot_states`
no Supabase. Todo reconcile entre elas escolhe o **maior total**
(`_pick_preferred_session_score`, "nunca rebaixa"). Isso está certo para WIN/LOSS
novo — a réplica atrasada é sempre a menor — e é exatamente errado para a
**exclusão**, a única operação que baixa o placar de propósito.

Sempre há uma réplica atrasada: `persist_robot` grava em background e o
`_snapshot_publisher` do runtime republica a cada 1s. Bastava uma volta do poll
para a operação excluída voltar — e de forma **permanente**, porque o
`persist_robot` seguinte regravava o valor ressuscitado. Cada correção anterior
fechou um caminho de leitura; o defeito voltava pelo próximo.

A saída projetada — a marca `robot:score_authority:{user_id}` — existia em
`robot_bus.py` (`set/get/clear_score_authority`), era chamada em
`robot_runtime_main.py` via `getattr(gateway, "clear_session_score_authority")`
e tinha 8 testes escritos. **Só que `main.py` nunca implementou a função**: o
`getattr` devolvia `None` em silêncio e os 8 testes ficavam em ERROR. Mesmo
padrão do `panel_auto_reconnect_allowed` (ver `ROBO_E_SUPORTE.md`).

### O que foi feito

1. **`main.py` ganhou a marca de baixa intencional**:
   `mark_session_score_authority`, `get_session_score_authority`,
   `clear_session_score_authority` e `apply_session_score_authority_to_state`.
   Memória do processo (TTL 120s) + espelho no Redis, para gateway e
   `robot-runtime` obedecerem juntos. Com Redis ligado a chave compartilhada
   manda: é a **ausência** dela que libera o placar quando o outro processo
   contabiliza o WIN seguinte.
2. **Todo caminho de leitura passou a obedecer a marca**, em vez de promover a
   réplica atrasada: `reconcile_session_score_on_gateway`,
   `enrich_robot_snapshot_session_score` (`GET /robot/state` e WS),
   `rehydrate_score_from_persistence_if_blank` (0-0 pós-exclusão é intencional)
   e `reconcile_gateway_enabled_from_runtime_snapshot`.
3. **A marca é gravada** em `apply_marketing_score_removal` e em
   `sync_marketing_display_to_robot`, e **liberada** em `finish_monitored_trade`
   (resultado real contabilizado), no `POST /robot/reset-score`, no
   `/robot/reset-cycle` e no auto-reset de start da conta marketing.
4. **`robot-runtime`**: `apply_score` grava a marca no processo dele, e a
   hidratação (`start`) reaplica a marca depois do `restore` — `restore`
   recalcula pelo histórico e `_prefer_live_session_score` mantém o maior
   total; os dois desfaziam a exclusão.
5. **Segundo defeito, independente**: ordem ao vivo que só existia no espelho
   `robot_trades`. `robot_persistence.delete_trade` devolve apenas `bool`, então
   `trade_meta` saía `{"order_id": ...}` sem `result` e
   `apply_marketing_score_removal` retornava em silêncio — a operação sumia do
   Histórico e continuava no placar, **sem nenhuma linha de log**. Agora a linha
   é lida ANTES de apagar, e o caso "sem result" loga
   `[MARKETING_SCORE_REMOVAL_SKIPPED]`.
6. **Frontend**: `preserveRobotSessionScore` tinha a mesma regra "nunca
   rebaixa", com um palpite (`isLikelyLegitimateScoreDecrease`) que só aceitava
   queda de exatamente 1 operação. Excluir a **última** operação (1x0 → 0x0)
   caía no ramo "snapshot chegou em branco" e o placar antigo voltava para
   sempre. O palpite saiu; entrou `lib/sessionScoreAuthority.ts`, espelho da
   marca do backend em `localStorage` (mesmo TTL, vale nas outras abas), mais um
   fallback de **confirmação**: sem marca, uma queda que se repete no update
   seguinte é aceita — sem isso a rejeição era permanente, porque o cache
   guardava o valor alto e toda comparação seguinte era contra ele.

### Logs novos

`[SCORE_AUTHORITY_MARKED]`, `[SCORE_AUTHORITY_ENFORCED]`,
`[SCORE_AUTHORITY_CLEARED]`, `[SCORE_SNAPSHOT_CLAMPED_TO_AUTHORITY]` e
`[MARKETING_SCORE_REMOVAL_SKIPPED]` — todos em WARNING de propósito: o logger do
`backend-gateway` está em WARNING e `logger.info` é invisível lá.

### Testes

`tests/test_marketing_score_authority.py` (36) cobre um caminho de ressurreição
por teste — a marca em si, cada função de reconcile, os dois processos, o
`DELETE` real pelo router com todas as combinações de onde a linha estava, e uma
simulação de 60 ciclos de poll com as réplicas atrasadas republicando. No front,
`src/lib/sessionScoreAuthority.test.ts` (20) cobre marca, expiração, outra aba,
storage bloqueado e a confirmação.

## Correção (2026-09-03) — a baixa do placar era descartada em silêncio

### Sintoma

Conta marketing (`sergioromerotrader`, `11e0b3d5-...`): excluir uma operação
ao vivo no Histórico **tirava a linha da lista mas não mexia no placar**.

Sobre o "zerou no F5": houve **9 `POST /robot/reset-score` na mesma tarde**,
e o de 16:07:51 saiu da própria conta marketing (o `persist_robot` do
endpoint logou `user_id=11e0b3d5-...` 58ms antes) — 4s **depois** do DELETE,
e um outro 19s **antes** dele. `resetScore` só é chamado no clique do botão
(`AppShell.tsx:346`), não há chamada automática. A atribuição das outras 7
chamadas não foi confirmada (log rotacionado no redeploy), mas o zero veio de
**Reiniciar placar**, não de reidratação no F5.

### Como foi diagnosticado

Em 46h de produção, com 4 `DELETE /marketing-simulation/trades/*` respondendo
`204`, **nenhuma linha de log de marketing foi emitida**: nem
`[MARKETING_SCORE_REMOVED]`, nem `[SCORE_AUTHORITY_SET]`. A baixa nunca rodou.

### Causa 1 — `trade_meta` sem `result` (corrigida no código)

`delete_marketing_robot_history_item` procura a operação em três fontes:

| Fonte | Devolve |
|-------|---------|
| `robot_trade_history` (`delete_trade_history_item`) | a linha excluída |
| memória do `auto_trader` (`remove_history_trade`) | a linha excluída |
| espelho `robot_trades` (`delete_trade`) | **apenas `bool`** |

Ordem ao vivo da Bullex que só existia no espelho caía no terceiro caso:

```python
trade_meta = deleted or memory_trade   # ambos None
if trade_meta is None:
    trade_meta = {"order_id": normalized_order}   # sem `result`
```

E `apply_marketing_score_removal` descartava isso **sem log nenhum**:

```python
if result not in {"WIN", "LOSS"}:
    return
```

Resultado: a linha era apagada do espelho (some do Histórico) e o placar
ficava intacto. Exatamente o relato.

**Correção:** ler a linha do espelho (`load_trades`) **antes** de apagar, para
recuperar `result`/`profit`; e transformar as duas saídas mudas em log
(`[MARKETING_SCORE_REMOVAL_SKIPPED]`,
`[MARKETING_HISTORY_DELETED_WITHOUT_RESULT]`).

Teste: `test_delete_adjusts_score_when_only_mirror_has_trade`.

### Causa 2 — migration `broker_order_id` nunca rodou em produção

`marketing_simulated_trades` em produção tem só:

```text
amount, asset, company_id, created_at, direction, id,
is_simulated, payout, profit, result, source,
synthetic_sequence, user_id
```

**Não existe `broker_order_id`.** A migration `migration_marketing_broker_order_id.sql`
(documentada como aplicada em 2026-07-29) nunca foi executada. Como o backend
tolera a ausência da coluna (`_is_missing_broker_column` → devolve `None`),
a falha era invisível: `delete_simulated_trade` **sempre** devolve `None`
para ordens ao vivo, o espelho do Shift+O fica órfão, a operação continua
listada no painel e volta ao histórico no próximo
`sync_marketing_display_to_robot`.

Todo o desenho de vínculo de 29/07 — e as correções de 21/08, 26/08 e 01/09
construídas sobre ele — estavam inertes nesse caminho.

**Correção:** migration aplicada em **produção em 03/09/2026**. Conferido
depois: a coluna existe e o filtro `broker_order_id=eq.<order_id>` responde
(PostgREST já recarregou o schema).

> **Staging (`/opt/elcapo2`) usa outro projeto Supabase** — a migration
> **ainda não foi aplicada lá**. Rodar antes de testar exclusão marketing
> em staging.

#### Linhas antigas continuam sem vínculo (esperado)

As 593 linhas que já existiam ficaram com `broker_order_id = NULL`, e **não
há backfill seguro**: `source` é `"marketing_demo"` tanto no espelho de
operação ao vivo quanto na operação gerada no Shift+O
(`supabase_admin_repository.py:822` força o valor), então não dá para
distinguir uma da outra.

Isso não deixou estrago: varredura de `robot_trade_history` nas contas
marketing achou **0 pares suspeitos** (mesmo ativo/resultado/valor/lucro no
mesmo minuto sob UUID e sob order_id numérico). As linhas com UUID são
operações geradas no Shift+O, onde o UUID é o identificador correto. Só
operações ao vivo **novas** passam a nascer vinculadas.

### Lição — e o que mudou por causa dela

A tolerância a schema desatualizado degradava **em silêncio**: foi o que
transformou uma migration esquecida em bug de 5 semanas. Ajustes de 03/09:

| Onde | Antes | Agora |
|------|-------|-------|
| `_insert_simulated_trade` | `payload.pop("broker_order_id")` mudo | `[MARKETING_BROKER_COLUMN_MISSING] op=insert` nomeando a migration |
| `delete_simulated_trade` | `return None` mudo | `[MARKETING_BROKER_COLUMN_MISSING] op=delete` idem |
| `apply_marketing_score_removal` | `return` mudo sem `result` | `[MARKETING_SCORE_REMOVAL_SKIPPED]` |
| `delete_marketing_robot_history_item` | `trade_meta` sem `result` | `[MARKETING_HISTORY_DELETED_WITHOUT_RESULT]` |
| `delete_simulated_trade` (router) | `except TypeError` cego | `inspect.signature(...).bind(...)` |

O `except TypeError` cego era pior que verboso: um `TypeError` levantado
**dentro** do deleter caía no fallback com `adjust_score=True` e o placar
seria decrementado **duas vezes** (no deleter e no `marketing_score_remover`).
Agora a capacidade é decidida pela assinatura, e erro de dentro sobe.

## Correção (2026-09-01) — operação excluída voltava ao placar no poll seguinte

### Sintoma

Conta marketing: excluir uma operação (Shift+O ou `/history`) removia a linha
e o overlay caía por um instante, mas **a operação voltava ao placar** no
poll/WS seguinte — e ficava. Reiniciar a página não resolvia.

### Causa

A baixa em si estava certa (5x3 → 4x3 na memória do gateway, no Redis e no
comando `apply_score` do runtime). O que desfazia era a **leitura**:

1. `persist_robot` grava `robot_states` numa **thread de background**
   (`_ROBOT_PERSIST_EXECUTOR`). O front dispara `invalidateQueries` assim que
   recebe o `204`, então o `GET /robot/state` chega **antes** da escrita.
2. `enrich_robot_snapshot_session_score` → `reconcile_session_score_on_gateway`
   escolhe a fonte com **mais** WIN+LOSS entre memória, Redis e Supabase
   (regra "nunca rebaixa", de 2026-08-24). O Supabase ainda tinha 5x3.
3. O reconcile **grava** esse 5x3 de volta na memória do gateway. A partir
   daí o estrago é permanente: mesmo depois de a DB virar 4x3, a memória
   (5x3) continua ganhando o `_pick_preferred_session_score`, e o próximo
   `persist_robot` regrava 5x3 na DB.

`trust_local_score` (2026-08-26) só protege o **publish**; não havia proteção
nenhuma no caminho de leitura.

Variante do mesmo bug ao excluir a **última** operação: placar volta a 0-0,
`rehydrate_score_from_persistence_if_blank` vê memória em branco e reidrata
o 1x0 da DB atrasada.

### Correção — marca de placar autoritativo

Uma baixa intencional agora publica **qual é o placar correto agora**, numa
chave Redis compartilhada pelos dois processos (`robot:score_authority:{user_id}`,
TTL 120s — só precisa cobrir a janela em que Redis/Supabase estão atrasados).

| Camada | Função | Comportamento |
|--------|--------|---------------|
| Bus | `RobotBus.set/get/clear_score_authority` | Chave Redis compartilhada gateway ↔ runtime |
| Backend | `set_session_score_authority` | Chamada em `apply_marketing_score_removal` **antes** do persist/publish, e em `sync_marketing_display_to_robot` (gerar placar) |
| Backend | `reconcile_session_score_on_gateway` | Com marca válida, adota a marca em vez do "maior placar" |
| Backend | `enrich_robot_snapshot_session_score` | Idem antes de servir o snapshot ao painel/WS |
| Backend | `reconcile_gateway_enabled_from_runtime_snapshot` | Idem ao alinhar com o runtime parado |
| Backend | `rehydrate_score_from_persistence_if_blank` | Não reidrata 0-0 intencional (exclusão da última operação) |
| Backend | `clear_session_score_authority` | Em `finish_monitored_trade` (resultado real novo), `reset_score` e `reset_cycle` |
| Runtime | cmd `reset_score` | Limpa a marca junto com o placar |

A marca é descartada assim que o placar sobe de verdade, então ela nunca
trava um WIN/LOSS novo — e expira sozinha em 120s.

Logs: `[SCORE_AUTHORITY_SET]`, `[SCORE_AUTHORITY_APPLIED]`.

Testes (`tests/test_marketing_history_deletion_persistence.py`):
`test_poll_after_removal_keeps_score_down_with_stale_sources`,
`test_removing_last_operation_does_not_rehydrate_from_database`,
`test_real_result_releases_authority_and_score_can_rise`.

### Limitação conhecida (frontend)

`preserveRobotSessionScore` só aceita queda de **1** operação
(`isLikelyLegitimateScoreDecrease`). Excluir várias operações em sequência
depende do subtract otimista do cache para manter `previous` alinhado; um
snapshot atrasado que chegue no meio pode segurar o placar até o poll
seguinte. Não foi alterado aqui para não reabrir a regressão de 2026-08-24
(5x3 → 2x0 no start/stop).

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
