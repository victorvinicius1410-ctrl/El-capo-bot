# Deploy VPS — El Capo AutoBot

Guia operacional do ambiente organizado nesta VPS.

## Layout de pastas

```text
/opt/elcapo/
  backend/          # Gateway FastAPI + BullEx + docker compose
  frontend/         # Código-fonte TanStack Start / Vite
  docs/             # DNS.md, este arquivo
  scripts/          # utilitários de deploy

/var/www/elcapobot/ # Build estático servido pelo Nginx (frontend)

/etc/nginx/sites-available/
  api.elcapobot.online      # proxy → 127.0.0.1:8080
  app.elcapobot.online      # static (painel) → /var/www/elcapobot
```

Uploads brutos do usuário ficam em `/root/Backend` e `/root/Frontend` (espelho).
A fonte de verdade de produção é `/opt/elcapo`.

## Serviços Docker

Projeto Compose: `elcapooneline` (mantém volumes antigos).

| Container | Função | Porta |
|-----------|--------|-------|
| `backend-gateway` | FastAPI API/WS (`ROBOT_RUNTIME_MODE=external`) | host `8080` |
| `robot-runtime` | Workers do robô (`ROBOT_RUNTIME_MODE=worker`) | — |
| `bullex-service` | API BullEx interna (`BULLEX_MAX_CONCURRENT_API_CALLS=3`) | rede Docker `8000` |
| `webhook-redis` | Redis (Celery DB0 + robot bus DB1) | interno `6379` |
| `webhook-worker` | Worker Celery | — |

Volumes persistentes:

- `elcapooneline_backend-data` → robô / SQLite gateway
- `elcapooneline_bullex-session-data` → sessões BullEx
- `elcapooneline_webhook-redis-data` → Redis AOF

## Variáveis de ambiente

Arquivo: `/opt/elcapo/backend/.env` (permissão `600`).

Obrigatórias em produção:

- `APP_ENV=production`
- `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` (somente backend)
- `PANEL_API_KEY` (igual a `VITE_PANEL_API_KEY` do frontend)
- `BULLEX_SESSION_ENCRYPTION_KEY`
- `BULLEX_PROXY_URL` / `BULLEX_PROXY_URLS` (opcional — egress proxy para contornar
  rate limit de login; ver [`BULLEX_RATE_LIMIT_E_PROXY.md`](./BULLEX_RATE_LIMIT_E_PROXY.md))
- `CORS_ORIGINS` com `https://app.elcapobot.online`
- `FRONTEND_URL=https://app.elcapobot.online`
- `PUBLIC_API_URL=https://api.elcapobot.online`
- `@` / `www` reservados para a LP futura (não são o painel)

Prefixos `PROD_*` para webhooks, e-mails e Cakto (ver `.env.example` e
[`CAKTO.md`](./CAKTO.md)). Inclua `PROD_CAKTO_DEFAULT_PRODUCT_ID` para criação
automática de ofertas no produto padrão.

**Nunca** coloque `SUPABASE_SERVICE_ROLE_KEY` no frontend.

## Subir / atualizar o backend

```bash
cd /opt/elcapo/backend
docker compose -p elcapooneline --env-file .env up -d --build
# ou: /opt/elcapo/scripts/deploy-backend.sh
docker compose -p elcapooneline ps
curl -sS http://127.0.0.1:8080/health
curl -sS https://api.elcapobot.online/health
```

O script `deploy-backend.sh` tenta o `/health` por até ~60 s (startup pode
atrasar se o Supabase estiver lento na hidratação da pattern memory).

Parar (mantém volumes):

```bash
cd /opt/elcapo/backend
docker compose -p elcapooneline down
```

## Frontend (build estático)

O build atual foi publicado em `/var/www/elcapobot` a partir de `Frontend/dist/client`.

Rebuild a partir do código:

```bash
cd /opt/elcapo/frontend
# garantir .env com VITE_API_BASE_URL=https://api.elcapobot.online
npm ci   # ou npm install
npm run build
rsync -a --delete dist/client/ /var/www/elcapobot/
```

Nginx já aponta `app.elcapobot.online` para esse diretório (`@`/`www` ficam para a LP).

SSL do site (após DNS correto):

```bash
certbot --nginx -d app.elcapobot.online
```

### Assets públicos (robô, áudios)

O robô animado é o vídeo `public/robo-wink-orig.webm` (lima original). A cor
roxo/teal vem do filtro CSS `hue-rotate`, aplicado num `<canvas>`
(`RobotAvatarVideo`) para Safari/iOS (MacBook/celular) ficarem iguais ao
Windows. Referência: `/robo-wink-orig.webm`. O `publish-frontend.sh` copia
`*.webm` de `/root/Frontend/public`
para `/var/www/elcapobot`.

**Cache:** `/assets/` = 1 ano; `.webm`/`.mp3` = 1h `must-revalidate`.
Detalhes: `OVERLAY_ROBO.md`.

## Healthchecks

```bash
curl -sS http://127.0.0.1:8080/health
# esperado: {"ok":true,"data":{"status":"healthy",...}}

docker logs backend-gateway --tail 80
docker logs bullex-service --tail 80
```

Depois de subir, dê ~20s antes do primeiro `/health`: o startup do gateway leva
alguns segundos e o script de deploy pode reportar `Connection reset by peer`
mesmo com o container saudável.

### Todo deploy derruba as sessões da corretora

O `bullex-service` sobe com `[STARTUP_READY] restore disabled`: sessões BullEx
**não** são restauradas automaticamente. Logo após o deploy é esperado ver:

- `/sessions/status` → `SESSION_NOT_FOUND`
- `/candles` → `CANDLES_TEMPORARY_UNAVAILABLE`
- gateway com `[ROBOT_WORKER_BLOCKED_DISCONNECTED]` / `[WORKER_NOT_RUNNING]`

A sessão volta quando o painel é aberto (reconexão com as credenciais salvas,
`bullex_saved_credentials`) e o usuário liga o robô de novo. **Consequência:
cada deploy custa alguns minutos de robô parado** — evite deploys em sequência
durante o horário de operação e avise o usuário para reabrir o painel.

Verificação rápida de que as sessões voltaram:

```bash
docker exec -i backend-gateway python -c "
import httpx
r = httpx.get('http://bullex-service:8000/candles',
              params={'active':'EURUSD-OTC','interval':60,'count':5},
              headers={'x-user-id':'<USER_ID>'}, timeout=20)
print(r.json().get('ok'), r.json().get('error'))
"
```

## DNS

Ver [`DNS.md`](./DNS.md) — tabela pronta para enviar ao responsável do domínio.

## Rollback rápido

Se a nova imagem falhar, o código antigo ainda existe em:

`/opt/bullex-system/elcapooneline`

```bash
cd /opt/elcapo/backend && docker compose -p elcapooneline down
cd /opt/bullex-system/elcapooneline && docker compose -p elcapooneline up -d --build
```

## Segurança (resumo)

- Service role só no `.env` do backend / containers.
- Escrita de dados de negócio via API autenticada, não via cliente Supabase no browser.
- Cookies de sessão: configurados no backend (`httpOnly`, `secure` em prod, `sameSite=lax`).
- Isolamento multi-tenant: queries com `company_id` da sessão.
- `webhook-worker` roda como usuário sem privilégios (`user: appuser` no
  `docker-compose.yml`, criado no `backend/Dockerfile`). `backend-gateway` e
  `bullex-service` continuam root porque gravam nos volumes `/data`.

## Higiene de Docker

Deploys repetidos (`up -d --build`) acumulam imagens `<none>` e build cache
sem limpeza automática — checagem em 2026-08-07 encontrou 169 imagens
(24,4 GB, 159 delas "dangling") e 14,8 GB de build cache nunca liberado.
Rodar periodicamente (ou após cada deploy):

```bash
docker image prune -af
docker builder prune -af
docker system df   # confirma o que ficou
```

`-a` remove qualquer imagem sem container usando (mantém as 4 em uso:
`backend`, `bullex-service`, `webhook-worker`, `redis`). Seguro mesmo com o
stack no ar — não afeta containers rodando.


## Publicar frontend

```bash
/opt/elcapo/scripts/publish-frontend.sh
```

SSL app (já emitido): renova automaticamente via certbot timer.
