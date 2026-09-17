# Placar: por que operação não é salva (diagnóstico + plano de execução)

Levantamento de **2026-09-15**, feito em `/opt/elcapo` (código real) e no
Supabase/Redis **de produção**. Responde ao relato do dono: *"quando o El Capo
opera não está salvando no placar; às vezes aparece depois que inicia operação
de novo"*.

Complementa `PLACAR_OVERLAY.md` (correção de 07/09, exclusão no Shift+O). Aqui o
problema é o **oposto**: o placar **perde** operação real.

---

## 1. Resumo executivo

O placar da sessão tem **quatro réplicas**: memória do `backend-gateway`,
memória do `robot-runtime`, `robot:snapshot:{user}` no Redis (TTL 600s) e
`robot_states.state_json` no Supabase. Com `ROBOT_RUNTIME_MODE=external`, **quem
conta WIN/LOSS é o `robot-runtime`** — o gateway só serve o que lê.

O defeito central: **o gateway grava o placar dele (que não é o vivo) por cima
do placar autoritativo no Supabase**. Enquanto o snapshot do Redis existe
(10 min), o painel ainda mostra o número certo; quando ele expira, o painel cai
no Supabase — que já foi zerado — e **o placar some**. No "Iniciar Operação"
seguinte o runtime reidrata e **recalcula o placar pelas operações do dia em
`robot_trades`**: é exatamente por isso que "aparece depois que inicia de novo".

Foram encontrados **8 defeitos**, todos com prova nos dados de produção. Três
deles (F1, F3, F4) fazem operação real sumir da tela do cliente hoje.

| # | Defeito | Impacto medido |
|---|---|---|
| F1 | Gateway persiste placar não-autoritativo (stop e mais ~20 rotas) | 3 de 21 clientes ativos hoje com placar zerado no banco — **13 operações reais fora do placar** |
| F2 | Placar vivo não vira o dia (Brasília); o restore só conta "hoje" | 1 cliente com 6 operações de ontem ainda somadas — cai sozinho no próximo restart |
| F3 | Gale que não entra nunca fecha o ciclo: o LOSS não conta | 1 cliente hoje: placar 5x0, histórico 4x1 |
| F4 | Empate (DRAW) nunca chega ao Histórico — trava é **do banco** | **45 de 45 DRAW desde 01/09** ausentes; 0 linhas DRAW na tabela inteira |
| F5 | Resultado descartado em silêncio no `finish_trade` (4 saídas sem log) | Sem log não dá para medir; é o que torna F1/F3/F6 invisíveis |
| F6 | Monitor de resultado morre com o processo; sem recuperação no boot | **35 ordens órfãs** (`PENDING_RESULT`) desde 01/09, 15 no dia do deploy 10/09 |
| F7 | Recuperação de TIMEOUT roda no gateway, sobre memória errada | 4 TIMEOUT desde 01/09 — nenhum no placar nem no Histórico |
| F8 | 14 LOSS reais sem linha no Histórico, sem retry nem auditoria | 0,35% das operações (14 em 4.038) |

---

## 2. Como o placar funciona hoje

```
ordem enviada ──> robot_trades (result=PENDING_RESULT)      [espelho de restore]
       │
       └─> TradeResultMonitor (asyncio.Task, 1 poll/s)
              └─> finish_monitored_trade                     [robot-runtime]
                     ├─> auto_trader.finish_trade  → wins/losses/profit em memória
                     ├─> save_trade_history         → robot_trade_history  [Histórico]
                     ├─> save_trade                 → robot_trades          [restore]
                     └─> persist_robot              → robot_states          [placar]

placar na tela  <── Redis robot:snapshot (publicado 1x/s pelo runtime, TTL 600s)
                <── se expirou: robot_states (Supabase)
"Iniciar Operação" ──> runtime reidrata: restore() recalcula o placar por
                       robot_trades do dia civil de Brasília
```

Dois pontos que explicam quase tudo:

- O `_snapshot_publisher` (`robot_runtime_main.py:441`) **só publica usuários com
  worker ativo**. Robô parado = snapshot congela e expira em 600s.
- `restore()` → `_recompute_score_from_history` (`auto_trader.py:1127`)
  **recalcula o placar pelo histórico do dia**. É a rede de segurança — e é o
  motivo de a operação "voltar" depois do start.

---

## 3. Achados

### F1 — O gateway grava o placar dele por cima do placar vivo  *(causa do relato)*

`_robot_stop_impl` (`backend/main.py:16139`):

```python
state = auto_trader.stop(user_id)      # memória do GATEWAY — não é o placar vivo
persist_robot(user_id)                 # grava esse placar no Supabase
control_payload = publish_robot_control_snapshot(user_id, worker_running=False)
```

Em `external`, a memória do gateway só acompanha o placar quando um **caminho de
leitura** (`GET /robot/state` ou push do WS) roda o `enrich` → 
`reconcile_session_score_on_gateway`. Entre uma leitura e outra ela fica para
trás — e quem grava por último manda no banco.

**O gatilho exato, medido na bancada (15/09):**

1. **Sem "Reiniciar placar":** é intermitente. Se o painel leu o estado entre a
   última operação e o clique em Parar, o placar sobrevive; se não leu, o stop
   grava `0x0`. Foi assim que o defeito virou "às vezes".
2. **Depois de um "Reiniciar placar":** é permanente. O reset deixa a memória do
   gateway com `stop_reset_at` **e** placar em branco — e tanto
   `reconcile_session_score_on_gateway` quanto
   `rehydrate_score_from_persistence_if_blank` tratam esse par como baixa
   intencional e **param de promover** (`main.py:8527` e `main.py:9474`). A
   memória do gateway congela em `0x0` para sempre e **toda** gravação dele
   apaga o placar — inclusive a do próprio `GET /robot/state`.

**Quem está exposto:** **270 dos 382** clientes com estado salvo (71%) já usaram
"Reiniciar placar" pelo menos uma vez.
No "Parar Operação" o gateway grava wins=0 / losses=0 / `last_trade=None` em
`robot_states`. O `publish_robot_control_snapshot` até corrige a memória depois
(`adopt_live_session_score_if_blank`, `main.py:8697`), mas **ninguém regrava o
banco**. Resultado:

1. Painel segue certo por até 600s (snapshot do Redis).
2. Snapshot expira → `rehydrate_score_from_persistence_if_blank` (`main.py:9454`)
   lê `robot_states` → **0x0**. O placar some.
3. "Iniciar Operação" → runtime reidrata → `restore()` recalcula por
   `robot_trades` → **o placar volta**. ← o que o dono descreve.

**Prova (produção, 15/09):**

| cliente | `robot_states` | operações reais após o reset | estado gravado |
|---|---|---|---|
| `8863e832` | 0x0 | 0x1 (LOSS 14:57) | 14:58, `last_trade=None`, STOPPED |
| `a6bffba9` | 0x0 | 5x2 (7 ops, até 14:46) | 14:47, `last_trade=None`, STOPPED |
| `ec5c3568` | 0x0 | 1x4 (5 ops, até 14:31) | 14:32, `last_trade=None`, STOPPED |

Sempre o mesmo padrão: gravação **1 minuto depois da última operação**, placar
zerado e `last_trade` apagado — assinatura da memória do gateway, não do runtime.

**Não é só o stop.** `persist_robot` tem 49 chamadas; ~20 estão em rotas que só
rodam no gateway: `_robot_state_impl` (3), `robot_panel_maintenance`,
`robot_config`, `_robot_start_impl` (6), `robot_live_mode`, `_robot_stop_impl`,
`robot_sync_connection`, `_bullex_connect_impl` (3), `bullex_disconnect`,
`_bullex_account_impl` (4). Qualquer uma pode rebaixar o placar. Corrigir só o
stop fecha **um** caminho — é o mesmo erro das correções anteriores do placar.

### F2 — O placar vivo não vira o dia

`_recompute_score_from_history` filtra `is_brasilia_today`, mas **nada zera o
placar em memória à meia-noite**. Com o robô rodando madrugada adentro, o placar
acumula dois dias; no primeiro restart/reidratação ele despenca para o dia
corrente.

**Prova:** `3226931f` com placar 9x5 (14 operações) e só 8 operações hoje —
6 são de 14/09 à noite (reset às 21:18 de Brasília).

### F3 — Gale que não entra deixa o LOSS fora do placar

Em `auto_trader.finish_trade` (`auto_trader.py:2870`), LOSS com gale previsto
chama `trigger_gale` e retorna `finalized=False`: o ciclo fica aberto, o LOSS vai
para o Histórico (`main.py:12537`) mas **não entra no placar**. Se a etapa de
gale nunca sai (janela perdida, ativo recusado, stop), esse LOSS **nunca é
contabilizado**.

**Prova:** `43fe2225`, gale ligado, LOSS 14263382820 às 04:15 com
`cycle_result=None`, sem etapa seguinte. Placar 5x0, Histórico 4x1.

### F4 — Empate nunca chega ao Histórico (a trava é do banco)

O Python **já aceita DRAW** (`robot_persistence.py:build_trade_history_item`,
corrigido em 11/09 e no ar). Mas o banco não:

```sql
-- el_capo_full_bootstrap.sql:223
result text not null check (result in ('WIN', 'LOSS')),
```

O POST volta 400, `raise_for_status` estoura, e o `except Exception` genérico
engole com `[ROBOT HISTORY ERROR]`. O cliente vê o empate acontecer e depois não
acha.

**Prova:** **45 de 45** DRAW de `robot_trades` desde 01/09 sem linha no
Histórico, inclusive depois do deploy da correção (12, 13, 14 e 15/09). A tabela
`robot_trade_history` tem **0 linhas DRAW em toda a sua história**. É o padrão de
`elcapo-migrations-nao-aplicadas`: a correção subiu no código e parou no schema.

### F5 — Resultado descartado em silêncio

`auto_trader.finish_trade` tem **quatro saídas mudas** (`auto_trader.py:2779-2809`):
sem `last_trade`, `order_id` diferente do da ordem em memória, ordem já
contabilizada, ou ciclo já fechado. Nenhuma loga nada — e o monitor loga
`[ORDER_RESULT_MONITOR_FINISHED] result=WIN` mesmo assim. **O log diz que
terminou; o placar não mexeu.** É por isso que o defeito nunca aparecia na
investigação por log.

### F6 — Monitor morre com o processo; ordem fica órfã

`trade_result_monitor.start` (`main.py:14216`) é a **única** criação de monitor.
Deploy, restart ou queda do `robot-runtime` no meio da vela mata o
`asyncio.Task`: ninguém volta a buscar o resultado, a linha fica
`PENDING_RESULT` para sempre — fora do placar e fora do Histórico.

**Prova:** 35 órfãs desde 01/09; **15 no dia 10/09** (dia de deploy). Hoje já há
1 (14264580663, 15:10:28 — 30 segundos antes do restart das 15:11).

### F7 — Recuperação de TIMEOUT roda no processo errado

`reconcile_timeout_last_trade` (`main.py:12441`) lê `auto_trader.get(user_id).last_trade`
**no gateway**, onde o `last_trade` é o da hidratação, não o vivo. Em `external`
ela quase nunca tem o que reconciliar; quando tem, contabiliza na memória errada.
TIMEOUT também é recusado no Histórico de propósito (resultado desconhecido) —
então a operação não existe em lugar nenhum.

### F8 — 14 LOSS sem linha no Histórico

14 operações reais (0,35%) com `robot_trades` gravado e `robot_trade_history`
ausente, sem nada em comum (CALL e PUT, M1 e M15, clientes diferentes, todos os
campos obrigatórios presentes). Sintoma de falha transitória no POST ao Supabase:
`save_trade_history` **não tem retry** e o `except` genérico não deixa rastro
acionável. Sem uma auditoria diária, ninguém descobre.

---

## 4. Plano de execução

Ordem por impacto/risco. Cada fase é deployável sozinha.

### Fase 0 — Parar a sangria (mesmo dia)

**0.1 `persist_robot` nunca rebaixa o placar `(F1)`** — `backend/main.py:10560`

Um ponto de estrangulamento em vez de 20 correções:

- Em `robot_runtime_mode() == "external"`, chamar
  `reconcile_session_score_on_gateway(user_id)` **antes** de `state.to_dict()`.
- Trava final antes do `save_state`: se o payload sai com placar em branco e o
  Redis **ou** o Supabase têm placar com valor, **e** não há
  `score_authority` (baixa intencional do Shift+O) nem `stop_reset_at` recente,
  preservar wins/losses/profit e `last_trade` do valor persistido. Logar
  `[SCORE_PERSIST_DOWNGRADE_BLOCKED]` em WARNING.
- Exceções que **precisam** poder zerar: `/robot/reset-score`,
  `/robot/reset-cycle` e a exclusão do Shift+O. Todas já marcam autoridade ou
  `stop_reset_at` — usar isso como passe, nunca um parâmetro novo em 20 sites.

**0.2 `_robot_stop_impl` para com o placar certo `(F1)`** — `main.py:16139`

Trocar por `get_user_robot_state(user_id)` + `adopt_live_session_score_if_blank(user_id)`
**antes** de `auto_trader.stop(user_id)`. O stop passa a gravar o placar vivo, e
`last_trade` deixa de ser apagado.

**0.3 Migration do empate `(F4)`** — nova `migration_history_allow_draw.sql`

```sql
alter table public.robot_trade_history
  drop constraint if exists robot_trade_history_result_check;
alter table public.robot_trade_history
  add constraint robot_trade_history_result_check
  check (result in ('WIN', 'LOSS', 'DRAW'));
```

Conferir o nome real da constraint antes:

```sql
select conname, pg_get_constraintdef(oid)
from pg_constraint
where conrelid = 'public.robot_trade_history'::regclass;
```

Rodar no SQL editor do Supabase e **provar o efeito** (inserir um DRAW de teste e
apagar), não confiar no doc — ver `elcapo-migrations-nao-aplicadas`.

**0.4 Backfill dos 45 empates + 14 LOSS**

Script `scripts/backfill_history_from_trades.py`: lê `robot_trades` com
`result in (WIN, LOSS, DRAW)` sem par em `robot_trade_history` e reinsere via
`build_trade_history_item`. Rodar **depois** da 0.3, com `--dry-run` primeiro.
O placar não muda (empate não conta); o Histórico do cliente volta a bater.

### Fase 1 — Fechar os buracos de contabilidade

**1.1 Log em toda saída muda `(F5)`** — `auto_trader.py:2779-2809`
`[TRADE_RESULT_DISCARDED] user_id=… order_id=… reason=no_last_trade|order_mismatch|already_completed|cycle_closed`,
em WARNING. Sem isto não dá para medir nada do que vem depois.

**1.2 Gale abandonado fecha o ciclo `(F3)`**
Quando o gale é disparado e a etapa seguinte não sai (janela perdida, ativo
recusado, stop, robô parado), fechar o ciclo com o LOSS da etapa que já perdeu:
contabilizar no placar e marcar `cycle_result=LOSS`. Ponto natural:
`reset_cycle_after_finish`/`waiting_result_stale` — hoje limpam o ciclo sem
contabilizar. Teste obrigatório: gale que entra continua contando **um** LOSS,
não dois.

**1.3 Recuperar ordem órfã no boot `(F6)`** — `robot_runtime_main.amain`
Na subida, para cada usuário restaurado, ler `robot_trades` com
`result=PENDING_RESULT` das últimas 6h e **religar o monitor**
(`trade_result_monitor.start`) ou buscar o resultado uma vez na corretora e
chamar `finish_monitored_trade`. Cobre todo deploy futuro. Guardar
`[ORPHAN_TRADE_RECOVERED]`.

**1.4 TIMEOUT reconciliado no dono do placar `(F7)`**
Mover a reconciliação para o `robot-runtime` (novo comando `reconcile_timeout`
no `robot_bus`, ou direto no tick do worker). No gateway, manter só quando
`robot_runtime_mode() != "external"`.

**1.5 Retry na gravação do Histórico `(F8)`**
`save_trade_history`/`save_trade`: 3 tentativas com backoff curto; na falha
final, logar `[HISTORY_WRITE_FAILED]` com `order_id` **e** enfileirar para o
backfill da 3.2.

### Fase 2 — Virada do dia `(F2)`

No `robot-runtime`, ao cruzar a meia-noite de Brasília com o robô ligado, zerar
wins/losses/profit da sessão (e `stop_offset_*`), persistir e publicar snapshot.
Sem isso o placar continua caindo sozinho no primeiro restart depois da virada.
Cuidado: **não** mexer em `stop_reset_at` (é a marca do "Reiniciar placar") e
conferir o efeito no stop win/loss diário.

### Fase 3 — Não deixar voltar

**3.1 Auditoria diária** — `scripts/auditoria_placar.py` (cron 1x/dia):
para cada cliente ativo, comparar `robot_states.wins/losses` × `robot_trades`
pós-reset × `robot_trade_history`, e listar `PENDING_RESULT` com mais de 1h.
Sair com código ≠ 0 quando houver divergência. **Este script é o teste de
aceitação de todas as fases acima.**

**3.2 Testes** (`backend/tests/`):
- `test_placar_nao_rebaixa_no_persist.py`: stop em `external` com gateway em
  branco e runtime em 5x3 → `robot_states` continua 5x3; reset-score ainda zera.
- `test_gale_abandonado_conta_loss.py`.
- `test_draw_vai_ao_historico.py` (contra o fake de persistência).
- `test_orfaos_recuperados_no_boot.py`.
- `test_placar_vira_o_dia.py`.
Rodar sempre em rede isolada:
`docker run --rm --network none -v /opt/elcapo/backend:/w -w /w elcapooneline-backend:latest python -m unittest tests.<arquivo>`
(**nunca** `docker compose run` — cai na rede de produção; ver
`elcapo-fonte-de-verdade-do-codigo`). Comparar com a linha de base conhecida:
~1014 testes, ~40 falhas + 21 erros pré-existentes.

**3.3 Documentação**: atualizar `PLACAR_OVERLAY.md` com a regra nova ("o gateway
nunca escreve placar que não é dele") e os logs novos.

### Ordem de deploy sugerida

| Passo | O que sobe | Risco |
|---|---|---|
| 1 | Migration 0.3 (SQL) | nenhum — só amplia o `check` |
| 2 | Backfill 0.4 (`--dry-run` → real) | baixo; só insere linha faltante |
| 3 | Fase 0.1+0.2+1.1 no `backend` | médio — **reinicia a corretora** |
| 4 | Fase 1.2–1.5 + Fase 2 no `robot-runtime` | alto — **pausa o robô de todos** |
| 5 | Fase 3 (scripts/testes) | nenhum |

```bash
cd /opt/elcapo/backend && docker compose -p elcapooneline --env-file .env up -d --build --no-deps backend
```

Antes: `docker tag elcapooneline-backend:latest elcapooneline-backend:rollback-20260915-pre-placar`.
Lembrar que nomear serviços no compose **não** poupa a corretora de reconectar
(`elcapo-deploy-nao-e-cirurgico`) e que subir o `robot-runtime` **pausa o robô de
todos os clientes** — só o `/robot/start` do cliente religa. Conferir depois:
`[SCORE_PERSIST_DOWNGRADE_BLOCKED]`, `[TRADE_RESULT_DISCARDED]`,
`[ORPHAN_TRADE_RECOVERED]` e a auditoria 3.1 com saída limpa em 24h.

---

## 5. Consultas de verificação (rodar antes e depois)

```sql
-- Empates fora do Histórico (esperado: 0 depois da Fase 0)
select count(*) from robot_trades t
where t.result = 'DRAW'
  and not exists (select 1 from robot_trade_history h
                  where h.user_id = t.user_id and h.order_id = t.order_id);

-- Ordens órfãs (esperado: 0 fora da vela corrente)
select user_id, order_id, executed_at from robot_trades
where result = 'PENDING_RESULT' and executed_at < now() - interval '1 hour'
order by executed_at desc;

-- Placar do banco × operações do dia (esperado: sem linha)
select s.user_id, (s.state_json->>'wins')::int as placar_wins,
       (s.state_json->>'losses')::int as placar_losses,
       count(*) filter (where h.result='WIN')  as hist_wins,
       count(*) filter (where h.result='LOSS') as hist_losses
from robot_states s
join robot_trade_history h
  on h.user_id = s.user_id
 and h.finished_at >= coalesce((s.state_json->>'stop_reset_at')::timestamptz,
                               date_trunc('day', now() at time zone 'America/Sao_Paulo'))
group by 1,2,3
having (s.state_json->>'wins')::int   <> count(*) filter (where h.result='WIN')
    or (s.state_json->>'losses')::int <> count(*) filter (where h.result='LOSS');
```

A terceira consulta acusa **também** o caso legítimo do gale (o Histórico guarda
as duas pernas, o placar conta um ciclo) e a conta de marketing com Shift+O —
tratar esses dois como exceção conhecida no script da 3.1.

---

## 6. Revisão completa: o que foi testado, um a um

Feita em **15/09/2026**, a pedido do dono, antes de qualquer correção. Três
frentes:

1. **Suítes automatizadas existentes** — backend em rede isolada
   (`docker run --network none`) e frontend (`npm test`).
2. **Bancada nova** (`scripts/placar_bench/`) — cópia isolada da arquitetura de
   produção (gateway `external` + `robot-runtime` `worker` + Redis próprio,
   persistência SQLite), exercitando cada caminho com o **código real**.
3. **Conferência ao vivo em produção** — só leitura, comparando as quatro
   réplicas do placar.

### Suítes existentes

| Suíte | Resultado |
|---|---|
| Backend, arquivos de placar/histórico/gale/stop (12 arquivos) | **311 testes, 0 falhas** |
| Frontend (`npm test`) | **225 testes, 0 falhas** |

**Nenhum dos 6 defeitos aparece nessas suítes.** Elas testam as peças
isoladamente; os defeitos vivem na costura entre os dois processos, na virada do
dia e no schema do banco — que nenhum teste cobre.

### Bancada: 40 caminhos

Funcionando (23):

| Caminho | Prova |
|---|---|
| WIN conta e grava nas 4 réplicas | 1x0, lucro 8.7, 1 linha no Histórico |
| LOSS conta e grava | 0x1, lucro -10 |
| Empate não mexe no placar e **vai** ao Histórico | 0x0 + linha DRAW (no SQLite passa: o Python está certo, quem recusa é o Postgres — F4) |
| Gale completo conta **um** ciclo | perna 1: 0x0; depois do gale: 1x0, 2 linhas no Histórico |
| TIMEOUT não conta nem grava | 0x0, Histórico vazio (proposital) |
| Painel enxerga o placar do runtime (HTTP) | `GET /robot/state` = 1x1 = Redis |
| Painel enxerga o placar do runtime (WebSocket) | 1ª mensagem do `/ws/robot-state` = 1x1 |
| Snapshot expirado cai na persistência | 1x1 na tela com o Redis vazio |
| "Iniciar Operação" devolve o placar | recalculado por `robot_trades` |
| "Reiniciar placar" zera e não volta | 0x0 na resposta e no poll seguinte |
| "Reiniciar ciclo" zera placar **e** Histórico | 0x0, 0 linhas, 0 espelhos |
| Shift+O "gerar placar" chega ao painel | 8x2/480 no Redis e na tela |
| Shift+O marca o placar como sintético | `stop_offset_*` = 8/2/480 no banco |
| Shift+O "excluir operação" baixa e não volta | 2x1 → 1x1, painel concorda |
| Placar de vitrine não dispara Stop Win real | nenhum stop com 8x2 +480 |
| Stop Win por dinheiro dispara | lucro 60 → `STOP_WIN_HIT` |
| Stop Loss por número de operações dispara | 2 derrotas → `STOP_LOSS_HIT` |
| Pausa por stop mantém o placar na tela | 2x0 com status `STOP_WIN_HIT` |
| Placar sobrevive à pausa + snapshot expirado | 2x0 pela persistência |
| Histórico do painel lista tudo | 4 linhas, empate incluído |
| Estatísticas batem com o placar | 2 WIN / 1 LOSS |
| Placar de um cliente não vaza para o outro | A=3x0, B=0x1 |
| Placar não oscila entre polls | 1x0 estável em 6 leituras |

Falhando (6):

| Caminho | Esperado | Obtido |
|---|---|---|
| **Parar Operação preserva o placar** (F1) | banco continua 3x1 | **banco vira 0x0** |
| **Qualquer rota do gateway preserva o placar** (F1) | banco continua 3x0 | **`/robot/config`, `/robot/state` e `/robot/sync-connection` zeram** |
| **Parar sem leitura recente do painel** (F1) | 2x0 | com leitura: 2x0; **sem leitura: 0x0** |
| **Gale que não entra contabiliza o LOSS** (F3) | 0x1 | **0x0** — o LOSS fica só no Histórico |
| **Resultado atrasado não some** (F5) | 1x0 ou log de descarte | **0x0 e nenhum log** |
| **Placar não muda sozinho na virada do dia** (F2) | mesmo placar | **1x0 → 0x0** ao reidratar |
| **Ordem em aberto recuperada após restart** (F6) | monitor religado | **1 órfã, 0 monitores** |

### Conferência em produção (15/09, 17h)

- **382** clientes com estado salvo; **270 (71%)** já usaram "Reiniciar placar" —
  todos na condição permanente do F1.
- **22** clientes operaram hoje; **3 (14%)** estão com o placar **apagado no
  banco** neste momento: `6dacdcda` (0x1 hoje), `a6bffba9` (5x2), `ec5c3568`
  (1x4). Os três com status `STOPPED` e os três já tinham reiniciado o placar.
- A lista muda ao longo do dia: quando o cliente volta a operar, o runtime
  regrava o valor certo. Quem **para e não volta** fica com o placar perdido até
  o próximo "Iniciar Operação".
- Conta `victorvinicius@gmail.com` (`81c49f33`, marketing/simulação): sem
  operações hoje — placar 0x0 legítimo, réplicas coerentes (Redis = banco), com
  `stop_reset_at` de 10/09. Está na população exposta ao F1, mas não há
  divergência para medir sem colocar ordem na corretora, o que não foi feito.

### O que isto muda no plano

Nada sai; duas coisas ficam mais firmes:

- **A correção 0.1 (`persist_robot` nunca rebaixa) é a que resolve o F1** —
  corrigir só o `/robot/stop` (0.2) deixaria `/robot/config`, `/robot/state` e
  `/robot/sync-connection` zerando o placar. As duas juntas.
- Junto da 0.1, o par `stop_reset_at` + placar em branco **não pode mais
  bloquear o reconcile para sempre**: ou o bloqueio passa a valer só enquanto a
  marca de baixa intencional estiver viva (TTL de 120s), ou o reset passa a
  gravar o `stop_reset_at` **com** o placar que o runtime tem. Sem isso a memória
  do gateway continua congelada mesmo com o `persist_robot` protegido.
- A bancada (`scripts/placar_bench/`) vira o teste de aceitação: **os 6 cenários
  acima têm de passar** depois das Fases 0 a 2.

---

## 7. Estado da execução (15/09/2026, 18h30)

**Fase 0 concluída nos dois sistemas.**

| Item | Sistema 01 (produção) | Sistema 02 |
|---|---|---|
| Código (0.1, 0.2, 0.3, 0.6) | no ar — **só o `backend`** | no ar — `backend` + `robot-runtime` |
| Migration do empate (0.4) | aplicada pelo dono no SQL Editor | aplicada |
| Backfill (0.5) | **61 linhas** (16 LOSS + 45 DRAW) | 1 linha |
| Verificação | `Sem linha no Histórico: 0` | idem |

Rollback: `elcapooneline-backend:rollback-20260915-pre-placar` e
`elcapo2staging-backend:rollback-20260915-pre-placar`.

**O `robot-runtime` de produção NÃO subiu** — ele pausa o robô de todos os
clientes até cada um clicar em Iniciar Operação, e o conserto que o cliente vê é
todo do gateway. Fases 1 e 2 dependem dele: precisam de janela combinada.

### Achados durante a execução (que o diagnóstico não previa)

1. **O `robot_bus.py` do sistema 02 não tinha os métodos da marca de placar**
   (`set/get/clear_score_authority`). O `except` engolia o `AttributeError` e a
   marca nunca cruzava os processos — mesmo padrão do `panel_auto_reconnect_allowed`
   e do `clear_session_score_authority`. Portado junto.
2. **`robot_trades` de produção tem lixo de teste** (`user-real-finished`,
   `marketing-scoreboard`, ordens `ord-finish-1`). O backfill copiou 2 dessas
   linhas para o Histórico; foram removidas e o script passou a exigir `user_id`
   UUID, como o `_is_valid_account_user_id` do runtime.
3. **Paginação do PostgREST sem `order` repete e pula linhas**: a primeira versão
   do backfill acusou 1202 faltantes onde havia 61.
4. **O `set_display_score`/`stop_offset_*` (10/09) nunca foi portado para o
   sistema 02** — por isso o cenário S48 falha lá. Não é regressão do placar.

---

## 8. Fases 1, 2 e 3 — execução (15/09/2026, 19h)

### O que entrou

| Fase | Correção | Onde |
|---|---|---|
| 1.1 | **Gale abandonado conta o LOSS** — `close_abandoned_gale` fecha o ciclo ao reciclar, ao pausar por stop e ao parar o robô | `auto_trader.py`, `main.py`, `robot_runtime_main.py` |
| 1.2 | **Ordem órfã recuperada no boot** — `load_pending_trades` + uma busca por ordem no start do runtime | `robot_persistence.py`, `robot_runtime_main.py` |
| 1.3 | **TIMEOUT reconciliado no dono do placar** — sai do gateway (memória errada em `external`) e passa para o ciclo do worker | `main.py` |
| 1.4 | **Retry na gravação do Histórico** — 3 tentativas em falha transitória; 4xx não repete (é regra do banco) | `robot_persistence.py` |
| 1.5 | **Resultado atrasado passa a contar** — `count_late_result` contabiliza fora do ciclo em vez de descartar em silêncio | `auto_trader.py`, `main.py` |
| 2 | **Virada do dia** — recalcula pelo histórico do dia (não zera cego) e marca baixa intencional | `auto_trader.py`, `main.py` |
| 3.1 | **Auditoria diária** — `scripts/auditoria_placar.py` | novo |
| 3.2 | **14 testes novos** — `tests/test_placar_integridade.py` | novo |

### Dois defeitos que só a execução revelou

1. **O recálculo contava perna de gale como derrota separada.** `_recompute_score_from_history` somava a perna E o fechamento do mesmo ciclo, então o placar de quem usa gale inflava a cada reidratação (medido: cliente com 6x3 no banco e 6x1 de ciclos fechados). A regra nova usa o vínculo `parent_order_id` — a perna superada é a que aparece como pai da etapa seguinte. **Não** se usa "está sem `cycle_result`": linha antiga e linha do Shift+O também vêm sem esse campo e precisam contar. `management_totals` continua somando a perna, porque lá se soma dinheiro.
2. **Em modo `worker` o runtime reidratava o placar do banco.** `rehydrate_score_from_persistence_if_blank` rodava dentro do construtor de snapshot e ressuscitava o placar de ONTEM logo depois da virada do dia. O runtime é o dono do placar: agora essa função é no-op para ele.

### Estado

- **Sistema 02**: Fases 1–3 no ar (`backend` + `robot-runtime`), 44 cenários de bancada verdes, auditoria limpa, testado na conta `victorvinicius@gmail.com` pelo barramento real: Shift+O chega ao painel, "Reiniciar placar" zera **e o zero aguenta**. Rollback: `elcapo2staging-backend:rollback-20260915-pre-fases123`.
- **Sistema 01**: só a **Fase 0** está no ar (gateway). O código das Fases 1–3 está na árvore, testado, **aguardando janela** — elas exigem subir o `robot-runtime`, que pausa o robô de todos os clientes até cada um clicar em Iniciar Operação.
- Regressão nas duas árvores: **zero**. Produção: 327 testes, 4 falhas pré-existentes. Sistema 02: 212 testes, 6 falhas pré-existentes (2 delas do código de exclusão marketing, que naquela árvore é mais antigo).

### Diferença conhecida do sistema 02

`set_display_score` / `stop_offset_*` (correção de 10/09 — placar do Shift+O não pode disparar Stop Win real) **nunca foi portado** para lá. Os cenários S17/S48 detectam a ausência e se declaram não aplicáveis. Vale portar em separado: mexe em lógica de stop, não de placar.
