# Estratégia do Robô El Capo — pipeline de análise e portão de entrada

Documento de referência da lógica de seleção e aprovação de operações.
Criado em 2026-07-21 junto com a correção do portão de qualidade da entrada.

## Estado atual (2026-08-16 — **produção: volta 13–14/08**)

O robô opera com a **estratégia clássica** (`confidence_model_version =
backup-classic`) + bloqueio `SR_ZONE` + **anti-loss de 04/08 e substitution
de 13/08**. Os cortes de **15/08** (WEAK PUT, vela 45%, DOJI hard, PUT/CALL
chase, REPEAT_ENTRY) foram **desligados**: no sábado o WR caiu para 32–45%
contra ~50–51% da substitution. Flags em `signal_engine.py` = `False`.

Cadência de análise contínua permanece. A ordem segue a **mesma direção**
aprovada pela análise.

| Aspecto | Valor atual |
|---|---|
| Modelo de confiança | `backup-classic` (score bruto); **portão usa `strategy_score`**, não confiança bruta |
| Velas analisadas por ativo | **100** (`ROBOT_CANDLE_COUNT`) do timeframe da operação |
| Perfis | aggressive 70 / balanced 80 / conservative 90 |
| Setups de price action | `CONTINUATION`, `REVERSAL`, `SUPPORT_RESISTANCE` (WEAK como fallback, **CALL e PUT**) |
| Zona de suporte/resistência | **Bloqueio crítico `SR_ZONE`** |
| Anti-loss (2026-08-04) | `LAST_3_ALIGNMENT`, `WEAK_CONTINUATION_PUT`, `CONTINUATION_DEAD_RSI`, `TREND_CLEAR`, cooldown pós-LOSS |
| Substitution (2026-08-13) | Ranking `(quality_tier, strategy_score, payout, conf≤92)` — WEAK perde o slot; AUDUSD demovido |
| Cortes 15/08 | **Off** (`WEAK_PUT`, `CANDLE_WEAK`, `DOJI`, `PUT_*`, `CALL_CHASE`, `REPEAT_ENTRY` = False) |
| Estratégias nomeadas no pipeline | Desativadas |
| Direção executada | **Igual ao sinal aprovado** |

### Edge medido por timeframe (backtest walk-forward, 2026-07-29)

Candles reais da BullEx, 8 ativos OTC × 1000 velas por timeframe, portão de
confiança 80 (o configurado pelos usuários), payout 88% (empate = 53,2%).
Metodologia e comandos em [`BACKTEST_VALIDACAO.md`](./BACKTEST_VALIDACAO.md).

| Timeframe | Operações | Acerto | Resultado |
|---|---|---|---|
| **M1** | 226 | **54,4%** | positivo (acima do empate) |
| M5 | 279 | 48,7% | negativo |
| M15 | 334 | 50,9% | negativo |

Consequência prática: **a estratégia só tem vantagem em M1**. Contas em M5/M15
tendem a perder no longo prazo mesmo com a análise funcionando corretamente. O
perfil (aggressive/balanced/conservative) não altera o resultado quando o
usuário exige confiança ≥ 80, porque o portão do usuário é mais restritivo que
o do perfil.

## 1. Pipeline de um ciclo de análise

Arquivos: `backend/signal_engine.py` (análise técnica) e `backend/main.py`
(orquestração do ciclo, funções `update_cycle_analysis`,
`resolve_cycle_entry_candidate` e `candidate_meets_cycle_threshold`).

1. **Varredura** (`scan_local_signals`): o robô analisa os ativos permitidos
   para o modo de mercado **efetivo** (ver §7), buscando **100 candles**
   (`ROBOT_CANDLE_COUNT`) do timeframe da operação para cada ativo.
2. **Análise técnica** (`analyze_signal`): confluência de EMA9/EMA21, RSI(14),
   sequência dos últimos 3 e 5 candles, força do candle atual, pavios,
   setup de price action (continuação/reversão/suporte-resistência),
   volatilidade (ATR) e payout. A confiança é o **score bruto** (modelo
   `backup-classic`), limitado a 0–100 em `_build_signal`. Gera
   `confidence`, `strategy_score`, `approved_filters` e `blocked_filters`.
3. **Filtros de qualidade** (`_apply_quality_filters`): cada critério aprova
   ou bloqueia. Bloqueios **críticos** (lista `CRITICAL_TRADE_BLOCKS` em
   `main.py`) tornam o sinal reprovado (`trade_allowed = False`):
   `CANDLE_STRENGTH`, `DOJI_FILTER`, `PRICE_ACTION_SETUP`, `REVERSAL_AGAINST`,
   `LEVEL_CONFLICT`, `LEVEL_REJECTION`, `SR_ZONE`, `TREND_CLEAR` (só sideways),
   `LAST_3_ALIGNMENT`, `WEAK_CONTINUATION_PUT`, `WEAK_PUT`,
   além dos operacionais (`ACTIVE_CLOSED`, `ASSET_COOLDOWN`,
   `GLOBAL_LOSS_COOLDOWN`, `CANDLES_UNAVAILABLE`, stops etc.). Soft
   (só penalizam score): `TREND_STRENGTH`, `SIDEWAYS_FILTER`, `WICK_REJECTION`,
   `CONTINUATION_DEAD_RSI`, `SUPPORT_RESISTANCE`, `LAST_5_CONFIRMATION`,
   `NO_ALTERNATING_LAST_3`, `EMA_TREND`, `RSI_RANGE`.

### Política anti-loss (2026-08-04)

Auditoria do histórico real (CONTINUATION analisada, 7d / 24h) mostrou padrões
repetidos com WR muito abaixo do empate (~53%). Esses contextos **não operam**:

| Bloqueio | Regra | Evidência |
|---|---|---|
| `LAST_3_ALIGNMENT` | CONTINUATION só se `last_3_direction` = UP (CALL) / DOWN (PUT) | WR **8,3%** quando contra as 3 velas |
| `WEAK_CONTINUATION_PUT` | CONTINUATION+PUT vetado em EURUSD, AUDUSD, USDCAD, USDCHF, **EURGBP**, **AUDJPY** (OTC/aberto) | WR 30–41% (2026-08-04) + EURGBP PUT ~29% / AUDJPY PUT ~35% (2026-08-13) |
| `CONTINUATION_DEAD_RSI` | Soft (penalidade 18); portão `strategy_score` ainda barre a maioria | WR **28,6%** — hard zerava frequência |
| `TREND_CLEAR` | **Crítico só se `trend==SIDEWAYS`**; força fraca → soft `TREND_STRENGTH` | WR **30,4%** no sideways |
| Portão por `strategy_score` | `candidate_meets_cycle_threshold` exige score **após** penalidades ≥ `min_confidence`; confiança bruta não abre ordem | conf ≥95 tinha WR **37,8%** |
| Ranking | `candidate_rank` = `(quality_tier, strategy_score, payout, conf≤92)` — **WEAK (tier 0) perde** para CONTINUATION/REVERSAL/S-R; AUDUSD demovido; confiança só desempate limitado | substitution no mesmo ciclo |
| `FREQUENCY_RECOVERY` | Após **2** ciclos `NO_OPPORTUNITY`, suaviza hard de: `TREND_CLEAR`, `PRICE_ACTION_SETUP`, `LEVEL_REJECTION`; score mínimo 70. **Não** afrouxa `LAST_3`, `WEAK_CONTINUATION_PUT`, `SR_ZONE`, `LEVEL_CONFLICT` | volume via WEAK |

### Substitution (2026-08-13) — mais acerto sem cortar volume

Objetivo: **não cancelar o ciclo**; trocar o candidato ruim pelo próximo bom no
mesmo scan.

1. O recovery ainda pode liberar `trade_allowed` em setup WEAK (evita robô parado).
2. O ranking coloca WEAK no tier 0; qualquer CONTINUATION/REVERSAL/S-R elegível
   ganha o slot mesmo com `strategy_score` menor.
3. Se **só** existir WEAK aprovado, ele opera (fallback de frequência), **CALL ou PUT**.
   Cortes `WEAK_PUT` / vela 45% / chase **estão desligados** (2026-08-16).
4. CONTINUATION+PUT nos pares tóxicos (agora com EURGBP/AUDJPY) continua
   **hard block** — não depende de ranking.

Funções: `candidate_quality_tier`, `candidate_rank`, `choose_better_candidate`
em `backend/main.py`; constantes em `signal_engine.py`.
Testes: `tests/test_accuracy_ranking_substitution.py`.

| Filtro | Papel |
|---|---|
| `TREND_CLEAR` | **Crítico** — apenas `trend == SIDEWAYS` |
| `TREND_STRENGTH` | Soft — UP/DOWN com strength abaixo do perfil |
| `SIDEWAYS_FILTER` | Soft (penaliza score); redundante com `TREND_CLEAR` |
| `WICK_REJECTION` / `CONTINUATION_DEAD_RSI` | Soft (penalizam score) |
| `LAST_3_ALIGNMENT` / `WEAK_CONTINUATION_PUT` | **Críticos** anti-loss |
| `SR_ZONE` / `LEVEL_*` | **Críticos** de estrutura |
| `CANDLE_STRENGTH` / `DOJI_FILTER` | **Soft** (perfil); cortes 45%/DOJI hard = **off** |

Flags em `signal_engine.py`: `LAST_3_ALIGNMENT_HARD_BLOCK`,
`CONTINUATION_DEAD_RSI_HARD_BLOCK`, `WEAK_CONTINUATION_PUT_HARD_BLOCK`,
`TREND_CLEAR_HARD_BLOCK` (todas `True`). Cortes 15/08 (`WEAK_PUT_HARD_BLOCK`,
`CANDLE_WEAK_HARD_BLOCK`, `DOJI_HARD_BLOCK`, `PUT_*`, `CALL_CHASE`,
`REPEAT_ENTRY`) = **False**.
Lista: `WEAK_CONTINUATION_PUT_ASSETS`. Helper: `is_weak_put_setup` (código
mantido, flag off).

**Nota sobre confiança:** em opções binárias OTC o score técnico alto **não**
prova edge. A decisão usa filtros estruturais + `strategy_score`.

Horários UTC fracos foram observados, mas **não** viraram hard block nesta
rodada (pedido do cliente).

Testes: `tests/test_loss_pattern_blocks.py`.

### Política de suporte / resistência (2026-07-29)

O robô **não entra com o preço dentro da zona de suporte/resistência**, em
nenhuma direção e em nenhum setup.

| Situação | Resultado |
|---|---|
| Preço na zona de nível (`near_support` ou `near_resistance`) | Bloqueio crítico **`SR_ZONE`** |
| Conflito de nível (ex.: CALL na resistência) | Bloqueio crítico `LEVEL_CONFLICT` |
| Reversão/S-R sem rejeição confirmada no nível | Bloqueio crítico `LEVEL_REJECTION` |
| Só “perto do nível” sem setup | Soft penalty `SUPPORT_RESISTANCE` (não reprova) |
| Preço no meio do range | Segue o pipeline normal |

Implementação:

| Item | Onde |
|---|---|
| Chave liga/desliga | `SR_ZONE_HARD_BLOCK` (topo de `signal_engine.py`) |
| Definição da zona | `_support_resistance_context`: nível = min/max das 24 velas anteriores; tolerância = `max(12% do range, 65% do range médio)` |
| Campo no sinal | `in_support_resistance_zone` (bool, vai para o payload e o histórico) |
| Filtro | `check("SR_ZONE", ...)` em `_apply_quality_filters`, penalidade 20 no `strategy_score` |
| Bloqueio crítico | `CRITICAL_TRADE_BLOCKS` e `RECOVERY_NON_RELAXABLE_TRADE_BLOCKS` (`main.py`) — nem o modo recovery relaxa |
| Testes | `tests/test_sr_zone_block.py` |

Impacto medido antes de ativar (mesma amostra da tabela de timeframes, M1):

| Cenário | Operações | Acerto | Resultado (stake 1) |
|---|---|---|---|
| Sem `SR_ZONE` | 228 | 53,9% | +3,24 |
| Com `SR_ZONE` | 226 | **54,4%** | **+5,24** |
| Só as entradas cortadas | 2 | **0,0%** | −2,00 |

Ou seja: o bloqueio custa ~1% das entradas e as que ele remove eram perdedoras
na amostra. Para voltar ao comportamento clássico (zona liberada com rejeição
confirmada) basta `SR_ZONE_HARD_BLOCK = False`.

Detalhe histórico das estratégias nomeadas (fora do pipeline atual):
[`ESTRATEGIAS_NOMEADAS.md`](./ESTRATEGIAS_NOMEADAS.md).

4. **Ranqueamento**: os candidatos são ordenados por `strategy_score`,
   depois `confidence`, depois `payout` (`candidate_rank`). O melhor que
   passa nos limites do usuário vira `cycle_best_trade_candidate`.
5. **Entrada** (`resolve_cycle_entry_candidate` + loop de ordens): na janela
   de entrada o candidato é revalidado pelo portão final antes do envio. A
   análise, a aprovação e a ordem enviada à BullEx usam a **mesma direção**
   técnica (§2a).

### 2a. Direção alinhada à análise (2026-08-07)

O El Capo mantém integralmente a análise técnica, o score, os filtros, o
ranking e o portão de qualidade na direção encontrada pelo pipeline. Depois da
aprovação final, a entrada enviada à BullEx **segue a mesma direção** da
análise (política anterior de inversão CALL↔PUT foi desativada em 2026-08-07):

| Direção aprovada pela análise | Direção enviada à corretora |
|---|---|
| `CALL` | `CALL` |
| `PUT` | `PUT` |

Implementação: `resolve_robot_execution_direction` valida e devolve a direção
analisada no loop de ordens em `backend/main.py` (entrada inicial e gale).
O helper `opposite_execution_direction` permanece disponível para utilitário/
testes, mas **não** é mais usado na montagem da ordem. A resolução acontece
depois de `resolve_entry_validation_reason`, portanto não modifica a leitura
das velas nem os filtros.

O histórico da ordem registra:

- `analyzed_direction`: direção aprovada pela análise;
- `direction`: direção efetivamente enviada à BullEx (igual à analisada);
- `execution_direction_inverted=false`: confirma que não houve inversão
  (o campo continua existindo para auditar ordens antigas com `true`).

Ordens de **gale** repetem a direção da primeira ordem efetivamente
executada (já alinhada à análise).

## 2. Portão final de entrada (`candidate_meets_cycle_threshold`)

Um candidato só vira ordem se **todas** as condições forem verdadeiras:

| Verificação | Regra |
|---|---|
| Direção | `CALL` ou `PUT` |
| Ativo | aberto e não suspenso (`candidate_pre_order_block_reason`) |
| **Dados frescos** | não pode ter `stale` / `from_cache` / `STALE_MARKET_DATA`. Timeout de análise (5s) com cache **ainda no TTL** (60s) **não** é dado velho — vira `ANALYSIS_TIMEOUT_FRESH` e segue o `trade_allowed` da estratégia. Só cache já expirado (janela stale de 120s) carimba `STALE_MARKET_DATA`. |
| **Aprovação da estratégia** | `trade_allowed == True` (obrigatório) |
| **Bloqueios críticos** | nenhum item de `blocked_filters` pode estar em `CRITICAL_TRADE_BLOCKS` (inclui `SR_ZONE`) |
| Payout | `>= min_payout` do usuário |
| Confiança / score | **`strategy_score` ≥ `min_confidence`** do usuário (estrito) ou ≥ 70 no ramo de fallback. A confiança bruta **não** abre ordem sozinha. |
| **Memória de padrões** | Caderno **global** (todas as contas): veta contextos fracos (`PATTERN_MEMORY_WEAK`) — ver [`MEMORIA_PADROES.md`](./MEMORIA_PADROES.md) |

Exceção: ordens de **gale** não passam por esse portão (repetem a entrada
anterior por definição do martingale).

## 3. Fallback operacional

- `resolve_cycle_entry_candidate` tem um ramo de fallback que aceita o melhor
  candidato **aprovado** com confiança entre 70 e o `min_confidence` do
  usuário (marcado com `fallback_candidate_used=true` no histórico). Após a
  correção, esse ramo também exige `trade_allowed=True` e zero bloqueios
  críticos.
- `select_fallback_candidate` ("movimento simples das últimas velas") gera
  candidatos com `trade_allowed=False` — com o portão atual eles **não são
  mais executados**; servem apenas para exibição no painel quando nenhum
  setup de verdade existe. O robô então agenda a **próxima janela de
  análise da vela** (`NO_TRADE`), em cadência contínua (M1/M5/M15 a cada
  vela — ver `ROBO_E_SUPORTE.md` e `ANALISE_CONTINUA.md`). A janela de
  compra permanece 0–8s no início da vela; o worker faz poll fino para
  não perder a entrada.

## 4. Confirmação multi-timeframe (estado atual)

`confirm_ranked_candidates_multi_timeframe` está deliberadamente em modo
"estratégia clássica": preenche os campos `mtf_*` para telemetria mas **não
bloqueia** por confluência entre M1/M5/M15. Essa decisão veio do código-fonte
enviado pelo cliente (docstring "Estratégia clássica do backup"). Se quiser
reativar o gate MTF, a lógica completa está em
`confirm_candidate_multi_timeframe` + `merge_multi_timeframe_signals`
(`signal_engine.py`).

## 5. Testes

- `tests/test_accuracy_ranking_substitution.py` — ranking anti-loss,
  substitution WEAK→CONTINUATION, EURGBP/AUDJPY no hard PUT, penalidade de
  `PRICE_ACTION_SETUP` no recovery, hard block `WEAK_PUT` (WEAK CALL ok).
- `tests/test_loss_pattern_blocks.py` — bloqueios anti-loss (last_3, PUT fraco,
  RSI morto, TREND_CLEAR crítico, portão por strategy_score, cooldown pós-LOSS).
- `tests/test_pattern_memory.py` — memória de padrões (chave, veto, gale,
  isolamento multi-tenant e portão).
- `tests/test_sr_zone_block.py` — política `SR_ZONE` (sinal, portão e recovery).
- `tests/test_entry_quality_gate.py` — portão de entrada.
- `tests/test_robot_market_data_resilience.py` —
  `test_analysis_timeout_keeps_fresh_cache_tradeable` /
  `test_analysis_timeout_still_blocks_expired_cache` — timeout de 5s não
  pode tratar cache no TTL como STALE.
- `tests/test_confidence_calibration.py` — helper `_calibrate_confidence`
  ainda existe, mas `analyze_signal` usa `backup-classic`; também cobre
  rejeição de candidatos stale/`from_cache`.
- `tests/test_named_strategies.py` — detecção do módulo isolado + confirma
  que `analyze_signal` **não** anexa campos nomeados no pipeline clássico.
- `tests/test_candle_analysis.py` / `test_strategy_filters.py` /
  `test_operation_cycle_strategies.py` / `test_multi_timeframe_analysis.py` /
  `test_strategy_backtest.py` — suíte clássica.

## 6. Histórico de mudanças

- **2026-09-08 (canal pré-aquecido antes da vela)** — Medição de 107 ordens
  reais: `min 0,29s · mediana 3,77s · max 5,62s` dentro da vela, com **só
  30/107 (28%)** na janela declarada `window_start=0 window_end=3`. O custo
  estava na `refresh_candidate_execution_channel`, que roda DENTRO da janela e
  estourou o timeout de 0,9s **82 vezes em 6h** sem devolver resposta — o sinal
  espera a vela inteira, o cache de payout passa de
  `CHANNEL_CACHE_MAX_AGE_SECONDS` (10s) e a compra refaz a consulta quase
  sempre. Agora `prewarm_execution_channel_before_entry` roda a **mesma**
  consulta a `CHANNEL_PREWARM_LEAD_SECONDS` (5s) da abertura, enquanto a vela
  anterior ainda corre, uma vez por ciclo (`_channel_prewarmed_cycle_by_user`)
  e com timeout nunca maior que o tempo restante. Na compra o cache está
  fresco e o caminho crítico fica sem rede. **Nenhuma validação foi
  afrouxada** — a checagem da compra continua onde estava. Falha no
  pré-aquecimento não propaga (`CHANNEL_PREWARM_FAILED`); o caminho antigo
  segue inteiro. Teste: `tests/test_channel_prewarm.py` (o arquivo já existia,
  com a medição de 01/09 — faltava a implementação).
  **Correção no mesmo dia:** a trava de "uma vez por ciclo" era por
  `(usuário, ciclo)` e o `cycle_id` **não roda a cada entrada** — o ciclo
  2178739c serviu GBPCHF-OTC, GBPAUD-OTC e EURCAD-OTC em ~6 min. Só o 1º ativo
  aquecia; o EURCAD-OTC, sem pré-aquecimento, estourou o timeout de 0,9s dentro
  da janela. A chave passou a incluir o **ativo**.
- **2026-09-08 (contagem de recusa passa a ser global)** — A escada de cooldown
  contava por `(usuário, ativo)` e o EURJPY-OTC ainda queimava um ciclo em cada
  conta antes de escalar: cinco contas registrando `strikes=1` no mesmo par, na
  mesma janela. `asset is not available` é condição da **corretora**, não da
  conta, então a contagem virou global por ativo. A **aplicação** do cooldown
  segue por usuário de propósito: nenhuma conta é barrada pela recusa de outra
  — o que muda é o degrau em que ela entra quando bate no par.
- **2026-09-09 (a marca do modo LIVE é só o booleano)** — `strategy_key` deixou
  de gravar `LIVE_DEMO` e passou a levar a chave real do setup
  (`CANDLE_FLOW`/`CONTINUATION`/`EXHAUSTION_REVERSAL`): a chave é campo de tela
  (badge do Histórico e primeiro item da lista de estratégias que o robô fala
  em voz alta), e entregava o modo na transmissão. **Toda consulta que precisar
  excluir o modo LIVE de uma medição usa `analysis_json->>'live_demo' = 'true'`**
  — e as contas do modo são sempre `account_type=marketing`, que a auditoria já
  exclui. As ~184 operações gravadas antes desta data continuam com
  `strategy_key=LIVE_DEMO` e são reconhecíveis pelos dois campos. Ver
  `NARRACAO_ROBO.md` §4e.
- **2026-09-08 (marca `live_demo` no histórico)** — 184 operações do modo LIVE
  gravadas com `strategy_key=LIVE_DEMO` e **zero** com o booleano `live_demo`,
  que `robot_persistence.TRADE_ANALYSIS_FIELDS` documenta como sendo o campo
  que a auditoria consulta. O dict do registro da operação não carregava o
  campo e a montagem do `analysis_json` descarta `None`. Corrigido com
  `"live_demo": True if selected.get("live_demo") is True else None` — `None`
  na operação normal preserva as linhas existentes. Teste:
  `tests/test_live_demo_marca_historico.py`.

- **2026-09-08 (cooldown progressivo do par recusado)** — Medido nos logs:
  de 133 ordens reais, **94 recusadas** com `asset is not available` e **89
  delas no mesmo par** (EURJPY-OTC, 89/89 recusadas). O catálogo da corretora
  anunciava `open_turbo=true` com payout 88 — o maior da roda — então o scorer
  reelegia o par toda vela; o cooldown fixo de 60s era exatamente uma vela M1,
  expirava, o catálogo voltava a dizer "aberto" e o ciclo recomeçava. Agora
  `mark_execution_channel_unavailable` sobe degraus por recusas **seguidas do
  mesmo par**: `UNAVAILABLE_ASSET_COOLDOWN_LADDER_SECONDS = (60, 300, 900,
  3600)`. O 1º degrau continua sendo a vela M1 de antes (recusa isolada não é
  punida); a contagem zera quando uma ordem do par é aceita e decai sozinha
  após `UNAVAILABLE_ASSET_STRIKE_DECAY_SECONDS` (30 min) sem nova recusa. O
  log `EXECUTION_CHANNEL_MARKED_UNAVAILABLE` passou a trazer `strikes=`.
  Teste: `tests/test_unavailable_asset_cooldown.py`.

- **2026-08-19 (timeout 5s ≠ cache velho)** — Fallback de
  `ANALYSIS_TIMEOUT` deixou de forçar `stale`/`from_cache` quando as
  candles ainda estão no TTL do cache compartilhado. O portão de entrada
  não muda: STALE de verdade continua bloqueando. Ver
  [`ROBO_E_SUPORTE.md`](./ROBO_E_SUPORTE.md).
- **2026-08-15 (vela fraca + DOJI — interno, sem deploy)** — Além de WEAK
  PUT: hard block corpo &lt; 45% e DOJI ≤ 10%. Recovery não afrouxa.
  Hipótese nas 677 ops: 370 restantes, 220x150, **59,5%**. Volume: WEAK CALL
  com vela forte + substitution + não cortar horário. Ver
  [`CORTES_QUALIDADE.md`](./CORTES_QUALIDADE.md).
- **2026-08-14 (WEAK PUT cortado — código interno, sem deploy)** — Auditoria
  das 677 ops pós-substitution: WEAK PUT 265 ops, 113x152, **42,6%**; WEAK
  CALL 194 ops, 111x83, **57,2%**. Sem WEAK PUT o placar hipotético seria
  235x177 (**57,0%**). Hard block `WEAK_PUT` em todos os ativos; recovery e
  portão do ciclo não afrouxam. WEAK CALL permanece. Detalhe:
  [`WEAK_PUT_BLOCK.md`](./WEAK_PUT_BLOCK.md). Testes:
  `WeakPutHardBlockTests` em `test_accuracy_ranking_substitution.py`.
- **2026-08-13 (acertividade / substitution)** — Auditoria das últimas 100
  LOSS: 64% WEAK liberado no recovery, conf ≥95 sem edge, CONTINUATION+PUT
  fraco em EURGBP/AUDJPY. Ranking com `quality_tier` (WEAK perde o slot),
  `PRICE_ACTION_SETUP` mantém penalidade de score no recovery, lista tóxica
  PUT + EURGBP/AUDJPY, demote AUDUSD. Volume preservado (WEAK só fallback).
  Testes: `test_accuracy_ranking_substitution.py`.
- **2026-08-07 (execução alinhada à análise)** — Remove a política de
  inversão CALL↔PUT na montagem da ordem. `resolve_robot_execution_direction`
  envia à BullEx a mesma direção aprovada pelo pipeline; gale continua
  repetindo a direção executada. Histórico mantém `analyzed_direction` /
  `direction` / `execution_direction_inverted` (agora `false` nas novas
  ordens). Teste:
  `AutoTraderStateTests.test_execution_direction_follows_analysis_direction`.
- **2026-08-05 (execução contrária — revertida em 2026-08-07)** — Entrada
  inicial executava a direção oposta (CALL→PUT, PUT→CALL). Mantido no
  histórico apenas como registro; política ativa é a alinhada (§2a).
- **2026-08-04 (frequência v4)** — Após 22 min ainda `allowed=0` em 10/10
  ativos (PRICE_ACTION_SETUP, corpo, sideways, SR). **Frequency recovery**
  após 8 ciclos sem entrada: suaviza TREND_CLEAR / PRICE_ACTION /
  CANDLE_STRENGTH / DOJI / LEVEL_REJECTION; mantém LAST_3, WEAK_PUT, SR_ZONE,
  LEVEL_CONFLICT. Corpo conservative 0.40; CANDLE/DOJI soft permanente.
  Sergio segue `enabled=False` (precisa ligar no painel).
- **2026-08-04 (frequência v3)** — Victor com 60 ciclos `NO_PATTERN_FOUND`
  e bloqueio `TREND_CLEAR`+SIDEWAYS em mercado lateral; Sergio ainda
  `enabled=False`. Ajuste: `TREND_CLEAR` crítico só em SIDEWAYS; força
  fraca = soft `TREND_STRENGTH`; `CONTINUATION_DEAD_RSI` soft; log
  `[SIGNAL SCAN SUMMARY]` e `[NO_OPPORTUNITY_STREAK]` em WARNING.
- **2026-08-04 (frequência v2)** — Conta Sergio estava `enabled=False`
  (STOPPED). Victor ainda caía em `ACTIVE_CLOSED` pelo path
  `resolve_cycle_entry_candidate` (PAYOUT + SIDEWAYS). Unificado
  `classify_no_opportunity_reason`. `WICK_REJECTION` e `SIDEWAYS_FILTER`
  viraram soft; `TREND_CLEAR` strength ≥ 15; perfil conservative strength 15.
  Anti-loss estrutural mantido.
- **2026-08-04 (frequência sem afrouxar anti-loss)** — Diagnóstico: robô
  “parado” por (1) `GLOBAL_LOSS_COOLDOWN` 3 min/30 min gerando centenas de
  bloqueios; (2) `[ENTRY_WINDOW_MISSED]` em ~6,3s com janela 0–5s; (3) bug
  que classificava rejeição de estratégia + `PAYOUT_UNAVAILABLE` como
  `ACTIVE_CLOSED` e forçava backoff operacional de 30s em vez da próxima
  janela de análise. Ajustes: cooldown **60s / 10 min**, compra **0–8s**,
  poll de entrada mais fino, e `NO_PATTERN_FOUND` quando há bloqueio de
  qualidade. Hard blocks anti-loss **mantidos**.
- **2026-08-04 (anti-loss)** — Hard blocks a partir da auditoria de losses:
  `LAST_3_ALIGNMENT`, `WEAK_CONTINUATION_PUT` (EURUSD/AUDUSD/USDCAD/USDCHF),
  `CONTINUATION_DEAD_RSI` (RSI 50–59), `TREND_CLEAR` crítico, portão e ranking
  por `strategy_score` (confiança bruta não decide), `GLOBAL_LOSS_COOLDOWN`
  após 1 LOSS ligado no guard (calibrado depois para 60s / 10 min). Velas
  por ativo: 100. Ver §"Política anti-loss".
- **2026-08-03 (caderno global)** — Memória de padrões unificada: todas as
  contas alimentam e consultam o mesmo caderno
  (`robot_pattern_memory_global`). Cadernos pessoais permanecem como arquivo;
  SQL v4 + `unify_all_into_global` somam o histórico existente. Ver
  [`MEMORIA_PADROES.md`](./MEMORIA_PADROES.md).
- **2026-07-31 (cooldown GBPJPY em loop)** — Ativo rejeitado pela corretora
  (`asset is not available`) voltava a ser analisado/comprado a cada vela M1
  porque cache/`from_cache` furavam o cooldown e `ACTIVE_COOLDOWN` não era
  hard block. Agora: skip duro **só desse símbolo**, cooldown **60s**, marca
  canal fechado no cache. Análise dos outros ativos segue a cada vela.
  Ver `ROBO_E_SUPORTE.md` §1.
- **2026-07-31 (entrada anunciada sem compra)** — Na hora do buy, canal
  turbo fechado era mascarado como “baixa qualidade”; overlay/voz ainda
  promoviam `best_candidate`. `resolve_entry_validation_reason` + balão/voz
  só com `pending_signal`. Ver `ROBO_E_SUPORTE.md` e `NARRACAO_ROBO.md`.
- **2026-07-31 (memória de padrões)** — Portão estatístico por
  ativo×hora×setup×direção×TF (`PATTERN_MEMORY_WEAK`). Fail-open com amostra
  &lt; 12; bloqueia WR &lt; 52%. Desde 2026-08-03 o caderno é **global**
  (todas as contas). Ver [`MEMORIA_PADROES.md`](./MEMORIA_PADROES.md).
- **2026-07-29 (bloqueio de suporte/resistência)** — `SR_ZONE` volta como
  bloqueio crítico, agora sem exceções: preço na zona de nível não opera. A
  auditoria confirmou que o resto da análise é idêntico ao backup; o backtest
  walk-forward com candles reais embasou a decisão e revelou que só **M1** tem
  vantagem (54,4% vs 48,7% em M5). Ver §"Estado atual" e
  [`BACKTEST_VALIDACAO.md`](./BACKTEST_VALIDACAO.md).
- **2026-07-26 (restauração clássica)** — Volta a estratégia `backup-classic`
  do backup (`/root/backup/Backend`): score bruto, filtros com
  `SUPPORT_RESISTANCE` / `LEVEL_REJECTION`, sem hard-block `SR_ZONE` e sem
  estratégias nomeadas no pipeline. Análise contínua por vela e rejeição de
  dados stale no portão foram mantidas. Backup **não** foi modificado.
- **2026-07-29** — Revalidação de canal na compra deixa de ser só-cache: busca
  `/payouts` fresco quando o cache passa de 10s (teto 1,2s por ativo, 2,0s por
  ciclo; falha → cache). Reduz rejeição `NO_AVAILABLE_ASSET` no buy. Ver
  `ROBO_E_SUPORTE.md` §1.
- **2026-07-26 (estratégias nomeadas)** — Adicionou Retração S/R, Exaustão e
  Fluxo (`named_strategies.py`) + isenção `SR_ZONE`. **Fora do pipeline
  ativo** após a restauração clássica; ver `ESTRATEGIAS_NOMEADAS.md`.
- **2026-07-26 (noite)** — Bloqueio duro `SR_ZONE` (revertido pela restauração).
- **2026-07-26** — Correções de canal turbo/binary fechado e UI falsa de
  entrada (`pending_signal` apenas).
- **2026-07-25** — Confiança calibrada (`calibrated-v1`); BOTH→OTC; stale
  bloqueado. Calibração deixou de ser aplicada no score de entrada após a
  restauração clássica.
- **2026-07-24** — Análise contínua por vela (remove cooldown 5/15/45).
- **2026-07-21 (noite)** — Portão de qualidade: exige `trade_allowed=True` e
  ausência de bloqueios críticos.

## 7. Modo de mercado (OTC / Aberto / Ambos)

Funções: `normalize_market_mode`, `is_forex_open_market_open`,
`effective_market_mode`, `coerce_selectable_market_mode`,
`resolve_analysis_assets` em `backend/main.py`.

| Escolha do usuário | Sessão forex | Modo efetivo (varredura/ordens) |
|---|---|---|
| OTC | qualquer | OTC (`*-OTC`) |
| Ambos (BOTH) | **aberta** | **BOTH** (10 OTC + 10 abertos; OTC primeiro) |
| Ambos (BOTH) | fechada | OTC |
| Aberto (OPEN) | aberta | OPEN (pares sem `-OTC`) |
| Aberto (OPEN) | aberta, canal turbo/binary fechado em todos | **OTC** (fallback; `reason=execution_channel_closed_on_all_open_assets`) |
| Aberto (OPEN) | aberta, payout ausente após 3 ciclos sem entrada | **OTC** (fallback; `reason=payout_unavailable_on_open_assets`) |
| Aberto (OPEN) | fechada | **OTC** (UI cadeado + horas até abrir) |

Motivo histórico do BOTH→só-OTC: varrer ~20 ativos estourava timeout de
payout. Com cache compartilhado de mercado isso foi revertido em 2026-08-15
para o Aberto **de fato operar**. Ver [`MERCADO_ABERTO.md`](./MERCADO_ABERTO.md).

UI (`RobotControlPanel`, `StartOperationDialog`): as três opções ficam
sempre visíveis. Com forex fechado, **Mercado aberto** aparece com ícone
de cadeado e o texto `Abre em X horas` (próxima abertura: domingo 22:00
UTC). Não é possível selecionar até a sessão reabrir. Ver
`CONFIGURACOES.md`.

Campos no payload do robô: `open_market_available`,
`open_market_hours_until`, `market_mode_effective`.
