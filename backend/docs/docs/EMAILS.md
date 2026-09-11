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


## Publicar o conteúdo de fábrica em um tenant

`scripts/publish-email-templates.py` grava o HTML de
`backend/email_templates_default.py` na persistência real e liga o envio.

```bash
cd /opt/elcapo/backend
set -a && . ./.env && set +a
.venv/bin/python ../scripts/publish-email-templates.py --skip purchase.completed          # simula
.venv/bin/python ../scripts/publish-email-templates.py --skip purchase.completed --apply  # grava
```

> **Ordem importa:** faça o deploy do backend **antes** de publicar. Os
> templates usam variáveis formatadas (`amount_display`, `expires_at_br`,
> `expires_in_human`, `plan_name_display`, `first_access_cta_url`) que só
> existem no código novo — um backend antigo renderiza esses campos em branco,
> porque o motor apaga toda variável que não conhece. O `webhook-worker`
> também precisa subir: é ele que emite `trial.ended`.

Use `--skip` para preservar templates que o admin personalizou na UI; sem ele,
todos os eventos voltam ao conteúdo de fábrica.

## Permissões

- Contas com `is_admin=true` recebem automaticamente `emails.view` e
  `emails.manage` (catálogo completo de permissões admin).
- A sidebar **E-mails** lista sempre os eventos canônicos; editar assunto/HTML
  exige `emails.manage` ou admin.

## Admin → Emails

Acesso rápido: sidebar **E-mails** (`/admin/emails`), sempre visível para admin.

- **Templates HTML**: um template por evento; marque **Enviar este email** para ativar.
- **Variáveis**: clique para copiar `{{customer_name}}`, etc.
- **Salvar**: `PATCH /admin/emails/templates/{event}` (PUT legado ainda aceito).
- **Enviar teste**: campo **E-mail para teste** (obrigatório na UI). O body
  `POST .../test` aceita `{ "recipient_email": "..." }`; se omitido, usa o
  e-mail da sessão admin. Exige envio ativo no servidor (`EMAILS_ENABLED`).
- **Entregas**: histórico sanitizado (hash do destinatário, sem PII em texto).
  Status esperados: `pending` (acabou de enfileirar), `delivered` (SMTP/Resend
  aceitou), `retrying`, `failed`. Pendentes com 0 tentativas há mais de 5 min
  são reconciliados para `failed` + `DISPATCH_TIMEOUT` (nunca despachados).

### Worker Celery (obrigatório para status “Entregue”)

O container `webhook-worker` deve registrar a task **`emails.deliver`** além de
`webhooks.deliver` e `trials.end`. Sem isso, a entrega é gravada como
`pending`, a mensagem vai para o Redis e **ninguém envia** — o e-mail não sai
de verdade. O app Celery importa `backend.workers.email_tasks` no startup.

Verificação:

```bash
docker exec webhook-worker celery -A backend.workers.webhook_tasks inspect registered
# deve listar emails.deliver
```

### Persistência (importante)

1. **Preferido:** tabelas `public.email_templates` e `public.email_deliveries`.
   SQL: `Backend/backend/migration_native_emails.sql` (também em
   `frontend/supabase/migrations/20260807010000_native_emails.sql`).
   Rode no **SQL Editor** do projeto Supabase da VPS
   (`https://dfxmasxpqpujxahwzpqn.supabase.co`). Sem isso, o PostgREST responde
   `PGRST205` e o admin mostrava “Falha de comunicação com a API”.
2. **Fallback automático:** se as tabelas ainda não existirem, o backend grava
   no bucket privado Storage `email-templates` (somente service role), com
   **revisões versionadas** por evento (evita GET stale/CDN na mesma chave).
   O save funciona; quando a migration SQL for aplicada, reinicie o backend
   para passar a usar as tabelas.

### CORS

O browser faz preflight `OPTIONS` no save. O gateway precisa permitir
`PUT` e `PATCH` em `CORS_ALLOWED_METHODS`. Sem `PUT`/`PATCH`, o preflight
volta **400** e a UI exibe a mesma falha de API.

### Eventos

| Código | Uso |
|--------|-----|
| `purchase.completed` | 1ª compra / boas-vindas (conta nova + link para definir senha) |
| `purchase.existing_account` | Compra de quem **já tinha conta** (mesma senha; CTA de login) |
| `subscription.renewed` | Renovação da assinatura (acesso segue; CTA de login) |
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
| `first_access_block` | Bloco HTML com o link de 1º acesso; vazio se a conta já existia (aí o evento é `purchase.existing_account`) |
| `recovery_url` | Reset de senha |
| `expires_at` / `expires_in_seconds` | Validade do link |
| `login_url` | Login do painel |
| `company_name` | ElCapo AutoBot |
| `event_type` | Código do evento |
| `checkout_url` | Checkout Cakto — **nunca preenchido pelos eventos atuais** |
| `support_url` | Suporte — **nunca preenchido pelos eventos atuais** |
| `name` / `email` / `reset_url` | Aliases amigáveis |

#### Variáveis já formatadas (preferir estas no HTML)

O motor de render não tem filtros nem `{% if %}`: o valor precisa chegar pronto.

| Variável | Vale | Quando falta |
|----------|------|--------------|
| `amount_display` | `R$ 147,90` (em vez de `BRL 147.9`) | `—` |
| `plan_name_display` | Nome do plano | `ElCapo AutoBot` |
| `expires_at_br` | `15/09/2026 às 23:59` (fuso de Brasília) | `—` |
| `expires_in_human` | `1 hora`, `30 minutos`, `2 dias` | `tempo limitado` |
| `first_access_cta_url` | `first_access_url` do evento | cai para `login_url` |

> **Nunca** use `{{checkout_url}}` ou `{{support_url}}` dentro de um `href`:
> nenhum evento preenche essas variáveis, e o link renderiza como `href=""`.
> O canal de suporte dos templates é "responda este e-mail" — o remetente é a
> própria caixa de suporte.

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
| Entregas sempre “Pendente” | Worker sem `emails.deliver` | Redeploy worker; ver seção Worker Celery |

## Arquivos

| Camada | Arquivo |
|--------|---------|
| Regras / SMTP / Resend | `Backend/backend/email_service.py` |
| Design system dos e-mails | `Backend/backend/email_layout.py` |
| Conteúdo de fábrica (assunto + HTML) | `Backend/backend/email_templates_default.py` |
| Persistência (+ fallback Storage) | `Backend/backend/email_repository.py` |
| Modelos | `Backend/backend/email_models.py` |
| Rotas admin | `Backend/backend/email_router.py` |
| Migration SQL | `Backend/backend/migration_native_emails.sql` |
| Worker | `Backend/backend/workers/email_tasks.py` |
| UI admin | `Frontend/src/routes/_authenticated/admin.emails.tsx` |
| Cliente API | `Frontend/src/lib/api.ts` (`PATCH`) |
| Variáveis UI | `Frontend/src/lib/emailPresentation.ts` |
| Testes | `Backend/tests/test_native_emails.py` |
| Regras de acesso/plano na compra | `Backend/backend/finance_service.py`, `Backend/backend/finance_repository.py` |

## Changelog

- **2026-09-08** — Todos os 10 e-mails ganharam HTML próprio no mesmo design
  system (`email_layout.py` + `email_templates_default.py`): tabelas
  `role="presentation"`, estilos inline, 560px, sem imagem, sem CSS externo e
  sem media query. Os 7 eventos que estavam desativados (cancelamento,
  reembolso, chargeback, falha de cobrança, trial iniciado/encerrado e
  recuperação de senha) foram reescritos e **ativados** em produção;
  `purchase.existing_account` e `subscription.renewed` trocaram o HTML curto de
  fábrica pelo novo. `purchase.completed` foi preservado como estava.
  `ENABLED_ON_SEED` passou a cobrir todos os eventos, então um tenant novo já
  nasce comunicando o ciclo de vida inteiro. Entraram cinco variáveis já
  formatadas (`amount_display`, `plan_name_display`, `expires_at_br`,
  `expires_in_human`, `first_access_cta_url`) porque o render só substitui
  texto — `BRL 147.9` e datas ISO vazavam para o cliente, e o botão de 1º
  acesso podia virar `href=""`.

- **2026-08-15** — Templates separados: `purchase.existing_account` (comprou e
  já tinha conta; senha não muda) e HTML novo de `subscription.renewed`
  (renovação com CTA de login). Os dois nascem **ativos**. Compra Cakto com
  `first_access_url` continua em `purchase.completed`; sem esse link dispara
  `purchase.existing_account`. O HTML curto de fábrica da renovação é
  atualizado na próxima listagem Admin → E-mails.

- **2026-08-09** — Status sempre “Pendente”: o `webhook-worker` não registrava
  a task `emails.deliver`, então as entregas eram criadas e enfileiradas, mas
  **nunca enviadas** pelo SMTP. Correção: import de `email_tasks` no app Celery
  do worker; fallback `deliver_now` se a fila falhar; reconciliação de pending
  órfãos → `failed`/`DISPATCH_TIMEOUT`. UI de teste agora exige campo de
  e-mail destino (`recipient_email` no POST).

- **2026-08-07** — Aba Entregas vazia: webhooks Cakto em 401 (secret desalinhado) impediam `purchase.completed`. Secret alinhado ao painel; ver `docs/CAKTO.md`.


- **2026-08-07** — Save quebrado: CORS sem `PUT` no container + tabelas
  `email_templates`/`email_deliveries` ausentes no Supabase. Correção: PUT/PATCH
  no CORS, admin usa `PATCH`, fallback Storage `email-templates`, migration SQL
  documentada.
- **2026-08-07 (revisão compra Cakto)** — Nenhum e-mail estava saindo em
  produção:
  1. `PROD_EMAILS_ENABLED=false` no `.env` (envio desligado globalmente).
     Corrigido para `true` (SMTP Hostinger já estava configurado corretamente).
  2. Todos os 9 templates estavam com **Enviar este email** desmarcado
     (`is_enabled=false`, padrão de fábrica). Ativado `purchase.completed`
     (é o e-mail crítico de boas-vindas/liberação da compra do "el capo").
     Os demais 8 eventos (renovação, cancelamento, reembolso, chargeback,
     falha de cobrança, trial iniciado/encerrado, recuperação de senha)
     seguem **desativados** — ative em Admin → Emails conforme a
     necessidade do negócio.
  3. Bug no template padrão de `purchase.completed`: o link "Defina sua senha"
     era sempre exibido, mesmo em renovações de clientes que já têm senha
     (nesses casos `first_access_url` vem vazio → o e-mail mostrava um link
     quebrado `<a href="">`). O motor de render só faz substituição simples
     de `{{variavel}}` (sem suporte real a `{% if %}`), então a tag Jinja no
     código-fonte nunca funcionou. Corrigido com a variável nova
     `first_access_block` (ver tabela abaixo), que já vem pronta com o texto
     "Defina sua senha em: ..." ou vazia, dependendo do caso. Aplicado tanto
     no template padrão (`email_service.py`) quanto na revisão já salva no
     Storage em produção.
  4. **Bug de permissão relacionado:** um lead com trial aprovado pelo admin
     (`payment_status=not_required`) que comprava um plano de verdade na
     Cakto **não tinha o acesso promovido** — `apply_customer_access` nunca
     era chamado para essas contas, então `plan_id`/`plan_name` continuavam
     vazios e o `account_type` continuava `trial`. Na prática, o expirador
     agendado do trial (`end_trial`) podia **revogar o acesso de quem já
     tinha pago**, pois ele só olha `account_type=eq.trial`. Corrigido em
     `finance_service.py`/`finance_repository.py`: uma compra aprovada
     (ou qualquer evento que concede acesso) sempre promove o cliente para
     `account_type=client`, mesmo vindo de um trial. Contas de marketing
     continuam 100% protegidas de qualquer alteração. Ver `docs/CAKTO.md`.

