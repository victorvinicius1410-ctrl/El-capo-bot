# Autenticação — El Capo AutoBot (VPS)

Como o login funciona e como administrar usuários em produção.
Atualizado em **2026-08-07** (cache curto de `authenticate` no gateway).

## Fluxo de login

1. O frontend (`app.elcapobot.online`) envia `POST /auth/login` com email/senha para a API.
2. O backend chama o Supabase Auth (`/auth/v1/token?grant_type=password`) usando a service role — o navegador nunca fala com o Supabase.
3. Os tokens voltam apenas em cookies `httpOnly` (`__Host-elcapo-access` / `__Host-elcapo-refresh`, `Secure`, `SameSite=Lax`).
4. `GET /auth/session` valida o cookie e devolve o usuário.

Não existe usuário/senha no `.env`. As credenciais reais vivem no **Supabase Auth**.

### Cache de autorização no gateway (performance)

Toda rota autenticada passa por `SupabaseAuthService.authenticate` (perfil +
permissões). Sob polling do robô isso martelava o Supabase. Desde 2026-08-07 o
resultado fica em cache **45 s** por hash do access token:

- **Invalidação por usuário** em approve/update/delete de cliente ou admin
  (`access_profile_invalidator` no `admin_router`).
- **Invalidação por token** no `POST /auth/logout`.
- Detalhes e testes: [`PERFORMANCE_SISTEMA.md`](./PERFORMANCE_SISTEMA.md)
  (seção "Rodada instantânea").

### Quem é admin?

Em produção o menu `/admin` **não** depende de `ADMIN_EMAILS` / `VITE_ADMIN_EMAILS`.

| Fonte | O que controla |
|-------|----------------|
| `user_access_profiles.is_admin` **ou** `app_metadata.is_admin` / `role=admin` | Acesso real às rotas `/admin/*` via `/me/access` |
| `ADMIN_EMAILS` | Só no modo legado (`AUTH_ALLOW_LEGACY_HEADERS=true`) — **desligado em prod** |
| `VITE_ADMIN_EMAILS` | Só o “admin model” demo visual em `/admin` — **não** libera o painel |

Estado atual (2026-07-23):

| Email | `is_admin` | Observação |
|-------|------------|------------|
| `admin@elcapobot.online` | sim | Único admin do painel |
| `victorvinicius@gmail.com` | não | Conta marketing |
| `sergiotrader@gmail.com` | não | Conta cliente |

Para dar acesso admin a um membro da equipe: promover o perfil (`is_admin=true` + `app_metadata`) **ou** usar a conta `admin@…` com a senha correta.

## Sintoma: 401 Unauthorized em `/auth/login`

### O que o console mostra

```text
Failed to load resource: the server responded with a status of 401 (Unauthorized)
https://api.elcapobot.online/auth/login
```

Corpo típico:

```json
{"ok":false,"error":{"code":"INVALID_CREDENTIALS","message":"Email ou senha inválidos"}}
```

### Causa real (incidente 2026-07-23)

O pipeline (CORS, cookies, nginx, Supabase URL/key) estava **saudável**. O Supabase Auth rejeitou o par email/senha.

Evidências:

- Admin autenticou com sucesso às **13:14 UTC** (`POST /auth/login` → 200).
- Sergio autenticou às **14:30 UTC** (conta normal, não-admin).
- Depois disso, várias tentativas → **401** (IPs da equipe: Windows + Pixel).
- Probe com senha errada reproduz exatamente o mesmo payload `INVALID_CREDENTIALS`.

Hipóteses mais comuns (nessa ordem):

1. Senha errada / autofill desatualizado do Chrome.
2. Espaço no fim da senha ao colar (mitigado em 2026-07-23 com `.trim()` no backend e frontend).
3. Tentativa de entrar no painel com conta **não-admin** usando senha de outra conta (ou email inexistente).
4. Rate limit do Supabase após várias falhas (mensagem amigável: “Muitas tentativas…”).

**Não** é causa do 401: falta de `is_admin`, CORS, cookie `__Host-`, `grant_access`, drift de deploy.

### Se a pessoa loga mas não vê o menu Admin

Isso **não** é 401. Login OK + conta com `is_admin=false` → dashboard de cliente sem item Admin. Promover o usuário ou usar `admin@elcapobot.online`.

## Fluxo de logout (menu lateral "Sair")

1. O botão **Sair** do `AppShell` (e o de Conta/Configurações) chama `logoutSession()` no frontend.
2. O frontend faz `POST /auth/logout` com `credentials: "include"` para `api.elcapobot.online`.
3. O backend responde `204` **com** headers `Set-Cookie` expirando
   `__Host-elcapo-access` e `__Host-elcapo-refresh` (`Max-Age=0`, mesmos atributos
   `HttpOnly`/`Secure`/`SameSite=Lax`/`Path=/`).
4. Em seguida o frontend limpa o snapshot local (`clearAuthSnapshot`), cache do
   React Query, estado BullEx/robô, e navega para `/login`.

### Bug corrigido (2026-07-22)

`POST /auth/logout` gravava os cookies no `Response` injetado pelo FastAPI e
depois retornava um **novo** `Response(status_code=204)`, descartando os
`Set-Cookie`. Resultado: a UI ia para `/login`, mas o cookie httpOnly
continuava válido; a página de login chamava `refreshAuthSession()` e
redirecionava de volta ao dashboard — o "Sair" parecia não funcionar.

Correção: o handler só muta o `Response` injetado e retorna `None`
(status `204` via decorator), preservando os headers de limpeza.

### Diagnóstico rápido do logout

```bash
# Deve aparecer Set-Cookie com Max-Age=0 para access e refresh:
curl -sS -D - -o /dev/null -X POST https://api.elcapobot.online/auth/logout \
  -H 'Origin: https://app.elcapobot.online' \
  -H 'Cookie: __Host-elcapo-access=dummy; __Host-elcapo-refresh=dummy'
```

## Onde ficam as variáveis

- Produção (usada pelos containers): `/opt/elcapo/backend/.env`
- Espelho informativo: `/root/Backend/.env`
- Após editar: copiar para `/opt/elcapo/backend/.env` e rodar `/opt/elcapo/scripts/deploy-backend.sh`

Variáveis relevantes para autenticação:

```text
SUPABASE_URL=https://<projeto>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<somente backend>
APP_ENV=production          # ativa cookies __Host- + Secure
AUTH_ALLOW_LEGACY_HEADERS=false
ADMIN_EMAILS=admin@elcapobot.online   # legado; não controla /admin em prod
```

## Criar o primeiro admin (bootstrap)

O banco (bootstrap `el_capo_full_bootstrap.sql`) expõe a RPC `bootstrap_admin_user`,
executável só com a service role:

```bash
SUPABASE_URL=... ; KEY=<service-role>
curl -X POST "$SUPABASE_URL/rest/v1/rpc/bootstrap_admin_user" \
  -H "apikey: $KEY" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"target_email":"admin@elcapobot.online","target_password":"<SenhaForte>","target_name":"Administrador","activate_access":true}'
```

Requisitos de senha do painel: mínimo 8 caracteres, com maiúscula, minúscula e número.

Status atual: admin `admin@elcapobot.online` criado e login validado ponta a ponta
(Supabase → API → cookies → sessão) em 21/07/2026.

## Reset de senha do admin (quando a equipe esquece)

```bash
set -a; source /opt/elcapo/backend/.env; set +a
# Substitua USER_ID e a nova senha:
curl -sS -X PUT "$SUPABASE_URL/auth/v1/admin/users/2a9025cd-c7ee-4996-8231-c0408cc8ea08" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"password":"<NovaSenhaForte1>"}'

curl -sS -X POST https://api.elcapobot.online/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@elcapobot.online","password":"<NovaSenhaForte1>"}'
# Esperado: HTTP 200 + {"ok":true,...}
```

## Usuários adicionais

Criar pelo painel `/admin` (menu Clientes) logado como admin, ou pela API
`POST /admin/users` com sessão de um admin.

## Diagnóstico rápido

```bash
# Login funciona?
curl -sS -X POST https://api.elcapobot.online/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@elcapobot.online","password":"<senha>"}'

# 401 INVALID_CREDENTIALS → email/senha errados ou usuário não existe no Supabase
# 502 AUTH_PROVIDER_ERROR → SUPABASE_URL/SERVICE_ROLE_KEY errados no .env

# Listar usuários existentes (service role):
curl -sS "$SUPABASE_URL/auth/v1/admin/users" -H "apikey: $KEY" -H "Authorization: Bearer $KEY"

# Ver rejeições recentes no gateway (sem senha; email + provider_code):
docker logs backend-gateway --since 1h 2>&1 | grep 'auth.login.rejected'
```

## Melhorias 2026-07-23

- Backend e frontend fazem `.trim()` nas bordas de email/senha no login (evita 401 por espaço de colar).
- Logs de rejeição incluem `provider_code` / `provider_msg` / email (nunca a senha).
- Rate limit do Supabase devolve mensagem amigável em português.

## Renovação automática da sessão (access ~1h)

O access cookie do Supabase dura **~3600s**. O refresh cookie dura **~30 dias**.

Antes de **2026-08-03**, o backend **não** usava o refresh cookie: depois de ~1h
com a aba aberta (ou ao voltar de idle), `GET /auth/session` devolvia
`authenticated: false`, as APIs respondiam `NO_AUTH` e a UI parecia “travada”
até o F5 (que remonta React / limpa estado). Isso estava no backlog.

### Comportamento atual

| Camada | O que faz |
|--------|-----------|
| `GET /auth/session` | Se o access falhou/expirou e há refresh válido → renova cookies em silêncio (`refreshed: true`) |
| `POST /auth/refresh` | Renovação explícita (fallback do frontend) |
| Frontend `useAuth` | Ao voltar à aba (`visibilitychange`), revalida a sessão (throttle 30s) |
| Frontend `apiRequest` | Se a identidade vier nula, tenta `POST /auth/refresh` uma vez e lê de novo |

Arquivos: `backend/auth_router.py`, `backend/auth_session_service.py`
(`extract_refresh_token_from_request`), `Frontend/src/lib/useAuth.ts`,
`Frontend/src/lib/authSessionKeepAlive.ts`, `Frontend/src/lib/api.ts`.

### Console: `[AUTH_VISIBILITY_REFRESH_ERROR]` / “access control” em `/auth/refresh`

Ao voltar para a aba, o frontend chama `refreshAuthSession(true)`. Se o
`backend-gateway` estiver reiniciando (deploy), o Nginx devolve **502 sem
CORS** e o Safari/Chrome rotulam como falha de access control / `Load failed`.

Não é lista de `CORS_ORIGINS` errada. Após `/health` = 200, um reload limpa o
erro. Ver também `DEPLOY_VPS.md` (janela de 502) e o incidente de email/saldo
em `BULLEX_CREDENCIAIS.md`.

### Sintoma antigo (corrigido)

1. Usuário deixa o painel aberto / muda de aba por mais de ~1h.
2. Botões e dados param de responder (“Não autenticado”) sem ir para `/login`.
3. F5 “consertava” (remonta estado; às vezes pedia login de novo se o refresh
   também tivesse sido limpo).

## Crash no painel após login / ao abrir `/admin`

Se a UI cai em “Esta página não carregou” logo após `[AUTH USER CHANGED]` /
`[ROBOT STATE RESET]`, **não** é falha de cookie/sessão. Pode ser:

1. **`NotFoundError: removeChild`** — Tradutor do Chrome + unmount agressivo
   (corrigido 2026-07-22).
2. **`CancelledError` / `cancelQueries`** ao abrir `/admin` — o
   `AuthUserBoundary` cancelava o `ensureQueryData` do beforeLoad na mesma
   pintura do primeiro bind da sessão (corrigido 2026-08-07).

Detalhes, correção e checklist:
[`CHROME_REMOVECHILD_LOGIN.md`](./CHROME_REMOVECHILD_LOGIN.md).

## Problemas conhecidos / backlog

| Item | Severidade | Status |
|------|------------|--------|
| Access token ~1h sem renovar refresh cookie | média | **corrigido 2026-08-03** (refresh silencioso + `POST /auth/refresh`) |
| Docs antigos diziam que `ADMIN_EMAILS` libera `/admin` | baixa | corrigido neste doc |
| Contas da equipe sem `is_admin` tentando painel | operacional | promover ou usar admin@ |
