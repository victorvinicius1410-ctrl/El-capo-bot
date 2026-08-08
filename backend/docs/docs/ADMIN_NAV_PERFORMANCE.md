# Performance da navegação admin

Como a troca de abas no painel `/admin/*` era lenta e o que foi otimizado.
Atualizado em **2026-08-07**.

## Sintoma

Como admin, clicar entre itens da sidebar (Dashboard, Acessos, Financeiro,
Webhooks, Emails, Feedbacks) demorava vários segundos antes da nova tela
aparecer, mesmo com o shell já montado.

## Causas (antes)

### 1. Gate de rede a cada aba (`beforeLoad`)

`frontend/src/routes/_authenticated/admin.tsx` chamava `getMyAccess()` em
**toda** navegação filha. O TanStack Router reexecuta o `beforeLoad` do match
pai mesmo quando a causa é `stay`, então a pintura da nova aba esperava:

1. `GET /auth/session` (via `apiRequest`)
2. `GET /me/access`

### 2. Sessão duplicada em toda `apiRequest`

`getSessionIdentity()` fazia `GET /auth/session` **sem cache**. Qualquer
endpoint (e o polling do robô) virava 2 round-trips.

### 3. Cache de access quebrado

| Local | `queryKey` | `staleTime` |
|-------|------------|-------------|
| AppShell | `["me", "access", userId]` | 30s |
| Páginas admin | `["me", "access"]` | 0 (default) |

O AppShell já tinha o perfil; as abas **não reutilizavam** e refetchavam.

### 4. Polling BullEx/robô no painel admin

`LiveTradingDataProvider` + `FloatingRobot` ficavam ativos em `/admin/*`,
competindo por rede/CPU a cada 1–10s.

### 5. Fetch excessivo por página

- Webhooks carregava destinos + catálogo + entregas juntos.
- Emails carregava templates mesmo na seção Entregas.
- QueryClient sem `staleTime` default → remount = refetch.

## Mitigações aplicadas

| Mudança | Arquivo(s) | Efeito |
|---------|------------|--------|
| `ensureMeAccessData()` no `beforeLoad` | `admin.tsx`, `meAccessQuery.ts` | Gate usa cache fresco (60s); retry se a query foi cancelada no bind da sessão |
| Soft reset no primeiro bind (`cancelInFlightQueries: false`) | `authUserBoundary.ts`, `__root.tsx` | Evita `CancelledError` no error boundary ao abrir `/admin` (ver `CHROME_REMOVECHILD_LOGIN.md`) |
| Chave canônica `["me", "access"]` + `staleTime` 60s | `meAccessQuery.ts`, AppShell, páginas | Um único cache compartilhado |
| Cache em memória de `/auth/session` (TTL 30s) | `sessionIdentityCache.ts`, `api.ts` | Corta ~50% dos round-trips em navegação e polling |
| `showRobot = … && !pathname.startsWith("/admin")` | `AppShell.tsx` | Sem polling robô/BullEx no admin |
| Defaults RQ: `staleTime: 30s`, `refetchOnWindowFocus: false` | `router.tsx` | Remount de aba não refetcha tudo |
| `defaultPreloadDelay: 80` | `router.tsx` | Hover acidental na sidebar não dispara rede |
| Lazy fetch por seção | `admin.webhooks-api.tsx`, `admin.emails.tsx` | Só busca o que a seção precisa |
| `staleTime` explícito no dashboard/listas | `admin.dashboard.tsx`, clientes, feedbacks, etc. | Revisitar aba usa cache |

## Contrato do access query

```ts
// frontend/src/lib/meAccessQuery.ts
export const ME_ACCESS_QUERY_KEY = ["me", "access"] as const;
export const ME_ACCESS_STALE_TIME_MS = 60_000;

export function meAccessQueryOptions() { /* getMyAccess + ApiError */ }
export async function ensureMeAccessData(queryClient) { /* ensure + retry CancelledError */ }
```

Regras:

1. **Sempre** usar `meAccessQueryOptions()` (ou a mesma `queryKey`) para
   `/me/access`.
2. Invalidar com `queryClient.invalidateQueries({ queryKey: ["me", "access"] })`
   (prefix match cobre variantes futuras).
3. No `beforeLoad` de rotas protegidas por admin: preferir
   `ensureMeAccessData(context.queryClient)` — **nunca** `await getMyAccess()`
   frio. O helper faz retry se a query for cancelada no bind da sessão.

## Cache de sessão HTTP

```ts
// frontend/src/lib/sessionIdentityCache.ts
readSessionIdentityCache() / writeSessionIdentityCache() / clearSessionIdentityCache()
// TTL padrão: 120_000 ms (compartilhado com useAuth)
```

Invalidação obrigatória em:

- `logoutSession` / `clearAuthSnapshot`
- resposta `401` em `apiRequest`
- login bem-sucedido (`loginWithPassword`)

## Cache quente do dashboard (warmer 30 s)

Além do TTL de **75 s** em memória (`admin_dashboard_cache.py`), o gateway
mantém um **background warmer** (`admin_dashboard_warm.py`):

| Item | Valor |
|------|--------|
| Intervalo | **30 s** entre ciclos |
| Períodos | `days` ∈ `{7, 30}` |
| Escopo | `company_id` registrados ao bater em `GET /admin/dashboard` |
| Escrita | `compute(company_id, days)` → `write_admin_dashboard_cache` |
| Miss | log `[ADMIN_DASHBOARD_CACHE_MISS]` + compute síncrono no request |
| Invalidação | inalterada — mutações de cliente limpam o cache do tenant |

Com admin ativo, revisitas do dashboard costumam ser **cache hit <1 s**.
A 1ª carga de um tenant ainda frio pode demorar (batch + N clientes).
Detalhes: [`ADMIN_DASHBOARD_WARM.md`](./ADMIN_DASHBOARD_WARM.md).

## O que ainda pode ser lento

| Item | Nota |
|------|------|
| `GET /admin/dashboard` (1ª carga fria / tenant nunca visitado) | Batch de histórico + páginas paralelas de clientes. Depois do 1º hit, warmer 30 s mantém 7d/30d quentes (TTL cache 75 s). Ver [`PERFORMANCE_SISTEMA.md`](./PERFORMANCE_SISTEMA.md). |
| Listas grandes (clientes/feedbacks) | Pedidos: cache BE 20 s + prefetch no hover/loader + `staleTime` 45 s. Outros segmentos: prefetch no hover da aba. |
| Impersonação | Continua com `window.location.assign` (reload completo) — intencional. |

## Como validar

1. Login como admin → abrir DevTools Network.
2. Ir para `/admin/dashboard` (primeira carga: session + access + dashboard).
3. Alternar Dashboard ↔ Financeiro ↔ Acessos várias vezes em < 2 min.
4. Esperado: **sem** novo `GET /me/access` a cada clique; **sem**
   `/bullex/*` / `/robot/state` enquanto `pathname` começa com `/admin`.
5. Hover rápido na sidebar sem clicar: não deve spammar requests (preload delay 80ms).
6. Recarregar o dashboard em <75 s: resposta deve vir do cache backend (rápida).
7. Após 1ª visita, esperar ~30 s e recarregar 7d/30d: deve continuar quente
   (warmer), sem `[ADMIN_DASHBOARD_CACHE_MISS]` nos logs.

Teste unitário do cache de sessão:

```bash
cd /opt/elcapo/frontend
npx vitest run src/lib/sessionIdentityCache.test.ts
# ou: npm test
```

Testes do cache + warmer do dashboard:

```bash
cd /opt/elcapo/backend
PYTHONPATH=. /root/Backend/.venv/bin/python -m unittest \
  tests.test_admin_dashboard_warm \
  tests.test_admin_dashboard_cache -v
```

## Histórico

- **2026-08-07** — Warmer periódico 30 s do `admin_dashboard_cache` (7d/30d)
  + log `[ADMIN_DASHBOARD_CACHE_MISS]`. Ver [`ADMIN_DASHBOARD_WARM.md`](./ADMIN_DASHBOARD_WARM.md).
- **2026-08-07** — Batch + cache dashboard, TTL sessão 120 s, poll robô/Bullex
  mais lento. Ver [`PERFORMANCE_SISTEMA.md`](./PERFORMANCE_SISTEMA.md).
- **2026-08-07** — Soft reset auth (`cancel_queries: false`) no primeiro bind;
  ver [`CHROME_REMOVECHILD_LOGIN.md`](./CHROME_REMOVECHILD_LOGIN.md).
- **2026-08-06** — Item **E-mails** na sidebar admin sempre visível (`permission: null`),
  logo após Financeiro, rótulo com hífen.
- **2026-08-06** — Diagnóstico + mitigações (beforeLoad cache, session TTL,
  unificação de access, desligar robô no admin, lazy webhooks/emails).
