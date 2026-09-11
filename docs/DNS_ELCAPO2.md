# DNS — El Capo Staging (`elcapo2.shop`)

Documento para colar no registrador / Cloudflare do domínio de **teste**.

Produção (`elcapobot.online`) permanece em [`DNS.md`](./DNS.md) — não misture.

## Arquitetura de hosts

| Host | Uso |
|------|-----|
| `app.elcapo2.shop` | **Painel / sistema de TESTE** |
| `api.elcapo2.shop` | **API** FastAPI de TESTE |
| `elcapo2.shop` (`@`) | Opcional — redirect para `app` (não é o app em si) |
| `www.elcapo2.shop` | Opcional — redirect para `app` |

## IP desta VPS

| Tipo | Endereço |
|------|----------|
| IPv4 | `2.25.187.128` |
| IPv6 | `2a02:4780:75:fc74::1` |

## Registros DNS (obrigatórios)

| Tipo | Nome / Host | Valor | Proxy Cloudflare | TTL | Observação |
|------|-------------|-------|------------------|-----|------------|
| **A** | `app` | `2.25.187.128` | Opcional | Auto / 300 | Painel staging |
| **A** | `api` | `2.25.187.128` | **DNS only (cinza)** recomendado | Auto / 300 | Backend + WS |
| **AAAA** | `app` | `2a02:4780:75:fc74::1` | Opcional | Auto / 300 | IPv6 opcional |
| **AAAA** | `api` | `2a02:4780:75:fc74::1` | DNS only | Auto / 300 | IPv6 opcional |

\* Para `api`, prefira **DNS only** se usar WebSockets longos.

## Checklist para o responsável do domínio

```
Domínio: elcapo2.shop

Registros necessários AGORA (sistema de TESTE):
1) A     app   → 2.25.187.128     (painel / frontend)
2) A     api   → 2.25.187.128     (API; DNS only no Cloudflare)

Opcional IPv6:
3) AAAA  app   → 2a02:4780:75:fc74::1
4) AAAA  api   → 2a02:4780:75:fc74::1

Opcional raiz (só se quiser elcapo2.shop abrir o painel via redirect depois):
5) A     @     → 2.25.187.128
6) A     www   → 2.25.187.128

URLs finais:
- App (teste): https://app.elcapo2.shop
- API (teste): https://api.elcapo2.shop
- Health:      https://api.elcapo2.shop/health

NÃO alterar os DNS de elcapobot.online (produção).
```

## SSL nesta VPS (após propagação)

```bash
certbot --nginx -d app.elcapo2.shop -d api.elcapo2.shop
```

## Verificação rápida

```bash
dig +short app.elcapo2.shop A
dig +short api.elcapo2.shop A
# esperado: 2.25.187.128

curl -sS http://127.0.0.1:8081/health
curl -sI http://127.0.0.1/ -H 'Host: app.elcapo2.shop' | head -5
```

Detalhes operacionais: [`STAGING_ELCAPO2.md`](./STAGING_ELCAPO2.md).
