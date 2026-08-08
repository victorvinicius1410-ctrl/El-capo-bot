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

## Histórico

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
