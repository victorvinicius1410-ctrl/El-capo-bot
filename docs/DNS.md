# DNS — El Capo AutoBot (`elcapobot.online`)

Documento para configurar o domínio no registrador / Cloudflare / painel DNS.

## Arquitetura de hosts

| Host | Uso |
|------|-----|
| `app.elcapobot.online` | **Painel / sistema** (frontend desta VPS) |
| `api.elcapobot.online` | **API** FastAPI (backend desta VPS) |
| `elcapobot.online` (`@`) | **Landing page (LP)** — futura, não é o app |
| `www.elcapobot.online` | **LP** (mesmo site da raiz) — futura |

## IP desta VPS

| Tipo | Endereço |
|------|----------|
| IPv4 | `2.25.187.128` |
| IPv6 | `2a02:4780:75:fc74::1` |

Confirme o IPv4 no painel do provedor se a VPS for recriada.

## Registros DNS recomendados (agora)

Configure no painel DNS do domínio `elcapobot.online`:

| Tipo | Nome / Host | Valor | Proxy Cloudflare | TTL | Observação |
|------|-------------|-------|------------------|-----|------------|
| **A** | `app` | `2.25.187.128` | Opcional | Auto / 300 | Painel do sistema |
| **A** | `api` | `2.25.187.128` | **DNS only (cinza)** recomendado* | Auto / 300 | Backend |
| **AAAA** | `app` | `2a02:4780:75:fc74::1` | Opcional | Auto / 300 | Opcional IPv6 |
| **AAAA** | `api` | `2a02:4780:75:fc74::1` | DNS only | Auto / 300 | Opcional IPv6 |

\* Para `api`, prefira **DNS only** se usar WebSockets longos / timeouts altos.

### Reservados para a LP (depois)

Não apontar `@` / `www` para o painel. Quando a landing page existir:

| Tipo | Nome / Host | Valor | Observação |
|------|-------------|-------|------------|
| **A** | `@` | IP do host da LP (esta VPS ou outro) | Landing page |
| **A** | `www` | mesmo da LP | Landing page |

Se a LP for nesta mesma VPS no futuro, use `2.25.187.128` e um site Nginx separado (não o de `app`).

## Resultado esperado após propagação

| Host | Serviço |
|------|---------|
| `https://app.elcapobot.online` | Nginx → frontend estático `/var/www/elcapobot` |
| `https://api.elcapobot.online` | Nginx → Docker gateway `:8080` |
| `https://elcapobot.online` | LP (quando existir) |
| `https://www.elcapobot.online` | LP (quando existir) |

## SSL do app nesta VPS

Com o registro A de `app` apontando para `2.25.187.128`:

```bash
certbot --nginx -d app.elcapobot.online
```

SSL da API (já existente):

```bash
# já configurado em /etc/nginx/sites-available/api.elcapobot.online
```

## Checklist para enviar ao responsável do domínio

Copie e cole:

```
Domínio: elcapobot.online

Registros necessários AGORA (sistema):
1) A     app   → 2.25.187.128     (painel / frontend)
2) A     api   → 2.25.187.128     (API; DNS only no Cloudflare)

Opcional IPv6:
3) AAAA  app   → 2a02:4780:75:fc74::1
4) AAAA  api   → 2a02:4780:75:fc74::1

NÃO usar @ ou www para o sistema.
@ e www ficam reservados para a Landing Page (LP) futura.

URLs finais:
- App (sistema): https://app.elcapobot.online
- API:           https://api.elcapobot.online
- Health:        https://api.elcapobot.online/health
- LP (futuro):   https://elcapobot.online  e  https://www.elcapobot.online
```

## Verificação rápida

```bash
dig +short app.elcapobot.online A
dig +short api.elcapobot.online A
curl -sS https://api.elcapobot.online/health
curl -sI http://127.0.0.1/ -H 'Host: app.elcapobot.online' | head -5
```


## Status na VPS (2026-07-21)

- DNS `app` e `api` → `2.25.187.128` — OK
- SSL Let's Encrypt: `app.elcapobot.online` e `api.elcapobot.online` — OK
- `@` / `www` apontam para host externo da LP (AWS) — esperado
- Painel: https://app.elcapobot.online
- API: https://api.elcapobot.online/health

## Ambiente de teste (domínio separado)

Staging idêntico ao prod, em outro domínio — **não** altere os registros acima:

- Domínio: `elcapo2.shop` → `app.elcapo2.shop` + `api.elcapo2.shop`
- Docs: [`DNS_ELCAPO2.md`](./DNS_ELCAPO2.md) e [`STAGING_ELCAPO2.md`](./STAGING_ELCAPO2.md)
- Código/stack: `/opt/elcapo2` (projeto Docker `elcapo2staging`, porta `8081`)
