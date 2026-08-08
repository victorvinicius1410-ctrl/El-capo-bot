# Backend El Capo

Gateway FastAPI (`backend/`) + serviço BullEx (`bullex_service/`).

## Documentação operacional

- Execução local (portas, env, conta demo): [`../docs/LOCALHOST.md`](../docs/LOCALHOST.md)
- Operação / timeframe / estratégia clássica: [`../docs/OPERACAO.md`](../docs/OPERACAO.md)
- Estratégia de análise (`backup-classic`): [`../docs/ESTRATEGIA.md`](../docs/ESTRATEGIA.md)
- Checklist CPU BullEx: [`docs/bullex_cpu_100_checklist.md`](docs/bullex_cpu_100_checklist.md)
- Webhooks de saída e API: [`../docs/WEBHOOKS_API.md`](../docs/WEBHOOKS_API.md)
- Emails nativos (Resend + templates HTML): [`../docs/EMAILS.md`](../docs/EMAILS.md)
- Histórico e resultados simulados de marketing:
  [`../docs/MARKETING_SIMULATION.md`](../docs/MARKETING_SIMULATION.md)

## Portas locais padrão

| Serviço | Porta |
|---------|-------|
| Gateway | `8080` |
| BullEx service | `8001` |

Health: `GET http://127.0.0.1:8080/health`

## Fundação administrativa segura

As rotas novas em `/admin` aceitam exclusivamente
`Authorization: Bearer <access-token-supabase>`. Os headers legados
`x-user-id`, `x-user-email`, `x-api-key` e `PANEL_API_KEY` não autenticam esses
contratos. O backend valida o token em `/auth/v1/user`, extrai `user_id` e
`company_id` da identidade verificada e carrega RBAC pelo mesmo tenant.

Variáveis obrigatórias em ambiente integrado:

```text
SUPABASE_URL=https://<projeto>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<somente-no-backend>
APP_ENV=development|staging|production
```

A service role nunca deve ser enviada ao navegador. Toda consulta PostgREST do
módulo administrativo inclui `company_id` explicitamente.

## Backend financeiro e Cakto

O domínio financeiro está separado em:

- `backend/finance_models.py`: ofertas (contrato legado de planos), eventos e estado operacional;
- `backend/finance_repository.py`: contratos, memória e PostgREST assíncrono;
- `backend/finance_service.py`: regras, RBAC, idempotência, acesso e métricas;
- `backend/cakto_service.py`: configuração server-only, secret e OAuth;
- `backend/finance_router.py`: validação HTTP e delegação.

## Webhooks de saída

O domínio está separado em `webhook_models.py`, `webhook_repository.py`,
`webhook_service.py` e `webhook_router.py`. Segredos usam
`services/encryption_service.py`; entregas e fim de trial rodam no worker
`workers/webhook_tasks.py`.

Configuração por ambiente:

```text
DEV_WEBHOOKS_ENABLED=true
DEV_ENCRYPTION_KEY=<32 bytes Base64 URL-safe>
DEV_REDIS_URL=redis://redis:6379/0
FRONTEND_URL=http://localhost:5173
```

Troque `DEV_` por `STAGING_` ou `PROD_`. Em banco novo, aplique
`backend/el_capo_full_bootstrap.sql` (ver `docs/BANCO_DE_DADOS.md`). Em banco já
existente, a migration `20260718123000_outgoing_webhooks.sql` cria endpoints,
outbox, entregas, RLS, permissões e a RPC transacional. O frontend nunca recebe
segredo criptografado, service role ou senha.

O gateway usa FastAPI `0.139.2`/Starlette `1.3.1`; a atualização remove as
vulnerabilidades conhecidas detectadas no pin anterior pelo `pip-audit`.

Contratos:

- `GET|POST /admin/webhooks`
- `PATCH|DELETE /admin/webhooks/{endpoint_id}`
- `GET /admin/webhooks/catalog`
- `GET /admin/webhook-deliveries`
- `POST /admin/webhook-deliveries/{delivery_id}/replay`
- `POST /auth/password-recovery`

Configuração:

```text
DEV_CAKTO_ENABLED=false
DEV_CAKTO_WEBHOOK_SECRET=<mínimo 16 caracteres>
DEV_CAKTO_OAUTH_CLIENT_ID=<client id server-only>
DEV_CAKTO_OAUTH_CLIENT_SECRET=<client secret server-only>
DEV_CAKTO_OAUTH_TOKEN=<token temporário opcional>
DEV_CAKTO_DEFAULT_PRODUCT_ID=<id do produto padrão na Cakto>
DEV_CAKTO_API_BASE_URL=https://api.cakto.com.br
PUBLIC_API_URL=https://api.exemplo.com
```

Troque `DEV_` por `STAGING_` ou `PROD_` conforme `APP_ENV`. A reconciliação
obtém um Bearer temporário em `/public_api/token/` usando client credentials;
um token estático permanece apenas como fallback operacional.

`CAKTO_DEFAULT_PRODUCT_ID` é o produto único da conta Cakto. Ao criar uma oferta
em `POST /admin/finance/plans` sem `cakto_offer_id`, o backend chama
`POST /public_api/offers/` nesse produto, grava o ID retornado e monta o
checkout `https://pay.cakto.com.br/{offer_id}`. O frontend nunca recebe OAuth
nem o secret do webhook.

Com a integração desabilitada, nenhuma credencial é obrigatória. Habilitá-la
exige um segredo válido. Credenciais não são aceitas pelo frontend, persistidas,
retornadas ou logadas.

Fluxo híbrido:

1. `POST /webhooks/cakto` recebe eventos em tempo real.
2. O secret do body é comparado por `hmac.compare_digest`.
3. A oferta globalmente única resolve `billing_plans` e, portanto, o `company_id`;
   o produto compartilhado é validado quando informado, e qualquer `company_id`
   do payload é ignorado.
4. `billing_events` garante idempotência por
   `(provider, company_id, provider_event_key)`.
5. Eventos confirmados também alimentam `subscription_revenue_events`, mantendo
   o dashboard administrativo atual.
6. `POST /admin/finance/reconcile` usa `httpx.AsyncClient` e processa no máximo
   uma página de 50 eventos. Agendamento recorrente deve ser feito futuramente
   por worker/agendador externo.
7. `POST /admin/finance/plans` provisiona a oferta na Cakto quando a API e o
   produto padrão estão configurados.

A Cakto documenta secret no payload, mas não assinatura criptográfica do corpo.
Logo, existe uma limitação de integridade/anti-replay comparada a HMAC assinado;
a idempotência impede efeitos financeiros duplicados.

Contratos:

- `GET /billing/plans`
- `GET /billing/history?limit=20&offset=0`
- `GET /billing/checkout/{plan_id}`
- `GET|POST /admin/finance/plans`
- `PATCH|DELETE /admin/finance/plans/{plan_id}`
- `GET /admin/finance/metrics?days=30`
- `GET /admin/finance/settings`
- `POST /admin/finance/reconcile`

RBAC deny-by-default:

- `finance.view`
- `finance.plans.manage`
- `finance.settings.manage`
- `finance.reconcile`

Migration:

`../Frontend/supabase/migrations/20260718090000_cakto_finance_backend.sql`
adiciona catálogo, ledger append-only, estado operacional, RLS, índices,
constraints, permissões e os três planos iniciais inativos. A migration não é
aplicada automaticamente nem remotamente por este repositório.

`../Frontend/supabase/migrations/20260718115500_billing_product_offers.sql`
permite que uma empresa reutilize o mesmo produto Cakto em várias ofertas,
preserva `plans`/`plan_id` nos contratos por compatibilidade e faz a resolução do
webhook pela oferta. O produto não pode ser compartilhado entre empresas.

### Contratos REST

- `GET /admin/overview`
- `GET /admin/users?stage=active|trial|marketing|inactive&search=&limit=&offset=`
- `GET /admin/users/{id}`
- `POST /admin/users`
- `PATCH /admin/users/{id}`
- `DELETE /admin/users/{id}`
- `GET /admin/users/{id}/history`
- `GET|POST /admin/access-users`
- `PATCH|DELETE /admin/access-users/{id}`
- `POST /admin/support-sessions`
- `GET /admin/support-sessions/current`
- `DELETE /admin/support-sessions/{id}`

Os contratos legados `/admin/clients`, `/admin/admins` e
`/admin/impersonations` foram preservados para não quebrar o frontend atual.
As rotas de feedback continuam usando `require_admin`, que valida JWT Supabase
quando o Supabase está configurado; o fallback de headers permanece restrito ao
modo local explicitamente habilitado.

### Clientes e transições

`customer_type` aceita `trial`, `client` ou `marketing`. Trial tem expiração
calculada no servidor entre 1 e 365 dias. Cliente exige estado de pagamento.
Marketing opera com o **mesmo fluxo visual e operacional** do cliente (BullEx +
robô + ciclos 1m/5m/15m). O placar das operações ao vivo usa o WIN/LOSS **real**
da corretora; a taxa (`marketing_target_win_rate` / `marketing_win_rate`, 0–100)
vale só no painel oculto Shift+O (resultado AUTO / geração de histórico).

Contas `marketing` com `marketing_mode=simulation` gerenciam o histórico
editável da própria sessão. `company_id` e `user_id` vêm da identidade
autenticada. Contratos do painel Shift+O:

- `POST /marketing-simulation/trades` — gera trade sintético (`201`);
  body opcional: amount, payout, asset, direction, result (WIN|LOSS);
- `POST /marketing-simulation/generate-history` — gera histórico pelo placar
  (`201`); body: wins, losses, amount, period?, asset?, payout?;
- `GET /marketing-simulation/history` — lista o histórico sintético (`200`);
- `GET /marketing-simulation/stats` — placar derivado (wins/losses/profit) (`200`);
- `PATCH /marketing-simulation/trades/{trade_id}` — edita resultado, lucro,
  valor, ativo, direção e payout (`200`);
- `DELETE /marketing-simulation/trades/{trade_id}` — exclui o trade (`204`).

PATCH e DELETE recarregam o cache do simulador para que as estatísticas e o
próximo resultado reflitam imediatamente o histórico persistido. Consulte
[`../docs/MARKETING_SIMULATION.md`](../docs/MARKETING_SIMULATION.md) para
payloads, validações, erros e execução dos testes.

Senhas exigem oito ou mais caracteres, letra maiúscula, letra minúscula e
número. A criação e a alteração usam Supabase Admin Auth; senha, token, email,
telefone e identificador da corretora não entram na auditoria.

### RBAC e suporte

Permissões canônicas:

- `clients.create`, `clients.edit`, `clients.delete`
- `clients.view_history`, `clients.access_account`
- `admins.create`, `admins.edit`, `admins.delete`

O RBAC é deny-by-default. Um administrador só delega permissões que possui e
só gerencia cargos presentes em `manageable_roles`; `all` é persistido como
`can_manage_all_roles`.

Sessões de suporte separam ator e sujeito, duram exatamente 15 minutos e
possuem somente `account.view` e `broker_account.edit`. O token opaco é salvo
apenas como SHA-256. Em contexto de suporte, endpoints de robô, configuração,
reset, tick, compra real, gráfico e websocket são negados.

### Banco e auditoria

A migration
`../Frontend/supabase/migrations/20260718083000_secure_admin_foundation.sql`
expande perfis, preserva usuários na empresa padrão, atualiza
`app_metadata.company_id`, registra permissões canônicas e cria:

- `support_access_sessions`, com expiração, revogação e escopo fixo;
- `admin_customer_events`, append-only, com ator, sujeito, ação, snapshots
  sanitizados e `request_id`.

Todas as tabelas administrativas têm RLS e índices iniciados por `company_id`.

### Testes

```powershell
python -m unittest tests.test_admin_secure_foundation
python -m unittest tests.test_admin_management
python -m unittest tests.test_marketing_simulation_management
python -m unittest tests.test_finance
python -m unittest tests.test_outgoing_webhooks
python -m unittest discover -s tests
```

Os testes específicos cobrem JWT inválido, headers forjados, isolamento por
empresa, deny-by-default, validações de senha/trial/marketing, CRUD e histórico,
expiração/revogação de suporte e bloqueio de capacidades perigosas.
Os testes de simulação cobrem edição/exclusão por conta marketing, bloqueio de
contas reais, isolamento por empresa e usuário, payloads inválidos e
sincronização do cache após mutação.
Os testes financeiros cobrem URLs de checkout, isolamento, RBAC, idempotência,
eventos positivos/negativos/informativos, proteção de marketing, métricas,
configuração desabilitada, reconciliação assíncrona e contratos REST.


## Deploy nesta VPS

- Layout e operação: [`docs/DEPLOY_VPS.md`](docs/DEPLOY_VPS.md)
- Autenticação e criação de admin: [`docs/AUTENTICACAO.md`](docs/AUTENTICACAO.md)
- DNS do domínio: [`docs/DNS.md`](docs/DNS.md)
- Script: `/opt/elcapo/scripts/deploy-backend.sh`

Stack: `docker compose -p elcapooneline --env-file .env up -d --build` em `/opt/elcapo/backend`.
