"""Pool de proxies HTTP(S)/SOCKS para login/egress da Bullex.

O rate limit ``requests_limit_exceeded`` da corretora é por IP de origem.
Com todos os clientes autenticando pelo IP do VPS, um restart com
auto-reconnect em massa bloqueia o login por ~60 minutos.

Variáveis (sem prefixo de ambiente — aplicadas só ao bullex-service):

- ``BULLEX_PROXY_URL``: um proxy (ex.: ``http://user:pass@host:8080``)
- ``BULLEX_PROXY_URLS``: lista separada por vírgula (rotação round-robin)

Formatos aceitos: ``http://``, ``https://``, ``socks5://``, ``socks4://``.
SOCKS exige o pacote ``PySocks`` instalado no container.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

logger = logging.getLogger("bullex-service")

_lock = threading.Lock()
_rr_index = 0


@dataclass(frozen=True)
class ProxyEndpoint:
    """Endpoint de proxy normalizado para ``requests`` e websocket-client."""

    url: str
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    def as_requests_dict(self) -> dict[str, str]:
        """
        Retorna o dict no formato esperado por ``requests`` / ``session.request``.

        Returns:
            Mapa ``http``/``https`` → URL do proxy.
        """
        return {"http": self.url, "https": self.url}

    def websocket_kwargs(self) -> dict[str, Any]:
        """
        Kwargs de proxy para ``WebSocketApp.run_forever``.

        Returns:
            Dicionário com ``http_proxy_host``, ``http_proxy_port``, etc.
        """
        proxy_type = "http"
        if self.scheme.startswith("socks5"):
            proxy_type = "socks5"
        elif self.scheme.startswith("socks4"):
            proxy_type = "socks4"
        kwargs: dict[str, Any] = {
            "http_proxy_host": self.host,
            "http_proxy_port": self.port,
            "proxy_type": proxy_type,
        }
        if self.username is not None and self.password is not None:
            kwargs["http_proxy_auth"] = (self.username, self.password)
        return kwargs


def _default_port(scheme: str) -> int:
    if scheme in {"https"}:
        return 443
    if scheme.startswith("socks"):
        return 1080
    return 80


def parse_proxy_url(raw: str) -> ProxyEndpoint | None:
    """
    Faz parse de uma URL de proxy.

    Args:
        raw: URL completa (ex.: ``http://user:pass@1.2.3.4:8080``).

    Returns:
        ``ProxyEndpoint`` ou ``None`` se inválida.
    """
    value = (raw or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname:
        logger.warning("[BULLEX_PROXY_INVALID] value=%s", value[:80])
        return None
    port = parsed.port or _default_port(parsed.scheme.lower())
    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    return ProxyEndpoint(
        url=value,
        scheme=parsed.scheme.lower(),
        host=parsed.hostname,
        port=port,
        username=username,
        password=password,
    )


def load_proxy_endpoints() -> list[ProxyEndpoint]:
    """
    Carrega a lista de proxies a partir do ambiente.

    Returns:
        Lista (possivelmente vazia) de endpoints válidos.
    """
    raw_items: list[str] = []
    single = (os.getenv("BULLEX_PROXY_URL") or "").strip()
    if single:
        raw_items.append(single)
    multi = (os.getenv("BULLEX_PROXY_URLS") or "").strip()
    if multi:
        raw_items.extend(part.strip() for part in multi.split(",") if part.strip())
    endpoints: list[ProxyEndpoint] = []
    seen: set[str] = set()
    for item in raw_items:
        endpoint = parse_proxy_url(item)
        if endpoint is None or endpoint.url in seen:
            continue
        seen.add(endpoint.url)
        endpoints.append(endpoint)
    return endpoints


def proxies_configured() -> bool:
    """Retorna True se há ao menos um proxy válido configurado."""
    return bool(load_proxy_endpoints())


def next_proxy() -> ProxyEndpoint | None:
    """
    Seleciona o próximo proxy em round-robin.

    Returns:
        Endpoint ou ``None`` se o pool estiver vazio.
    """
    global _rr_index
    endpoints = load_proxy_endpoints()
    if not endpoints:
        return None
    with _lock:
        endpoint = endpoints[_rr_index % len(endpoints)]
        _rr_index = (_rr_index + 1) % len(endpoints)
    logger.info(
        "[BULLEX_PROXY_SELECTED] host=%s port=%s scheme=%s pool=%s",
        endpoint.host,
        endpoint.port,
        endpoint.scheme,
        len(endpoints),
    )
    return endpoint


def requests_proxies_for_client() -> dict[str, str] | None:
    """
    Dict de proxies para instanciar o cliente ``Bullex``, ou ``None``.

    Returns:
        Dict ``{"http": url, "https": url}`` ou ``None`` sem proxy.
    """
    endpoint = next_proxy()
    if endpoint is None:
        return None
    return endpoint.as_requests_dict()
