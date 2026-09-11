# Testes e qualidade (backend)

Estado da suíte automatizada do backend (`/opt/elcapo/backend/tests`) e
registro da limpeza de débito técnico feita em **2026-08-07**, durante a
revisão completa do sistema (ver [`PERFORMANCE_SISTEMA.md`](./PERFORMANCE_SISTEMA.md)
e [`ROBO_E_SUPORTE.md`](./ROBO_E_SUPORTE.md)).

## Como rodar

```bash
cd /opt/elcapo/backend
PYTHONPATH=. python3 -m unittest discover -s tests
# ou um arquivo específico:
PYTHONPATH=. python3 -m unittest tests.test_auto_trader -v
```

Se o Python do host não tiver as dependências (`ModuleNotFoundError: fastapi`),
use o venv já provisionado (`/root/Backend/.venv/bin/python3`) ou rode dentro
do container `backend-gateway`.

**Estado atual: 727 testes nos arquivos de runtime/scan desta rodada; suíte
completa no venv.** Contagem de referência anterior: 725. Em 19/08 ~02h
entraram +2 testes de timeout+cache fresco. Em 18/08 tarde
entraram +8 testes (recycle só PoolTimeout, early-stop, keep pending,
timeout de compra, orçamento do scan, pool HTTP dedicado da ordem). Em
18/08 ~19h20 entrou +1 teste (`test_scan_budget_fits_inside_entry_window_worst_case`).
Em 18/08 ~23h05 entraram +7 testes em
`tests/test_robot_worker_user_id_guard.py` (guarda de UUID no
`robot-runtime` — ver abaixo). Em **2026-08-19 ~02h** entraram +2 testes
em `tests/test_robot_market_data_resilience.py`
(`test_analysis_timeout_keeps_fresh_cache_tradeable` e
`test_analysis_timeout_still_blocks_expired_cache`): timeout do
`analyze` com cache compartilhado ainda no TTL **não** pode carimbar
`STALE_MARKET_DATA`; cache já expirado continua bloqueado. Ver
`ROBO_E_SUPORTE.md` (“cache fresco carimbado STALE”).

Em **2026-08-18** (manhã/tarde/noite) testes em
`tests/test_runtime_shared_market_cache.py` (reciclar o client httpx em
`PoolTimeout`, cooldown, `max_connections` = semáforo, **não** reciclar
`ConnectTimeout`, timeout de `buy-real`). Em
`tests/test_robot_market_data_resilience.py`: early-stop do scan,
preservar `pending_signal` no timeout do ciclo, e (~19h20) travar
`ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS` matematicamente dentro da janela real
de compra (`SCAN_BUDGET < (TIMEFRAME - ANALYSIS_WINDOW_END) +
ENTRY_WINDOW_END`) — ver `PERFORMANCE_SISTEMA.md` e `ROBO_E_SUPORTE.md`
para o incidente que motivou. Em `tests/test_robot_worker_user_id_guard.py`
(~23h05): `_is_valid_account_user_id` e `_handle_command` em
`backend/robot_runtime_main.py` recusam `action=start`/`ensure` para
`user_id` que não seja UUID (bloqueia fixtures de teste — tipo
`user-demo` — de subirem worker real mesmo que fiquem `enabled=true` no
Supabase de produção; `stop`/`reset_score`/`disconnect` continuam livres
para qualquer formato, sem regressão para os testes existentes que usam
ids como `user-runtime-stop`). Ver `ROBO_E_SUPORTE.md` §"2026-08-18
~22:18 UTC" e `PERFORMANCE_SISTEMA.md` §`_call_gate`.

Suíte completa via `pytest` no venv do projeto (`/opt/elcapo/backend/.venv`):
9 falhas pré-existentes e não relacionadas (mensagens de saldo
insuficiente, flag `enabled` do snapshot de controle e tenant-scoping do
financeiro) continuam pendentes de correção — não bloqueiam este fix e
não têm relação com o robô de análise/entrada nem com o guard de
`user_id` acima (confirmado rodando a suíte antes e depois da alteração:
mesmas falhas, nenhuma nova).

```bash
PYTHONPATH=. /root/Backend/.venv/bin/python3 -m unittest \
  tests.test_runtime_shared_market_cache \
  tests.test_robot_market_data_resilience -v
```

Uma linha `ERROR:asyncio:Task was destroyed but it is pending!` pode aparecer
**depois** do `OK` — é ruído de encerramento do event loop (uma task de
`robot_worker` cancelada ao fim do processo de teste), não afeta a contagem
nem indica falha real.

## Contexto: por que havia 87 falhas

O código de produção evoluiu (cadência contínua, execução contrarian, modo
REAL-only, etc. — ver histórico de cada `.md` específico) mas boa parte dos
testes continuou presa nas expectativas antigas. Cada falha foi classificada
em duas categorias antes de qualquer correção:

- **Caso A — teste desatualizado**: o comportamento em produção está correto
  e documentado; o teste que precisa mudar.
- **Caso B — bug real**: o comportamento em produção está errado; o código
  de produção precisa mudar (o teste estava certo em apontar o problema).

## Caso A — testes desatualizados (81 falhas)

### Cadência contínua (substituiu cooldown fixo)

Ver [`ANALISE_CONTINUA.md`](./ANALISE_CONTINUA.md). Nomes de status mudaram
(`STATUS_WAITING_NEXT_CYCLE`/`STATUS_WAITING_NEXT_CANDLE_ENTRY` →
`STATUS_WAITING_ENTRY`) e o `cycle_minutes` padrão passou a ser **1**, não 5.
Afetou `tests/test_auto_trader.py`, `tests/test_phase36_continuous_cycle.py`,
`tests/test_persistence_restore.py`.

### Execução contrarian (inversão da direção analisada)

Ver [`ESTRATEGIA.md`](./ESTRATEGIA.md). `last_trade.direction` guarda a
direção **invertida** em relação à análise técnica;
`last_trade.analyzed_direction` guarda a direção original analisada. Testes
que comparavam `direction` contra o sinal técnico esperavam `CALL`/`PUT`
trocados. Afetou `tests/test_phase36_continuous_cycle.py`.

### Modo REAL-only

O sistema não aceita mais `active_mode="PRACTICE"` (`buy-demo`); todo mock
precisa simular `active_mode="REAL"` e `buy-real`. Afetou
`tests/test_auto_trader.py`, `tests/test_phase36_continuous_cycle.py`,
`tests/test_supabase_resilience.py`, `tests/test_gateway_fast_fallback.py`.

### Sincronização de conexão no `robot_start`

`robot_start`/`_robot_start_impl` hoje reconsulta `/sessions/status` **e**
`/account` para revalidar a sessão persistida antes de liberar o ciclo —
mocks antigos de `call_bullex_service` não cobriam essas chamadas extras ou
assumiam que a última chamada era sempre a mesma rota. Corrigido buscando a
chamada específica em `await_args_list` em vez de assumir a posição.

### `force_refresh` em `call_bullex_service`

Algumas chamadas internas (ex.: `fresh_asset_open_for_active`) passaram a
enviar `force_refresh=True`. Mocks de `fake_bullex` sem `**_kwargs`
quebravam com `TypeError: unexpected keyword argument 'force_refresh'`.

### `entry_value` padrão 2.0 → 5.0

Testes com valor de entrada hardcoded em 2.0 foram atualizados para 5.0.

### Mínimo de entrada BRL R$ 5 / USD US$ 1, sem teto (2026-08-16)

`POST /robot/config` recusa só valores abaixo do mínimo da moeda.
Valores altos (80, 250, …) são aceitos. Casos em
`tests/test_entry_currency_limits.py`.

### US$ 1 não pode virar US$ 5 (2026-08-17)

`normalizeRobotSettings` no painel não usa mais mínimo 5 para dólar.
`POST /robot/config` aceita `entryValue: 1` quando a conta está em USD.
Ver `VALOR_ENTRADA.md`.

### `resolve_order_expiration` — proteção contra `server_time` obsoleto

A função usa `max(server_timestamp, sent_at_utc.timestamp())` para nunca
aceitar um horário de servidor mais antigo que o próprio envio da ordem
(evita expiração calculada com timestamp de época 1970 vindo de um mock/cache
obsoleto). Testes que fixavam `server_time` bem no passado e esperavam esse
valor no `expiration_source`/`server_timestamp_at_send` foram corrigidos para
esperar a fonte `sent_at_minimum` e um timestamp próximo de "agora".

### Resultado literal em vez de `STATUS_RESULT_RECEIVED`

`finish_trade` grava o resultado literal (`"WIN"`/`"LOSS"`/`"DRAW"`) no
histórico, não mais uma constante `STATUS_RESULT_RECEIVED`. Afetou
`tests/test_phase36_continuous_cycle.py`, `tests/test_robot_history.py`.

### Colunas/config persistidas no Supabase

`timeframe` é uma coluna real persistida — o teste
`test_extract_robot_settings_keeps_only_supabase_settings_columns` assumia
incorretamente que era descartada.

### Recuperação automática de fallback de conexão

A recuperação automática (documentada em `ROBO_E_SUPORTE.md` §1) hoje confirma
`ok=True`/`connected=True` em cenários que antes fabricavam um estado de
backoff. Testes em `tests/test_supabase_resilience.py` e
`tests/test_gateway_fast_fallback.py` foram ajustados para pré-semear cache
REAL e esperar o contrato de sucesso, refletindo o comportamento real.

### Cache de candles diferenciado por `count`/`endtime`

`test_candles_cache_ignores_count_and_endtime_for_same_asset_timeframe` tinha
premissa **errada**: um cache que ignora `count`/`endtime` serviria menos
velas do que o pedido para um `count` maior — isso seria um bug, não uma
otimização. Reescrito como
`test_candles_cache_differentiates_by_count_and_endtime`, validando que
params diferentes geram 2 chamadas upstream e params idênticos reaproveitam
o cache (mesmo comportamento que `cached_market_response` já tem no
backend-gateway).

### `raw_direction_score` sem teto vs `confidence` limitado a 100

Confluências técnicas fortes podem produzir um score aditivo bruto acima de
100 (ex.: 102) — isso é intencional (`raw_direction_score` não é uma
probabilidade, ver docstring de `_calibrate_confidence`), enquanto
`confidence` é o mesmo valor limitado a 100 para uso como percentual.
`test_analyze_signal_uses_backup_classic_raw_confidence` foi corrigido para
comparar `confidence == min(100, raw_direction_score)`.

### CORS e datas relativas

`CORS_ALLOWED_METHODS` hoje inclui `PUT`. Um teste de segmentação de
estatísticas usava uma data fixa (`2026-07-17`) que saiu da janela de 7 dias
do histórico conforme o tempo passou — trocado por data relativa a `now()`.

## Caso B — bugs reais encontrados e corrigidos (2 bugs)

### 1. `clear_session_backoff` apagando o próprio cache recém-escrito

Ver detalhamento em [`ROBO_E_SUPORTE.md`](./ROBO_E_SUPORTE.md) §4 ("Gap
residual: `clear_session_backoff` apagando o próprio cache"). Resumo: dentro
de `call_bullex_service`, uma checagem `connected: true` bem-sucedida gravava
seu próprio cache e, na linha seguinte, `clear_session_backoff` apagava essa
mesma entrada — anulando o throttle de 10s de `/sessions/status`/`/account` e
forçando uma chamada upstream extra a cada checagem "conectado".

**Correção**: separada a responsabilidade em
`reset_session_failure_counters` (zera só contadores, mantém cache) — usada
após uma resposta bem-sucedida — e `reset_session_connection_cache` (zera
contadores **e** descarta cache) — reservada para login/connect novo.
Arquivo: `backend/main.py` (~linha 1173, ~3540). Testes:
`tests/test_supabase_resilience.py`.

### 2. Vazamento de estado global entre testes (isolamento)

`tests/test_bullex_rate_limit_proxy.py`, classe
`TestAutoReconnectRespectsRateLimit`: o `setUp` setava
`main.bullex_login_rate_limited_until` (gate global de rate-limit de login)
sem `tearDown` correspondente. Ao rodar a suíte completa via
`unittest discover`, esse estado vazava para testes executados depois no
mesmo processo (`tests/test_gateway_fast_fallback.py`,
`tests/test_supabase_resilience.py`), causando `BULLEX_REQUESTS_LIMIT_EXCEEDED`
inesperado e `KeyError: 'connected'`.

**Correção**: adicionado `tearDown` que reseta
`bullex_login_rate_limited_until` e `bullex_auto_reconnect_at`. Sem impacto em
produção (é puramente um bug de isolamento entre testes), mas mascarava
falhas reais em outros arquivos quando a suíte inteira rodava junta —
por isso a validação final **sempre** deve rodar `unittest discover -s tests`
completo, não arquivo por arquivo isoladamente.

## Regra para novas falhas

Ao investigar uma falha de teste, sempre classificar antes de tocar em
qualquer código:

1. Ler a documentação (`.md`) relacionada à funcionalidade testada.
2. Confirmar no código de produção qual é o comportamento **atual e
   intencional**.
3. Se o código de produção está certo e documentado → Caso A, corrigir o
   teste.
4. Se o código de produção diverge do que está documentado/é o
   comportamento esperado → Caso B, corrigir o código de produção (e então
   atualizar o `.md` correspondente, nunca o inverso).
5. Rodar a suíte **completa** (`unittest discover`), não só o arquivo
   alterado — isolamento entre testes é frágil (estado global em
   `backend/main.py`) e falhas cruzadas só aparecem na suíte inteira.

## Histórico

- **2026-08-18** — Dia civil em Brasília: `tests/test_brasilia_time.py` e
  testes de `dateRange` / `dashboardDailyStats` / `marketingDemoSettings`.
  Ver [`DATAS_BRASILIA.md`](./DATAS_BRASILIA.md).
- **2026-08-07** — Limpeza completa de débito técnico: 87 falhas → 0 (716
  testes). 81 falhas de Caso A (testes desatualizados) e 2 bugs reais de
  Caso B corrigidos (ver seções acima). Trabalho dividido em dois lotes:
  `tests/test_auto_trader.py` + `tests/test_phase36_continuous_cycle.py`
  (66 falhas) e os 6 arquivos restantes (`tests/test_persistence_restore.py`,
  `tests/test_robot_history.py`, `tests/test_gateway_fast_fallback.py`,
  `tests/test_supabase_resilience.py`, `tests/test_session_worker_lifecycle.py`,
  `tests/test_confidence_calibration.py`).
