# Rate limit Bullex e proxy / VPN de egress

Como contornar o bloqueio `requests_limit_exceeded` da corretora Bullex
quando muitos clientes autenticam pelo mesmo IP do VPS.

Atualizado em **2026-08-07** (pool de 5 ISP Proxies IPRoyal em produção).

## Sintoma

No painel: mensagem de indisponibilidade ou
`BULLEX_REQUESTS_LIMIT_EXCEEDED`.

Nos logs do `bullex-service`:

```text
[LOGIN_FAILED] ... error=falha ao conectar: {"code":"requests_limit_exceeded",
  "message":"The number of requests has been exceeded. Try again in 60 minutes.",
  "ttl":3600}
```

Causa típica: restart do `bullex-service` (sessões em memória caem) →
auto-reconnect em massa → flood de `POST` em `auth.trade.bull-ex.com` →
bloqueio do **IP público do VPS** por ~60 minutos.

## O que o código faz agora

| Camada | Comportamento |
|---|---|
| `bullex_service/rate_limit.py` | Detecta o código, extrai `ttl`, ativa gate global |
| `bullex_service` connect | Sem proxy: recusa login enquanto o gate estiver ativo |
| Gateway | Mesmo gate; auto-reconnect não martela; código estável na API |
| Frontend | Mensagem pedindo espera (~60 min); **sem** mencionar proxy |
| `bullexapi` | Propaga `proxies` no HTTP **e** no websocket |

Códigos / logs úteis:

- `BULLEX_REQUESTS_LIMIT_EXCEEDED`
- `[BULLEX_LOGIN_RATE_LIMIT]`
- `[BULLEX_AUTO_RECONNECT_RATE_LIMIT]`
- `[BULLEX_PROXY_SELECTED]`
- `[BULLEX_LOGIN_RATE_LIMIT_BYPASS_PROXY]`

## Opção A — Proxy HTTP/SOCKS (recomendado)

Configure no `/opt/elcapo/backend/.env` (lido pelo container `bullex-service`).
O cliente do painel **não** vê nem configura proxy.

### Onde comprar (5 proxies pagos)

| Prioridade | Site | O que pedir |
|---|---|---|
| **1º (recomendado)** | [IPRoyal](https://iproyal.com) | Residencial, 5 IPs sticky ou gateway HTTP com user/senha |
| 2º (mais barato) | [Webshare](https://www.webshare.io) | 5 proxies HTTP estáticos (datacenter ou residential) |

Para corretora (login HTTPS + WS), **residencial** falha menos que datacenter
grátis/queimado. Comece com o plano menor do IPRoyal (créditos/GB ou sticky).

No painel do provedor, copie cada proxy no formato:

```text
http://USUARIO:SENHA@HOST:PORTA
```

Se o provedor der SOCKS5:

```text
socks5://USUARIO:SENHA@HOST:PORTA
```

### Como colar no `.env` (pool de 5)

Uma linha, 5 proxies, vírgula, **sem espaços**:

```bash
BULLEX_PROXY_URLS=http://u:p@ip1:porta,http://u:p@ip2:porta,http://u:p@ip3:porta,http://u:p@ip4:porta,http://u:p@ip5:porta
```

Arquivo: `/opt/elcapo/backend/.env` → variável `BULLEX_PROXY_URLS`.

Depois:

```bash
/opt/elcapo/scripts/deploy-backend.sh
```

Log de sucesso: `[BULLEX_PROXY_SELECTED] host=... pool=5`.

### Formato antigo (1 proxy só)

```bash
BULLEX_PROXY_URL=http://usuario:senha@host:porta
```

Requisitos:

- Proxy que aceite HTTPS CONNECT (login) e, de preferência, websocket.
- SOCKS: pacote `PySocks` já está em `requirements.txt`.
- Preferir **proxies residenciais**; listas gratuitas não usar.

Com proxy ativo, o gate global **não** impede novas tentativas (o egress
muda). Sem proxy, o sistema espera o TTL.

## Opção B — VPN só no `bullex-service`

Envolva o serviço com Gluetun / WireGuard para um IP de saída único e estável:

```yaml
# esboço — ajustar provedor/credenciais
services:
  bullex-vpn:
    image: qmcgaw/gluetun
    cap_add: [NET_ADMIN]
    environment:
      - VPN_SERVICE_PROVIDER=...
      - ...
  bullex-service:
    network_mode: "service:bullex-vpn"
    depends_on: [bullex-vpn]
```

O `backend-gateway` continua na rede Docker normal e fala com
`http://bullex-vpn:8000` (mesmo network namespace). Útil quando você quer
**um** IP residencial/VPN fixo sem gerenciar pool de proxies.

## Opção C — Segundo VPS / IP elástico

Subir outro `bullex-service` (ou trocar o IP elástico do provedor) e apontar
`BULLEX_SERVICE_URL` do gateway. Mais operacional, sem mudar o app.

## O que NÃO resolve

- Pedir ao usuário para “tentar de novo em alguns segundos” enquanto o TTL
  for 3600s — só piora o bloqueio se o gate estiver desligado.
- VPN no notebook do cliente — o login é feito **no servidor**.
- Só mudar DNS/Nginx — o IP de origem para a Bullex é o do container.

## Testes

```bash
cd /opt/elcapo/backend   # ou /root/Backend
python -m unittest tests.test_bullex_rate_limit_proxy -v
```

## Arquivos

- `bullex_service/rate_limit.py`
- `bullex_service/proxy_config.py`
- `bullex_service/main.py` (`create_bullex_client`, gate no `connect`)
- `bullexapi/stable_api.py` / `bullexapi/api.py` (proxies HTTP + WS)
- `backend/main.py` (`classify_bullex_connect_error`, gate no gateway)
- `Frontend/src/lib/api.ts` (mensagem PT)
- `tests/test_bullex_rate_limit_proxy.py`
- `.env.example` (`BULLEX_PROXY_URL` / `BULLEX_PROXY_URLS`)

## Histórico

- **2026-08-07** — Documento criado junto com backoff global + suporte a proxy.
