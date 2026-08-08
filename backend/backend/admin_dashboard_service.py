"""Agregações puras do dashboard administrativo."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.admin_models import AccountType, ClientRecord


def calculate_admin_dashboard(
    clients: list[ClientRecord],
    histories_by_user: dict[str, list[dict[str, Any]]],
    *,
    days: int,
    revenue_events: list[dict[str, Any]] | None = None,
    lifecycle_events: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Consolida clientes e operações reais de uma empresa.

    Args:
        clients: Clientes já filtrados pelo tenant autenticado.
        histories_by_user: Histórico real por usuário dentro do período.
        days: Tamanho do período consultado.
        revenue_events: Vendas e renovações confirmadas dentro do período.
        lifecycle_events: Transições de trial/ativação/inativação do período.
        now: Instante final do período, injetável para testes.

    Returns:
        Indicadores, rankings de usuários e precisão por ativo.

    Raises:
        ValueError: Se o período não estiver entre 1 e 365 dias.
    """
    if days < 1 or days > 365:
        raise ValueError("Período deve ficar entre 1 e 365 dias")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    period_start = current_time - timedelta(days=days - 1)
    period_start = period_start.replace(hour=0, minute=0, second=0, microsecond=0)

    def in_period(value: datetime | None) -> bool:
        return value is not None and period_start <= value <= current_time

    active_clients = sum(
        1
        for client in clients
        if client.account_type == AccountType.CLIENT
        and client.deleted_at is None
        and client.grant_access
    )
    lifecycle = lifecycle_events or []
    inactive_ids = {
        str(event.get("user_id"))
        for event in lifecycle
        if event.get("event_type") == "client_inactivated"
    }
    trial_ids = {
        str(event.get("user_id"))
        for event in lifecycle
        if event.get("event_type") == "trial_started"
    }
    new_client_ids = {
        str(event.get("user_id"))
        for event in lifecycle
        if event.get("event_type") == "client_activated"
    }
    for client in clients:
        if (
            client.account_type in {AccountType.CLIENT, AccountType.TRIAL}
            and not client.grant_access
            and (in_period(client.deleted_at) or in_period(client.updated_at))
        ):
            inactive_ids.add(client.user_id)
        if client.account_type == AccountType.TRIAL and in_period(client.created_at):
            trial_ids.add(client.user_id)
        if (
            client.account_type == AccountType.CLIENT
            and client.grant_access
            and in_period(client.created_at)
        ):
            new_client_ids.add(client.user_id)

    revenue_by_currency: dict[str, float] = defaultdict(float)
    new_revenue_by_currency: dict[str, float] = defaultdict(float)
    for event in revenue_events or []:
        amount = _safe_float(event.get("amount"))
        currency = str(event.get("currency") or "BRL").strip().upper()
        revenue_by_currency[currency] += amount
        if event.get("event_type") == "sale":
            new_revenue_by_currency[currency] += amount

    clients_by_id = {client.user_id: client for client in clients}
    user_profits: dict[str, float] = defaultdict(float)
    assets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"wins": 0, "losses": 0}
    )

    for user_id, history in histories_by_user.items():
        if user_id not in clients_by_id:
            continue
        seen_orders: set[str] = set()
        for trade in history:
            if trade.get("is_simulated") or trade.get("source") == "marketing_demo":
                continue
            order_id = str(trade.get("order_id") or "").strip()
            if order_id and order_id in seen_orders:
                continue
            if order_id:
                seen_orders.add(order_id)
            result = str(trade.get("result") or "").strip().upper()
            if result not in {"WIN", "LOSS"}:
                continue
            profit = _safe_float(trade.get("profit"))
            user_profits[user_id] += profit
            asset = str(trade.get("active") or trade.get("asset") or "").strip().upper()
            if asset:
                assets[asset]["wins" if result == "WIN" else "losses"] += 1

    user_rankings = [
        {
            "user_id": user_id,
            "name": clients_by_id[user_id].name,
            "email": clients_by_id[user_id].email,
            "profit": round(profit, 2),
        }
        for user_id, profit in user_profits.items()
    ]
    winners = sorted(
        (item for item in user_rankings if item["profit"] > 0),
        key=lambda item: (-item["profit"], item["name"].lower()),
    )[:5]
    losers = sorted(
        (item for item in user_rankings if item["profit"] < 0),
        key=lambda item: (item["profit"], item["name"].lower()),
    )[:5]

    asset_rankings = []
    for asset, result_counts in assets.items():
        operations = result_counts["wins"] + result_counts["losses"]
        asset_rankings.append(
            {
                "asset": asset,
                "wins": result_counts["wins"],
                "losses": result_counts["losses"],
                "operations": operations,
                "accuracy": round(result_counts["wins"] / operations * 100, 2),
            }
        )
    most_accurate = sorted(
        asset_rankings,
        key=lambda item: (-item["accuracy"], -item["operations"], item["asset"]),
    )[:5]
    least_accurate = sorted(
        asset_rankings,
        key=lambda item: (item["accuracy"], -item["operations"], item["asset"]),
    )[:5]

    rounded_revenue_by_currency = {
        currency: round(value, 2)
        for currency, value in sorted(revenue_by_currency.items())
    }
    rounded_new_revenue_by_currency = {
        currency: round(value, 2)
        for currency, value in sorted(new_revenue_by_currency.items())
    }
    return {
        "period_days": days,
        "period_start": period_start.isoformat(),
        "period_end": current_time.isoformat(),
        "active_clients": active_clients,
        "inactive_clients": len(inactive_ids),
        "trial_clients": len(trial_ids),
        "new_clients": len(new_client_ids),
        "revenue": round(sum(revenue_by_currency.values()), 2),
        "revenue_by_currency": rounded_revenue_by_currency,
        "new_client_revenue": round(sum(new_revenue_by_currency.values()), 2),
        "new_client_revenue_by_currency": rounded_new_revenue_by_currency,
        "top_winners": winners,
        "top_losers": losers,
        "most_accurate_assets": most_accurate,
        "least_accurate_assets": least_accurate,
    }


def _safe_float(value: Any) -> float:
    """Converte valor numérico inválido para zero."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
