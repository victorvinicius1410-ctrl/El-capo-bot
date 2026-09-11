# API Base URL — allowlist do painel

## Motivo

O frontend não confia cegamente em `VITE_API_BASE_URL`: valores inválidos ou
hosts não autorizados são substituídos pelo endpoint de **produção**
(`https://api.elcapobot.online`). Isso evita que um build mal configurado
aponte o painel para um backend arbitrário.

## Hosts permitidos

| Host | Ambiente |
|------|----------|
| `localhost` / `127.0.0.1` / `[::1]` | Desenvolvimento (http ou https) |
| `api.elcapobot.online` | Produção |
| `api.elcapo2.shop` | Staging / testes de estratégia |

Implementação: `frontend/src/lib/apiBaseUrl.ts` (`resolveApiBaseUrl`).

Testes: `frontend/src/lib/apiBaseUrl.test.ts` (incluído em `npm test`).

## Relação com staging

O ambiente `/opt/elcapo2` usa `VITE_API_BASE_URL=https://api.elcapo2.shop`.
Sem `api.elcapo2.shop` na allowlist, o painel de teste **cairia na API de
produção** em silêncio. Ver [`STAGING_ELCAPO2.md`](./STAGING_ELCAPO2.md).

## Como adicionar outro host

1. Incluir o hostname em `ALLOWED_HTTPS_API_HOSTS` em `apiBaseUrl.ts`.
2. Adicionar caso de teste em `apiBaseUrl.test.ts`.
3. Rebuild + publish do frontend do ambiente correspondente.
