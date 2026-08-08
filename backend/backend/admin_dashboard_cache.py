"""Cache em memória do dashboard administrativo (TTL curto).

Evita recomputar N queries de histórico + agregações quando o admin
recarrega ou troca abas dentro da janela de TTL.
"""

from __future__ import annotations

import time
from typing import Any

ADMIN_DASHBOARD_CACHE_TTL_SECONDS = 75.0

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_key(company_id: str, days: int) -> str:
    """Monta a chave isolada por tenant e período."""
    return f"{company_id}:{int(days)}"


def read_admin_dashboard_cache(
    company_id: str,
    days: int,
    *,
    now_monotonic: float | None = None,
    ttl_seconds: float = ADMIN_DASHBOARD_CACHE_TTL_SECONDS,
) -> dict[str, Any] | None:
    """
    Lê o dashboard cacheado se ainda estiver fresco.

    Args:
        company_id: Tenant autenticado (nunca vem do frontend).
        days: Período do dashboard.
        now_monotonic: Relógio injetável (testes).
        ttl_seconds: Validade do cache.

    Returns:
        Payload do dashboard ou None se miss/expirado.
    """
    key = _cache_key(company_id, days)
    entry = _cache.get(key)
    if entry is None:
        return None
    stored_at, payload = entry
    current = time.monotonic() if now_monotonic is None else now_monotonic
    if current - stored_at >= ttl_seconds:
        _cache.pop(key, None)
        return None
    return payload


def write_admin_dashboard_cache(
    company_id: str,
    days: int,
    payload: dict[str, Any],
    *,
    now_monotonic: float | None = None,
) -> None:
    """
    Grava o dashboard no cache curto.

    Args:
        company_id: Tenant autenticado.
        days: Período do dashboard.
        payload: Resultado de ``calculate_admin_dashboard``.
        now_monotonic: Relógio injetável (testes).
    """
    current = time.monotonic() if now_monotonic is None else now_monotonic
    _cache[_cache_key(company_id, days)] = (current, payload)


def clear_admin_dashboard_cache_for_company(company_id: str) -> None:
    """
    Invalida o dashboard de um tenant (após approve/CRUD de clientes).

    Args:
        company_id: Empresa cujos KPIs devem ser recalculados na próxima visita.
    """
    prefix = f"{company_id}:"
    for key in list(_cache):
        if key.startswith(prefix):
            _cache.pop(key, None)


def clear_admin_dashboard_cache() -> None:
    """Invalida todo o cache (testes / deploy)."""
    _cache.clear()
