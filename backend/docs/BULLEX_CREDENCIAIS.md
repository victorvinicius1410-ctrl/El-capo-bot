# Credenciais Bullex salvas (auto-reconexão)

Documento da persistência criptografada de email/senha da corretora Bullex,
para o cliente não precisar reconectar a cada queda de sessão e para o robô
operar com a tela fechada.

Atualizado em **2026-08-07**.

## Objetivo

- Salvar **email + senha** da conta Bullex do cliente após o primeiro
  `POST /bullex/connect` bem-sucedido.
- Criptografar a senha em repouso com **AES-256-GCM** (`EncryptionService`,
  envelope `v1.<payload>`), usando `PROD_ENCRYPTION_KEY` / `ENCRYPTION_KEY`.
- Permitir **auto-reconexão** quando a sessão/SSID cair:
  - ao **entrar no painel** (`GET /bullex/status`, `GET /bullex/account` e
    hook `useEnsureBullexSession` no frontend);
  - no `robot_worker` com a tela fechada;
  - em `POST /bullex/reconnect` (SSID ou login salvo).
- **Nunca** devolver a senha ao frontend.
- Depois do primeiro connect, o cliente **não** precisa digitar de novo a
  cada visita — só em “Esquecer credenciais” ou troca de conta.

## Pré-requisitos

1. Rodar o SQL `migration_bullex_saved_credentials.sql` no Supabase.
2. Backend com `PROD_ENCRYPTION_KEY` (APP_ENV=production) — prefixo canônico
   `PROD_` / `DEV_` / `STAGING_` via `backend/env_prefix.py` (não usar
   `PRODUCTION_ENCRYPTION_KEY`).
3. Redeploy do `backend-gateway` após o SQL.

```sql
alter table public.bullex_connections
  add column if not exists encrypted_password text;

alter table public.bullex_connections
  add column if not exists credentials_saved_at timestamptz;
```

Colunas:

| Coluna | Tipo | Uso |
|---|---|---|
| `encrypted_password` | text | Envelope AES-256-GCM (só service_role) |
| `credentials_saved_at` | timestamptz | Quando as credenciais foram gravadas |

RLS continua ativo; `anon`/`authenticated` sem acesso; só `service_role`.

## Fluxo

```
Cliente informa email/senha em Configurações → Conta Corretora
  → POST /bullex/connect
  → bullex-service autentica na corretora
  → gateway criptografa a senha (BullexCredentialsService)
  → UPSERT em bullex_connections (encrypted_password + bullex_email)

Quando a sessão cai (SSID inválido / restart do bullex-service):
  → cliente abre o painel
    → GET /bullex/status e GET /bullex/account detectam disconnected
    → try_auto_reconnect_with_saved_credentials(user_id)
    → OU frontend (useEnsureBullexSession) chama POST /bullex/reconnect
  → robot_worker (tela fechada) também tenta o mesmo
  → decrypt + POST /sessions/connect de novo
  → sessão volta sem pedir senha
```

## APIs

| Método | Rota | Comportamento |
|---|---|---|
| POST | `/bullex/connect` | Conecta e **salva** credenciais criptografadas |
| POST | `/bullex/disconnect` | Derruba sessão; **mantém** credenciais salvas |
| POST | `/bullex/reconnect` | Tenta SSID; se não conectar, usa credenciais salvas |
| GET | `/bullex/status` | Se desconectado + login salvo → auto-reconnect |
| GET | `/bullex/account` | Se desconectado + login salvo → auto-reconnect |
| GET | `/bullex/credentials` | `{ credentials_saved, email }` — sem senha |
| DELETE | `/bullex/credentials` | Esquece credenciais (ação explícita do cliente) |

Rate-limit normal: `BULLEX_AUTO_RECONNECT_COOLDOWN_SECONDS` (60s) por usuário.

## Rate limit da corretora (`requests_limit_exceeded`)

A Bullex limita **logins por IP de origem**. Com dezenas de clientes no mesmo
VPS, um restart do `bullex-service` + auto-reconnect em massa pode devolver:

```json
{"code":"requests_limit_exceeded","message":"Try again in 60 minutes.","ttl":3600}
```

### O que o sistema faz (2026-08-07)

1. Detecta o código e responde `BULLEX_REQUESTS_LIMIT_EXCEEDED` (não mascara
   como “corretora indisponível”).
2. Ativa **gate global** de login no `bullex-service` e no gateway pelo TTL
   (~3600s) — novas tentativas nem chegam na API da corretora.
3. Auto-reconnect / painel respeitam o gate (`[BULLEX_AUTO_RECONNECT_RATE_LIMIT]`,
   `[BULLEX_LOGIN_RATE_LIMIT]`).
4. UI: mensagem pedindo espera (~60 min). Proxy é só ops (`.env`), nunca
   exposto ao cliente.

### Proxy / VPN para contornar

O login HTTP (`auth.trade.bull-ex.com`) e o websocket usam o IP do container.
Para sair por outro IP:

| Opção | Como | Quando usar |
|---|---|---|
| **Proxy HTTP/SOCKS** | `BULLEX_PROXY_URL` ou `BULLEX_PROXY_URLS` no `.env` do `bullex-service` | Preferido: pool residencial rotativo |
| **VPN no container** | Gluetun/WireGuard envolvendo só o `bullex-service` | Um IP fixo “limpo” |
| **VPS secundário** | Segundo egress / réplica com outro IP | Escala alta |

Variáveis (sem prefixo `PROD_` — só o serviço Bullex):

```bash
# Um proxy
BULLEX_PROXY_URL=http://user:pass@host:8080

# Pool round-robin (recomendado)
BULLEX_PROXY_URLS=http://p1:8080,http://p2:8080,socks5://p3:1080
```

Com proxy configurado, o gate global **libera tentativas** (egress diferente).
Sem proxy, o gate bloqueia até o TTL acabar.

Arquivos: `bullex_service/proxy_config.py`, `bullex_service/rate_limit.py`,
`bullexapi` (propaga `proxies` no HTTP + WS), `backend/main.py`
(`classify_bullex_connect_error`, `note_bullex_login_rate_limit`).

Detalhes operacionais: [`BULLEX_RATE_LIMIT_E_PROXY.md`](./BULLEX_RATE_LIMIT_E_PROXY.md).

## UI

`BullexConnectionPanel` + dashboard:

- Ao entrar no sistema com login salvo: **Reconectando automaticamente...**
- Badge “Login salvo no servidor”
- Botão **Reconectar com login salvo** (fallback manual)
- Botão **Esquecer credenciais salvas**
- Formulário “Conectar e salvar”

## Arquivos

- `backend/services/bullex_credentials_service.py`
- `backend/services/encryption_service.py`
- `backend/user_store.py`
- `backend/main.py` (`persist_bullex_credentials`, `try_auto_reconnect_with_saved_credentials`,
  auto-reconnect em status/account/reconnect)
- `backend/migration_bullex_saved_credentials.sql`
- `Frontend/src/components/BullexConnectionPanel.tsx`
- `Frontend/src/hooks/useEnsureBullexSession.ts`
- `Frontend/src/lib/ensureBullexSession.ts`
- `Frontend/src/hooks/useLiveTradingData.tsx`
- `tests/test_bullex_credentials_service.py`
- `tests/test_bullex_panel_auto_reconnect.py`
- `tests/test_bullex_rate_limit_proxy.py`

## Relação com SSID

O SQLite do `bullex-service` continua guardando só o **SSID** (Fernet).
A senha **não** vai para esse SQLite — vai para o Supabase criptografada.
Isso resolve o caso `broker_invalidates_ssid` após restart do container e o
caso “pedi reconectar toda vez que abri o painel”.

## Reconnect soft (SSID antes da senha) — 2026-08-07

Sintoma: ao clicar **Iniciar Operação**, o painel marcava Bullex
desconectada e/ou a sessão do usuário na corretora (app/site) caía.

Causa: `try_auto_reconnect_with_saved_credentials` ia direto para
`POST /sessions/connect` (login novo). Isso:

1. Fechava o websocket da sessão anterior (`CONNECT_CLEAR_OLD_SESSION`).
2. Revogava o SSID no SQLite (`revoke_token=True`).
3. Criava login novo na corretora — pode derrubar sessão paralela do cliente.
4. No start falho, `disconnect_account` marcava `ACCOUNT_DISCONNECTED` no painel
   mesmo com a sessão ainda viva (timeout/fila em `/account`).

Correção:

| Camada | Comportamento |
|---|---|
| Gateway `try_auto_reconnect_with_saved_credentials` | 1º `POST /sessions/reconnect` (SSID); senha só se SSID falhar |
| Gateway `try_ssid_session_reconnect` | Soft path; cooldown 15s; **não** bloqueado pelo gate de login HTTP |
| Gateway `POST /robot/start` | Em falha transitória **não** chama `disconnect_account` (`[ROBOT_START_BLOCKED_KEEP_SESSION]`) |
| `SessionManager.connect` | Mesmo email + WS vivo → `[CONNECT_REUSE_ALIVE_SESSION]` (não fecha WS) |

Logs úteis: `[BULLEX_SSID_RECONNECT_START]`, `[BULLEX_AUTO_RECONNECT_OK] via=ssid|password`,
`[CONNECT_REUSE_ALIVE_SESSION]`, `[ROBOT_START_BLOCKED_KEEP_SESSION]`.

Testes: `tests/test_bullex_soft_reconnect.py`, `tests/test_bullex_connect_reuse.py`.

## Anti-flap do painel (email/saldo)

Quando o probe da corretora falha de forma transitória:

1. **bullex-service** agenda backoff **sem** gravar `connected:false` por cima
   do último `/account` e `/sessions/status` bons (`last_*_cache`).
2. Em backoff/offline, se o TTL do response cache expirou, devolve o
   `last_*_cache` (stale) em vez de desconectado vazio.
3. **Gateway** (`resolve_backoff_panel_payload`): se ainda assim receber
   `status=backoff`, preenche com memória/grace antes de responder ao
   frontend — logs `[ACCOUNT_BACKOFF_PANEL_RECOVERED]` /
   `[STATUS_BACKOFF_PANEL_RECOVERED]`.
4. **Frontend** (`preferStableBullExAccount`): não troca snapshot com email/
   saldo por um desconectado vazio; status `BACKOFF` não abre o banner
   "Conta Bullex desconectada".

Isso evita o flicker ao entrar/sair de Configurações enquanto o robô opera.

## Incidente: email/saldo `—` com pill "Conectado" (2026-08-07)

### Sintoma

- Pill **Conectado** (ou grace) nas Configurações.
- Métricas **EMAIL** e **SALDO** em `—`.
- Console: `[AUTH_VISIBILITY_REFRESH_ERROR]` / falha em `/auth/refresh`
  (Safari: “access control checks”) — em geral **efeito colateral** de
  restart do gateway (502 sem CORS), não lista de origins errada.

### Causa raiz (email/saldo)

1. Deploy recria `bullex-service` → sessões em memória/SSID caem
   (`SESSION_NOT_FOUND`, `USER_OFFLINE_SKIPPED`).
2. Poll de `/bullex/account` sob backoff devolvia `email: null` /
   `balance: null` com `connected` stale/grace.
3. `build_connection_payload` + upsert Supabase **gravavam `bullex_email=null`**,
   apagando o email salvo. A senha criptografada ficava órfã.
4. `BullexCredentialsService.load()` exige email+senha → auto-reconnect
   impossível; `GET /bullex/credentials` sem email; UI mostra `—`.

Auditoria (2026-08-07): **34/62** contas com `encrypted_password` e
`bullex_email` vazio.

### Correção

| Camada | Mudança |
|--------|---------|
| `build_connection_payload` | Não inclui `bullex_email` / `last_balance` / `currency` se vazios |
| `connection_upsert_diagnostic` | Remove `bullex_email` null do body do merge |
| `has_saved` | Só `true` com email **e** senha; log `[BULLEX_CREDENTIALS_INCOMPLETE]` |
| Testes | `tests/test_bullex_email_preserve.py` |

### O que o cliente afetado precisa fazer

Contas já órfãs **não** recuperam o email sozinhas (não está no blob da
senha). Em Configurações → Conta: **Desconectar Bullex** → informar de novo
email/senha (“Conectar e salvar”). Depois do connect, email e saldo voltam.

## Incidente: Desconectar “só carrega” (2026-08-07)

### Sintoma

Clique em **Desconectar Bullex** → spinner → pill continua **Conectado**
(com email/saldo `—`). Formulário de login não aparece.

### Causas

1. **Backend:** `mark_session_failure(force_offline=True)` no disconnect
   **preservava** o último `/account` REAL. O poll seguinte (grace/backoff)
   devolvia `connected:true` de novo.
2. **Frontend:** `preferStableBullExAccount` mascarava desconectado vazio;
   `useEnsureBullexSession` auto-reconectava se havia login salvo;
   estado `syncing` com `BACKOFF` escondia o formulário.

### Correção

| Camada | Mudança |
|--------|---------|
| Gateway | `apply_manual_disconnect_session_state` — zera grace/cache sem preservar REAL |
| FE painel | UI otimista no disconnect + toast; hint de sessão incompleta |
| FE | `markManualBullexDisconnect()` — 60s sem auto-reconnect / preferStable |
| FE | `syncing` só com `connected && metricsMissing` (BACKOFF sozinho não trava login) |
| Testes | `test_manual_disconnect_cache.py`, testes FE de suppress manual |

## Incidente: sessão cai sozinha e `/robot/start` devolve 409 (2026-08-08)

### Sintoma

- Cliente conecta normalmente; **depois de um tempo** o painel mostra
  “Conta Bullex desconectada ou sessão expirada. Reconecte em Configurações →
  Conta Corretora”. Repete várias vezes ao longo do dia.
- `POST /robot/start` → **409** (`BULLEX_NOT_CONNECTED`).
- Às vezes o painel fica **Conectado** com **E-mail `—`**, **Saldo `—`** e
  “Nenhum login salvo ainda” → “Sessão incompleta (sem email/saldo)”.
- Console do browser: `WebSocket … /ws/robot-state … is closed before the
  connection is established` (2×).

### Causas

| # | Causa | Efeito |
|---|-------|--------|
| 1 | `run_forever` sem `ping_interval` (`bullexapi/api.py`) | WS ocioso com a corretora morre no NAT/proxy residencial; só se descobre na próxima operação → `[SESSION-DEAD]` → 409 |
| 2 | `load_connected_user` exigia `connected = 1` | Toda queda transitória chama `mark_disconnected` (connected=0) e **inutilizava o SSID salvo**; restore virava `SESSION_NOT_FOUND` e caía no login por senha, barrado pelo rate limit |
| 3 | `_populate_ready_state` retornava cedo quando `force_real_mode` falhava | Sessão “meio pronta”: `connected=true`, sem `get_balance`/`get_currency` e sem SSID persistido → painel com `—` e “Nenhum login salvo ainda” |

O WS `/ws/robot-state` **não** é causa: `closed before the connection is
established` é o browser avisando que o **cliente** fechou o socket durante o
handshake (cleanup do effect em `useLiveTradingData`, ex.: troca de aba). Falha
real de servidor aparece como 404 / `bad response` / 502 — ver tabela de
troubleshooting em [`ROBOT_STATE_WEBSOCKET.md`](./ROBOT_STATE_WEBSOCKET.md).

### Correção

| Camada | Mudança |
|--------|---------|
| `bullexapi/api.py` | `_websocket_keepalive_kwargs`: `ping_interval=20s`, `ping_timeout=10s` no `run_forever` (env `BULLEX_WS_PING_INTERVAL_SECONDS` / `BULLEX_WS_PING_TIMEOUT_SECONDS`; `0` desliga) |
| `bullex_service/session_store.py` | `load_connected_user` não exige `connected = 1` — só token presente. Quem desconecta de propósito usa `revoke_token=True` e continua bloqueado |
| `SessionManager.restore_on_demand` | Revoga o SSID só quando a corretora **rejeitou** (`invalid_ssid`/`restore_rejected`); timeout/rate limit preservam o token. Cooldown de 30s (`SESSION_RESTORE_COOLDOWN_SECONDS`) para o poll não virar loop de login; `force=True` em reconexão explícita |
| `SessionManager._populate_ready_state` | Modo REAL não confirmado **propaga** `BULLEX_ACTIVE_MODE_NOT_REAL` em vez de devolver sessão zumbi (saldo PRACTICE continua não sendo carregado) |
| `SessionManager.connect` | Fecha o websocket órfão no `except` antes do `remove` (a thread seguia viva escrevendo em `global_value`) |

Testes: `tests/test_persistence_restore.py::SessionKeepAliveAndRestoreTests` e
`::SessionPersistenceTests::test_ready_state_raises_on_unconfirmed_real_mode_without_loading_practice_balance`.

### Pendente (causa estrutural)

`bullexapi` guarda conexão em **estado global de módulo**
(`bullexapi/global_value.py`): `check_connect()` lê
`global_value.check_websocket_if_connect`, e a thread WS de **cada** usuário
escreve nele (`ws/client.py` on_open/on_close/on_error), além de
`start_websocket()` zerar a flag globalmente a cada login. O `_activate`/
`_capture` do `SessionManager` (o `MVP_SAFE_MODE` do log) só protege a chamada
em primeiro plano — as threads de callback continuam contaminando as outras
sessões. Sintoma: usuário B cai porque o socket do usuário A fechou.

Correção definitiva = uma sessão Bullex por processo (worker por usuário ou
pool com afinidade por `user_id`), ou mover o estado do `ws/client.py` para um
objeto por sessão. Os fixes acima reduzem a frequência, **não** eliminam.

## Incidente: 100% dos logins com `Websocket connection timeout` (2026-08-08)

### Sintoma

Nenhuma sessão da corretora subia — `LOGIN_SUCCESS` e `SESSION-ALIVE` zerados,
`[BULLEX_SSID_RECONNECT_FAILED]` ~273/15min, `[ROBOT_WORKER_BLOCKED_DISCONNECTED]`
~1180/15min e `/sessions/reconnect` → 404 em série. No painel: “Conta Bullex
desconectada ou sessão expirada”. Última sessão boa: 2026-08-07T23:48Z.

### Causa raiz

Regressão do isolamento por sessão (Fase 3, `SessionGlobals` + `ContextVar`):

1. `SessionManager._activate_session` prende o `SessionGlobals` da sessão no
   ContextVar **e** no `session.client.api` daquele momento.
2. `stable_api.connect()` e `restore_with_ssid()` fazem `self.api = BullexAPI(...)`
   logo depois — e o `__init__` criava `self._session_globals = SessionGlobals()`
   **zerado**.
3. A thread do websocket chamava `bind_api_session_globals(api_novo)` e gravava
   `check_websocket_if_connect = 1` nesse objeto novo.
4. `start_websocket` esperava no objeto **antigo** (ContextVar da thread
   chamadora) → nunca via o `1` → `Websocket connection timeout` aos 15s, mesmo
   com o log `INFO:websocket:Websocket connected`.

O proxy e a rede estavam saudáveis (handshake WS pelo proxy em ~0,7s).

### Correção

`bullexapi/api.py` (`BullexAPI.__init__`): herda o `SessionGlobals` ativo no
ContextVar quando há sessão; fora de `_session_context` usa objeto próprio
(`get_session_globals()` devolveria o fallback do processo, o que faria duas
sessões compartilharem estado).

```python
_active = global_value.current_session_globals.get()
self._session_globals = _active if _active is not None else global_value.SessionGlobals()
```

Efeito medido no minuto seguinte ao deploy: `Websocket connection timeout` de
100% → **0**; `LOGIN_SUCCESS`, `[SESSION_RESTORE] status=success`,
`[REAL_MODE_CONFIRMED] active_mode=REAL` voltaram.

### Regressão logo depois: `invalid_credentials` com a senha certa

Herdar o `SessionGlobals` fez o `BullexAPI` novo enxergar o **SSID antigo** da
sessão. `BullexAPI.connect()` tem um atalho “temp ssid reconnect for speed up”
que só dispara com `global_value.SSID != None` — antes, um api novo sempre
tinha SSID vazio e ia direto para o login limpo. Com o SSID morto herdado, o
atalho falhava e o erro voltava ao painel como **“Email ou senha Bullex
inválidos”**.

Correção: `stable_api.connect()` zera `global_value.SSID` antes de
`self.api.connect()` — login por senha é sempre limpo; quem quer reusar SSID
usa `restore_with_ssid()`. Medido após o deploy: `invalid_credentials` 15 → **0**.

## Histórico

- **2026-08-08** — `BullexAPI.__init__` criava `SessionGlobals` zerado depois do
  `_activate_session`, quebrando 100% dos logins com timeout de websocket; ver
  seção incidente acima.
- **2026-08-08** — Keepalive do WS da corretora, SSID salvo sobrevivendo a
  queda transitória e fim da sessão “meio pronta”; ver seção incidente acima.
- **2026-08-07 (noite — disconnect stuck)** — Desconectar não “pegava” por
  cache REAL + auto-reconnect; ver seção incidente acima.
- **2026-08-07 (noite — wipe bullex_email)** — Sync de account com
  `email:null` apagava login salvo; ver seção incidente acima.
- **2026-08-07 (noite — flap visual Configurações)** — Anti-flap acima;
  ver também `CONFIGURACOES.md` e `ROBO_E_SUPORTE.md` §4.
- **2026-08-07 (noite — start não derruba Bullex)** — Soft reconnect SSID
  antes da senha; reuse de sessão viva no connect; start não marca
  `ACCOUNT_DISCONNECTED` em falha de saldo/contrato. Ver seção acima.
- **2026-08-07** — Backoff global em `requests_limit_exceeded`; código
  `BULLEX_REQUESTS_LIMIT_EXCEEDED` na UI; suporte a proxy de egress
  (`BULLEX_PROXY_URL` / `BULLEX_PROXY_URLS`) no login HTTP + websocket.
- **2026-08-03** — Ao voltar à aba após idle, `useEnsureBullexSession` zera
  `attemptedForUser` e dispara nova tentativa de reconnect (antes só o F5
  remonta o hook e “consertava”). Complementa o refresh de auth em
  [`AUTENTICACAO.md`](./AUTENTICACAO.md) (access ~1h).
- **2026-07-24** — Se `/sessions/status` vier `connected` sem `active_mode`,
  o gateway resolve o modo via `/account` para não bloquear Iniciar Operação
  (`ACTIVE_MODE_RESOLVED_FROM_ACCOUNT`). Detalhe em `ROBO_E_SUPORTE.md` §4.

- **2026-07-23** — Auto-reconexão na **entrada do painel** (status/account +
  hook frontend). Depois do 1º connect, a sessão restaura sozinha.
- **2026-07-22** — Persistência criptografada de email/senha + auto-reconnect
  do robô com tela fechada; SQL dedicado para o operador rodar no banco.
