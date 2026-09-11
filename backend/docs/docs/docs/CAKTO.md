# Integração Cakto — El Capo AutoBot

Documentação operacional e técnica da cobrança via Cakto.

## Visão geral

- **Um produto padrão** na conta Cakto (configurado no `.env`).
- **Várias ofertas** (Mensal, Semestral, Anual…) criadas pelo admin Financeiro.
- Ao salvar uma **nova oferta** no painel, o backend cria a oferta correspondente
  na Cakto (`POST /public_api/offers/`) e grava `cakto_offer_id` + checkout.
- Webhooks e reconciliação atualizam assinaturas e o ledger local.

O frontend **nunca** recebe `client_secret`, webhook secret nem service role.

## Variáveis de ambiente

Prefixo conforme `APP_ENV`: `DEV_` | `STAGING_` | `PROD_`.

| Variável | Obrigatória se enabled | Descrição |
|----------|------------------------|-----------|
| `CAKTO_ENABLED` | — | `true` liga a integração |
| `CAKTO_WEBHOOK_SECRET` | sim (≥16) | Secret do body do webhook |
| `CAKTO_OAUTH_CLIENT_ID` | para API | Client ID do painel Cakto API |
| `CAKTO_OAUTH_CLIENT_SECRET` | para API | Client Secret |
| `CAKTO_OAUTH_TOKEN` | opcional | Bearer estático (fallback) |
| `CAKTO_DEFAULT_PRODUCT_ID` | para criar ofertas | ID do produto único na Cakto |
| `CAKTO_API_BASE_URL` | default oficial | `https://api.cakto.com.br` |
| `PUBLIC_API_URL` | sim | Base pública, ex.: `https://api.elcapobot.online` |

Exemplo produção:

```text
PROD_CAKTO_ENABLED=true
PROD_CAKTO_WEBHOOK_SECRET=<secret-forte>
PROD_CAKTO_OAUTH_CLIENT_ID=<client-id>
PROD_CAKTO_OAUTH_CLIENT_SECRET=<client-secret>
PROD_CAKTO_DEFAULT_PRODUCT_ID=<uuid-ou-id-do-produto>
PROD_CAKTO_API_BASE_URL=https://api.cakto.com.br
PUBLIC_API_URL=https://api.elcapobot.online
```

Após alterar o `.env`, reinicie o `backend-gateway`.

## Fluxo de criação de oferta

1. Admin preenche **apenas** nome, preço, ciclo e apresentação em **Financeiro → Ofertas**.
2. A etapa **Oferta Cakto** **não** pede ID de produto/oferta/checkout na criação:
   mostra o produto padrão do `.env` e explica que o vínculo será automático.
3. `POST /admin/finance/plans` (sem `cakto_offer_id` / `checkout_url`).
4. `FinanceService` resolve `CAKTO_DEFAULT_PRODUCT_ID`.
5. `CaktoService.create_offer` chama a API pública com tipo `subscription`.
6. Resposta traz `id` da oferta → checkout `https://pay.cakto.com.br/{id}`.
7. Plano é persistido em `billing_plans` com produto + oferta + checkout.

IDs manuais aparecem **somente** ao **editar** uma oferta já existente (migração /
ajuste). Se a criação automática estiver indisponível, o modal bloqueia o
salvamento e orienta a conferir Configurações (flags verdes).

### Por que a UI pedia IDs manuais?

Com `PROD_CAKTO_ENABLED=false` o backend **descarta** OAuth e
`CAKTO_DEFAULT_PRODUCT_ID` no startup. Aí `offers_provisioning_configured`
fica `false` e o formulário caía no modo migração (produto + oferta + checkout
obrigatórios). Em produção o `.env` efetivo é `/opt/elcapo/backend/.env`
(não basta preencher só o espelho em `/root/Backend/.env`).

## Webhook

URL a cadastrar no painel Cakto:

```text
https://api.elcapobot.online/webhooks/cakto
```

Eventos essenciais: `purchase_approved`, `purchase_refused`, `refund`,
`chargeback`, `subscription_created`, `subscription_canceled`,
`subscription_renewed`, `subscription_renewal_refused`.

Validação: `secret` no body com `hmac.compare_digest` (a Cakto não assina o
corpo). Resolução de tenant pela **oferta** (`cakto_offer_id`), nunca por
`company_id` do payload.

### Fluxo automático na compra aprovada

1. Cakto `POST /webhooks/cakto` com `purchase_approved` (ou renovação).
2. Backend valida o `secret` (401 se divergir do `.env`).
3. Se o e-mail ainda não tem perfil → cria usuário Auth + linha em
   `user_access_profiles` + link de primeiro acesso.
4. Libera `grant_access=true`, `payment_status=paid` e `approval_status=approved`.
5. Enfileira e-mail nativo `purchase.completed` (aba **E-mails → Entregas**).
6. Responde **202** mesmo se destinos HTTP de saída falharem (a liberação já
   foi persistida).

#### Ordem dos webhooks Cakto (importante)

Na prática a Cakto envia, em sequência curta:

1. `pix_gerado` (ou equivalente) → ledger `pending`; **não** libera acesso.
2. `purchase_approved` → **pago** + notificação de venda + libera acesso.
3. `subscription_created` → **somente ledger** (não altera `payment_status` /
   `grant_access`).

**Bug corrigido em 2026-08-09:** `subscription_created` era mapeado para
`payment_status=pending` + `grant_access=false`. Quando chegava *depois* de
`purchase_approved`, a conta voltava para **“Ainda não pagou”** mesmo com a
venda já notificada no admin. Eventos soft (`pix_gerado` etc.) também não
podem mais rebaixar um cliente já `paid`.

| Evento | Ledger | Acesso do cliente |
|--------|--------|-------------------|
| `purchase_approved` / `subscription_renewed` | approved + receita | `paid`, `grant_access=true` |
| `subscription_created` | pending (informativo) | **sem mudança** |
| `pix_gerado` / boleto / etc. | pending | `pending` só se ainda não for `paid` |
| `refund` / `chargeback` / cancel / refuse | negativo | revoga acesso |

### Troubleshooting — compras sem liberação / Entregas vazia

| Sintoma | Causa típica | Correção |
|---------|--------------|----------|
| Nginx `POST /webhooks/cakto` → **401** | `PROD_CAKTO_WEBHOOK_SECRET` ≠ secret do app webhook na Cakto | Alinhar o `.env` com o secret do painel (API `GET /public_api/webhook/` / histórico) e redeploy |
| Nginx **500** após auth OK | Filtro PostgREST inválido em `outgoing_webhook_endpoints` ou falha ao enfileirar e-mail | Backend resiliente: e-mail enfileira mesmo sem destinos; filtro usa `{evento}` Postgres |
| Conta Auth criada sem acesso | Perfil `user_access_profiles` ausente | `ensure_purchase_customer` no webhook de compra |
| Entregas de e-mail vazia | Eventos nunca processados (401) **ou** tabelas `email_*` ausentes | Corrigir secret + migration `migration_native_emails.sql` |

Para recuperar compras já pagas após alinhar o secret: reprocessar o histórico
Cakto (`POST /admin/finance/reconcile` ou reenvio dos eventos no painel).

## Settings (admin)

`GET /admin/finance/settings` devolve apenas flags:

- `webhook_configured`
- `api_configured`
- `offers_provisioning_configured` (API + produto padrão)
- `default_product_id`
- `webhook_url`
- timestamps de último evento / reconciliação

## Arquivos

| Camada | Arquivo |
|--------|---------|
| Config / HTTP Cakto | `Backend/backend/cakto_service.py` |
| Regras | `Backend/backend/finance_service.py` |
| Rotas | `Backend/backend/finance_router.py` |
| UI admin | `Frontend/src/routes/_authenticated/admin.financeiro.tsx` |
| Modal scroll | `Frontend/src/components/AdminFormDialog.tsx` |
| Testes | `Backend/tests/test_finance.py` |

## Checklist de go-live

1. Criar **um produto** na Cakto (assinatura).
2. Copiar o ID do produto → `PROD_CAKTO_DEFAULT_PRODUCT_ID`.
3. Gerar OAuth + webhook secret no painel Cakto API.
4. Preencher `.env`, reiniciar gateway.
5. Cadastrar URL do webhook + eventos.
6. Em Admin → Financeiro → Configurações, confirmar as três flags verdes.
7. Criar oferta de teste e verificar na Cakto + checkout no catálogo.

## Limitação conhecida

Reconciliação (`POST /admin/finance/reconcile`) processa no máximo uma página
(50 eventos). Agendamento periódico via worker ainda não está implementado.

## Changelog

- **2026-08-09** — `subscription_created` (e soft events como `pix_gerado`) não
  rebaixam mais cliente já `paid` para “Ainda não pagou” após a notificação de
  venda. Causa: webhook de assinatura chegava logo após `purchase_approved` e
  `_access_rule` forçava `pending` + `grant_access=false`.
- **2026-08-07** — Webhooks Cakto em produção retornavam 401 (secret do painel
  divergente do `.env`). Após alinhar o secret, 500 em
  `list_subscribed_endpoints` (literal JSON em coluna array Postgres) impedia
  e-mail/entregas. Correções: secret alinhado, filtro `{evento}`, fila de
  e-mail independente de destinos HTTP, `ensure_purchase_customer` na 1ª compra.
