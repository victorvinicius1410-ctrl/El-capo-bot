# Registro de leads e aprovação administrativa — El Capo

Documentação do cadastro público, fila de aprovação e liberação por dias de acesso
ou compra de plano. Atualizado em **2026-08-07**.

## Objetivo

Permitir que um lead se cadastre sozinho no painel (`app.elcapobot.online/register`)
sem receber acesso operacional imediato. Um administrador precisa:

1. Abrir **Admin → Acessos → Pedidos**
2. Aprovar e informar **quantos dias** de acesso

Alternativa do lead: comprar um plano em **Financeiro** e liberar na hora via
webhook Cakto (`purchase_approved` / renovação).

## Performance (Pedidos / Aprovar) — 2026-08-07

### Sintoma

Abrir **Acessos → Pedidos** e clicar em **Aprovar** demorava vários segundos
(às vezes o admin clicava de novo e o mesmo lead era aprovado 2–3 vezes).

### Causas

1. `GET /admin/clients?segment=pending` buscava até **100 clientes quaisquer**
   (`offset` sempre 0 no banco) e filtrava `approval_status=pending` **em
   memória**. Com muitos leads/aprovados, cada abertura da aba era pesada e a
   paginação mentia.
2. `POST .../approve` esperava **webhooks + Celery** (`TRIAL_STARTED` /
   `end_trial`) antes de responder ao browser.
3. Cada chamada ao Supabase no repositório admin abria um `httpx.AsyncClient`
   novo (handshake repetido).
4. UI podia reenviar o form enquanto a primeira aprovação ainda rodava.

### Mitigações (rodada 1)

| Camada | Mudança |
|--------|---------|
| Router | Segmentos `pending` / `active` / `inactive` / `trial` / `marketing` filtram no PostgREST (`approval_status`, `grant_access`, paginação real) |
| Approve | Persiste acesso e responde; webhooks vão em `BackgroundTasks` |
| Repository | `httpx.AsyncClient` compartilhado; audit+lifecycle em paralelo no approve |
| Frontend | Remoção otimista do card em Pedidos + bloqueio de double-submit |

### Mitigações (rodada 2 — carga / velocidade de abertura)

| Camada | Mudança |
|--------|---------|
| Cache BE | `admin_clients_cache.py` — TTL **20 s** por `company_id:segment:offset:limit` |
| Invalidate | Approve/CRUD limpam cache de clientes **e** dashboard do tenant |
| Approve | `defer_side_effects=True`: HTTP responde após `save_client`; audit + lifecycle + webhooks no background |
| Dashboard | Páginas de clientes após a 1ª vão em **lotes paralelos de 3** |
| Índice SQL | Migration `20260807020000_pending_leads_partial_index.sql` (`user_access_profiles_pending_idx`) |
| Frontend | Prefetch Pedidos no hover da sidebar + `loader` da rota; `keepPreviousData` ao trocar segmento; `staleTime` Pedidos **45 s** (`adminClientsQuery.ts`) |

### Contrato de filtros

```http
GET /admin/clients?segment=pending&limit=10&offset=0
→ user_access_profiles
   company_id=…&is_admin=eq.false
   &approval_status=eq.pending&grant_access=eq.false&deleted_at=is.null
   &limit=11&offset=0
```

```http
POST /admin/clients/{id}/approve
Body: { "access_days": 7 }
→ 200 com cliente aprovado (trial) após persistência
→ audit + lifecycle + webhooks TRIAL_STARTED em background
```

## Fluxo completo

```text
Lead → POST /auth/register
     → Auth user (Supabase) + user_access_profiles
       approval_status=pending, grant_access=false
     → cookies de sessão (opcional)
     → UI: aviso "Aguardando aprovação" + planos

Admin → POST /admin/clients/{id}/approve { access_days: N }
      → approval_status=approved
      → account_type=trial, payment_status=not_required
      → expires_at=now+N days, grant_access=true
      → (async) webhook trial_started + agenda end_trial

OU

Lead → GET /billing/checkout/{plan_id} → Cakto
     → webhook purchase_approved
     → grant_access=true, approval_status=approved
```

## Estados de acesso (`/me/access`)

| `access_status`      | Quando                                      | Rotas liberadas              |
|----------------------|---------------------------------------------|------------------------------|
| `active`             | `grant_access=true`                         | Todas                        |
| `pending_approval`   | `approval_status=pending` e sem acesso      | `/payments`, `/feedbacks`    |
| `inactive`           | Sem acesso e não pendente (ex.: expirado)   | `/payments`, `/feedbacks`    |

Campo adicional: `approval_status` ∈ `pending` | `approved` | `rejected`.

## Contratos REST

### Público

- `POST /auth/register`
  - Body: `{ name, email, password, phone? }`
  - Senha: 8+ caracteres, maiúscula, minúscula e número
  - `company_id` **nunca** vem do frontend (fixado no servidor: empresa ElCapo)
  - Resposta 201: usuário + `approval_status=pending` + sessão iniciada quando possível

### Admin (sessão admin + RBAC)

- `GET /admin/clients?segment=pending|active|trial|marketing|inactive`
- `POST /admin/clients/{user_id}/approve`
  - Body: `{ access_days: 1..365 }`
  - Permissões: `clients.update` ou `clients.edit`

## Banco de dados

Coluna em `user_access_profiles`:

```sql
approval_status text not null default 'approved'
  check (approval_status in ('pending', 'approved', 'rejected'));
```

Migration:

`frontend/supabase/migrations/20260806220000_lead_registration_approval.sql`

Contas existentes ficam `approved` (default). Leads novos nascem `pending`.

Índice recomendado (se ainda não existir) para a fila de Pedidos:

```sql
create index if not exists user_access_profiles_pending_idx
  on public.user_access_profiles (company_id, created_at desc)
  where approval_status = 'pending' and grant_access = false and deleted_at is null;
```

Migration do índice parcial:

`frontend/supabase/migrations/20260807020000_pending_leads_partial_index.sql`

## Frontend

| Rota / tela              | Comportamento |
|--------------------------|---------------|
| `/register`              | Formulário de cadastro (visual alinhado ao login) |
| `/login`                 | Link “Cadastre-se” |
| AppShell (lead pendente) | Modal: aguardar admin **ou** ir ao Financeiro |
| `/payments`              | Banner de aprovação + cards de planos |
| `/admin/clientes` (menu **Acessos**) | Aba **Pedidos** + botão **Aprovar acesso** (dias); remoção otimista do card ao aprovar |

## Arquivos principais

Backend:

- `backend/registration_service.py` — regras do cadastro público
- `backend/auth_router.py` — `POST /auth/register`
- `backend/admin_service.py` — `approve_lead` (+ `complete_lead_approval_side_effects`)
- `backend/admin_router.py` — segmentos filtrados + cache 20 s + approve deferido
- `backend/admin_clients_cache.py` — cache curto da listagem por segmento
- `backend/supabase_admin_repository.py` — filtros PostgREST + client HTTP reutilizado
- `backend/auth_service.py` / `main.py` — propaga `approval_status`
- `backend/finance_repository.py` — compra aprova o lead

Frontend:

- `src/routes/register.tsx`
- `src/lib/api.ts` — `registerWithPassword`, `adminApproveClient`
- `src/lib/adminClientsQuery.ts` — queryKey + prefetch Pedidos
- `src/components/AppShell.tsx` — prefetch no hover de Acessos
- `src/routes/_authenticated/payments.tsx`
- `src/routes/_authenticated/admin.clientes.tsx` — loader + keepPreviousData

Testes:

- `backend/tests/test_lead_registration.py`
- `backend/tests/test_admin_client_segments.py`
- `backend/tests/test_admin_clients_cache.py`

## Segurança

- Sem `SUPABASE_SERVICE_ROLE_KEY` no frontend
- Escrita de perfil só no backend (Admin API + PostgREST service role)
- Isolamento por `company_id` da sessão admin na aprovação
- Cookies de sessão `httpOnly` / `Secure` / `SameSite=Lax` (mesmo padrão do login)

## Operação / deploy

1. Aplicar a migration SQL no Supabase (SQL Editor ou CLI), incluindo o índice
   parcial `user_access_profiles_pending_idx`
   (`20260807020000_pending_leads_partial_index.sql`).
2. Deploy backend: `/opt/elcapo/scripts/deploy-backend.sh`
3. Build + publish frontend:
   - `cd /opt/elcapo/frontend && npm run build`
   - `/opt/elcapo/scripts/publish-frontend.sh`

## Verificação rápida

```bash
# Cadastro (esperado 201 + pending)
curl -sS -X POST https://api.elcapobot.online/auth/register \
  -H 'Content-Type: application/json' \
  -H 'Origin: https://app.elcapobot.online' \
  -d '{"name":"Lead Teste","email":"lead.teste@example.com","password":"SenhaForte1"}'

# Login do lead → /me/access deve trazer access_status=pending_approval
# Admin aprova com N dias → access_status=active, account_type=trial
# Pedidos: Network mostra GET .../clients?segment=pending com resposta rápida
# Approve: POST responde sem esperar entrega de webhook externo
```
