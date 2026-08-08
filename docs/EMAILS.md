# Emails nativos — El Capo AutoBot

O **servidor** envia os e-mails necessários (compra, trial, cobrança, recuperação
de senha). No **Admin → Emails** você configura o HTML, o assunto e se cada tipo
deve ser enviado.

Para pedir a uma IA que **crie/refine os templates**, use o brief pronto:

→ [`EMAILS_BRIEF_IA.md`](./EMAILS_BRIEF_IA.md)

## Fluxo

1. Evento no sistema (ex.: compra aprovada na Cakto, trial iniciado).
2. Backend verifica se o template daquele evento está **ativo** no tenant.
3. Renderiza `{{variaveis}}` e enfileira a entrega (Celery / worker).
4. Provedor entrega: **SMTP Hostinger** (recomendado) ou Resend.

## Variáveis de ambiente (produção)

Arquivo: `/opt/elcapo/backend/.env` (e espelho `/root/Backend/.env`).

```text
PROD_EMAILS_ENABLED=true
PROD_EMAIL_PROVIDER=smtp
PROD_SMTP_HOST=smtp.hostinger.com
PROD_SMTP_PORT=465
PROD_SMTP_USE_SSL=true
PROD_SMTP_USERNAME=suporte@visaodarota.com
PROD_SMTP_PASSWORD=<senha do e-mail na Hostinger>
PROD_EMAIL_FROM="ElCapo AutoBot <suporte@visaodarota.com>"
```

| Variável | Descrição |
|----------|-----------|
| `EMAILS_ENABLED` | `true` liga o envio |
| `EMAIL_PROVIDER` | `smtp` (Hostinger) ou `resend` |
| `SMTP_HOST` | `smtp.hostinger.com` |
| `SMTP_PORT` | `465` (SSL) ou `587` (STARTTLS) |
| `SMTP_USE_SSL` | `true` para porta 465 |
| `SMTP_USERNAME` / `SMTP_USER` | Endereço completo do mailbox |
| `SMTP_PASSWORD` | Senha do e-mail Hostinger |
| `EMAIL_FROM` | Remetente exibido (`Nome <email@dominio>`) |
| `RESEND_API_KEY` | Só se `EMAIL_PROVIDER=resend` |

Prefixo conforme `APP_ENV`: `PROD_` | `STAGING_` | `DEV_`.

Após alterar, reinicie:

```bash
/opt/elcapo/scripts/deploy-backend.sh
```


## Permissões

- Contas com `is_admin=true` recebem automaticamente `emails.view` e
  `emails.manage` (catálogo completo de permissões admin).
- A sidebar **E-mails** lista sempre os 9 eventos canônicos; editar assunto/HTML
  exige `emails.manage` ou admin.

## Admin → Emails

Acesso rápido: sidebar **E-mails** (`/admin/emails`), sempre visível para admin.

- **Templates HTML**: um template por evento; marque **Enviar este email** para ativar.
- **Variáveis**: clique para copiar `{{customer_name}}`, etc.
- **Salvar**: `PATCH /admin/emails/templates/{event}` (PUT legado ainda aceito).
- **Enviar teste**: usa o e-mail do admin logado (exige envio ativo no servidor).
- **Entregas**: histórico sanitizado (hash do destinatário, sem PII em texto).

### Persistência (importante)

1. **Preferido:** tabelas `public.email_templates` e `public.email_deliveries`.
   SQL: `Backend/backend/migration_native_emails.sql` (também em
   `frontend/supabase/migrations/20260807010000_native_emails.sql`).
   Rode no **SQL Editor** do projeto Supabase da VPS
   (`https://dfxmasxpqpujxahwzpqn.supabase.co`). Sem isso, o PostgREST responde
   `PGRST205` e o admin mostrava “Falha de comunicação com a API”.
2. **Fallback automático:** se as tabelas ainda não existirem, o backend grava
   no bucket privado Storage `email-templates` (somente service role). O save
   funciona; quando a migration SQL for aplicada, reinicie o backend para
   passar a usar as tabelas.

### CORS

O browser faz preflight `OPTIONS` no save. O gateway precisa permitir
`PUT` e `PATCH` em `CORS_ALLOWED_METHODS`. Sem `PUT`/`PATCH`, o preflight
volta **400** e a UI exibe a mesma falha de API.

### Eventos

| Código | Uso |
|--------|-----|
| `purchase.completed` | Compra / boas-vindas |
| `subscription.renewed` | Renovação |
| `subscription.canceled` | Cancelamento |
| `payment.refunded` | Reembolso |
| `payment.chargeback` | Chargeback |
| `subscription.payment_failed` | Falha de cobrança |
| `trial.started` | Início do teste |
| `trial.ended` | Fim do teste |
| `user.password_recovery_requested` | Recuperação de senha |

### Variáveis `{{...}}`

| Variável | Significado |
|----------|-------------|
| `customer_name` | Nome do cliente |
| `customer_email` | E-mail do cliente |
| `plan_name` | Oferta/plano |
| `amount` / `currency` | Valor e moeda |
| `first_access_url` | Definir senha (1º acesso) |
| `recovery_url` | Reset de senha |
| `expires_at` / `expires_in_seconds` | Validade do link |
| `login_url` | Login do painel |
| `company_name` | ElCapo AutoBot |
| `event_type` | Código do evento |
| `checkout_url` | Checkout Cakto |
| `support_url` | Suporte |
| `name` / `email` / `reset_url` | Aliases amigáveis |

## Hostinger — checklist

1. Criar/usar mailbox `suporte@visaodarota.com` no hPanel Hostinger.
2. Preencher SMTP no `.env` com a senha desse mailbox.
3. `PROD_EMAILS_ENABLED=true` e `PROD_EMAIL_PROVIDER=smtp`.
4. Reiniciar backend.
5. Em Admin → Emails, ativar os templates desejados e enviar um teste.

## Troubleshooting — “Falha de comunicação com a API” ao salvar

| Causa | Sintoma | Correção |
|-------|---------|----------|
| CORS sem PUT/PATCH | `OPTIONS .../templates/...` → 400 | Redeploy backend com `PUT`/`PATCH` em CORS |
| Tabela ausente | Logs `404 .../email_templates` + PGRST205 | Rodar `migration_native_emails.sql` **ou** usar fallback Storage (já no código) |
| Sessão/admin | 401/403 | Relogar como admin |

## Arquivos

| Camada | Arquivo |
|--------|---------|
| Regras / SMTP / Resend | `Backend/backend/email_service.py` |
| Persistência (+ fallback Storage) | `Backend/backend/email_repository.py` |
| Modelos | `Backend/backend/email_models.py` |
| Rotas admin | `Backend/backend/email_router.py` |
| Migration SQL | `Backend/backend/migration_native_emails.sql` |
| Worker | `Backend/backend/workers/email_tasks.py` |
| UI admin | `Frontend/src/routes/_authenticated/admin.emails.tsx` |
| Cliente API | `Frontend/src/lib/api.ts` (`PATCH`) |
| Variáveis UI | `Frontend/src/lib/emailPresentation.ts` |
| Testes | `Backend/tests/test_native_emails.py` |

## Changelog

- **2026-08-07** — Aba Entregas vazia: webhooks Cakto em 401 (secret desalinhado) impediam `purchase.completed`. Secret alinhado ao painel; ver `docs/CAKTO.md`.


- **2026-08-07** — Save quebrado: CORS sem `PUT` no container + tabelas
  `email_templates`/`email_deliveries` ausentes no Supabase. Correção: PUT/PATCH
  no CORS, admin usa `PATCH`, fallback Storage `email-templates`, migration SQL
  documentada.

