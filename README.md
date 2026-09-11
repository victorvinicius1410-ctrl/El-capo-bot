# El Capo AutoBot — VPS

Estrutura organizada para rodar **frontend (app)** e **backend (api)** nesta VPS.

## Hosts

| URL | Função |
|-----|--------|
| `https://app.elcapobot.online` | Painel / sistema (frontend) |
| `https://api.elcapobot.online` | API FastAPI |
| `https://elcapobot.online` / `www` | Landing page (LP) — futura, não é o app |

## Pastas

| Caminho | Conteúdo |
|---------|----------|
| `backend/` | FastAPI + BullEx + Docker Compose |
| `frontend/` | Código-fonte do painel |
| `docs/DNS.md` | Registros DNS para configurar no domínio |
| `docs/DEPLOY_VPS.md` | Como subir, atualizar e validar |
| `docs/GITHUB.md` | Remote GitHub, chave SSH da VPS, push/pull |
| `scripts/deploy-backend.sh` | Deploy rápido do backend |
| `scripts/sync-from-github.sh` | `git pull` + deploy backend + publish frontend |

Frontend publicado em: `/var/www/elcapobot` (servido como `app.elcapobot.online`)

## Repositório GitHub

```text
git@github.com:victorvinicius1410-ctrl/El-capo-bot.git
```

Setup de SSH, primeiro push e sync com a VPS: [`docs/GITHUB.md`](docs/GITHUB.md).

## Início rápido

```bash
/opt/elcapo/scripts/deploy-backend.sh
curl -sS https://api.elcapobot.online/health
```

DNS: veja `docs/DNS.md` (IP `2.25.187.128`).

## Staging (teste)

Sistema secundário em `/opt/elcapo2` → `app.elcapo2.shop` / `api.elcapo2.shop`.
Docs: [`docs/STAGING_ELCAPO2.md`](docs/STAGING_ELCAPO2.md) e
[`docs/DNS_ELCAPO2.md`](docs/DNS_ELCAPO2.md).
