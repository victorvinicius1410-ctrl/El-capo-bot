# Ambiente Staging — El Capo 2 (`elcapo2.shop`)

Sistema **secundário idêntico** ao El Capo de produção, isolado para testar
estratégias, deploys e mudanças sem afetar clientes reais.

| Item | Produção | Staging (este) |
|------|----------|----------------|
| Código | `/opt/elcapo` | `/opt/elcapo2` |
| Domínio app | `app.elcapobot.online` | `app.elcapo2.shop` |
| Domínio API | `api.elcapobot.online` | `api.elcapo2.shop` |
| Compose project | `elcapooneline` | `elcapo2staging` |
| Porta gateway (host) | `8080` | `8081` |
| Frontend estático | `/var/www/elcapobot` | `/var/www/elcapo2` |
| `APP_ENV` | `production` → `PROD_*` | `staging` → `STAGING_*` |
| Volumes Docker | `elcapooneline_*` | `elcapo2staging_*` |
| Containers | `backend-gateway`, `robot-runtime`, … | `*-staging` |
| Banco (Supabase) | projeto **produção** | projeto **staging separado** (obrigatório) |

## Banco de dados: secundário ou o mesmo?

**Use sempre um banco (projeto Supabase) secundário.**

| Opção | Veredito | Motivo |
|-------|----------|--------|
| **Banco secundário (recomendado)** | ✅ | Isola usuários, `robot_states`, histórico, financeiro e memória de padrões. Estratégias experimentais não corrompem produção (LEI 13). |
| Mesmo banco de produção | ❌ | Teste de estratégia altera `robot_states`/trades reais; risco de desligar robôs de clientes, misturar métricas e vazar dados de teste no painel admin. |

### Como criar o Supabase de staging

1. Acesse [supabase.com](https://supabase.com) → **New project** (nome ex.: `elcapo-staging`).
2. Em **Project Settings → API**, copie:
   - Project URL → `SUPABASE_URL`
   - `service_role` (secret) → `SUPABASE_SERVICE_ROLE_KEY`
3. No **SQL Editor**, rode o schema completo:
   - Arquivo único: [`sql/STAGING_SCHEMA_ALL_IN_ONE.sql`](./sql/STAGING_SCHEMA_ALL_IN_ONE.sql)
   - Guia detalhado: [`SUPABASE_STAGING_SCHEMA.md`](./SUPABASE_STAGING_SCHEMA.md)
4. Crie o admin: `select public.bootstrap_admin_user('admin@elcapo2.shop','SenhaForte123','Admin Staging',true);`
5. Edite `/opt/elcapo2/backend/.env` e substitua os placeholders.
6. Rode: `/opt/elcapo2/scripts/deploy-backend.sh`

**Nunca** coloque a `SUPABASE_SERVICE_ROLE_KEY` de produção no staging, nem a de staging no frontend.

## Layout na VPS

```text
/opt/elcapo2/
  backend/                 # FastAPI + BullEx + docker-compose (projeto elcapo2staging)
  frontend/                # Código do painel (build → /var/www/elcapo2)
  docs/                    # Este arquivo + DNS_ELCAPO2.md
  scripts/
    deploy-backend.sh
    deploy-robot-runtime.sh
    publish-frontend.sh
    sync-from-github.sh

/var/www/elcapo2/          # Build estático (Nginx app.elcapo2.shop)

/etc/nginx/sites-available/
  api.elcapo2.shop         # proxy → 127.0.0.1:8081
  app.elcapo2.shop         # static → /var/www/elcapo2
```

Symlink: `/root/elcapo2` → `/opt/elcapo2`.

## DNS (cole no registrador do domínio `elcapo2.shop`)

IP desta VPS (mesmo do El Capo prod):

| Tipo | Endereço |
|------|----------|
| IPv4 | `2.25.187.128` |
| IPv6 | `2a02:4780:75:fc74::1` |

### Registros obrigatórios

| Tipo | Nome / Host | Valor | Proxy Cloudflare | TTL | Observação |
|------|-------------|-------|------------------|-----|------------|
| **A** | `app` | `2.25.187.128` | Opcional | Auto / 300 | Painel staging |
| **A** | `api` | `2.25.187.128` | **DNS only (cinza)** | Auto / 300 | API + WebSocket |
| **AAAA** | `app` | `2a02:4780:75:fc74::1` | Opcional | Auto / 300 | IPv6 (opcional) |
| **AAAA** | `api` | `2a02:4780:75:fc74::1` | DNS only | Auto / 300 | IPv6 (opcional) |

### Opcional — raiz do domínio

Se quiser que `https://elcapo2.shop` abra o painel (redirect):

| Tipo | Nome | Valor |
|------|------|-------|
| **A** | `@` | `2.25.187.128` |
| **A** | `www` | `2.25.187.128` |

(Depois cria-se um server Nginx de redirect `@`/`www` → `app.elcapo2.shop`. O sistema em si usa **app** + **api**.)

### Checklist para colar no painel DNS

```
Domínio: elcapo2.shop

1) A     app   → 2.25.187.128     (painel / frontend de TESTE)
2) A     api   → 2.25.187.128     (API de TESTE; DNS only no Cloudflare)

Opcional IPv6:
3) AAAA  app   → 2a02:4780:75:fc74::1
4) AAAA  api   → 2a02:4780:75:fc74::1

URLs finais:
- App (teste):  https://app.elcapo2.shop
- API (teste):  https://api.elcapo2.shop
- Health:       https://api.elcapo2.shop/health

Produção NÃO muda:
- https://app.elcapobot.online
- https://api.elcapobot.online
```

## SSL (depois do DNS propagar)

```bash
certbot --nginx -d app.elcapo2.shop -d api.elcapo2.shop
```

Verificar:

```bash
dig +short app.elcapo2.shop A
dig +short api.elcapo2.shop A
curl -sS https://api.elcapo2.shop/health
curl -sI https://app.elcapo2.shop/ | head -5
```

## Variáveis de ambiente

Arquivo: `/opt/elcapo2/backend/.env` (permissão `600`).

Obrigações:

- `APP_ENV=staging`
- `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` do **projeto staging**
- `PANEL_API_KEY` igual a `VITE_PANEL_API_KEY` em `/opt/elcapo2/frontend/.env`
- `CORS_ORIGINS` com `https://app.elcapo2.shop`
- `FRONTEND_URL=https://app.elcapo2.shop`
- `PUBLIC_API_URL=https://api.elcapo2.shop`
- Prefixo `STAGING_*` para Redis, e-mails, Cakto, webhooks, encryption

Padrão seguro neste ambiente:

- `STAGING_CAKTO_ENABLED=false` — sem cobrança real
- `STAGING_EMAILS_ENABLED=false` — sem disparar e-mail de clientes
- `STAGING_WEBHOOKS_ENABLED=false`

Frontend: `/opt/elcapo2/frontend/.env` com `VITE_API_BASE_URL=https://api.elcapo2.shop`.

O resolve da URL (`frontend/src/lib/apiBaseUrl.ts`) **permite** explicitamente
`api.elcapobot.online` e `api.elcapo2.shop`. Sem o host de staging na allowlist,
o painel de teste caía silenciosamente na API de produção — corrigido em
2026-08-21.

## Subir / atualizar o backend staging

```bash
/opt/elcapo2/scripts/deploy-backend.sh
# ou:
cd /opt/elcapo2/backend
docker compose -p elcapo2staging --env-file .env up -d --build

curl -sS http://127.0.0.1:8081/health
```

**Sempre** use `-p elcapo2staging` (nunca `elcapooneline`).

Só runtime (preserva sessões BullEx do staging):

```bash
/opt/elcapo2/scripts/deploy-robot-runtime.sh
```

## Frontend staging

```bash
cd /opt/elcapo2/frontend
npm ci
npm run build
/opt/elcapo2/scripts/publish-frontend.sh
```

## Serviços Docker (staging)

| Container | Função | Porta host |
|-----------|--------|------------|
| `backend-gateway-staging` | FastAPI API/WS | `8081` |
| `robot-runtime-staging` | Workers do robô | — |
| `bullex-service-staging` | API BullEx interna | rede Docker |
| `webhook-redis-staging` | Redis | interno |
| `webhook-worker-staging` | Celery | — |

Volumes: `elcapo2staging_backend-data`, `elcapo2staging_bullex-session-data`,
`elcapo2staging_webhook-redis-data`.

## Fluxo recomendado para testar estratégia

1. Alterar código / parâmetros **só** em `/opt/elcapo2` (ou branch → sync staging).
2. Deploy staging (`deploy-backend.sh` / `deploy-robot-runtime.sh`).
3. Operar conta **demo** BullEx no painel `app.elcapo2.shop`.
4. Validar métricas no histórico/admin do staging.
5. Só então promover a mesma mudança para `/opt/elcapo` (produção).

## Isolamento e segurança

- Produção e staging **não** compartilham volumes Redis/SQLite/sessões BullEx.
- Service role só no `.env` do backend staging.
- Cakto/e-mails desligados por padrão no staging.
- Proxies Bullex podem ser reutilizados (egress), mas sessões ficam no volume staging.
- RAM: stack extra ~1–2 GB; VPS atual tem folga.

## Parar staging (mantém volumes)

```bash
cd /opt/elcapo2/backend
docker compose -p elcapo2staging down
```

## Troubleshooting

```bash
docker compose -p elcapo2staging -f /opt/elcapo2/backend/docker-compose.yml ps
docker logs backend-gateway-staging --tail 80
docker logs robot-runtime-staging --tail 80
curl -sS http://127.0.0.1:8081/health
nginx -t && systemctl reload nginx
```

Se o health local funciona mas HTTPS falha: DNS ainda não propagou ou falta
`certbot`.

Se login falha com erro de Supabase: placeholders ainda no `.env` — configure o
projeto staging.

## Status na VPS (2026-08-21)

- Pastas `/opt/elcapo2`, compose `elcapo2staging`, volumes, scripts: OK
- Containers `*-staging` no ar; health local `http://127.0.0.1:8081/health` → 200
- Nginx HTTP `app.elcapo2.shop` + `api.elcapo2.shop`: OK
- Frontend publicado em `/var/www/elcapo2` (apontando para `api.elcapo2.shop`)
- Produção (`:8080` / `elcapobot.online`) **intacta** após o setup
- SSL: aguarda DNS + `certbot --nginx -d app.elcapo2.shop -d api.elcapo2.shop`
- Supabase staging: **pendente** — criar projeto separado e colar URL + service_role em `/opt/elcapo2/backend/.env`
