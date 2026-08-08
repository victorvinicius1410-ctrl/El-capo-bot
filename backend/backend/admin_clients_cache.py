"""Cache em memória da listagem administrativa de clientes (TTL curto).

Acelera revisitas da fila de Pedidos e das demais abas de Acessos sem
reconsultar o PostgREST a cada troca de aba/admin.
"""

from __future__ import annotations

import time
from typing import Any

ADMIN_CLIENTS_CACHE_TTL_SECONDS = 20.0

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_key(
    company_id: str,
    segment: str,
    offset: int,
    limit: int,
    search: str | None = None,
) -> str:
    """Monta a chave isolada por tenant, segmento, página e busca."""
    normalized_search = (search or "").strip().casefold()
    return f"{company_id}:{segment}:{int(offset)}:{int(limit)}:{normalized_search}"


def read_admin_clients_cache(
    company_id: str,
    segment: str,
    offset: int,
    limit: int,
    *,
    search: str | None = None,
    now_monotonic: float | None = None,
    ttl_seconds: float = ADMIN_CLIENTS_CACHE_TTL_SECONDS,
) -> dict[str, Any] | None:
    """
    Lê a página de clientes cacheada se ainda estiver fresca.

    Args:
        company_id: Tenant autenticado (nunca vem do frontend).
        segment: pending | active | trial | marketing | inactive | all.
        offset: Offset da página.
        limit: Tamanho da página pedida pelo cliente (sem o +1 interno).
        search: Termo de busca por nome/email/ID trader (isola a chave).
        now_monotonic: Relógio injetável (testes).
        ttl_seconds: Validade do cache.

    Returns:
        Payload ``{items, has_more, next_offset}`` ou None se miss/expirado.
    """
    key = _cache_key(company_id, segment, offset, limit, search)
    entry = _cache.get(key)
    if entry is None:
        return None
    stored_at, payload = entry
    current = time.monotonic() if now_monotonic is None else now_monotonic
    if current - stored_at >= ttl_seconds:
        _cache.pop(key, None)
        return None
    return payload


def write_admin_clients_cache(
    company_id: str,
    segment: str,
    offset: int,
    limit: int,
    payload: dict[str, Any],
    *,
    search: str | None = None,
    now_monotonic: float | None = None,
) -> None:
    """
    Grava a página de clientes no cache curto.

    Args:
        company_id: Tenant autenticado.
        segment: Identificador do segmento.
        offset: Offset da página.
        limit: Tamanho da página.
        payload: Resposta já serializada (items + paginação).
        search: Termo de busca por nome/email/ID trader (isola a chave).
        now_monotonic: Relógio injetável (testes).
    """
    current = time.monotonic() if now_monotonic is None else now_monotonic
    _cache[_cache_key(company_id, segment, offset, limit, search)] = (current, payload)


def clear_admin_clients_cache_for_company(company_id: str) -> None:
    """
    Invalida todas as páginas cacheadas de um tenant.

    Args:
        company_id: Empresa cujos caches devem sair (após approve/CRUD).
    """
    prefix = f"{company_id}:"
    for key in list(_cache):
        if key.startswith(prefix):
            _cache.pop(key, None)


def clear_admin_clients_cache() -> None:
    """Invalida todo o cache (testes / deploy)."""
    _cache.clear()
