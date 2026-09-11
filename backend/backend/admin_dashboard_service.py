"""Agregações puras do dashboard administrativo."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from backend.admin_models import AccountType, ClientRecord
from backend.brasilia_time import BRASILIA_TZ, history_cutoff

USER_RANKING_LIMIT = 10
ASSET_RANKING_LIMIT = 5


def _exclude_marketing_from_dashboard() -> bool:
    """
    Lê se contas marketing devem ficar fora das métricas operacionais.

    Produção (`ADMIN_DASHBOARD_EXCLUDE_MARKETING=true`) exclui marketing.
    Staging El Capo 2 pode usar ``false`` para manter essas contas nos KPIs.

    Returns:
        ``True`` quando marketing não entra em win rate, horários e rankings.
    """
    raw = os.getenv("ADMIN_DASHBOARD_EXCLUDE_MARKETING", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _counts_for_dashboard_operations(
    client: ClientRecord,
    *,
    exclude_marketing: bool,
) -> bool:
    """
    Indica se a conta entra em win rate, horários e rankings de lucro.

    Args:
        client: Registro do tenant.
        exclude_marketing: Se ``True``, contas ``marketing`` ficam de fora.

    Returns:
        ``True`` quando a conta deve ser agregada.
    """
    if exclude_marketing and client.account_type == AccountType.MARKETING:
        return False
    return True


def calculate_admin_dashboard(
    clients: list[ClientRecord],
    histories_by_user: dict[str, list[dict[str, Any]]],
    *,
    days: int,
    revenue_events: list[dict[str, Any]] | None = None,
    lifecycle_events: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    exclude_marketing_accounts: bool | None = None,
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
        exclude_marketing_accounts: Se ``True``, ignora contas marketing nas
            métricas operacionais. Padrão: ``ADMIN_DASHBOARD_EXCLUDE_MARKETING``.

    Returns:
        Indicadores, top 10 ganhadores/perdedores e precisão por ativo (top 5).

    Raises:
        ValueError: Se o período não estiver entre 1 e 365 dias.
    """
    if days < 1 or days > 365:
        raise ValueError("Período deve ficar entre 1 e 365 dias")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    period_start = history_cutoff(days, current_time)
    exclude_marketing = (
        exclude_marketing_accounts
        if exclude_marketing_accounts is not None
        else _exclude_marketing_from_dashboard()
    )

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
    operation_wins = 0
    operation_losses = 0
    operation_profit = 0.0
    hourly_buckets: dict[int, dict[str, float | int]] = defaultdict(
        lambda: {"wins": 0, "losses": 0, "profit": 0.0, "operations": 0}
    )

    for user_id, history in histories_by_user.items():
        client = clients_by_id.get(user_id)
        if client is None or not _counts_for_dashboard_operations(
            client,
            exclude_marketing=exclude_marketing,
        ):
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
            operation_profit += profit
            if result == "WIN":
                operation_wins += 1
            else:
                operation_losses += 1
            trade_stamp = _trade_timestamp(trade)
            if trade_stamp is not None:
                hour = _brasilia_hour(trade_stamp)
                bucket = hourly_buckets[hour]
                bucket["operations"] = int(bucket["operations"]) + 1
                bucket["profit"] = float(bucket["profit"]) + profit
                if result == "WIN":
                    bucket["wins"] = int(bucket["wins"]) + 1
                else:
                    bucket["losses"] = int(bucket["losses"]) + 1
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
    )[:USER_RANKING_LIMIT]
    losers = sorted(
        (item for item in user_rankings if item["profit"] < 0),
        key=lambda item: (item["profit"], item["name"].lower()),
    )[:USER_RANKING_LIMIT]

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
    )[:ASSET_RANKING_LIMIT]
    least_accurate = sorted(
        asset_rankings,
        key=lambda item: (item["accuracy"], -item["operations"], item["asset"]),
    )[:ASSET_RANKING_LIMIT]

    rounded_revenue_by_currency = {
        currency: round(value, 2)
        for currency, value in sorted(revenue_by_currency.items())
    }
    rounded_new_revenue_by_currency = {
        currency: round(value, 2)
        for currency, value in sorted(new_revenue_by_currency.items())
    }
    total_operations = operation_wins + operation_losses
    hourly_results = _build_hourly_results(hourly_buckets)
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
        "operations": {
            "total": total_operations,
            "wins": operation_wins,
            "losses": operation_losses,
            "win_rate": round(operation_wins / total_operations * 100, 2)
            if total_operations
            else 0.0,
            "profit": round(operation_profit, 2),
        },
        "hourly_results": hourly_results,
        "top_winners": winners,
        "top_losers": losers,
        "most_accurate_assets": most_accurate,
        "least_accurate_assets": least_accurate,
    }


def _parse_datetime(value: Any) -> datetime | None:
    """
    Converte string ISO ou datetime para UTC.

    Args:
        value: Valor de timestamp do trade.

    Returns:
        Datetime timezone-aware em UTC ou ``None``.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _trade_timestamp(trade: dict[str, Any]) -> datetime | None:
    """
    Extrai o instante da operação para agregação horária.

    Args:
        trade: Registro de trade do histórico.

    Returns:
        Datetime timezone-aware ou ``None`` se não houver timestamp válido.
    """
    for key in ("finished_at", "opened_at", "created_at", "sent_at"):
        parsed = _parse_datetime(trade.get(key))
        if parsed is not None:
            return parsed
    return None


def _brasilia_hour(value: datetime) -> int:
    """
    Converte instante para hora civil de Brasília (0–23).

    Args:
        value: Instante da operação.

    Returns:
        Hora civil em ``America/Sao_Paulo``.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BRASILIA_TZ).hour


def _build_hourly_results(
    hourly_buckets: dict[int, dict[str, float | int]],
) -> list[dict[str, Any]]:
    """
    Monta as 24 faixas horárias do dia civil de Brasília.

    Args:
        hourly_buckets: Contadores por hora já agregados.

    Returns:
        Lista ordenada de 00:00 a 23:00 com operações, wins, loss e lucro.
    """
    rows: list[dict[str, Any]] = []
    for hour in range(24):
        bucket = hourly_buckets.get(hour, {"wins": 0, "losses": 0, "profit": 0.0, "operations": 0})
        wins = int(bucket["wins"])
        losses = int(bucket["losses"])
        operations = int(bucket["operations"])
        rows.append(
            {
                "hour": hour,
                "hour_label": f"{hour:02d}:00",
                "operations": operations,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / operations * 100, 2) if operations else 0.0,
                "profit": round(float(bucket["profit"]), 2),
            }
        )
    return rows


def _safe_float(value: Any) -> float:
    """Converte valor numérico inválido para zero."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
