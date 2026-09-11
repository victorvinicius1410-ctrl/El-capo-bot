# Deploy VPS — El Capo AutoBot

Guia operacional do ambiente organizado nesta VPS.

## GitHub

Código versionado em
`git@github.com:victorvinicius1410-ctrl/El-capo-bot.git`.

Chave SSH da VPS, primeiro push, pull do PC e script de sync:
[`GITHUB.md`](./GITHUB.md).

Atualizar esta VPS a partir do GitHub e republicar:

```bash
/opt/elcapo/scripts/sync-from-github.sh
```

## Layout de pastas

```text
/opt/elcapo/
  backend/          # Gateway FastAPI + BullEx + docker compose
  frontend/         # Código-fonte TanStack Start / Vite
  docs/             # DNS.md, GITHUB.md, este arquivo
  scripts/          # utilitários de deploy / sync-from-github.sh

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
| `backend-gateway` | FastAPI API/WS (`ROBOT_RUNTIME_MODE=external`); relay `robot:state` → WS | host `8080` |
| `robot-runtime` | Workers do robô (`ROBOT_RUNTIME_MODE=worker`); pub snapshots Redis DB1 | — |
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

**⚠️ SEMPRE inclua `-p elcapooneline` E `--build` no MESMO comando `up`.**
Rodar `docker compose build` separado (sem `-p elcapooneline`) e depois
`docker compose -p elcapooneline up -d --force-recreate` **não** atualiza o
código: o `build` sem `-p` cria uma imagem `backend-*:latest` órfã (nome de
projeto errado, geralmente derivado do diretório — `backend`), enquanto o
container real usa a tag `elcapooneline-*:latest`. O `up --force-recreate`
recria o container com a imagem `elcapooneline-*:latest` **antiga**,
silenciosamente — sem erro, sem aviso. Incidente 2026-08-18 ~21h: um fix de
`ROBOT_ANALYSIS_SCAN_BUDGET_SECONDS` foi "deployado" assim, o container
reiniciou normalmente, mas continuou rodando o valor antigo por ~1h40 até
alguém notar. Sempre confira depois:

```bash
docker inspect robot-runtime --format '{{.Config.Image}}'   # deve ser elcapooneline-robot-runtime
docker exec robot-runtime grep -n "NOME_DA_CONSTANTE" /app/backend/main.py  # bate com o host?
```

```bash
cd /opt/elcapo/backend
docker compose -p elcapooneline --env-file .env up -d --build
# ou: /opt/elcapo/scripts/deploy-backend.sh

# Correção só no runtime (NÃO recria bullex-service — preserva sessões):
docker compose -p elcapooneline --env-file .env up -d --build --no-deps robot-runtime
# ou, preferível (já valida imagem + religa workers via Supabase):
#   /opt/elcapo/scripts/deploy-robot-runtime.sh
# Sem --no-deps o compose sobe o bullex-service junto e derruba as sessões.
# Sem -p elcapooneline no build (mesmo comando ou anterior), o container fica
# com o código ANTIGO — ver aviso acima.

# Pool httpx saturado (robô “analisa” e não opera — incidente 2026-08-17/18):
# NÃO rebuild. Só restart do processo para devolver as conexões.
docker restart robot-runtime
# O runtime sobe com worker_start=false. Republicar start de quem estava
# enabled=True, senão só volta quem abrir o painel:
#   docker exec webhook-redis redis-cli -n 1 PUBLISH robot:cmd \
#     '{"user_id":"<UUID>","action":"start"}'
#
# ⚠️ NÃO use `robot:snapshot:*` do Redis DB1 como lista de quem publicar —
# essas chaves têm TTL de 600s e ficam com o `enabled/worker_running` da
# execução ANTERIOR até expirar, mesmo que o novo container não tenha
# recriado o worker (dá falso positivo se você checar logo em seguida).
# ⚠️ NÃO publique em rajada nos primeiros segundos após o restart: se o
# processo ainda não terminou de assinar o canal `robot:cmd`, o PUBLISH é
# perdido (Redis pub/sub não é fila — sem subscriber no instante do
# PUBLISH, a mensagem simplesmente some). Incidente 2026-08-18 ~21h10: de
# 35 comandos `start` publicados ~15s após o `up`, só ~1 pegou; os outros
# 34 sumiram sem log nenhum (nem erro, nem [SESSION_CHECK_SKIPPED]).
# Fonte de verdade para a lista de `enabled=True` é a tabela `robot_states`
# do Supabase, não o Redis:
#   curl -sS "$SUPABASE_URL/rest/v1/robot_states?select=user_id&enabled=eq.true" \
#     -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY"
# ⚠️ Filtre por UUID (`user_id` que não bate com o formato do Supabase Auth
# é fixture de teste esquecida — ver incidente 2026-08-18 ~22h18 em
# ROBO_E_SUPORTE.md). O runtime tem um guard (`_is_valid_account_user_id`
# em backend/robot_runtime_main.py) que recusa subir worker para essas,
# mas republicar `start` só para elas é trabalho perdido e, se o guard
# algum dia for removido/quebrado, é exatamente o que sufocou o
# `_call_gate` por 40+ min. Ao achar alguma, corrija a causa:
#   PATCH robot_states?user_id=eq.<id> {"enabled": false}
# Confirme a criação de cada worker pelo log (não pelo snapshot):
#   docker logs robot-runtime --since 1m | rg -c "WORKER_CREATED|WORKER_ALREADY_RUNNING"
# Validar: zero `PoolTimeout` / `ativas=100` e vários `SHARED_MARKET_CACHE_HIT`.
#
# `scripts/deploy-robot-runtime.sh` (atualizado 2026-08-18 ~23h10) já faz
# tudo isso automaticamente: espera aparecer
# `[ROBOT_RUNTIME] subscribed channel=robot:cmd` no log ANTES de publicar
# (em vez de um sleep fixo, que se mostrou insuficiente quando há muitos
# usuários restaurados no boot — 286 states levaram ~36s até o subscribe
# na recorrência de 23h09), filtra `user_id` não-UUID com aviso em vez de
# religar, e republica automaticamente quem não confirmou `WORKER_CREATED`/
# `WORKER_ALREADY_RUNNING` na primeira tentativa. Prefira sempre o script
# ao invés de publicar manualmente em rajada.
# A partir de 2026-08-18 o runtime recicla o client **somente** em PoolTimeout
# (`[BULLEX_HTTP_CLIENT_RECYCLE]`). Recycle em ConnectTimeout piora o handshake.
# Compra REAL usa pool HTTP próprio (não o semáforo dos candles) e timeout 45s.
# Se o overlay falhar com TEMPORARY_UNAVAILABLE e o bullex-service tiver
# REAL BUY SUCCESS no mesmo segundo, ver ROBO_E_SUPORTE.md (incidente 18/08 15:31).
docker compose -p elcapooneline ps
curl -sS http://127.0.0.1:8080/health
curl -sS https://api.elcapobot.online/health
```

O script `deploy-backend.sh` tenta o `/health` por até ~60 s (startup pode
atrasar se o Supabase estiver lento na hidratação da pattern memory).

**Janela de 502 no deploy:** `docker compose up -d --build` recria
`backend-gateway` (e dependentes). Enquanto o upstream em `:8080` está
morto/reiniciando, o Nginx responde **502** sem `Access-Control-Allow-Origin`.
No browser isso aparece como “CORS blocked” + falha em
`/robot/state`, `/bullex/credentials` e `wss://…/ws/robot-state`. Após o
health local/remoto voltar a 200, os headers CORS voltam (origem
`https://app.elcapobot.online`).

**WebSocket no gateway:** a imagem precisa de `websockets` (ver
[`ROBOT_STATE_WEBSOCKET.md`](./ROBOT_STATE_WEBSOCKET.md)). Sem isso o upgrade
falha com 404 e o log `No supported WebSocket library detected`.

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

## Ambiente staging (teste de estratégia)

Há um segundo sistema isolado em `/opt/elcapo2` no domínio `elcapo2.shop`
(gateway na porta `8081`, compose `elcapo2staging`). Use-o para testar
estratégias sem tocar em produção. Banco Supabase **deve ser outro projeto**.

Guia completo: [`STAGING_ELCAPO2.md`](./STAGING_ELCAPO2.md) · DNS:
[`DNS_ELCAPO2.md`](./DNS_ELCAPO2.md).

```bash
/opt/elcapo2/scripts/deploy-backend.sh
curl -sS http://127.0.0.1:8081/health
```

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
