# Crash no login Chrome — `removeChild` / `CancelledError` / “Esta página não carregou”

Documento de diagnóstico e correção dos erros que faziam o painel cair para a
tela de erro genérica após login ou ao abrir `/admin`. Atualizado em
**2026-08-07**.

## Sintoma A — `removeChild` (relato original, 2026-07-22)

1. Usuário entra em `https://app.elcapobot.online` com conta normal.
2. No DevTools → Console aparece a sequência:
   - `[AUTH USER CHANGED] ▸ { previous_user_id, user_id }`
   - `[ROBOT STATE RESET]`
   - `NotFoundError: Failed to execute 'removeChild' on 'Node': The node to be removed is not a child of this node.`
3. A UI cai no `errorComponent` do root (“Esta página não carregou”).

### Causa A

Tradutor do Chrome (`lang="en"`) + unmount agressivo do `AuthUserBoundary` no
login. Corrigido com `lang="pt-BR"`, `translate="no"` e gate seletivo (só
userA→userB desmonta o Outlet).

## Sintoma B — `CancelledError` em `/admin` (2026-08-07)

1. Admin abre `https://app.elcapobot.online/admin/` (ou `/admin` após login).
2. Console:
   - `[AUTH USER CHANGED] ▸ { …, cancel_queries: … }`
   - `[ROBOT STATE RESET]`
   - vários `CancelledError` com stack em `cancelQueries` (`index-*.js`)
3. Tela: “Esta pagina nao carregou” / “Tentar novamente”.

### Causa B (raiz)

Corrida entre autenticação e o `beforeLoad` de `/admin`:

1. Sessão resolve → `AuthUserBoundary` vê primeiro bind `undefined → userId`.
2. Na mesma pintura o router roda `beforeLoad` de `/admin` com
   `ensureQueryData(meAccessQueryOptions())` (fetch de `GET /me/access`).
3. O boundary chamava **sempre** `queryClient.cancelQueries()` + `clear()`.
4. A promise do `ensureQueryData` rejeitava com `CancelledError`.
5. O TanStack Router tratava isso como erro de rota → `RootErrorComponent`.

Os `CancelledError` no console **são** o erro capturado pelo boundary (não
apenas ruído). Não é falha de API, cookie ou permissão admin.

### Correção B (2026-08-07)

| Camada | Mudança |
|--------|---------|
| `planAuthIdentityTransition` | Novo flag `cancelInFlightQueries`: **false** no primeiro bind, login e logout; **true** só em userA→userB |
| `AuthUserBoundary` | Soft reset (só stores Bullex/robô) quando `cancelInFlightQueries` é false — **não** chama `cancelQueries` |
| `ensureMeAccessData` (`meAccessQuery.ts`) | `ensureQueryData` + retry único via `fetchQuery` se vier `CancelledError` |
| `admin.tsx` `beforeLoad` | Usa `ensureMeAccessData` em vez de `ensureQueryData` direto |
| `RootErrorComponent` | Se o erro for cancelamento de query, mostra spinner e faz `invalidate`+`reset` (rede de segurança) |

### Tabela atualizada de transições

| Transição | Limpa stores? | `cancelQueries`? | Gate (desmonta Outlet)? |
|-----------|---------------|------------------|-------------------------|
| id igual | não | não | não |
| primeiro bind (`undefined → *`) | sim | **não** | não |
| login (`null → user`) | sim | **não** | não |
| logout (`user → null`) | sim | **não** | não |
| troca real (`userA → userB`) | sim | **sim** + `clear` | **sim** |

O `logoutSession()` do frontend continua responsável por `queryClient.clear()`
no logout explícito.

## Arquivos tocados

- `Frontend/src/routes/__root.tsx` — soft reset + recovery de CancelledError
- `Frontend/src/lib/authUserBoundary.ts` — planner com `cancelInFlightQueries`
- `Frontend/src/lib/authUserBoundary.test.ts`
- `Frontend/src/lib/meAccessQuery.ts` — `ensureMeAccessData`
- `Frontend/src/lib/queryCancellation.ts` — `isQueryCancellationError`
- `Frontend/src/lib/queryCancellation.test.ts`
- `Frontend/src/routes/_authenticated/admin.tsx` — beforeLoad resiliente
- `Frontend/src/lib/adminNavPerformance.test.ts` — contrato atualizado
- `Frontend/package.json` — inclui `queryCancellation.test.ts` no `npm test`

## Sintoma C — tela azul / `Uncaught undefined` em `/admin/dashboard` (2026-08-07)

1. Admin abre `https://app.elcapobot.online/admin/dashboard`.
2. Console:
   - `[AUTH USER CHANGED] ▸ { …, cancel_queries: false }`
   - `[ROBOT STATE RESET]`
   - **`Uncaught undefined`**
3. Tela: fundo escuro/azulado vazio (sem “Esta pagina nao carregou”).

### Causa C

O `ensureMeAccessData` fazia `throw error` quando a rejeição vinha **sem valor**
(`undefined`). Isso vira `unhandledrejection` no Chrome — o error boundary do
React **não** captura — e a UI fica no `bg-background` (`#03070a`).

### Correção C

| Camada | Mudança |
|--------|---------|
| `isBenignRouteLoadError` | Trata `null`/`undefined` + CancelledError como recuperáveis |
| `ensureMeAccessData` | Nunca relança nullish; retry + `ApiError` se payload vazio |
| `RootComponent` | Listener de `unhandledrejection` benigno (`preventDefault`) |
| `RootErrorComponent` | Recovery também para erro nullish |
| Soft reset | `try/catch` ao limpar stores Bullex/robô |
| `admin.tsx` | `access?.is_admin` defensivo |

## Como validar

```bash
cd /opt/elcapo/frontend
npm test
npm run build
/opt/elcapo/scripts/publish-frontend.sh
```

No Chrome (aba anônima ou hard refresh `Ctrl+Shift+R`):

1. Login como admin e abrir `/admin/dashboard`.
2. Painel deve carregar (dashboard ou spinner curto) — **sem** tela azul vazia.
3. Console: sem `Uncaught undefined` após `[ROBOT STATE RESET]`.
4. Hard refresh em `/admin/dashboard` com sessão já autenticada — mesma expectativa.

## Mitigação imediata (build antiga)

1. Hard refresh (`Ctrl+Shift+R`) após o publish.
2. Se ainda cair na tela de erro: “Ir para o inicio” e entrar de novo em
   `/admin/dashboard` (não só `/admin/`).
3. Desativar Tradutor do Chrome no site (ainda relevante para o sintoma A).

## Relação com autenticação e admin

- Cookies / `POST /auth/login` / `GET /auth/session` **não** estão quebrados
  nesse sintoma.
- Conta precisa de `is_admin=true` para passar no gate; se não for admin o
  comportamento correto é redirect para `/dashboard`, **não** esta tela de erro.
- Ver também [`AUTENTICACAO.md`](./AUTENTICACAO.md) e
  [`ADMIN_NAV_PERFORMANCE.md`](./ADMIN_NAV_PERFORMANCE.md).

## Histórico

- **2026-08-07** — Sintoma C (`Uncaught undefined` / tela azul) + recovery.
- **2026-08-07** — Sintoma B (`CancelledError` no beforeLoad admin).
- **2026-07-22** — Sintoma A (`removeChild` + tradutor Chrome).
