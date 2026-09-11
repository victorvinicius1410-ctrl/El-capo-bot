# Robô El Capo — Operação contínua, cadência e sessão de suporte

Documento de referência das regras de operação do robô e do modo suporte
(admin acessando conta de outro usuário). Atualizado em **2026-08-21**.

## 1. Cadência de análise contínua por timeframe

Definida em `backend/signal_engine.py` (`CYCLE_MINUTES_BY_TIMEFRAME`,
`seconds_until_next_analysis`) e aplicada em `auto_trader.start()`,
`_schedule_next_cycle`, `_schedule_continuous_wait`,
`reset_cycle_after_result` e `schedule_next_analysis_session`.

A IA **monitora o mercado a cada vela** do timeframe da operação.
Não existe mais o cooldown legado (M1 a cada 5 min / M5 a cada 15 /
M15 a cada 45). Detalhes: `ANALISE_CONTINUA.md`.

| Timeframe (duração da operação) | Varredura | Expiração da ordem | Meta mín. ops/hora |
|---|---|---|---|
| M1 (1 minuto) | **a cada vela** (janela 5–20s) | 1 minuto | 12 |
| M5 (5 minutos) | **a cada vela** (janela 5–20s) | 5 minutos | 6 |
| M15 (15 minutos) | **a cada vela** (janela 5–20s) | 15 minutos | 2 |

A duração da operação é o próprio timeframe (`TIMEFRAME_SECONDS`: M1=60,
M5=300, M15=900). O campo `cycle_minutes` guarda a duração da vela
(1 / 5 / 15) para compatibilidade com o painel — **não** é intervalo
entre análises.

### Janela de compra (entrada)

Após a análise, a ordem só é enviada nos **primeiros 0–8 segundos** da
próxima vela (`ENTRY_WINDOWS`). O worker faz poll fino perto dessa
abertura (`robot_worker_entry_wait_seconds`) — não dorme a espera
inteira de uma vez (isso fazia perder a janela e o robô “parava” de
pegar operações após alguns acertos). Em 2026-08-04 a janela passou de
0–5s para 0–8s para absorver latência de refresh/canal
(`[ENTRY_WINDOW_MISSED]` em ~6,3s).

**2026-08-21 — relógio não pode adiantar a compra:** entre polls o worker
estima o horário Bullex (`server_time` + elapsed desde
`server_time_sampled_at`). Estimativa **não** vira nova âncora
(`persist_server_clock=False`); amostra absoluta a cada ≤45s
(`SERVER_CLOCK_RESAMPLE_SECONDS`). Sem isso o drift composto abria a
janela 0–8s no meio da vela (~45s antes do close no M1). Detalhes:
`ANALISE_CONTINUA.md` §3.

Sem padrão aprovado, o robô agenda a **próxima janela de análise** da
vela (não espera minutos extras). Falhas operacionais (candles/sessão)
usam backoff curto de 30s. Rejeição de estratégia **não** conta como
falha operacional (`classify_no_opportunity_reason` → `NO_PATTERN_FOUND`).

## 1b. Empate (DRAW) e retomada após operações

Quando a Bullex fecha a ordem como `equal`/`draw`:

- O monitor normaliza para **DRAW** com profit `0` (stake devolvida).
- O placar **não** incrementa WIN nem LOSS; o P/L da sessão não muda.
- Gale **não** é disparado.
- Após ~5s de exibição (`result_display_until`), o ciclo reagenda a
  próxima análise normalmente (`reset_cycle_after_result`).

Bug histórico: `equal` era mapeado para **LOSS** e forçava `-entry` no
placar — empate virava loss falsa e podia disparar gale.

### Compra bloqueada após 1–2 operações (`ACCOUNT_MODE_NOT_REAL`)

Sintoma: overlay fica em **Analisando...** / **Parar Operação**, mas não
abre novas ordens. Logs:

```
[REAL BUY BLOCKED reason=ACCOUNT_MODE_NOT_REAL] detail=mode invalido. Use PRACTICE, REAL ou TOURNAMENT
```

Causa: após trades, `get_balance_mode()` às vezes devolve `None` (balance_id
stale). `normalize_mode(None)` falhava e a compra era abortada — o worker
seguia analisando candles/payouts sem conseguir comprar.

Correção (2026-07-24): no buy REAL, se o modo estiver inválido, o
`bullex-service` chama `force_real_mode` e tenta de novo antes de falhar
(`REAL_MODE_RECOVER_BEFORE_BUY`).

### Compra falhou com oportunidade achada (`User balance not found`)

Sintoma (2026-07-24 ~17:35 UTC e de novo ~19:05): overlay em **“El Capo
está analisando o mercado”**, conta com saldo REAL (ex. R$10k+), mas a
ordem não abre. Nos logs o robô **encontrou** setup e tentou comprar:

```
[REAL BUY ATTEMPT] active=EURUSD-OTC action=call amount=100.0 expiration=1
[REAL_BUY_BALANCE_ID] balance_id=1209400704 source=real
[REAL BUY BLOCKED reason=falha ao criar ordem real: User balance not found]
```

**Não é saldo zerado.** A corretora rejeita o `user_balance_id` enviado no
`buyv3`. O profile do websocket (login) guardava um id REAL **antigo**
(`1209400704`); o id válido atual era outro (`1226252784`). Conta tinha
dinheiro — o id estava stale.

Correção (2026-07-24 noite + reforço):

1. `discover_balance_ids` / `fetch_balances_list` preferem **`get_balances()`
   fresco** (não o profile cacheado do login).
2. Sync do profile em memória com a lista fresca (`_sync_profile_balances`)
   para `change_balance` / `get_balance_mode` não reidratarem o id velho.
3. `ensure_real_balance_id_for_buy` **pina** o id descoberto
   (`REAL_BALANCE_ID_PIN`) mesmo após `change_balance("REAL")`.
4. Retry em `User balance not found`: force REAL + pin do id fresco
   (`REAL_BUY_RETRY_PINNED`).
5. Prefere balance REAL com `amount > 0` quando há vários type=1.

### Travamento em `REAL_BALANCE_NOT_DETECTED` sob alta demanda (2026-08-07)

Sintoma: com muitos usuários operando ao mesmo tempo, o painel/start fica
preso no erro de saldo real (`REAL_BALANCE_NOT_DETECTED` /
`[REAL_MODE_NOT_CONFIRMED] active_mode=None`) mesmo com a conta REAL
conectada e com dinheiro. Compras podem ainda passar para alguns usuários,
mas polls de `/account` e o `POST /robot/start` falham em massa.

Causa (cadeia):

1. `bullexapi` usava **`global_value.balance_id` compartilhado** entre todas as
   sessões do processo. No login, enquanto `balance_id` ainda é `None`, o
   websocket de **outro usuário** podia gravar o próprio id (handler
   `profile`) — a sessão ativa capturava o id errado.
2. `get_balance_mode()` não achava o id no profile → `None` → contrato
   `REAL_BALANCE_NOT_DETECTED`.
3. `get_balances()` fazia busy-wait **sem timeout**; sob carga segurava o
   lock global (`BULLEX_MAX_CONCURRENT_API_CALLS=1` no `MVP_SAFE_MODE`) por
   dezenas de segundos (latência média de `/account` ~4s, picos ~20s) e
   marcava usuários `offline` por 60s com `connected: false`.
4. O start abortava no contrato falho sem tentar o cache REAL conhecido.

Correção (2026-08-07):

| Peça | Comportamento |
|---|---|
| `global_value.balance_id_owner` | Só o WS do client dono do `_session_context` grava `balance_id` |
| `_activate_session` | Define o owner pelo `id(api)` da sessão ativa |
| `get_balances(timeout=5s)` | Timeout + sleep; não trava o lock global |
| `resolve_active_balance_mode` | Se o modo veio `None`, rediscobre/pina REAL (`[ACTIVE_MODE_RECOVERED]`) |
| `/account` | Não marca `offline` agressivo se a sessão já confirma REAL |
| `recover_real_account_contract_for_start` | Start usa cache/snapshot REAL (`[REAL_BALANCE_START_RECOVERED]`) |
| `recover_real_account_contract_for_poll` | Poll de `/bullex/account` recupera contrato REAL do cache/memória/estado do robô (`[REAL_BALANCE_POLL_RECOVERED]`) e devolve `ok=True` ao painel |

#### Fase 3 — isolamento `SessionGlobals` + gate=3 (2026-08-07)

O `MVP_SAFE_MODE` (gate=1 + `_runtime_lock` no yield) foi substituído por
isolamento real:

| Peça | Comportamento |
|---|---|
| `SessionGlobals` + ContextVar | SSID/balance_id/flags WS por sessão (`bullexapi/global_value.py`) |
| `BullexAPI.__init__` | Todos os mutáveis de classe viram atributos de instância |
| WS `client.py` | Bind de `api._session_globals` na thread do websocket |
| `BULLEX_MAX_CONCURRENT_API_CALLS` | Default **3** (compose/`.env`/`read_max_concurrent_api_calls`) |

Testes: `tests/test_bullexapi_session_isolation.py`. Detalhe operacional em
`PERFORMANCE_SISTEMA.md` ("Fase 3 — isolamento de sessão e gate=3").
**WIN/LOSS** continua com cinto em `order_result` (id da mensagem deve
bater com o order_id pedido).

#### Banner “Não foi possível confirmar o saldo REAL” no Dashboard (2026-08-07 tarde)

Sintoma: robô **continua operando** (overlay “analisando…” / Parar Operação),
mas o Dashboard mostra banner vermelho
`Erro na API: Não foi possível confirmar o saldo REAL…` e **Saldo/Moeda = -**.

Causa: o gateway mantinha o robô ligado
(`[ACCOUNT_CONTRACT_KEEP_ROBOT]`), porém o poll de `/bullex/account` ainda
respondia `ok=false` + `REAL_BALANCE_NOT_DETECTED`. O front
(`useBullExAccountQuery`) trata `!ok` como `ApiError` e exibe o banner.

Correção: `finalize_account_contract_with_poll_recovery` aplica
`recover_real_account_contract_for_poll` antes de devolver o contrato ao
painel — usa cache do start, `memory_account_fallback` ou estado do robô
já em REAL (`enabled`/`connected` + `active_mode=REAL`). Só permanece o
erro quando **não** há evidência REAL.

Logs úteis: `[REAL_BALANCE_POLL_RECOVERED]`, `[ACCOUNT_CONTRACT_KEEP_ROBOT]`,
`[REAL_MODE_NOT_CONFIRMED]`.

Testes: `AccountPollRealBalanceRecoveryTests` em
`tests/test_real_balance_isolation.py`.

Logs úteis (isolamento multi-sessão): `[ACTIVE_MODE_RECOVERED]`, `[ACTIVE_MODE_KEPT_FROM_SESSION]`,
`[ACCOUNT_CONNECTED_FALSE_KEEP_SESSION]`, `[REAL_BALANCE_START_RECOVERED]`,
`get_balances timeout`.

Testes: `tests/test_real_balance_isolation.py` +
`tests.test_persistence_restore.SessionPersistenceTests`.

### Overlay “analisando” sem comprar — ANALYSIS_TIMEOUT + pool httpx (2026-08-11)

Sintoma: **vários** clientes com robô ligado, pill Conectado / REAL, overlay
em “El Capo está analisando o mercado” / “Buscando melhor oportunidade”,
mas **quase nenhuma ordem**. Snapshots Redis (33 robôs `enabled=True`):
maioria com `last_analysis_result=ANALYSIS_TIMEOUT`, o resto
`NO_OPPORTUNITY_FOUND`; `last_entry` de dias atrás.

Logs do `robot-runtime` no mesmo segundo:

```
[BULLEX_POOL_STATS] path=/candles kind=PoolTimeout total=100 ativas=100 ociosas=0
[UPSTREAM_ERROR_HANDLED] path=/candles reason=timeout
```

O `bullex-service` respondia `/health` e `/account` (CPU ~1%). As requests
de candle **não saíam** do runtime — o pool httpx do processo único estava
cheio. Causa: cache de `/candles`/`/payouts` era **por usuário**; 33 robôs
M1 no fechamento da vela + `schedule_background_refresh` por hit × usuário
disparavam centenas de GETs contra `max_connections=100`. O scan sequencial
estourava o orçamento e `complete_cycle_without_trade(..., ANALYSIS_TIMEOUT)`.

Correção (2026-08-11) em `backend/main.py` (processo do `robot-runtime`):

1. Cache de mercado **do processo** (`_shared_market_cache`) — 1 fetch de
   EURUSD-OTC serve os 33 robôs.
2. Lock por `cache_key` (single-flight) no fechamento da vela.
3. Refresh de fundo **uma vez por ativo**, só com TTL restante ≤ 20s.
4. Semáforo `BULLEX_HTTP_MAX_INFLIGHT=40` no hop runtime→bullex-service.
5. Timeout de candle passa a reutilizar o cache compartilhado.

O cache compartilhado que já existia no `bullex-service` (08/08) continua
válido — ele só não era atingido quando o pool do runtime saturava.
Detalhe e validação: `PERFORMANCE_SISTEMA.md` (“Cache compartilhado no
robot-runtime”). Testes: `tests/test_runtime_shared_market_cache.py`.

Deploy: **só** `robot-runtime` com `--no-deps` (não derrubar
`bullex-service`). Restart do runtime não religa worker sozinho; publicar
`start` no Redis para quem estava `enabled=True`, ou o cliente clica
Iniciar. Trava de manutenção (`paused_by_maintenance`) permanece para
restart sem lista de recuperação.

### Recorrência 2026-08-17/18 — 14h sem operar (pool saturado de novo)

Sintoma relatado pelos clientes: “parado desde ~23h”. VPS, Nginx, gateway
(`/health` 200) e `bullex-service` (CPU ~1%) estavam ok. 31–32 robôs
`enabled=true` / `worker_running=true`, overlay em análise.

Linha do tempo (UTC; BRT = UTC−3):

| UTC | BRT | Evento |
|---|---|---|
| 17/08 18:47 | 15:47 | Redeploy gateway + runtime |
| 17/08 18–21h | 15–18h | Operando (cache hit/store, 28 sucessos na hora 21) |
| **17/08 22:11:00** | **19:11** | Última `[REAL BUY SUCCESS]` |
| **17/08 22:11:19** | **19:11** | Primeiro `PoolTimeout total=100 ativas=100` |
| 17/08 23h → 18/08 12h | 20h → 09h | 0 cache, 0 sucesso, ~4 mil `PoolTimeout`/hora |
| 18/08 12:49 | 09:49 | `docker restart robot-runtime` + PUBLISH `start` |

Causa: o pool httpx do **processo** `robot-runtime` encheu e não devolveu
conexões. O cache compartilhado (11/08) depende de GET bem-sucedido para
popular; com timeout em massa o cache zera e cada robô bate de novo no
pool — espiral. O `_call_gate` da corretora **não** era o gargalo
(quase nenhuma compra chegou no `bullex-service` depois das 22:11).

Recuperação:

```bash
# 1) listar enabled no Redis DB1 (robot:snapshot:*)
# 2) NÃO rebuild / NÃO restart bullex-service
docker restart robot-runtime
# 3) esperar [ROBOT_RUNTIME] subscribed channel=robot:cmd
# 4) para cada user enabled:
docker exec webhook-redis redis-cli -n 1 PUBLISH robot:cmd \
  '{"user_id":"<UUID>","action":"start"}'
```

Pós-restart: 32 workers, `PoolTimeout=0`, `SHARED_MARKET_CACHE_HIT` de
volta, candidatos e tentativas de ordem. Timeouts residuais
(`TimeoutError` / `ConnectTimeout`) podem aparecer no aquecimento — não
confundir com `kind=PoolTimeout ativas=100`.

**O restart das 12:49 UTC não durou.** Às 13:13 UTC o pool encheu outra vez
(~24 min); das 14h às 17h UTC de novo 0 cache / 0 compras / ~5 mil
`PoolTimeout`/hora. 37–38 snapshots em `ANALYSIS_TIMEOUT`.

Correção (18/08 ~17:15 UTC), imagem nova **só** do `robot-runtime`:

1. Pool httpx limitado a **40** conexões (igual ao semáforo in-flight).
2. `recycle_saturated_bullex_http_client`: **somente** `PoolTimeout`
   descarta o client vazado e abre outro; cooldown 15s. Log:
   `[BULLEX_HTTP_CLIENT_RECYCLE]`. Reciclar em `ConnectTimeout` com
   ativas ≥ 32 **piora** o handshake (incidente da tarde de 18/08).
3. Testes em `tests/test_runtime_shared_market_cache.py`.

Procedimento permanente: `DEPLOY_VPS.md` (restart só do runtime). Reciclar
o client em processo é a defesa para não precisar restart a cada ~20 min.

### Overlay “analisando” sem comprar — ciclo 110s + recycle agressivo (2026-08-18 tarde)

Sintoma após o deploy das 17:15 UTC: pool **não** voltou a 100/100, cache
compartilhado voltou (`SHARED_MARKET_CACHE_HIT`), mas **0**
`[REAL BUY SUCCESS]`. Overlay em “Buscando melhor oportunidade”.
Quem ligou o robô via painel via `ANALYSIS_TIMEOUT` ou `NO_OPPORTUNITY_FOUND`.

Três falhas empilhadas (não é o pool 100/100 da noite):

1. **Varredura sequencial lenta.** BOTH = ~20 ativos. Cada GET `/candles`
   em miss levava 8–12s (`ConnectTimeout`). O ciclo estoura
   `ROBOT_CYCLE_TIMEOUT_SECONDS=110` no meio da lista.
2. **Sinal achado e descartado.** Log `[ACTIVE_OK] … CALL confidence=100`
   no mesmo segundo que `[ROBOT_CYCLE_TIMEOUT] action=reschedule`.
   `complete_cycle_without_trade(..., ANALYSIS_TIMEOUT)` apagava o
   candidato (`pending_signal` / melhor ativo).
3. **Compra REAL com timeout de 5s.** As poucas ordens que chegaram a
   `POST /orders/buy-real` morriam em `BULLEX_TEMPORARY_UNAVAILABLE`
   (~7s no log) porque herdavam `BULLEX_UPSTREAM_TIMEOUT_SECONDS=5`.
4. **Recycle agressivo.** `should_recycle` fechava o client em qualquer
   timeout com ativas ≥ 32. 52 recycles em ~55 min → mais
   `ConnectTimeout` → mais recycle.

Correção (18/08 ~18:20 UTC), deploy **só** `robot-runtime --no-deps`:

| Peça | Comportamento |
|---|---|
| `should_recycle_bullex_http_client` | Só `httpx.PoolTimeout`. `ConnectTimeout` não fecha o client. |
| `ROBOT_ANALYSIS_ASSET_TIMEOUT_SECONDS=5` | Antes 18s; cabe mais ativos no orçamento de 110s. |
| `ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS=85` | Corta a lista se o scan já gastou 85s (`[ANALYSIS_SCAN_BUDGET]`). |
| `[ANALYSIS_EARLY_STOP]` | Primeiro CALL/PUT com `trade_allowed` encerra o scan. |
| `[ROBOT_CYCLE_TIMEOUT_KEEP_SIGNAL]` | Timeout do ciclo **não** apaga `pending_signal`. |
| `BULLEX_BUY_TIMEOUT_SECONDS=45` | `POST /orders/buy-real` e `/orders/buy-demo` (antes 20s). |
| `get_bullex_order_http_client` | Pool HTTP **só** da compra (8 conexões); não disputa o semáforo dos candles. |

Validar:

```bash
docker logs robot-runtime --since 10m 2>&1 | rg -c "REAL BUY SUCCESS|ANALYSIS_EARLY_STOP|ROBOT_CYCLE_TIMEOUT|BULLEX_HTTP_CLIENT_RECYCLE|BULLEX_TEMPORARY_UNAVAILABLE"
# esperado: BUY SUCCESS subindo; CYCLE_TIMEOUT caindo; RECYCLE só com kind=PoolTimeout
```

Testes: `test_connect_timeout_does_not_recycle_http_client`,
`test_should_recycle_only_on_pool_timeout`,
`test_buy_real_uses_extended_timeout`,
`test_buy_real_bypasses_market_http_semaphore`,
`test_buy_real_uses_dedicated_order_http_client`,
`test_scan_stops_after_first_trade_allowed`,
`test_worker_cycle_timeout_keeps_pending_signal`.

### Overlay marca falha mas a BullEx comprou (2026-08-18 ~15:31 BRT)

Sintoma: `[ORDER_SENT]` seguido de `[ORDER_SEND_FAILED] error=BULLEX_TEMPORARY_UNAVAILABLE`.
No `bullex-service` o mesmo instante tem `[REAL BUY SUCCESS order_id=…]` e HTTP 200.

Causa: o POST `/orders/buy-real` usava o **mesmo** pool/semáforo dos GETs
`/candles`. No segundo 0–8 da vela M1 os 38 robôs enchem as 40 vagas; a
compra espera slot, o `wait_for` de 20s estoura **depois** da corretora
já ter criado a ordem. O overlay mostra erro; a posição existe na BullEx.

Correção (18/08 ~18:45 UTC), deploy **só** `robot-runtime --no-deps`:

| Peça | Comportamento |
|---|---|
| `get_bullex_order_http_client` | Pool httpx separado, 8 conexões |
| Semáforo | Compra **não** entra em `BULLEX_HTTP_MAX_INFLIGHT` |
| `BULLEX_BUY_TIMEOUT_SECONDS=45` | Cobre a fila do `_call_gate` da corretora (máx. 3) |
| Log | `[BUY_HTTP_TIMEOUT]` se ainda estourar |

Não reiniciar `bullex-service`. Validar: `REAL BUY SUCCESS` no **runtime**
alinhado com o mesmo log no `bullex-service`; zero `ORDER_SEND_FAILED` com
`TEMPORARY_UNAVAILABLE` no mesmo segundo de um SUCCESS da corretora.

### Overlay analisa, acha 100/87 e não gasta — cache fresco carimbado STALE (2026-08-19 ~02h UTC)

Sintoma: conta de teste (e várias outras) com robô ligado, OTC, REAL,
conectado. Overlay em “Buscando melhor oportunidade”. Última ordem da
conta de marketing às **22:53 UTC de 18/08**. Depois: dezenas de
`[BEST_CANDIDATE]` + `[NO_TRADE]`, `consecutive_no_opportunity_cycles` na
casa das dezenas. A **frota não estava parada** — outras contas compravam
no mesmo segundo.

Exemplo 02:18:54 UTC:

- Conta `c3d12a6f-…` **comprou** GBPUSD-OTC CALL 100/87.
- Conta de teste, no mesmo segundo: `[ACTIVE_CACHE] reason=ANALYSIS_TIMEOUT`,
  `ASSET_SCORE allowed=False`, depois `STRATEGY_FILTER_PASS score=80`,
  `BEST_CANDIDATE GBPUSD 100/87 fallback=False`, e **`NO_TRADE`**.

Causa: `scan_local_signals` envolve `analyze_active_signal` em
`asyncio.wait_for(..., ROBOT_ANALYSIS_ASSET_TIMEOUT_SECONDS=5)`. Se o GET
estoura, o fallback lia o cache (muitas vezes o **compartilhado ainda no
TTL de 60s** — o mesmo dado que a outra conta usou para comprar) e
**forçava** `stale=True`, `from_cache=True`, `market_data_stale=True`,
`trade_allowed=False` + `STALE_MARKET_DATA`.
`candidate_meets_cycle_threshold` recusa qualquer um desses flags. O
`STRATEGY_FILTER_PASS` do recovery **não** atravessa esse portão. Resultado:
sinal perfeito no overlay, zero gasto.

Não é reincidência dos fixtures UUID, do pool httpx 100/100 nem do
`CALL_GATE_TIMEOUT`. Guard de UUID ok; containers saudáveis.

Correção (19/08 ~02:30 UTC), deploy **só** `robot-runtime --no-deps`:

| Peça | Comportamento |
|---|---|
| `resolve_analysis_timeout_cache` | Prefere cache compartilhado fresco; senão cache pessoal. `is_fresh` = candles ainda no TTL. |
| Timeout + cache fresco | Analisa o sinal **sem** carimbar STALE. Log: `[ACTIVE_CACHE] reason=ANALYSIS_TIMEOUT_FRESH`. `trade_allowed` vem da estratégia. |
| Timeout + cache expirado (janela stale) | Mantém o bloqueio antigo (`STALE_MARKET_DATA`, `trade_allowed=False`). |
| Sem cache | Continua `[ACTIVE_TIMEOUT]` como hoje. |

A estratégia clássica **não muda** — só deixa de tratar cache de 60s como
dado velho só porque o `wait_for` de 5s estourou.

Validar na conta de teste `81c49f33-3995-42ce-a78c-0fd51f90dd12`:

```bash
docker logs robot-runtime --since 10m 2>&1 | rg "81c49f33|ANALYSIS_TIMEOUT_FRESH|NO_TRADE|ORDER_SENT|ORDER_ACCEPTED"
# esperado: ANALYSIS_TIMEOUT_FRESH e/ou SIGNAL_FOUND/ORDER_SENT; NO_TRADE
# deixa de aparecer no mesmo segundo em que outra conta compra o mesmo 100/87
```

Testes: `test_analysis_timeout_keeps_fresh_cache_tradeable` e
`test_analysis_timeout_still_blocks_expired_cache` em
`tests/test_robot_market_data_resilience.py`.

### Overlay “analisando” sem comprar — WAITING_RECOVERY sob carga (2026-08-07 tarde)

Sintoma: painel mostra **El Capo analisando o mercado**, candles/payouts
chegam, mas **nenhuma ordem**. Logs:

```
[ROBOT_WORKER_BLOCKED_DISCONNECTED] user_id=...
[WAITING_RECOVERY] user_id=... failures=N
[PAYOUT_TIMEOUT] ...
[ACCOUNT_FETCH_MS] ... ms=50000
```

Causa: o lock único do bullex enfileira probes; falhas marcam
`connected=false`. O worker consultava `robot_has_recent_real_cache`, que
**exigia** `state.connected=True` — nunca liberava o ciclo. Resultado:
loop de sleep 3s sem análise de verdade (só o front buscando mercado).

Correção:

1. `robot_has_recent_real_cache` aceita snapshot/cache REAL mesmo com
   `connected=false`.
2. `resume_robot_connection_from_real_cache` reidrata o estado e segue.
3. Offline **não apaga** o último `/account` REAL bem-sucedido.
4. `CALL_GATE_TIMEOUT_SECONDS=2.5` em status/account/candles/payouts —
   se a fila estoura, devolve cache em vez de segurar 50s.
5. TTL account 45s / status 20s (menos pressão no lock).

Logs: `[ROBOT_WORKER_USING_RECENT_REAL_CACHE]`,
`[ROBOT_WORKER_OFFLINE_BYPASSED_CACHE]`, `[CALL_GATE_TIMEOUT]`,
`[ROBOT_WORKER_START_RESUMED_CACHE]`.

Testes: `tests/test_robot_worker_real_cache_resume.py`.

### Sinal visual sem ordem (`asset is not available`)

Sintoma (2026-07-26, logs de produção): overlay/narração em **“Melhor ativo
encontrado”** (USDJPY-OTC, GBPJPY-OTC, AUDUSD-OTC), countdown de entrada, e
no fim **nenhuma operação** — só rejeição:

```
[ORDER_SEND_FAILED] active=USDJPY-OTC error=... Cannot purchase an option
  (the asset is not available at the moment).
[ORDER_REJECTED] last_order_error=Nenhum ativo disponível no momento da compra.
[ENTRY_WINDOW_MISSED] ... current_candle_seconds=8.5 window_end=5
```

Causas:

1. **Payout digital ≠ canal de compra.** O scan lia payout do cache só como
   número e perdia `open_turbo`/`open_binary`. Ativo “aberto” no digital
   mas fechado no turbo (M1/M5) ia para `pending_signal` e falhava no buy.
2. **UI mentia a entrada.** Qualquer `best_candidate` em `ANALYZING`
   virava status `SIGNAL_FOUND` / texto “Melhor ativo encontrado”, mesmo
   sem `pending_signal` e com `trade_allowed=False`.

Correção (2026-07-26):

1. Cache de `/payouts` reutiliza o **payload completo** (flags de canal).
2. `apply_execution_channel_open` + revalidação pré-ordem
   (`refresh_candidate_execution_channel`).
3. Cooldown **60s** só no símbolo rejeitado
   (`UNAVAILABLE_ASSET_COOLDOWN_SECONDS`). A varredura dos **outros** ativos
   segue a cada vela — o robô não pausa a análise.
4. Overlay/narração só anunciam entrada com `pending_signal` travado.
5. Docs: `ESTRATEGIA.md`, `NARRACAO_ROBO.md`.

#### Gap residual: revalidação só-cache (corrigido em 2026-07-29)

A revalidação de 26/07 lia **apenas o cache** de `/payouts` (TTL 60s) e, quando
o cache não sabia (`None`), simplesmente mantinha o `is_open` antigo. Só que o
sinal é travado numa vela e comprado na **seguinte** — no M1 são ~40–55s de
espera, no M15 até ~15 min. Ou seja: o canal podia fechar depois do lock e o
robô continuava anunciando “Melhor ativo encontrado”.

Medição em 29/07 (24h): **35 rejeições** (`ORDER_SEND_FAILED` +
`ORDER_REJECTED` com `NO_AVAILABLE_ASSET`) contra 22 operações efetivadas.

Agora `refresh_candidate_execution_channel` busca dado **fresco**
(`force_refresh=True`) quando o cache passa de `CHANNEL_CACHE_MAX_AGE_SECONDS`,
está ausente ou não sabe a flag:

| Constante | Valor | Papel |
|---|---|---|
| `CHANNEL_CACHE_MAX_AGE_SECONDS` | 10s | Acima disso o cache não vale para decidir a compra |
| `CHANNEL_REVALIDATION_TIMEOUT_SECONDS` | 1,2s | Teto por ativo |
| `CHANNEL_REVALIDATION_BUDGET_SECONDS` | 2,0s | Teto somando todos os candidatos do ciclo |

Regras de segurança (a janela de compra é 0–5s da vela):

- Timeout, erro de rede ou orçamento estourado → **usa o cache** e segue; a
  compra nunca é abortada por falta de resposta.
- Erro de rede **não relaxa** bloqueio: se o cache já dizia fechado, continua
  bloqueado.
- Cache recente (≤10s) não gera chamada nenhuma.
- Canal fechado confirmado → `[ORDER_FALLBACK_NEXT_CANDIDATE]` para o próximo
  candidato, em vez de tomar rejeição da corretora.

Logs para acompanhar: `[CHANNEL_REVALIDATED]` (com `open`, `cached_open`,
`cache_age`), `[CHANNEL_REVALIDATION_TIMEOUT]`, `[CHANNEL_REVALIDATION_FAILED]`.

Testes: `Backend/tests/test_binary_channel_open.py`
(`PreOrderChannelRevalidationTests`).

#### Gap residual: bloqueio rotulado como “baixa qualidade” (2026-07-31)

Sintoma em produção (~18:08–18:12 UTC): IA travava **USDJPY-OTC**, anunciava a
entrada, e no segundo da compra:

```
[ORDER_FALLBACK_NEXT_CANDIDATE] skipped_active=USDJPY-OTC
  reason=Sinal bloqueado por baixa qualidade
[ORDER_REJECTED] reason=NO_AVAILABLE_ASSET
```

Causa: a revalidação marcava o canal turbo fechado (`is_open=False`,
`ACTIVE_CLOSED`, `trade_allowed=False`), mas o loop de compra consultava
**primeiro** `candidate_meets_cycle_threshold` e, ao falhar, gravava sempre
`LOW_QUALITY_SIGNAL` — sem chegar em `candidate_pre_order_block_reason`.

Correção:

1. `resolve_entry_validation_reason` — canal fechado/suspenso e stale
   reportam o código real (`ACTIVE_CLOSED`, `STALE_MARKET_DATA`, etc.).
2. Log `[ENTRY_BLOCKED]` inclui `trade_allowed`, `is_open`, confiança, payout
   e `blocked_filters`.
3. Overlay (`RobotOverlay`) e voz (`auto_trader.to_dict`) usam **somente**
   `pending_signal` (sem fallback para `best_candidate`/`last_signal`).

Testes: `test_entry_quality_gate.py` e `test_robot_status.py`.

#### Gap residual: cooldown furado + GBPJPY rejeitado em loop (2026-07-31)

Sintoma (~19:00–19:24 UTC): robô **encontrava setup**, anunciava e tentava
`buy-real` de **GBPJPY-OTC** PUT a cada ~60s; BullEx respondia 409:

```
Cannot purchase an option (the asset is not available at the moment).
[ORDER_SEND_FAILED] active=GBPJPY-OTC
[ACTIVE_COOLDOWN] until=+90s
… e na vela seguinte o mesmo ativo era comprado de novo
```

Causas:

1. **`open_turbo=True` mentia.** Catálogo `enabled`/`is_suspended` dizia
   aberto; o buyv3 rejeitava mesmo assim. Revalidação pré-ordem liberava.
2. **Cooldown de 90s furado pelo cache.** Com candles em cache,
   `analyze_active_signal` **continuava analisando** o ativo em cooldown e
   podia travar `pending_signal` de novo. Candidatos `from_cache` também
   ignoravam `active_cooldown_remaining`.
3. **`ACTIVE_COOLDOWN` não era hard block.** Entrava em `blocked_filters`
   via `apply_strategy_guard`, mas **não** em `CRITICAL_TRADE_BLOCKS` —
   `trade_allowed` seguia `True` (só penalização de score).
4. Loop de compra **não checava cooldown** antes de reenviar o mesmo símbolo.

Correção (2026-07-31):

| Peça | Comportamento |
|---|---|
| `UNAVAILABLE_ASSET_COOLDOWN_SECONDS` | **60s** (1 vela M1) — só o símbolo rejeitado; demais ativos seguem no scan |
| `mark_execution_channel_unavailable` | cooldown curto + força `open_turbo`/`open_binary`/`is_open=False` no cache de `/payouts` |
| `mark_binary_option_closed` (bullex-service) | força canal fechado no mapa turbo/binary após 409 (~60s) |
| `analyze_active_signal` / fallback / ranking | **skip duro** só desse símbolo em cooldown (sem furar com cache/`from_cache`) |
| `CRITICAL_TRADE_BLOCKS` | inclui `ACTIVE_COOLDOWN`, `ASSET_COOLDOWN`, `GLOBAL_LOSS_COOLDOWN` |
| Loop de ordem | pula esse candidato → `[ORDER_FALLBACK_NEXT_CANDIDATE] reason=ACTIVE_COOLDOWN` e tenta o próximo |

Importante: **não** existe pausa de 5 minutos na análise contínua. Cadência
continua a cada vela (ver tabela no topo deste doc). O 300s chegou a ser
aplicado só no ativo “asset not available” e foi revertido para 60s para não
atrasar oportunidade em outro par.

Logs novos: `[EXECUTION_CHANNEL_MARKED_UNAVAILABLE]`,
`[BINARY_OPTION_MARKED_CLOSED]`.

Testes: `tests/test_binary_channel_open.py`
(`MarkUnavailableAfterBrokerRejectTests`),
`tests/test_robot_market_data_resilience.py`
(`test_active_cooldown_hard_skips_even_with_cache`).

## 2. Seleção do ativo mais rentável

Em cada ciclo o robô varre os ativos disponíveis (respeitando o modo de
mercado OTC/aberto) e ranqueia os candidatos por, nesta ordem:

1. `strategy_score` (qualidade do setup técnico);
2. `confidence` (confiança calibrada da IA);
3. `payout` (rentabilidade do ativo).

Só entram candidatos que passam nos filtros mínimos do perfil de estratégia
(`min_confidence`, `min_payout` — ex.: conservador exige confiança ≥ 90 e
payout ≥ 85). O melhor candidato é logado como `[BEST_CANDIDATE_SELECTED]`.

Desde 2026-07-21, o portão final de entrada também exige que o sinal esteja
**aprovado** pela estratégia (`trade_allowed=True`, sem bloqueios críticos) —
detalhes em `ESTRATEGIA.md`. Desde 2026-07-31, o mesmo portão consulta a
**memória de padrões global** (ativo×hora×setup×direção×TF, todas as contas) e veta
contextos com histórico fraco (`PATTERN_MEMORY_WEAK`) — ver
`MEMORIA_PADROES.md`.

## 3. Operação com o usuário fora da tela

O robô roda 100% no backend (`robot_worker` em `backend/main.py`). Regras:

- Enquanto o robô estiver **ligado**, o worker chama `mark_user_active()` a
  cada tick — o usuário é considerado "ativo" mesmo com o navegador fechado,
  e as consultas de sessão/conta ao BullEx continuam fluindo normalmente.
- `is_user_active()` também considera ativo qualquer usuário com robô
  habilitado e worker vivo (`robot_tasks`), antes de expirar o heartbeat.
- A proteção de CPU para usuários realmente offline (sem robô ligado)
  permanece: paths de sessão são servidos de cache e não batem no upstream.

Limitação conhecida (antes de 2026-07-22): se o **container**
`bullex-service` reiniciar, o BullEx invalida o SSID
(`broker_invalidates_ssid`) e o usuário precisava reconectar login/senha.

**Atualização 2026-07-22:** após o primeiro connect, email/senha ficam
criptografados em `bullex_connections.encrypted_password`. O
`robot_worker` chama `try_auto_reconnect_with_saved_credentials` quando
detecta sessão caída — o robô volta sozinho com a tela fechada.
Detalhes e SQL: `BULLEX_CREDENCIAIS.md`.

Workers do gateway ainda usam restore sob demanda (`ON_DEMAND_RESTORE_ONLY`)
após restart do `backend-gateway`.

### Fantasma “analisando” sem compra após restart do gateway (2026-07-24)

Sintoma: overlay em **“El Capo está analisando o mercado”**, payouts/
candles andando no painel, mas **nenhuma ordem** (zero `WORKER_CREATED` /
`BEST_CANDIDATE` / buy).

Causa:

1. Startup reidrata `enabled=True` + status `WAITING_NEXT_CYCLE` **sem**
   subir `robot_worker` (`worker_start=false`).
2. O poll `/robot/state` tentava `auto_create`, mas `ensure_robot_worker`
   exigia `is_user_active()` — e o painel só grava `panel_heartbeat`,
   não `mark_user_active`. Resultado: worker nunca nasce.
3. O front continua buscando payouts (parece análise) enquanto o backend
   não executa ciclo de compra.
4. Em hidratação on-demand, `enabled` era forçado a `False` mas o
   `status` antigo (`WAITING_NEXT_CYCLE`) podia vazar “analisando”.

Correção:

1. `ensure_robot_worker`: se o painel está online (`is_panel_online`) e o
   robô está `enabled`, faz `mark_user_active` e sobe o worker
   (`PANEL_ONLINE_RESUME_WORKER`).
2. Usa cache de `/account` para sincronizar `connected`/`active_mode`
   antes de bloquear por desconexão.
3. `/robot/state` auto_create também chama `mark_user_active` e tenta
   resume mesmo com `BULLEX_NOT_CONNECTED` (dispara auto-reconnect).
4. On-demand hydrate com `enabled=False` também força `status=STOPPED`
   para a UI não mostrar análise fantasma.

Ação do usuário após deploy do gateway: manter a aba do painel aberta
(ou clicar **Iniciar Operação** uma vez). Com a correção, o próximo
poll deve subir o worker sozinho se o robô estava ligado.

### Placar WIN/LOSS com tela fechada (2026-07-24)

Sintoma: o robô opera offline, mas ao reabrir a aba o overlay não mostra
se foi WIN ou LOSS nem o valor; às vezes o placar parece “zerado” em
relação ao histórico.

Causas:

1. `result_display_until` era só **5s** — com a tela fechada o usuário
   perdia o flash de resultado; o worker seguia analisando.
2. Restore do gateway usava só `robot_trades` (muitas vezes vazio) e não
   reidratava o histórico canônico.

Correção (placar offline):

1. Com o painel **offline**, o fechamento marca `unseen_result=True`. O payload
   de `/robot/state` continua expondo status WIN/LOSS + lucro até o painel
   voltar e “ver” o resultado (~8s de hold via `acknowledge_unseen_result`).
   Com o painel online o flag não é marcado — senão o payload mascara o ciclo
   novo (ver §“Narração do placar”).
2. Frontend: `shouldShowResult` / narração respeitam `unseen_result`.
3. Startup/on-demand restore hidrata trades a partir de
   `robot_trade_history` quando `robot_trades` está vazio.
4. Cada resultado final chama `save_trade` além de `save_trade_history`.

### Narração do placar só na 1ª operação (2026-07-28 → corrigido em 2026-07-29)

Com painel **online**, `unseen_result` era limpo no fechamento e o status
WIN/LOSS durava 5s. Se o TTS ainda falava análise/entrada, perdia a janela
nas ops 2+.

A 1ª tentativa (28/07) esticou o display para 12s, o hold para 15s e passou a
marcar `unseen_result` sempre. **Foi revertida em 29/07** porque:

- `result_display_until` bloqueia `prepare_cycle` → o reset pós-resultado caía
  no fim da janela de análise e a compra perdia os 0–5s da vela
  (`[ENTRY_WINDOW_MISSED]`). Acerto caiu de 55,6% para 31,8%.
- Payload preso em WIN/LOSS pulava o ramo `WAITING_NEXT_CYCLE` de
  `build_robot_payload`, que limpa `best_candidate`/`last_signal` → o overlay
  anunciava entradas que o robô nunca enviava.

Solução vigente: canal `result_voice` (TTL 25s) publicado no fechamento, que
alimenta só a narração e **não** altera `status` nem o ciclo. Detalhes,
medições e constantes que não devem ser alteradas em `NARRACAO_ROBO.md` §4c.

### Reiniciar placar vs histórico (2026-07-26)

`POST /robot/reset-score` (“Reiniciar placar”) zera **apenas** o placar
visual da sessão (`wins` / `losses` / `profit` + histórico em memória).

**Não** apaga:

- `robot_trade_history` / `robot_trades`
- histórico sintético de marketing (`marketing_simulated_trades`)
- a lista em `/history`

Para zerar placar **e** histórico (ciclo novo), use
`POST /robot/reset-cycle` com `reset_score: true`.

Ver `HISTORICO.md` e `REINICIAR_PLACAR.md` (correção 2026-08-13: Redis +
cmd ao runtime + cache do painel).

## 4. Estabilidade da conexão com a corretora (anti-flap)

### Iniciar Operação desconectava a Bullex (2026-08-07)

Sintoma: usuário conectado inicia a operação e o acesso Bullex “sai”
(painel desconectado e/ou kick na sessão do app/site da corretora).

Cadeia:

1. `/account` falha ou omite saldo sob carga → start chama auto-reconnect.
2. Auto-reconnect fazia **login novo com senha** → fecha WS + revoga SSID.
3. Start falho chamava `disconnect_account` → `ACCOUNT_DISCONNECTED` no painel.

Correção: soft reconnect (SSID) antes da senha; `SessionManager.connect`
reusa sessão viva do mesmo email; start **não** marca desconectado em
falha transitória (`[ROBOT_START_BLOCKED_KEEP_SESSION]`). Detalhes e
testes em `BULLEX_CREDENCIAIS.md` (“Reconnect soft”).

Problema histórico: um único `connected: false` transitório do BullEx
(durante `SESSION-CHECK`/reconexão de websocket) fazia o gateway apagar o
cache bom, servir "desconectado" por 60s e o frontend voltava para a tela de
login da corretora, mesmo com a sessão viva.

Regra atual (`mark_session_failure` em `backend/main.py`):

- O estado "offline" (que limpa cache e força `connected: false`) só é
  aplicado após **3 falhas consecutivas** (`OFFLINE_CONFIRMATION_FAILURES`).
- Falhas isoladas entram em backoff curto e o gateway continua servindo a
  última resposta boa (com `meta.stale=true`) — o painel permanece conectado.
- Qualquer resposta `connected: true` zera o contador
  (`clear_session_backoff`).
- Desconexão explícita (`POST /bullex/disconnect`) usa `force_offline=True`
  e derruba o estado imediatamente, como antes. Credenciais criptografadas
  **permanecem** salvas (usar `DELETE /bullex/credentials` para esquecer).

### Flicker "Desconectado" ao entrar/sair de Configurações (2026-08-07)

Sintoma: pill/banner vermelho **só na UI**, sem email/saldo; robô segue
comprando. Dispara ao navegar para `/configuracoes` e voltar.

Cadeia:

1. Poll bate em backoff/offline do bullex-service.
2. Resposta `connected:false` **vazia** (sem email/saldo) substituía o cache
   bom no React Query.
3. `BullexConnectionPanel` lia `!connected` → "Desconectado".

Correção: anti-flap no bullex-service + `resolve_backoff_panel_payload` no
gateway + `preferStableBullExAccount` / status `BACKOFF` no frontend.
Detalhe em `CONFIGURACOES.md` e `BULLEX_CREDENCIAIS.md`.

### "Operação aberta" infinita sem comprar (2026-08-07)

Sintoma: overlay em **Operação aberta / Aguardando resultado** para sempre;
robô não analisa nem compra (ex.: conta marketing `victorvinicius@gmail.com`).

Causa: `waiting_result_stale` retornava `False` sempre que
`operation_in_progress=True`, então o worker nunca dava `reset_cycle_after_result`.

Correção: stale por TTL (vela + margem) **mesmo com** `operation_in_progress`,
ou imediatamente se `last_trade.result` já for WIN/LOSS/TIMEOUT/DRAW.
Testes: `tests/test_waiting_result_stale.py`.

### Parou após 2 operações sem stop win (2026-07-24)

Sintoma: robô abre 1–2 ordens (ex.: USDCAD-OTC), placar longe do stop win
(ex.: lucro ~R$84 com stop R$1000) e **para de operar**. Overlay pode
mostrar desconectado / parado.

Causa (logs `81c49f33` ~19:39 buy + ~19:51):

1. Após o trade, um blip de `/sessions/status` (`failures=1`) com a janela
   de grace de 30s já expirada.
2. `execute_robot_cycle` chamava `disconnect_account()`, que força
   **`enabled=False`** + `ACCOUNT_DISCONNECTED` — desliga o robô como se
   fosse stop, sem ser stop win/loss.
3. Sem worker/`enabled`, não há mais compras até o usuário clicar Iniciar.

Correção:

1. Anti-flap: enquanto `connection_failure_count < 3`, mantém sessão em
   grace mesmo com grace_until expirado.
2. No ciclo, blip de conexão → `WAITING_RECOVERY` + auto-reconnect,
   **sem** `disconnect_account` (só o `POST /bullex/disconnect` do usuário
   desliga de verdade).

Log novo: `[ROBOT_CONNECTION_BLIP_RECOVER]`.

### “Só 1 operação em 40 minutos e foi loss” (2026-07-29)

Sintoma relatado: o robô ficou 40 minutos com uma única entrada, perdedora.
Conta `81c49f33`, 13:37–14:17 (16:37–17:17 UTC).

Causas encontradas, em ordem de peso:

1. **Timeframe M5.** A conta estava em M5 (`cycle_minutes=5`), então há uma
   análise por vela de 5 minutos e cada operação ocupa 5 minutos. Em 40
   minutos são ~8 janelas de análise. Pelo backtest, em M5 a estratégia aprova
   1 entrada a cada ~27 velas por ativo — **1 a 2 operações em 40 minutos é o
   comportamento esperado**, não falha. Pior: em M5 o acerto medido é 48,7%
   (abaixo do empate de 53,2%). Ver `ESTRATEGIA.md` e `BACKTEST_VALIDACAO.md`.
2. **Sessão da corretora caiu no meio da janela.** Logs às 16:37:
   `[WORKER_NOT_RUNNING] block_reason=BULLEX_NOT_CONNECTED` +
   `[ROBOT_WORKER_BLOCKED_DISCONNECTED]`; o worker só foi recriado às 16:41.
   Sem worker não há varredura.
3. **A outra conta parou por stop loss, não por defeito.** Na conta `11e0b3d5`,
   às 17:11: `[STOP_LOSS_HIT] profit=-224.0` seguido de
   `[ROBOT_PAUSED_BY_STOP]`. Pausa por stop é comportamento correto e exige
   religar no painel — depois disso a conta voltou e fez 3 WIN seguidos.
4. **O loss em si.** Entrada EURUSD-OTC CALL com confiança 94 às 16:54. Com
   54% de acerto em M1 (e menos em M5), losses isolados são esperados; a
   avaliação precisa ser feita por dezenas de operações.

Como diagnosticar de novo (rápido):

```bash
# stops, pausas e problemas de worker/conexão na janela
docker logs backend-gateway --since 60m -t 2>&1 | \
  rg "STOP_LOSS_HIT|STOP_WIN_HIT|ROBOT_PAUSED_BY_STOP|WORKER_NOT_RUNNING|BLOCKED_DISCONNECTED|USER_BACKOFF|ORDER_REJECTED"

# operações por usuário (não confundir contas!)
# robot_trade_history: filtre sempre por user_id
```

**Atenção ao ler logs:** o nível efetivo do container é `WARNING`. Marcadores
de ciclo como `[ROBOT TICK]`, `[CYCLE_START]` e `[ENTRY_ALLOWED]` são `INFO` e
**não aparecem** — a ausência deles não significa que o robô parou. Use os
`WARNING` acima e a tabela `robot_trade_history` para reconstruir a janela.

### Status connected sem `active_mode` (bloqueio / desligamento indevido)

Sintoma A: painel mostra **Conectado** / **Pronta para o robô** (via
`GET /bullex/account` com saldo REAL), mas **Iniciar Operação** não liga o
robô. Em Configurações pode aparecer `BULLEX_TEMPORARY_UNAVAILABLE` após
reconnect com timeout.

Sintoma B (2026-07-24): usuário inicia a operação, o overlay fica ativo por
alguns segundos e **volta para parado/inativo** sozinho.

Causa comum: `GET /sessions/status` às vezes devolve `connected: true` com
`active_mode: null` (`get_balance_mode()` não resolve o balance_id).

- No start: o gateway tratava `None` como “não REAL” / “não conectado”.
- No poll de status (`GET /bullex/status`): o gateway chamava
  `stop_robot_worker` + `require_real_mode` sempre que
  `active_mode != "REAL"` — e `None != "REAL"` é verdadeiro. Cada poll
  (a cada poucos segundos) **desligava** o robô recém-ligado.

Correção (2026-07-24):

1. `reconcile_robot_connection_from_payload` — se status está connected e
   o modo veio vazio, resolve via `GET /account` (ou mantém o modo já
   conhecido no estado do robô).
2. `AutoTrader.sync_connection` — não apaga `active_mode` conhecido quando
   o status omite o modo e a sessão continua connected.
3. `finalize_connected_status_payload` + `_bullex_status_impl` — só
   desligam o robô com PRACTICE/DEMO **explícito**. Com `active_mode=None`,
   preservam o modo/estado (`[ACTIVE_MODE_OMITTED_STATUS]`).
4. `POST /robot/start` — não aborta em modo omitido; confirma REAL via
   `/account`. PRACTICE explícito retorna `409` + `REAL_MODE_NOT_CONFIRMED`
   (`ok: false`) para o frontend não mostrar “operação iniciada” falso.
5. Frontend — códigos `BULLEX_TEMPORARY_UNAVAILABLE`,
   `BULLEX_NOT_CONNECTED` e `REAL_MODE_NOT_CONFIRMED` ganham mensagem
   amigável em português.

Logs úteis: `[ACTIVE_MODE_RESOLVED_FROM_ACCOUNT]`,
`[ACTIVE_MODE_KEPT_FROM_STATE]`, `[ACTIVE_MODE_OMITTED_STATUS]`,
`[REAL_MODE_NOT_CONFIRMED]`.

### Sintoma B² — `/account` com REAL sem saldo desligava o robô (2026-07-24 noite)

Mesmo com o status omitindo o modo de forma segura, o poll de
`GET /bullex/account` podia devolver `active_mode=REAL` **sem**
`balance_real`. `build_real_account_contract` falhava com
`REAL_BALANCE_NOT_DETECTED` e o endpoint chamava `require_real_mode` +
`stop_robot_worker` — robô ligado por poucos segundos e voltava parado.

Correção:

1. `build_real_account_contract` — `active_mode=REAL` confirma a conta mesmo
   com saldo omitido (`[REAL_BALANCE_OMITTED]`).
2. `should_stop_robot_for_account_contract` — só desliga em PRACTICE/DEMO
   explícito; robô já em REAL sobrevive a contrato incompleto
   (`[ACCOUNT_CONTRACT_KEEP_ROBOT]`).
3. `AutoTrader.update_config` — **não** desliga mais o robô ao gravar
   parâmetros (ligar/desligar só via `start`/`stop`).
4. Start: se o saldo REAL vier omitido, tenta o cache
   (`[REAL_BALANCE_FROM_CACHE]`).

Ver também `NARRACAO_ROBO.md` (pronúncia El Capo / El Kápo).

## 5. Sessão de suporte (admin dentro da conta de outro usuário)

Fluxo: `POST /admin/impersonations` cria a sessão e grava o cookie
`elcapo-support`/`elcapo-impersonation`. Enquanto o cookie é válido, o admin
enxerga a conta do usuário-alvo em modo **somente leitura**.

Endpoints permitidos durante a sessão de suporte (`support_safe_paths` em
`backend/main.py`):

- `GET /me/access`, `GET /admin/support-sessions/current`
- `GET /bullex/account`, `GET /bullex/status`, `GET /bullex/balance`
- `GET /robot`, `GET /robot/state`, `GET /robot/history`, `GET /robot/stats`
  — leitura do painel do robô (o dashboard de suporte precisa desses GETs)
- `GET /robot/ws-ticket` — canal ao vivo do estado do robô (somente leitura;
  o WS `/ws/robot-state` autentica pelo ticket já emitido para o usuário-alvo)
- `POST /bullex/connect`, `POST /bullex/disconnect`, `POST /bullex/reconnect`
  (suporte pode reconectar a corretora do cliente)
- `GET /bullex/credentials`, `DELETE /bullex/credentials`
- `DELETE /admin/impersonations/current` (encerrar a sessão)

Tudo o mais (ligar/desligar robô, configuração, reset, ordens, websocket de
gráfico) continua bloqueado com `403 IMPERSONATION_READ_ONLY` e auditado.

### Frontend (AppShell) na sessão de suporte

`POST /admin/impersonations` + `window.location.assign("/dashboard")` recarrega
o painel como o lead. O dashboard, Configurações e Histórico chamam
`useLiveTradingData()`.

| Peça | Comportamento em suporte |
|------|--------------------------|
| `LiveTradingDataProvider` | **Monta** (senão o hook explode: “precisa ser usado dentro de LiveTradingDataProvider”) |
| Overlay flutuante do robô | **Oculto** (`shouldShowRobotOverlay` = false) |
| Overlay “aguardando aprovação” | **Não aparece** — mesmo lead pendente sem `grant_access` |
| Aba Robô em Configurações | **Oculta** (`canUseRobotControls(false)`) |
| Rotas | Só `/dashboard`, `/configuracoes`, `/history` |

Helpers em `frontend/src/lib/adminPresentation.ts`:
`shouldMountLiveTradingProvider`, `shouldShowRobotOverlay`,
`shouldTreatSessionAsInactive`. Em `/admin/*` o provider continua **desligado**
(performance). Testes: `adminPresentation.test.ts`.

## 7. Configuração de valores (entrada, stop win, stop loss)

Regras do painel e de `POST /robot/config` (atualizado em 2026-07-31):

| Campo | Regra |
|---|---|
| Timeframe | M1 / M5 / M15 — persiste no estado e no SQLite/`robot_user_settings`. |
| Mercado | OTC / OPEN / BOTH — define a fila de ativos analisados. |
| Valor de entrada | Mínimo **R$ 5**. Zero e valores abaixo de 5 são rejeitados. Default: 5. |
| Stop Win / Stop Loss | Dois modos: **por valor** (R$ ≥ 5) ou **por operações** (≥ 1 WIN/LOSS do placar). Ver `STOP_WIN_LOSS.md`. |
| Gale (steps) | 1–10 no `RobotConfigUpdate`. Valores > 1 **não** rejeitam mais o payload inteiro. |

O botão **Config** do overlay foi removido; a configuração operacional fica no
diálogo **Iniciar operação** (e em Configurações → Robô).

No frontend, os campos usam buffer de digitação (`MoneyInput`) para não
resetar o valor enquanto o usuário apaga/redigita números pequenos.

### Persistência da escolha no diálogo “Iniciar operação”

Problema histórico (até 2026-07-24): ao escolher timeframe ≠ M1 ou mercado ≠
OTC (e também entrada/stops/gale), o poll de `/robot/state` sobrescrevia o
rascunho local e o diálogo voltava para M1/OTC em poucos segundos. Além disso,
`martingale_steps > 1` fazia o Pydantic rejeitar **todo** o `POST /robot/config`,
então entrada/stops/timeframe não chegavam ao backend.

Correções:

1. `rememberRobotSettingsFromState` ignora campos vazios do poll e **não**
   sobrescreve edições locais pendentes (`localEditPending`).
2. `StartOperationDialog` só hidrata o draft ao **abrir** (não a cada mudança
   de `settings` vinda do poll) e chama `markRobotSettingsSynced` após
   `robotConfig` OK.
3. Backend: `martingale_steps` aceita 1–10 (`le=10`).

Arquivos: `frontend/src/lib/robotSettings.ts`,
`frontend/src/components/StartOperationDialog.tsx`,
`backend/auto_trader.py`.

## 8. Overlay durante a análise contínua

Enquanto o robô monitora (sem ordem aberta), o overlay **não** mostra
“Próxima análise em mm:ss”. Exibe **“Buscando melhor oportunidade”**.

Narração padrão ao **iniciar** e ao analisar (TTS, sem MP3 legado):

1. “El Capo está analisando o mercado.”
2. “Identificando uma oportunidade de operação lucrativa.”

Detalhes: `NARRACAO_ROBO.md` e `ANALISE_CONTINUA.md`.

O backend ainda envia `next_cycle_at` internamente para o worker; o painel
só esconde o timer nessa fase.

### Saldo insuficiente (aviso na tela)

Se a Bullex rejeitar a compra com `Insufficient funds` (mesmo com
`balance=None` no cache), o runtime **para** o robô
(`INSUFFICIENT_BALANCE`) em vez de só marcar `ORDER_REJECTED` e
continuar o ciclo. Overlay: título **Saldo insuficiente** + pedido de
depósito ou redução da entrada. Log:
`[ORDER_REJECTED_INSUFFICIENT_FUNDS]`. Detalhes: `OVERLAY_ROBO.md` §5.

### Placar sumindo no painel

Causas comuns (2026-08-11):

1. Snapshot Redis expirava (TTL curto) e o gateway em modo `external`
   devolvia `auto_trader` local com placar 0.
2. Badges WIN/LOSS sem fundo — contraste fraco no dashboard.

Correção: TTL 600s; `rehydrate_score_from_persistence_if_blank` no
snapshot/HTTP; `/robot/state` prefere snapshot do runtime; badges com
fundo. Ver `OVERLAY_ROBO.md` §6.

## 9. Histórico de mudanças

- **2026-08-21 (compra ~45s antes do fechamento da vela)**
  - Sintoma: usuário via ordem no meio da vela M1 (~45s antes do close),
    fora da janela 0–8s de abertura.
  - Causa: `refresh_entry_window` em cache gravava `server_time` estimado
    sem renovar âncora; cada poll compostava o elapsed e adiantava o
    relógio da janela de compra.
  - Correção: `server_time_sampled_at`, `persist_server_clock=False` no
    path estimado, resample absoluto a cada 45s. Ver §1 e
    `ANALISE_CONTINUA.md` §3. Deploy: `robot-runtime --no-deps`.
- **2026-08-19 ~02h UTC (analisa 100/87 e não gasta — STALE em cache fresco)**
  - Sintoma: overlay “buscando”; conta de teste sem ordem desde 22:53 UTC
    18/08; outras contas compravam o mesmo GBPUSD CALL 100/87 no mesmo
    segundo. Log: `ANALYSIS_TIMEOUT` + `BEST_CANDIDATE` + `NO_TRADE`.
  - Causa: timeout de 5s no `analyze` carimbava `STALE_MARKET_DATA` mesmo
    com cache compartilhado ainda no TTL de 60s. O portão recusa stale/
    `from_cache`. Recovery não atravessa.
  - Correção: `resolve_analysis_timeout_cache`; cache fresco vira
    `ANALYSIS_TIMEOUT_FRESH` e permanece `trade_allowed` da estratégia.
    Cache expirado continua STALE.
  - Testes: `test_analysis_timeout_keeps_fresh_cache_tradeable`,
    `test_analysis_timeout_still_blocks_expired_cache`.
- **2026-08-18 (Shift+O gerava histórico e o overlay do El Capo ficava 0-0)**
  - Sintoma: conta marketing clicava em Gerar histórico do placar; o painel
    Shift+O listava as operações, mas WIN/LOSS do robô flutuante não mudava.
  - Causa: `sync_marketing_display_to_robot` atualizava só a memória do
    gateway. Em `ROBOT_RUNTIME_MODE=external` o overlay lê Redis do runtime,
    que seguia 0-0. Ver `MARKETING_SIMULATION.md` e `PLACAR_OVERLAY.md`.
  - Correção: `publish_marketing_score_to_overlay` + cmd `apply_score` no
    runtime; front aplica o placar no cache na hora.
- **2026-08-18 (hydrate do runtime falhava com `quote` indefinido)**
  - Sintoma: após recreate do `robot-runtime`, `action=start` não criava
    worker (`SESSION_RESTORE_SKIPPED reason=robot_disabled`) e o log tinha
    `NameError: name 'quote' is not defined` em `load_trade_history`.
  - Correção: `from urllib.parse import quote` em `robot_persistence.py`.
- **2026-08-17 (Acessar conta do lead quebrava o dashboard)**
  - Sintoma: admin clica “Acessar conta”, o console mostra
    `[AUTH USER CHANGED]` e em seguida
    `useLiveTradingData precisa ser usado dentro de LiveTradingDataProvider`.
  - Causa: `mountLiveTrading = !impersonating && …` desmontava o provider
    assim que `/me/access` marcava `impersonating: true`, mas o dashboard
    (e Configurações) continuam chamando o hook. Lead pendente ainda caía
    no overlay de “aguardando aprovação”.
  - Correção: provider monta na sessão de suporte; overlay do robô e
    bloqueio de inativo não. `GET /robot/ws-ticket` entra em
    `support_safe_paths`. Ver §5.
- **2026-08-13 (Reiniciar placar não zerava no overlay)**
  - Sintoma: clique em Reiniciar placar e o WIN/LOSS voltava ao valor antigo.
  - Causa: mode=external — gateway zerava só memória local; Redis/runtime
    mantinham o placar; front fazia `await refetch` do snapshot stale.
  - Correção: `publish_robot_control_snapshot` + cmd `reset_score` ao
    runtime; `applyRobotMutationToCache` no overlay; rehydrate respeita
    `stop_reset_at`. Ver `REINICIAR_PLACAR.md`.
- **2026-08-13 (Iniciar/Parar operação demorava no overlay)**
  - Sintoma: botão Parar/Iniciar atrasava vários segundos; às vezes ficava em
    Parar com `enabled=false` e `worker_running=true` no Redis.
  - Causa: em mode=external o painel lia snapshot Redis stale; o stop do
    runtime não republicava após sair de `robot_tasks`; o front fazia
    `await refetch` e usava `enabled || worker_running`.
  - Correção: `publish_robot_control_snapshot` no start/stop; runtime
    publica stop antes do cancel; front aplica payload da mutação e usa só
    `enabled` para o botão. Ver `INICIAR_PARAR_OPERACAO.md`.
  - Testes: `tests/test_robot_control_snapshot.py`,
    `robotCountdown.test.ts` (`isRobotOperationRunning`).
- **2026-08-19 ~19:45 UTC (conta marketing em Mercado aberto, 0 ops o dia todo)**
  - Sintoma: overlay ativo em “Buscando melhor oportunidade”, forex aberto,
    `enabled=true`, M1 conservative. Última ordem **18/08 22:53 UTC**.
    `consecutive_no_opportunity_cycles=248`. A frota em OTC comprava.
  - Causa: `market_mode=OPEN` varria só pares sem `-OTC`. `/payouts`
    timeoutava (`payout=None`, `PAYOUT_UNAVAILABLE`). O fallback
    `OPEN_MARKET_NO_CHANNEL_FALLBACK_OTC` só rodava com canal **conhecido
    fechado**; cache vazio (`known=0`) deixava o robô no aberto para sempre.
  - Correção: após 3 ciclos sem entrada com `PAYOUT_UNAVAILABLE` (ou cache
    de canal vazio), o ciclo OPEN varre OTC. Log
    `reason=payout_unavailable_on_open_assets`. Ver `MERCADO_ABERTO.md`.
  - Testes: `tests/test_market_mode_assets.py`.
- **2026-08-18 ~23:40 UTC (conta `victorvinicius@gmail.com` sem ordem após o
  deploy do guard — não foi reincidência do `_call_gate`)**
  - Sintoma: dono da conta marketing (`user_id`
    `81c49f33-3995-42ce-a78c-0fd51f90dd12`) relatou que o El Capo não
    operava mais desde o ajuste preventivo das 23h05. Última ordem real
    dessa conta: **22:53 UTC** (LOSS −20). Sessão Bullex **conectada**
    (`active_mode=REAL`, `/sessions/status` 200).
  - O sistema **não** estava parado: no mesmo intervalo outras contas
    tiveram `[SIGNAL_FOUND]` + `[ORDER_SENT]`/`[ORDER_ACCEPTED]` (ex.:
    GBPJPY-OTC PUT e EURUSD-OTC CALL). Worker da conta marketing estava
    ligado (`enabled=true`, `worker_running=true`) e gerava
    `[BEST_CANDIDATE]` (até confidence 100 / payout 87) seguido de
    `[NO_TRADE]`.
  - Causa desta conta (não da frota): `market_mode=BOTH` com sessão
    forex aberta varre ~20 ativos. Com `[ACTIVE_TIMEOUT]` / payout
    timeout frequentes nesta sessão, o scan ia a 45–90s
    (`[ANALYSIS_SCAN_BUDGET]`), o candidato ficava abaixo do portão
    (`trade_allowed` / `strategy_score` / `STALE_MARKET_DATA`) e
    `resolve_cycle_entry_candidate` devolvia `None` → `[NO_TRADE]`.
    Contas que operaram no mesmo horário estavam em **OTC** + M1 +
    conservative. Ver `MERCADO_ABERTO.md`.
  - Correção operacional (autorizada pelo dono da conta): `PUBLISH
    robot:cmd stop` → `market_mode=OTC` em `robot_user_settings` +
    `robot_states.state`/`state_json` → `PUBLISH start`. Worker
    confirmado com `ANALYSIS_ASSET_QUEUE market_mode=OTC` (10 pares
    `*-OTC`). Não alterou a estratégia clássica.
  - Depois do ajuste o robô analisa de novo a cada vela. Ciclos sem
    entrada (`NO_PATTERN_FOUND` / score 62–66) são o portão
    conservative, não pane — a mesma estratégia das contas que
    operaram quando o setup passou de 90+ com `trade_allowed`.
- **2026-08-18 ~22:18 UTC (zero sinais em 40+ min para TODOS os usuários —
  usuários de teste no Supabase de produção sufocando o `_call_gate`)**
  - Sintoma: usuário reportou "faz 37 minutos e minha conta não teve uma
    operação". Investigação achou que **nenhum** usuário (nem um só, dos
    ~41 reais) tinha `[SIGNAL_FOUND]`, `[BEST_CANDIDATE]` nem sequer um
    candidato rejeitado com filtro nos últimos 40 minutos —
    `last_rejection_reason=NO_PATTERN_FOUND` com `blocked_filters=[]` em
    100% dos ciclos, o que na prática significa "nenhum ativo produziu
    candidato algum", nem para aprovar nem para rejeitar.
  - Diagnóstico: `[ANALYSIS_SCAN_BUDGET]` disparava em 100% dos ciclos
    (nunca terminava naturalmente nem parava por `ANALYSIS_EARLY_STOP`).
    Um único ativo (`ROBOT_ANALYSIS_ASSET_TIMEOUT_SECONDS=5s` nominal)
    levava **41–47 segundos** para dar `[ACTIVE_TIMEOUT]` — ordem de
    grandeza maior que o timeout configurado. `[SHARED_MARKET_CACHE_HIT]`
    estava em **zero** apesar de dezenas de usuários pedindo os mesmos
    ativos. Teste direto e isolado (`call_bullex_service` na mesma
    sessão Python do processo, mesmo usuário real, mesmo `endtime`)
    respondia em 0.2s — ou seja, corretora/rede saudáveis; o problema só
    aparecia sob concorrência real dos workers.
  - Causa raiz: a tabela `robot_states` no Supabase de **produção** tinha
    35 registros de usuários de teste/fixture (`user-window-0`,
    `user-analysis-error`, `user-demo`, etc. — nomes não-UUID, claramente
    de `tests/`) com `enabled=true`, rodando workers completos e
    disputando o `_call_gate` (limite de 3 chamadas simultâneas à
    corretora, ver "Gargalo do `_call_gate`" em `PERFORMANCE_SISTEMA.md`)
    lado a lado com os ~39 usuários reais. Isso quase **dobrou** a carga
    concorrente (72 workers em vez de 39) exatamente no momento em que o
    cache compartilhado mais precisava respirar, e o cache nunca
    conseguia ficar "quente" o suficiente para servir hit — toda
    requisição de `/candles`/`/payouts` tinha que brigar pelo gate=3 do
    zero. Restart do `robot-runtime` (tentado antes de achar a causa) NÃO
    resolveu — o problema reapareceu em 1–3 minutos, confirmando que não
    era um leak transitório do processo e sim carga estrutural.
  - Violação da LEI 13 (Isolamento de Ambientes): dados de teste nunca
    deveriam ter `enabled=true` persistido no banco de produção.
  - Correção: `PUBLISH robot:cmd {"action":"stop"}` para os 35 usuários de
    teste + `PATCH robot_states SET enabled=false` no Supabase para eles
    não subirem de novo sozinhos num próximo restart/deploy.
  - Validação pós-correção (~22:30–22:45 UTC, 39 usuários reais): 96
    `[BEST_CANDIDATE]` e 119 `[SHARED_MARKET_CACHE_HIT]` em 4 min (antes:
    zero de ambos por 40+ min); latência média de candle caiu de
    "nunca completa" para ~1.2–2.3s; 0 `[ENTRY_WINDOW_MISSED]`. Frequência
    de `[SIGNAL_FOUND]` continuou baixa (0–1 a cada poucos minutos) — isso
    é esperado da estratégia `conservative` em mercado calmo, não é mais
    sintoma de falha sistêmica.
  - Ação preventiva pendente: os registros tinham `created_at=2026-07-21`
    (não foram escritos por uma execução recente da suíte de testes) —
    são fixtures antigas, provavelmente de um seed manual ou execução de
    teste isolada contra o Supabase de produção há quase um mês, que
    ficaram esquecidas com `enabled=true`. Não há isolamento dev/prod no
    Supabase hoje: `.env.example` já aponta para o MESMO projeto usado em
    produção (viola a LEI 13). Recomendado: (1) criar um projeto Supabase
    separado para testes/dev, (2) auditar a tabela por outras fixtures
    esquecidas (`bullex_connections`, `robot_trade_history`, etc.).
  - **Ação preventiva IMPLEMENTADA em 2026-08-18 ~23h05 UTC (defesa em
    profundidade, independe de limpar o Supabase de novo no futuro):**
    `_handle_command` em `backend/robot_runtime_main.py` agora recusa
    `action=start`/`ensure` para qualquer `user_id` que não seja um UUID
    válido (`_is_valid_account_user_id`, usa `uuid.UUID(...)`), logando
    `[ROBOT_WORKER_REJECTED_INVALID_USER_ID]` e retornando sem chamar
    `ensure_robot_worker` nem hidratar a persistência. Todo usuário real
    chega ao runtime com `user_id` = UUID da sessão Supabase Auth; nenhum
    fluxo legítimo de `/robot/start` usa outro formato. Mesmo que
    fixtures voltem a ficar `enabled=true` no Supabase de produção
    (seed manual, teste rodado sem querer contra prod, etc.), o
    robot-runtime **não sobe worker real para elas** — o pior caso vira
    um log de aviso, não uma disputa pelo `_call_gate`. `stop`/
    `reset_score`/`disconnect` continuam liberados para qualquer formato
    de id (não criam worker novo). Testes:
    `tests/test_robot_worker_user_id_guard.py` (7 casos, cobre aceitar
    UUID real, rejeitar ids de fixture/vazio/não-string, e não regressão
    das ações que não criam worker). Requer redeploy do `robot-runtime`
    (`scripts/deploy-robot-runtime.sh`) para entrar em vigor.
  - **Deploy do guard (2026-08-18 ~23h09–23h11 UTC) reproduziu DOIS
    problemas já documentados, confirmando que valia a pena corrigi-los
    de vez:**
    1. Achou **mais um** fixture `enabled=true` em produção que não tinha
       sido pego na limpeza das 22h18: `user-analysis-window-missed`,
       com `last_connected_at` de ~1 min antes (estava rodando de
       verdade). Desabilitado (`PATCH enabled=false`). Prova de que a
       limpeza manual pontual não é suficiente — o guard no runtime é a
       defesa que realmente fecha o buraco independente de quantas
       fixtures ainda existam ou apareçam.
    2. O script `scripts/deploy-robot-runtime.sh` publicou os 39 `start`
       logo depois do health check (~24s pós-`up`), mas o runtime só
       terminou de restaurar 286 estados e assinar `robot:cmd` aos ~36s
       — os 39 `PUBLISH` se perderam (0/39 workers confirmados no log),
       reproduzindo o incidente das 21h10 mesmo com o script "seguro".
       Corrigido republicando manualmente após confirmar o subscribe
       (38/38 workers confirmados) e, na sequência, atualizando o
       próprio script para esperar
       `[ROBOT_RUNTIME] subscribed channel=robot:cmd` no log antes de
       publicar (em vez de um sleep fixo) e republicar sozinho quem não
       confirmar `WORKER_CREATED`/`WORKER_ALREADY_RUNNING` — ver
       `DEPLOY_VPS.md`.
    - Validação final pós-fix (23h13, 38 usuários reais): `WORKER_CREATED`
      37 + `WORKER_ALREADY_RUNNING` 1 = 38/38; `PUBLISH` de teste com
      `user_id` fake gerou `[ROBOT_WORKER_REJECTED_INVALID_USER_ID]` sem
      criar worker; 48 `[BEST_CANDIDATE]` e 52 `[SHARED_MARKET_CACHE_HIT]`
      em 2 min; candles resolvendo em 0–32ms via cache; 1 único
      `[ENTRY_WINDOW_MISSED]` isolado; 0 `[SIGNAL_FOUND]` (esperado —
      estratégia `conservative` sem oportunidade no momento, não é
      sintoma de falha).
- **2026-08-18 ~21:00 UTC (fix das 19:20 "validado" mas NUNCA rodou em
  produção — deploy com nome de projeto compose errado)**
  - Sintoma: usuários continuaram reportando "ainda não está pegando
    operações" mesmo depois do fix de `ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS`
    (entrada anterior). `[ENTRY_WINDOW_MISSED]` recorrendo na mesma taxa de
    antes (~22–30/hora), como se o fix não existisse.
  - Causa raiz: o deploy foi feito em dois comandos separados —
    `docker compose build robot-runtime` (sem `-p elcapooneline`, project
    name inferido do diretório = `backend`) seguido de
    `docker compose -p elcapooneline up -d --force-recreate --no-deps
    robot-runtime`. O `build` sem `-p` criou uma imagem órfã
    `backend-robot-runtime:latest`, nunca usada por nada. O `up` com
    `-p elcapooneline` reaproveitou a imagem `elcapooneline-robot-runtime:latest`
    **já existente** (do build de 18/08 tarde, código antigo) porque `up`
    sem `--build` não rebuilda quando a imagem já existe — recria o
    container, mas com o binário de sempre. Sem erro, sem warning, container
    saudável, health check verde. Confirmado com
    `docker exec robot-runtime grep ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS
    /app/backend/main.py` → `85.0` (devia ser `45.0`).
  - Efeito colateral: ao religar os workers via `PUBLISH robot:cmd` logo
    após o `--force-recreate` (~15s depois), a maioria das mensagens se
    perdeu — o processo ainda não tinha assinado o canal Redis. O snapshot
    Redis (`robot:snapshot:*`, TTL 600s) ainda mostrava `worker_running=true`
    de execução anterior, mascarando o problema (falso positivo na
    verificação pós-deploy). De 35 `start` publicados, só ~1 pegou; 34
    ficaram sem worker por >1h sem nenhum log de erro.
  - Correção: rebuild com `docker compose -p elcapooneline build
    robot-runtime` (nome de projeto correto no MESMO namespace da imagem
    usada pelo `up`); confirmar `docker inspect robot-runtime --format
    '{{.Config.Image}}'` e o valor da constante dentro do container antes de
    considerar o deploy concluído. Religar workers usando a lista de
    `enabled=true` do Supabase (`robot_states`), não o snapshot Redis, e
    confirmar por `[WORKER_CREATED]`/`[WORKER_ALREADY_RUNNING]` no log — não
    pelo snapshot. Ver checklist em `DEPLOY_VPS.md`.
  - Validação pós-correção (~21:14–21:40 UTC, 41 usuários reais
    confirmados com worker rodando): `ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS`
    confirmado em `45.0` dentro do container; `[ENTRY_WINDOW_MISSED]` caiu
    para 3 em 15 min (era ~22–30/hora); 9 `WIN` + 5 `LOSS` completados no
    snapshot; 0 `PoolTimeout`; containers estáveis (`RestartCount=0`).
  - Lição: **sempre** validar um deploy lendo o código/constante de dentro
    do container (`docker exec ... grep`), nunca só pelo `docker compose
    ps`/health check. Health check verde não prova que o código novo está
    rodando.
- **2026-08-18 ~19:20 UTC (sinal achado, ordem nunca disparava —
  `ENTRY_WINDOW_MISSED` em massa)**
  - Sintoma: após o fix das 18:45, usuários reportaram "ainda não está
    pegando operações". Logs mostravam `[SIGNAL_FOUND]` seguido minutos
    depois de `[ENTRY_WINDOW_MISSED]` / `[SIGNAL_EXPIRED]` — o robô achava
    um candidato válido mas nunca chegava a comprar.
    `current_candle_seconds` no momento do descarte: 8, 9, 26, 27, 28, 35,
    36, 37, 48 (a janela de compra é só 0–8s, ver §1).
  - Causa: `ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS=85` (do fix das 18:20) foi
    calibrado contra `ROBOT_CYCLE_TIMEOUT_SECONDS=110`, não contra a janela
    real de entrada. A análise começa entre os segundos 5–20 da vela
    (`ANALYSIS_WINDOWS`) e só pode comprar nos primeiros 0–8s da vela
    SEGUINTE (`ENTRY_WINDOWS`). Pior caso: início no segundo 20 só tem
    `(60-20)+8=48s` até a janela fechar. Com 85s de orçamento o scan
    terminava rotineiramente 25–40s DENTRO da vela seguinte — a janela já
    tinha fechado antes mesmo de existir um candidato pronto.
  - Correção: `ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS` reduzido para **45s**
    (cabe no pior caso de 48s com folga para montar o candidato e disparar
    a compra).
  - Testes: `test_scan_budget_fits_inside_entry_window_worst_case` (trava a
    relação matemática `SCAN_BUDGET < (TIMEFRAME - ANALYSIS_WINDOW_END) +
    ENTRY_WINDOW_END` para não regredir).
  - Validação pós-deploy (~19:30–19:46 UTC, 40 robôs ativos): 0
    `[ENTRY_WINDOW_MISSED]` (antes: 22 em 60 min). 3 sinais achados, 3
    ordens enviadas, 3 rejeitadas pela BullEx por "asset is not available"
    em GBPJPY-OTC — confirmado batendo 1:1 com `bullex-service`
    (`409 Conflict`, mesmo motivo), ou seja, rejeição real de mercado, não
    bug do runtime.
  - Nota: `[ROBOT_CYCLE_TIMEOUT]` (110s) continua ocorrendo com frequência
    parecida à de antes (~80 em 16 min entre 40 usuários) por overhead fora
    do scan (checagens de sessão/conta, revalidação de canal). Isso reduz a
    frequência de novas análises por usuário, mas **não** derruba mais um
    sinal já encontrado (`should_keep_pending_on_cycle_timeout`). Fica como
    possível otimização futura, não bloqueia operação.
- **2026-08-18 ~18:45 UTC (BullEx comprou, overlay falhou)**
  - Sintoma: `ORDER_SEND_FAILED` / `TEMPORARY_UNAVAILABLE` no runtime;
    `[REAL BUY SUCCESS]` no `bullex-service` no mesmo instante.
  - Causa: POST `/orders/buy-real` no pool/semáforo dos candles; timeout 20s.
  - Correção: client HTTP de ordem (8 conexões), fora do semáforo, timeout 45s.
  - Testes: `test_buy_real_bypasses_market_http_semaphore`,
    `test_buy_real_uses_dedicated_order_http_client`.
- **2026-08-18 ~18:20 UTC (analisa, acha sinal, não compra)**
  - Sintoma: após o recycle das 17:15, cache ok, 0 compras. `ACTIVE_OK`
    seguido de `ROBOT_CYCLE_TIMEOUT` no mesmo segundo; buy-real 5s →
    `BULLEX_TEMPORARY_UNAVAILABLE`.
  - Causa: scan sequencial 8–12s/ativo estoura 110s; recycle em
    `ConnectTimeout` piorava o handshake; timeout de compra curto.
  - Correção: recycle só `PoolTimeout`; early-stop; timeout por ativo 6s;
    buy-real 20s; timeout do ciclo preserva `pending_signal`.
  - Testes: `test_scan_stops_after_first_trade_allowed`,
    `test_worker_cycle_timeout_keeps_pending_signal`,
    `test_connect_timeout_does_not_recycle_http_client`,
    `test_buy_real_uses_extended_timeout`.
- **2026-08-18 tarde (voltou a parar ~24 min após o restart da manhã)**
  - Mesma espiral: `PoolTimeout` 13:13 UTC, cache zerado, 0 compras até o
    deploy 17:15 UTC. Código: reciclar client httpx + `max_connections=40`.
  - Testes: `test_pool_timeout_recycles_http_client` e cooldown.
- **2026-08-17/18 (14h sem ordem — pool httpx 100/100 de novo)**
  - Sintoma: clientes desde ~23h BRT 17/08; overlay analisando; API no ar.
  - Causa: `PoolTimeout ativas=100` no `robot-runtime` a partir de 22:11 UTC;
    cache compartilhado esvaziou; compras não chegavam no `bullex-service`.
  - Recuperação: `docker restart robot-runtime` + PUBLISH `start` no Redis
    DB1 (sem rebuild, sem derrubar sessões). Ver §1 recorrência e
    `DEPLOY_VPS.md`.
- **2026-08-11 (saldo insuficiente + placar no overlay)**
  - Compra REAL com `Insufficient funds` agora para o robô e exibe aviso
    claro (não fica em loop “analisando”).
  - Placar: reidratação da persistência + contraste dos badges + TTL
    snapshot 600s. Testes:
    `tests/test_insufficient_funds_and_score.py`,
    `robotPresentation.insufficient.test.ts`.
- **2026-08-11 (ANALYSIS_TIMEOUT em massa — robô “analisa” e não opera)**
  - Sintoma: dezenas de contas ligadas, overlay em análise, quase zero
    ordens. Runtime: `[BULLEX_POOL_STATS] PoolTimeout total=100 ativas=100`.
  - Causa: cache de candles/payouts só por usuário + refresh por hit;
    33 robôs M1 esgotavam o pool httpx do `robot-runtime` antes do
    bullex-service. Ciclo virava `ANALYSIS_TIMEOUT`.
  - Correção: cache compartilhado + single-flight + refresh coalescido +
    semáforo 40 no runtime. Ver §1 e `PERFORMANCE_SISTEMA.md`.
  - Testes: `tests/test_runtime_shared_market_cache.py`.
- **2026-08-07 (noite — Iniciar Operação derrubava a Bullex)**
  - Sintoma: ao iniciar, painel marcava desconectado / kick na corretora.
  - Causa: auto-reconnect com login novo + `disconnect_account` no start.
  - Correção: SSID antes da senha; reuse de WS vivo; start sem
    `ACCOUNT_DISCONNECTED` em falha transitória. Ver §4 e
    `BULLEX_CREDENCIAIS.md`.
- **2026-08-07 (revisão completa do sistema — ordem travada em loop de 404)**
  - Sintoma: uma ordem com `last_trade.result=TIMEOUT` cujo `order_id` nunca
    resolve na corretora (ex.: sessão do usuário offline) era reconsultada em
    **todo** `GET /robot/state` (poll de 2,5-8s) — em produção, 29 chamadas em
    15 min só para essa ordem, sempre 404, disputando o `_call_gate` do
    `bullex-service` junto com candles/payouts de todo mundo.
  - Correção: `reconcile_timeout_last_trade` (`backend/main.py`) agora tem
    backoff de 30s por `(user_id, order_id)` e desiste definitivamente
    (`[TIMEOUT_RECONCILE_GIVEN_UP]`) após ~1h de tentativas falhas. Não afeta
    ordens que ainda podem resolver — só espaça as tentativas.
  - Mesma revisão: cache de mercado compartilhado entre usuários para
    `/candles`/`/payouts` no `bullex-service`, reduzindo a pressão sobre o
    `_call_gate` (à época `BULLEX_MAX_CONCURRENT_API_CALLS=1`). Ver
    `PERFORMANCE_SISTEMA.md` ("Gargalo do `_call_gate` da Bullex").
    Testes: `tests/test_timeout_reconcile_backoff.py`,
    `tests/test_shared_market_data_cache.py`.
  - **Fase 3 (mesmo dia):** isolamento `SessionGlobals`/ContextVar +
    mutáveis de `BullexAPI` por instância; default do gate subiu para
    **3**. Ver §1 "Fase 3" e `PERFORMANCE_SISTEMA.md`.
- **2026-08-07 (tarde — “analisando” fantasma / WAITING_RECOVERY sob carga)**
  - Sintoma: overlay em análise, candles/payouts no painel, mas **zero
    compras** e workers em `ROBOT_WORKER_BLOCKED_DISCONNECTED` /
    `WAITING_RECOVERY`. Lock global do bullex enfileirava /account até 50s.
  - Causa: `robot_has_recent_real_cache` exigia `connected=True` (inútil
    quando o worker está desconectado); offline limpava cache REAL.
  - Correção: cache REAL desbloqueia o worker (`resume_robot_connection_from_real_cache`);
    `CALL_GATE_TIMEOUT` 2,5s em status/account/candles/payouts; TTL de
    account/status aumentados. Ver §1 e §4.
- **2026-08-07 (banner REAL_BALANCE no Dashboard com robô ligado)**
  - Sintoma: overlay operando, mas Dashboard com
    “Não foi possível confirmar o saldo REAL” e saldo `-`.
  - Causa: poll `/bullex/account` devolvia `ok=false` mesmo com
    `ACCOUNT_CONTRACT_KEEP_ROBOT` (robô REAL).
  - Correção: `recover_real_account_contract_for_poll` +
    `finalize_account_contract_with_poll_recovery` restauram contrato
    REAL (cache/memória/estado do robô) antes da resposta ao front.
    Ver §1 sintoma banner no Dashboard.
- **2026-08-07 (noite+ — Confirmar e iniciar / saldo None)**
  - Sintoma (marketing e clientes): pop-up abre, **Confirmar e iniciar**
    não liga o robô; logs
    `ROBOT_START_BLOCKED_INSUFFICIENT_BALANCE ... balance=None` +
    `SESSION_NOT_FOUND`.
  - Causa: `memory_account_fallback` recuperava contrato REAL sem saldo;
    o start tratava `balance=None` como “sem depósito”.
  - Backend: reconecta antes do fallback; memory só com saldo > 0; saldo
    desconhecido → `409 BULLEX_NOT_CONNECTED` + disconnect.
    Logs: `[ROBOT_START_BLOCKED_BALANCE_UNKNOWN]`,
    `[REAL_BALANCE_START_MEMORY_SKIPPED]`.
  - Front: botão do `StartOperationDialog` com `stopPropagation`, checagem
    de `enabled` na resposta e mensagem clara de reconexão.
  - Testes: `test_real_robot_start_still_blocks_when_reconnect_fails`,
    `test_start_rejects_stale_memory_fallback_without_balance`.
  - Ver `MARKETING_SIMULATION.md`.
- **2026-08-07 (REAL_BALANCE_NOT_DETECTED sob alta demanda)**
  - Isolamento de `balance_id` multi-sessão (`balance_id_owner`), timeout em
    `get_balances`, recover de modo no `/account` e start via cache REAL.
    Corrige travamento em massa no erro de saldo real com muitos usuários.
    Ver §1 sintoma `REAL_BALANCE_NOT_DETECTED`.
- **2026-08-07 (madrugada — diálogo do flutuante fecha no mesmo toque)**
  - Sintoma: **Configurações → Robô** inicia; o botão **Iniciar Operação**
    do robô flutuante “não faz nada” (principalmente no celular).
  - Causa: `setStartDialogOpen(true)` síncrono no `onClick` montava o
    Radix Dialog a tempo do mesmo gesto tocar o overlay e dismissar.
  - Front: `scheduleDialogOpen` (`lib/scheduleDialogOpen.ts`); grace de
    dismiss no `StartOperationDialog`; `interactionLocked` no
    `RobotOverlay`; Dialog/Overlay em `z-[100]`. Ver `OVERLAY_ROBO.md`.
- **2026-08-07 (noite — Iniciar Operação sem POST /robot/start)**
  - Após parar o robô, o poll às vezes marcava Bullex `connected:false`
    (backoff / `offline_user`) enquanto o bullex-service seguia
    `SESSION-ALIVE`. O overlay e o diálogo **bloqueavam no front** e o
    clique não gerava `POST /robot/start`.
  - Front: overlay, diálogo e Configurações → Robô **não bloqueiam mais**
    por `connected` do poll; o start no backend valida. Aviso amarelo no
    diálogo se o painel ainda não confirma a sessão.
  - `isBullExConnected`: status `CONNECTED` prevalece sobre account stale
    com `connected:false`.
  - Gateway: em backoff sem cache útil, **não inventa** `connected:false`
    (`[BACKOFF_BYPASS_NO_CACHE]`); grace de account REAL também em
    `/account`; `last_successful` entra no grace; `inactive_user` tenta
    memory/grace antes de `offline_user`.
- **2026-08-07 (noite — overlay vs Configurações + menu lateral)**
  - Overlay: remove bloqueio local por `STOP_WIN_HIT`/`STOP_LOSS_HIT` que
    impedia abrir o diálogo enquanto Configurações → Robô ainda iniciava
    (API limpa stop órfão / exige reinício do placar).
  - `canStartRobotOperation`: não desabilita o botão só por `connected`
    (toast no clique, igual ao painel).
  - Menu lateral desktop: `position: fixed` + `margin-left` no conteúdo —
    o aside não rola mais com a página. Ver `SHELL_LAYOUT.md`.
- **2026-08-07 (noite — marketing start 403 STOP_* + start que “não pega”)**
  - Conta marketing com placar/histórico do Shift+O levava
    `POST /robot/start` a **403 STOP_WIN_HIT / STOP_LOSS_HIT**
    (`daily_stop_reason` + placar sincronizado).
  - Start marketing agora auto-chama `reset_score` quando o stop bloquearia
    (`[MARKETING_AUTO_RESET_SCORE_ON_START]`). Ver `MARKETING_SIMULATION.md`.
  - `clear_session_backoff` no início do start (antes do status/account) —
    evita `BULLEX_NOT_CONNECTED` / `offline_user` com Bullex viva.
  - `get_user_robot_state` não rehidrata por cima do estado em memória
    (antes forçava `enabled=False` e a UI voltava para parado após start 200).
- **2026-08-07 (página não carregou após deploy)**
  - Separar `showRobot` (overlay) de `mountLiveTrading` (provider): dashboard
    e Configurações chamam `useLiveTradingData()` e quebravam com
    “This page didn't load” enquanto `/me/access` ainda carregava.
- **2026-08-07 (Iniciar Operação travado após Stop Win)**
  - Sintoma: após Stop Win/Loss, **Reiniciar placar** zerava o placar mas o
    status `STOP_*_HIT` permanecia; `POST /robot/start` respondia
    `RESET_CYCLE_REQUIRED` / `STOP_WIN_HIT` e o botão “não ia”.
  - `reset_score` agora limpa o status de stop; start libera status órfão
    com placar já zerado; toast/mensagens amigáveis no overlay.
  - Overlay do robô só com `grant_access` confirmado; provider de dados
    monta também durante o carregamento do acesso.
  - Ver `STOP_WIN_LOSS.md`.
- **2026-07-31 (entrada anunciada sem compra)**
  - `resolve_entry_validation_reason`: `ACTIVE_CLOSED` / stale não mascaram
    mais como “baixa qualidade” na compra.
  - Overlay e voz só com `pending_signal`. Ver §1 e `NARRACAO_ROBO.md`.
- **2026-07-31 (stop por valor ou por operações)**
  - Stop Win/Loss aceitam modo `money` (R$) ou `operations` (WINs/LOSSes do
    placar). Overlay: botão Config removido (config só em Iniciar operação).
    Ver `STOP_WIN_LOSS.md` e §7.
- **2026-08-03 (caderno global de padrões)**
  - Memória unificada: `robot_pattern_memory_global` +
    `unify_all_into_global` soma todos os cadernos pessoais sem apagá-los.
    Portão e API usam `scope=global`. SQL:
    `migration_pattern_memory_global.sql`. Ver `MEMORIA_PADROES.md`.
- **2026-07-31 (memória de padrões)**
  - Portão `PATTERN_MEMORY_WEAK`: aprende WIN/LOSS por
    ativo×hora×setup×direção×timeframe e bloqueia contextos fracos (≥12 ops e
    WR &lt; 52%). SQL: `migration_pattern_memory.sql`. API:
    `GET /robot/pattern-memory`. Ver `MEMORIA_PADROES.md`.
- **2026-07-29 (suporte/resistência e diagnóstico de frequência)**
  - Bloqueio crítico `SR_ZONE`: entrada com o preço na zona de nível é
    reprovada. Ver `ESTRATEGIA.md`.
  - Diagnóstico de “1 operação em 40 minutos”: M5 + queda de sessão + pausa por
    stop loss. Ver §4.
  - `tests/test_auto_trader.py` deixava um worker real girando após os casos que
    passam por `robot_state` conectado, o que travava a suíte inteira. Agora a
    classe patcheia `ensure_robot_worker` no `setUp` e cancela workers no
    `asyncTearDown`. Com a suíte destravada apareceram 78 falhas **antigas**
    (testes desatualizados, ex.: esperam `cycle_minutes=5` quando a cadência
    contínua usa 1) — não são regressão e ficam pendentes de limpeza.
- **2026-07-29 (revalidação de canal na compra)**
  - `refresh_candidate_execution_channel` passa a buscar `/payouts` fresco
    quando o cache tem mais de 10s, com teto de 1,2s por ativo e 2,0s por
    ciclo. Corta as rejeições `NO_AVAILABLE_ASSET` que geravam “anuncia e não
    opera”. Ver §1.
- **2026-07-29 (placar falado sem afetar o ciclo)**
  - Canal `result_voice` (TTL 25s) para a narração do placar; reverte display
    12s → 5s, hold 15s → 8s e `unseen_result` sempre → só offline. Corrige
    queda de acerto e “entrada anunciada sem execução”. Ver §3 e
    `NARRACAO_ROBO.md` §4c.
- **2026-07-26 (reset placar sem apagar histórico)**
  - `POST /robot/reset-score` volta a zerar só o placar visual; mantém
    `robot_trade_history`, `robot_trades` e simulações marketing.
    Limpeza de histórico fica em `POST /robot/reset-cycle`. Ver §3.
- **2026-07-24 (noite, placar offline)**
  - WIN/LOSS com tela fechada: `unseen_result` mantém overlay/valor ao
    voltar; restore hidrata de `robot_trade_history`. Ver §3 e
    `HISTORICO.md`. (Nota: entre 24–26/07 o reset-score limpava o
    histórico; revertido em 2026-07-26.)
- **2026-07-24 (noite, User balance not found)**
  - Robô achava setup (ex. GBPUSD-OTC) mas a compra REAL falhava com
    `User balance not found` após recover de modo; UI ficava em
    “analisando”. `ensure_real_balance_id_for_buy` + retry
    (`REAL_BUY_RETRY_BALANCE`). Ver §1 sintoma de compra.
- **2026-07-24 (noite, stop após start + pronúncia)**
  - `/bullex/account` com REAL sem `balance_real` desligava o robô
    segundos após o start. Contrato REAL sem saldo agora é ok; só
    PRACTICE/DEMO explícito chama `require_real_mode`. `update_config`
    deixa de forçar `enabled=False`. TTS fala “El Kápo” (UI: El Capo).
    Ver §4 sintoma B² e `NARRACAO_ROBO.md`.
- **2026-07-24 (tarde, narração start)**
  - Áudio MP3 legado (`robot-voiceover.mp3`) deixava de falar “pode
    demorar mais de 5 minutos”. Start passa a TTS: “El Capo está
    analisando…” + “Identificando uma oportunidade…”. Ver `NARRACAO_ROBO.md`.
- **2026-07-24 (tarde, narração)**
  - Overlay/narração na análise: “El Capo está analisando o mercado” +
    “Identificando uma oportunidade de operação lucrativa”; rodapé
    “Buscando melhor oportunidade” (sem timer de próxima análise).
  - Ver `NARRACAO_ROBO.md`.
- **2026-07-24 (tarde, análise contínua)**
  - Remove cooldown legado 5/15/45 min. A IA varre o mercado a cada vela
    do timeframe; compra só em 0–5s; expiração M1/M5/M15 inalterada.
  - Ver `ANALISE_CONTINUA.md` e §1 atualizado.
  - Empate Bullex (`equal`) deixava de ser tratado como LOSS: agora é DRAW
    (profit 0, sem gale, placar intacto).
  - Após 1–2 ops o robô “analisava” sem comprar: `get_balance_mode()` vinha
    inválido e a compra caía em `ACCOUNT_MODE_NOT_REAL`. Buy agora recupera
    REAL via `force_real_mode` antes de falhar. Ver §1b.
- **2026-07-24 (manhã, operação volta a inativa)**
  - Poll de `/bullex/status` com `active_mode=null` chamava
    `stop_robot_worker` + `require_real_mode` e desligava o robô segundos
    após o start. Agora só PRACTICE/DEMO explícito desliga; modo omitido
    preserva o worker (`ACTIVE_MODE_OMITTED_STATUS`). Start confirma REAL
    via `/account` quando o status omite o modo. Ver §4 sintoma B.
- **2026-07-24 (noite, overlay)**
  - Botão Iniciar Operação do robô ficava disabled enquanto Configurações
    → Robô iniciava: `canStart` exigia `!loginState.isPending`, e o
    auto-reconnect (`useEnsureBullexSession`) cancelava o
    `completeBullExLogin` ao re-rodar o effect. Corrigido com
    `canStartRobotOperation` (sem bloquear por pending) + effect sem
    dep de `isPending` + complete ao detectar connected.
- **2026-07-24 (noite, sessão)**
  - Corrige bloqueio de Iniciar Operação com conta “conectada”: status
    Bullex sem `active_mode` passava a resolver o modo via `/account` e
    deixa de apagar REAL conhecido no estado do robô. Ver §4.

- **2026-07-24 (noite)**
  - Mínimo operacional unificado: entrada, stop win e stop loss ≥ **R$ 5**
    (`MIN_REAL_ENTRY` / `MIN_STOP_MONEY` / `ENTRY_VALUE_MIN` / `STOP_MONEY_MIN`).
- **2026-07-24**
  - Corrige reset visual para M1/OTC no diálogo/painel: poll não sobrescreve
    edições locais; draft do “Iniciar operação” só reinicia ao abrir.
  - `POST /robot/config` aceita `martingale_steps` 1–10 (antes `le=1`
    rejeitava o payload e nada da operação era aplicado).
- **2026-07-22 (tarde)**
  - Credenciais Bullex salvas criptografadas (`encrypted_password`) +
    auto-reconnect do `robot_worker` com tela fechada. Ver
    `BULLEX_CREDENCIAIS.md`.
  - Painel Configurações → Robô completo (mercado, stops, gale, salvar).
- **2026-07-22**
  - Cadência revalidada: M1→5 min / expira 1m; M5→15 min / expira 5m;
    M15→45 min / expira 15m. `cycle_minutes` re-derivado do timeframe em
    todo agendamento de ciclo.
  - Correção crítica: worker não dorme mais `seconds_until_entry` de uma
    vez (perdia a janela 0–3s). Agora poll fino + janela ampliada para 0–5s
    (`robot_worker_entry_wait_seconds`). Sintoma relatado: “pegou 2
    operações e parou”.
- **2026-07-21 (noite)**
  - Timer do overlay: countdown contínuo no cliente (fim das travadas em
    “Próxima análise em xx:xx” / “Entrada em …”).
  - Stop Win / Stop Loss: liberados valores ≤ 5; input não reseta ao digitar.
  - Valor de entrada: mínimo antigo de 2 removido — só bloqueia ≤ 0.
  - Backend: `MIN_REAL_ENTRY = 0.01`.
- **2026-07-21 (tarde)**
  - **Bug do loop de login na corretora**: após `POST /bullex/connect`
    bem-sucedido, o gateway ainda servia `GET /bullex/status` e
    `GET /bullex/account` do cache antigo com `connected: false` — o painel
    voltava para a tela de login. Corrigido com
    `reset_session_connection_cache()` no início do connect e
    `seed_connected_session_cache()` após sucesso.
  - Após connect com robô já ligado, o worker é retomado automaticamente
    (`ROBOT_WORKER_RESUMED_AFTER_CONNECT`).
  - Restore de SSID inválido no `bullex-service` não marca mais
    `offline_until` (não trava o usuário por 60s antes de poder logar de
    novo).
  - Sessão de suporte: liberados também `GET /robot/stats` e
    `GET /admin/impersonations/current`.
- **2026-07-21**
  - `mark_session_failure`: offline só após 3 falhas consecutivas; novo
    parâmetro `force_offline` para desconexão explícita.
  - `robot_worker`: chama `mark_user_active` por tick (robô opera fora da
    tela); `is_user_active` prioriza worker vivo antes de expirar heartbeat.
  - `support_safe_paths`: liberados `GET /robot`, `GET /robot/state`,
    `GET /robot/history` e `GET /bullex/balance` para o painel de suporte.
  - Testes: suítes `test_gateway_fast_fallback`, `test_supabase_resilience`,
    `test_admin_management`, `test_admin_secure_foundation`,
    `test_robot_status`, `test_robot_history`, `test_persistence_restore`
    executadas — nenhuma regressão nova (6 falhas pré-existentes no código
    original permanecem idênticas antes e depois).
