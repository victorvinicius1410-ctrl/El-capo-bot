# Performance do sistema (gateway + admin + polling)

Diagnóstico e mitigações para lentidão geral do SaaS sob carga de lançamento
(muitos leads no painel). Atualizado em **2026-08-07**.

## Veredito operacional

A VPS (CPU/RAM/disco) e a latência do Supabase (~50–60 ms) **não** são o
gargalo. O sintoma vem do **backend-gateway** saturado: 1 processo uvicorn +
polling agressivo dos clientes + `GET /admin/dashboard` com N queries.

Evidência típica antes das mitigações desta rodada:

| Sinal | Valor observado |
|-------|-----------------|
| `/health` local | 0,5–4,5 s (deveria ser <50 ms) |
| `GET /auth/session` | ~85/min (endpoint #1) |
| `GET /robot/state` | poll de 1 s quando ativo |
| CPU do gateway | ~20–25% contínuo |
| Dashboard admin | 1 GET PostgREST **por cliente** |

## Mitigações (2026-08-07 — rodada carga)

### Backend

| Mudança | Arquivo(s) | Efeito |
|---------|------------|--------|
| `load_trade_history_for_users` (batch SQLite/`user_id=in.(…)`) | `robot_persistence.py` | Dashboard deixa de fazer N round-trips |
| `history_batch_loader` no `GET /admin/dashboard` | `admin_router.py`, `main.py` | Usa o batch + `to_thread` |
| Cache em memória 75 s por `company_id:days` | `admin_dashboard_cache.py` | Revisitas/abas do admin sem recomputar |
| `httpx.Client` reutilizado no Supabase | `robot_persistence.py` | Menos handshake TLS por request |
| `asyncio.to_thread` em `/robot/history`, `/robot/stats`, histórico admin | `main.py`, `admin_router.py` | I/O sync não bloqueia o event loop |

### Frontend

| Mudança | Arquivo(s) | Efeito |
|---------|------------|--------|
| TTL de `/auth/session` **120 s** | `sessionIdentityCache.ts` | Menos da metade dos round-trips de sessão |
| `useAuth` escreve/lê o mesmo cache | `useAuth.ts` | Init + `apiRequest` compartilham identidade |
| Revalidate por visibility **120 s** | `authSessionKeepAlive.ts` | Menos rajadas ao trocar de aba |
| Poll robô **4 s** ativo / **12 s** idle | `robotState.ts` | Antes: 2,5 s / 8 s (e 1 s / 5 s na 1ª rodada) |
| Poll Bullex account/status **25 s** | `useBullExAccount.ts`, `useLiveTradingData.tsx` | Antes: 15 s (e 10 s na 1ª rodada) |

Mitigações anteriores de navegação admin (gate cache, sem robô em `/admin`,
`staleTime`) continuam válidas — ver [`ADMIN_NAV_PERFORMANCE.md`](./ADMIN_NAV_PERFORMANCE.md).

## Contrato: batch de histórico

```python
# RobotPersistence (default = N queries; SQLite/Supabase sobrescrevem)
histories = persistence.load_trade_history_for_users(user_ids, days)
# -> dict[user_id, list[trade]]

# Gateway
load_robot_history_items_for_users(user_ids, days)
# mescla memória do auto_trader por usuário
```

Regras:

1. Dashboard **deve** usar `history_batch_loader` (não `gather` por cliente).
2. Chunks PostgREST de no máx. **80** UUIDs (limite de URL).
3. Cache do dashboard é **por tenant**; TTL 75 s — aceitável para KPIs, não
   para ações mutáveis (aprovar lead etc. não passam por esse cache).

## O que NÃO fazer

- **Não** subir múltiplos workers uvicorn sem estado compartilhado: robô e
  sessões Bullex vivem em memória no processo único.
- **Não** ligar polling do robô de novo em `/admin/*`.
- **Não** baixar o TTL de sessão abaixo de 60 s sem medir `/auth/session` no
  gateway.

## Como validar (antes do publish)

```bash
# Backend (no container ou com deps instaladas)
cd /opt/elcapo/backend
PYTHONPATH=. python3 -m unittest \
  tests.test_trade_history_batch \
  tests.test_admin_dashboard_cache \
  tests.test_admin_management.AdminDashboardServiceTests -v

# Frontend
cd /opt/elcapo/frontend
npm test
npm run build
```

Pós-deploy:

1. `curl -w '%{time_total}\n' -o /dev/null -sS http://127.0.0.1:8080/health`
   — esperado **<0,2 s** na média (sob carga residual pode oscilar).
2. Login admin → `/admin/dashboard` (1ª carga); Network: **1** chamada dashboard.
3. Com clientes no painel: `/auth/session` não deve dominar os logs a cada 1–2 s.
4. Fora do admin, `/robot/state` no mínimo a cada ~4 s quando ativo.

## Gargalo do `_call_gate` da Bullex (2026-08-07 — revisão completa)

Revisão de sistema identificou um segundo gargalo, independente do
`backend-gateway`: o `bullex-service` limitava **todas** as chamadas à
corretora (`/account`, `/sessions/status`, `/candles`, `/payouts`) num
`BoundedSemaphore(BULLEX_MAX_CONCURRENT_API_CALLS)`. Antes da Fase 3 o
default era `=1` e um `_runtime_lock` serializava o yield inteiro, porque a
lib `bullexapi` guardava estado global (`SSID`, `balance_id`) por processo
(`MVP_SAFE_MODE`).

Evidência pré-isolamento (20 usuários simultâneos, amostra de 5-8 min):

| Sinal | Valor observado |
|-------|-----------------|
| `[CALL_GATE_TIMEOUT]` | 42–78 por período de 5-30 min |
| `[SESSION_NOT_FOUND]` / `[USER_OFFLINE_SKIPPED]` | 125+ no mesmo período |
| Causa dominante | `/payouts` e `/candles`, **não** `/account`/`/sessions/status` |

### Fase 3 — isolamento de sessão e `gate=3` (2026-08-07)

Estado mutável de `BullexAPI` (dicts/listas/objetos de classe, incl.
`socket_option_closed`, `candles`, `profile`) passou a ser **por instância**.
`bullexapi/global_value.py` expõe `SessionGlobals` + ContextVar
`current_session_globals`; o proxy do módulo mantém `global_value.SSID` /
`balance_id` compatíveis. `SessionManager._activate_session` /
`_capture` setam/lêem o ContextVar; callbacks WS em `ws/client.py` fazem
bind do `api._session_globals` na thread do websocket.
`_session_context` **não** segura mais `_runtime_lock` durante o yield.

| Config | Valor |
|---|---|
| `BULLEX_MAX_CONCURRENT_API_CALLS` (default) | **3** (`docker-compose`, `.env`, `read_max_concurrent_api_calls`) |
| Testes | `tests/test_bullexapi_session_isolation.py` (instância + ContextVar) |

**Risco residual:** subir o gate acima de 3 sem observar
`[CALL_GATE_TIMEOUT]`, WIN/LOSS (`order_result`) e
`REAL_BALANCE_NOT_DETECTED` sob carga. Ver `ROBO_E_SUPORTE.md` §1.

### Mitigação aplicada: cache de mercado compartilhado entre usuários

Candles e payouts de um ativo/intervalo são **iguais para qualquer usuário**
— o cache antes era só por `user_id` (`SessionManager._probe_cache`), então N
usuários vendo o mesmo par geravam N chamadas upstream. Agora existe um
segundo cache, compartilhado entre todos os usuários, checado **antes** de
disputar o `_call_gate`:

| Peça | Arquivo | Comportamento |
|---|---|---|
| `SessionManager._market_data_cache` (+ `_market_data_cache_lock`) | `bullex_service/main.py` | Cache global por `cache_key` (ativo/intervalo/vela), TTL igual ao cache por usuário (`CANDLES_TTL_SECONDS`/`PAYOUT_TTL_SECONDS` = 60s) |
| `get_shared_market_cache` / `set_shared_market_cache` | idem | Leitura/escrita do cache compartilhado |
| `GET /candles`, `GET /payouts` | idem | Checam cache por-usuário → cache compartilhado → só então disputam o gate; ao ter sucesso, gravam nos dois; em erro, tentam o compartilhado como fallback antes de devolver `*_TEMPORARY_UNAVAILABLE` |

Efeito esperado: o número de chamadas upstream por ativo cai de
O(usuários × ativos) para O(ativos), aliviando o `_call_gate` sem tocar no
semáforo. Testes: `tests/test_shared_market_data_cache.py`.

### Mitigação aplicada: backoff no reconcile de TIMEOUT (ordem travada em loop)

`reconcile_timeout_last_trade` (chamado em **todo** `GET /robot/state`, poll
de 2,5-8s) tentava reconsultar `/orders/{id}/result` para sempre quando a
ordem nunca resolvia (ex.: sessão do usuário offline) — visto em produção como
29 chamadas em 15 min para uma única ordem, sempre 404. Agora há backoff de
**30s** entre tentativas por `(user_id, order_id)`, com desistência definitiva
(`[TIMEOUT_RECONCILE_GIVEN_UP]`) após ~1h de tentativas falhas. Arquivo:
`backend/main.py` (`_timeout_reconcile_backoff`). Testes:
`tests/test_timeout_reconcile_backoff.py`.

### Corrigido: `socket_option_closed` / `order_binary` eram globais entre sessões (2026-08-07)

Investigação de relatos de leads ("operação foi loss e apareceu win")
confirmou o risco descrito acima: `socket_option_closed` e `order_binary`
(usados por `GET /orders/{id}/result`, único ponto que decide WIN/LOSS/DRAW
de uma ordem) eram atributos de **classe** em `bullexapi/api.py`
(`BullexAPI.socket_option_closed = {}` no corpo da classe, não no
`__init__`). Em Python isso é compartilhado por **todas as instâncias** —
ou seja, por todas as sessões/usuários do `bullex-service` no mesmo
processo, para sempre (nunca eram limpos).

Auditoria de 500 operações reais em `robot_trade_history` **não** encontrou
nenhum caso de `result` inconsistente com o sinal de `profit` (0
divergências), então não há evidência de que isso já tenha trocado um
WIN/LOSS em produção — mas o desenho era inseguro: qualquer colisão de
chave (order_id) entre sessões faria uma ordem "herdar" o resultado de
outra. Corrigido por precaução e para fechar a lacuna citada acima:

| Mudança | Arquivo |
|---|---|
| `socket_option_closed`/`order_binary` viram atributos de **instância** (reatribuídos no `__init__`) | `bullexapi/api.py` |
| Cinto de segurança: `GET /orders/{id}/result` só aceita a mensagem se o `id`/`option_id` dentro dela bater com o order_id pedido; senão devolve `PENDING_RESULT` em vez de arriscar o resultado errado | `bullex_service/main.py` (`order_result`) |

Testes: `tests/test_bullexapi_session_isolation.py`,
`tests/test_bullex_order_result.py`. **Atualização Fase 3:** o restante do
estado mutável de `BullexAPI` + `SessionGlobals` (ContextVar) também foi
isolado; default do gate subiu para **3** (ver subseção "Fase 3" acima).

## "Tela azul" e lentidão no painel admin (2026-08-07 — segunda causa raiz)

Lead reportou tela azul/preta em branco ("Uncaught undefined" no console) ao
abrir `/admin`, e lentidão geral. Reprodução com Playwright confirmou: login
levava **14–33 s** e `GET /admin/dashboard` **19–37 s**, mesmo já com o cache
de 45 s "quente". Isolando com `curl` direto contra o endpoint, a lógica do
dashboard em si respondia em **<2,5 s** — ou seja, o tempo não estava no
endpoint, e sim **antes** dele, na resolução de autenticação
(`require_headers`/`SupabaseAuthService.authenticate`) e na concorrência do
único event loop do `backend-gateway`. A "tela azul" era sintoma dessa
lentidão: o frontend estourava timeout/à espera de resposta e caía num estado
de erro não tratado.

Duas causas reais identificadas e corrigidas:

### 1. `persist_robot` bloqueava o event loop

`persist_robot` (chamada a cada mudança de estado do robô — inclusive a cada
ciclo de análise por candle, de **cada** usuário ativo com robô ligado) fazia
escrita síncrona no Supabase via `httpx.Client` (`robot_persistence.save_state
/save_settings/save_trade`). Como o `backend-gateway` roda com **1 único**
worker uvicorn/asyncio (obrigatório: estado do robô e sessões vivem em
memória no processo), cada escrita síncrona travava o **único** event loop
pela duração do round-trip HTTP — com dezenas de robôs ativos, isso empilhava
e travava login, `/admin/dashboard` e qualquer outra rota.

**Correção**: escrita passa a rodar numa `ThreadPoolExecutor` dedicada
(`_ROBOT_PERSIST_EXECUTOR`, 8 threads), fora do event loop. Lock por usuário
(`_get_robot_persist_lock`) garante que as escritas do mesmo usuário
continuem em ordem de submissão. `persist_robot` retorna o `Future` (usado só
nos testes para sincronizar). Arquivo: `backend/main.py`. Testes:
`tests/test_persistence_restore.py`
(`test_persist_robot_write_runs_in_background_without_blocking_caller`,
`test_persist_robot_preserves_write_order_per_user`).

### 2. `SupabaseAuthService.authenticate` sem connection pooling e sem paralelismo

Roda em **toda** requisição autenticada (poll de `/robot/state`, qualquer
rota admin) e fazia até 6 chamadas HTTP sequenciais ao Supabase
(`auth/v1/user` + `user_access_profiles` + até 4 para permissões/role), cada
uma abrindo um `httpx.AsyncClient` **novo** (`async with ...:`) — handshake
TCP/TLS do zero em cada uma das 6 chamadas, de cada requisição, de cada
usuário.

**Correção**: `backend/auth_service.py` passa a manter um único
`httpx.AsyncClient` compartilhado (keep-alive, `max_connections=50`,
`max_keepalive_connections=20`), criado no `__init__` e fechado em
`aclose()` no shutdown do app. As 3 consultas de `_load_authorization` que só
dependem de `role_id` (`role_permissions`, `security_roles`,
`role_assignable_roles`) passam a rodar em paralelo via `asyncio.gather` em
vez de em série. Testes: `tests/test_auth_service_performance.py`.

### Resultado medido (produção, pós-deploy)

| Métrica | Antes | Depois |
|---|---|---|
| Login (`curl`, tempo total) | 14–33 s | 2,5–5,2 s |
| `/admin/dashboard` cache frio | ~37 s | ~13 s |
| `/admin/dashboard` cache quente (45 s) | ~20 s | ~4,8 s |
| Reprodução Playwright do painel admin | tela em branco / `Uncaught undefined` | carrega dashboard completo, sem erro no console |

Validado com Playwright completo (login real → `/admin` → aguardar
renderização): dashboard carrega com KPIs, ranking de clientes e sem
`pageerror`. Nenhum outro container (`bullex-service`, `webhook-worker`,
`webhook-redis`) precisou de mudança para este problema.

**Gargalo residual**: ~4,8 s no cache quente ainda é mais alto que o ideal
(<1 s). Suspeita: contenção residual do único event loop sob carga real de
robôs ativos (mesmo após os dois fixes acima, outras chamadas síncronas
podem existir espalhadas pelo arquivo de ~12k linhas de `main.py`) e/ou
overhead de middleware/DI por requisição. Não bloqueia o uso do painel — é
uma otimização futura, não um bug ativo. Próximo passo sugerido: profiling
com `py-spy`/`asyncio` do processo `backend-gateway` sob carga real para achar
o próximo ponto de bloqueio, se necessário.

### Higiene de Docker

Deploys repetidos sem limpeza acumularam **169 imagens** (24,4 GB, 159
"dangling") e **14,8 GB** de build cache nunca liberado. Rotina recomendada
após cada deploy:

```bash
docker image prune -af
docker builder prune -af
```

## Acessos / Pedidos / Aprovar

Fila de leads e aprovação tinham gargalos próprios (filtro em memória +
webhooks no request). Mitigações em
[`REGISTRO_APROVACAO.md`](./REGISTRO_APROVACAO.md):

- Filtro PostgREST + UI otimista (rodada 1)
- Cache 20 s da listagem, approve com side-effects deferidos, prefetch FE,
  índice parcial `user_access_profiles_pending_idx` (rodada 2)

## Plano sistema instantâneo (2026-08-07 — 4 fases)

Implementação completa do plano de performance:

| Fase | Entrega | Arquivos-chave |
|------|---------|----------------|
| 1 | WebSocket `/ws/robot-state` + ticket + fallback HTTP 30s | `robot_state_ws.py`, FE `robotStateWs.ts` — ver [`ROBOT_STATE_WEBSOCKET.md`](./ROBOT_STATE_WEBSOCKET.md) |
| 2 | Warmer admin dashboard 30s (days 7/30) | `admin_dashboard_warm.py` — ver [`ADMIN_DASHBOARD_WARM.md`](./ADMIN_DASHBOARD_WARM.md) |
| 3 | Isolamento sessão Bullex + gate **3** | `bullexapi/global_value.py`, `api.py`, `bullex_service` |
| 4 | `robot-runtime` separado via Redis DB1 | `robot_bus.py`, `robot_runtime_main.py`, Compose |

`ROBOT_RUNTIME_MODE`: `external` no gateway (sem `robot_worker` local), `worker` no
container `robot-runtime`. Snapshots em `robot:snapshot:{user_id}` (TTL 120s).

## Rodada instantânea (2026-08-07 — cache auth + menos polling)

Auditoria ao vivo mostrou `/health` oscilando 0,2–2 s, ~280 `/robot/state` e
~80 `CALL_GATE_TIMEOUT` em 5 min, com RTT Supabase já baixo (~35 ms). O teto
continuava sendo **auth em toda request** + **polling** + **client HTTP novo
por hop BullEx**.

### Backend

| Mudança | Arquivo(s) | Efeito |
|---------|------------|--------|
| Cache de `authenticate` por hash do token (**TTL 45 s**) | `auth_service.py` | Polls de `/robot/state` deixam de martelar o Supabase |
| `invalidate_user` / `invalidate_token` | `admin_router.py`, `auth_router.py`, `main.py` | Approve/update/delete/logout limpam o cache |
| `httpx.AsyncClient` keep-alive no BullEx gateway | `main.py` (`get_bullex_http_client`) | Menos handshake TCP por `/account`/`/status`/`/candles` |
| Client keep-alive em login/refresh/session | `auth_session_service.py` | Login e `/auth/session` reusam conexão |
| TTL cache account/status **25 s / 20 s** | `main.py` | Alinha com poll FE e reduz pressão no `_call_gate` |
| Headers `Retry-After` robô/account **4 s / 25 s** | `main.py` | Hint correto aos clientes |
| Cache dashboard admin **75 s** | `admin_dashboard_cache.py` | Menos recomputes ao trocar abas |

### Frontend

| Mudança | Arquivo(s) | Efeito |
|---------|------------|--------|
| Poll robô **4 s** ativo / **12 s** idle | `robotState.ts` | Antes: 2,5 s / 8 s |
| Poll Bullex account/status **25 s** | `useBullExAccount.ts`, `useLiveTradingData.tsx` | Antes: 15 s |
| Histórico/stats robô **45 s** | `useRobotHistory.ts` | Antes: 30 s |

### Contrato do cache de auth

```python
# SupabaseAuthService
user = await authenticate(access_token)  # hit cache se TTL ok
service.invalidate_user(user_id)         # após approve/update/delete/role
service.invalidate_token(access_token)   # no logout
```

Regras:

1. TTL padrão **45 s** (`AUTH_CACHE_TTL_SECONDS`) — revoke de acesso pode
   demorar até esse tempo se a invalidação falhar; mutações admin **devem**
   chamar `invalidate_user`.
2. Chave = SHA-256 do token (nunca o JWT cru como chave de dict).
3. Falhas de auth **não** são cacheadas.
4. Máximo **2000** entradas; prune por expiração + overflow.

### Como validar (esta rodada)

```bash
cd /opt/elcapo/backend
PYTHONPATH=. python3 -m unittest \
  tests.test_auth_cache \
  tests.test_auth_service_performance \
  tests.test_auth_session_api \
  tests.test_admin_dashboard_cache \
  tests.test_gateway_fast_fallback \
  tests.test_supabase_resilience -v

cd /opt/elcapo/frontend
node --experimental-strip-types --test src/lib/robotState.poll.test.ts
npm run build && /opt/elcapo/scripts/publish-frontend.sh
/opt/elcapo/scripts/deploy-backend.sh
```

Pós-deploy:

1. `/health` local média **<0,2 s** sob carga residual.
2. Logs: menos rajadas de idas ao Supabase Auth por poll (cache hit).
3. `/robot/state` no Network do cliente ≥ ~4 s quando ativo.
4. Approve de lead → usuário sente `grant_access` sem esperar o TTL inteiro
   (invalidação explícita).

## Histórico

- **2026-08-07 (rodada instantânea)** — cache `authenticate` 45 s +
  invalidação em mutações/logout; keep-alive BullEx/auth-session; poll FE
  4/12 s e Bullex 25 s; TTLs account/status/dashboard ampliados. Ver seção
  "Rodada instantânea" acima.
- **2026-08-07 (tela azul / lentidão no admin)** — `persist_robot` deixa de
  bloquear o event loop (escrita em `ThreadPoolExecutor` + lock por usuário);
  `SupabaseAuthService` passa a reutilizar `httpx.AsyncClient` com pooling e
  paraleliza as 3 consultas de permissões em `_load_authorization`. Login:
  14-33s → 2,5-5,2s. Dashboard admin: 37s → 13s (frio), 20s → 4,8s (quente).
  Ver seção "Tela azul e lentidão no painel admin" acima.
- **2026-08-07 (revisão completa do sistema)** — Cache de mercado
  compartilhado entre usuários (`candles`/`payouts`) para reduzir disputa no
  `_call_gate` da Bullex; backoff no reconcile de TIMEOUT (fim do loop de
  poll em ordem travada); limpeza de 169→4 imagens Docker e build cache
  (~17,7 GB liberados); `webhook-worker` deixa de rodar como root. Ver seção
  "Gargalo do `_call_gate` da Bullex" acima.
- **2026-08-07** — Pedidos rodada 2: cache listagem 20 s, defer audit/webhooks,
  prefetch/hover FE, páginas paralelas no dashboard, índice parcial.
- **2026-08-07** — Pedidos: filtro PostgREST + approve em background + UI
  otimista. Ver `REGISTRO_APROVACAO.md`.
- **2026-08-07** — Batch dashboard, cache 45 s, pool httpx, to_thread history,
  TTL sessão 120 s, poll robô 2,5/8 s, Bullex 15 s.
- **2026-08-06** — Nav admin (beforeLoad cache, session TTL 30 s inicial,
  desligar robô no admin). Ver `ADMIN_NAV_PERFORMANCE.md`.
