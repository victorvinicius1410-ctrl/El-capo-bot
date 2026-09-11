"""Fuso civil do El Capo: meia-noite a meia-noite em Brasília.

Toda janela de "hoje", histórico, placar diário, stop win/loss e KPIs
administrativos usa ``America/Sao_Paulo`` (UTC−3, sem horário de verão
desde 2019). Timestamps continuam gravados em UTC; só o recorte do dia
civil é convertido.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    BRASILIA_TZ: tzinfo = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:
    BRASILIA_TZ = timezone(timedelta(hours=-3))

BRASILIA_TIMEZONE_NAME = "America/Sao_Paulo"


def _as_aware_utc(value: datetime) -> datetime:
    """
    Normaliza datetime para UTC com timezone.

    Args:
        value: Instante com ou sem tzinfo. Naive é tratado como UTC.

    Returns:
        O mesmo instante em UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_brasilia_date(value: datetime) -> date:
    """
    Converte um instante para a data civil de Brasília.

    Args:
        value: Instante UTC ou naive (interpretado como UTC).

    Returns:
        Dia civil em ``America/Sao_Paulo``.
    """
    return _as_aware_utc(value).astimezone(BRASILIA_TZ).date()


def brasilia_today(now: datetime | None = None) -> date:
    """
    Retorna o dia civil atual em Brasília.

    Args:
        now: Instante de referência. Padrão: agora em UTC.

    Returns:
        Data de hoje no fuso de Brasília.
    """
    current = now or datetime.now(timezone.utc)
    return to_brasilia_date(current)


def start_of_brasilia_day(day: date | datetime | None = None) -> datetime:
    """
    Meia-noite de Brasília do dia informado.

    Args:
        day: Data civil, instante (convertido para o dia em Brasília) ou
            ``None`` para hoje.

    Returns:
        Datetime timezone-aware em ``00:00:00-03:00``.
    """
    if day is None:
        civil = brasilia_today()
    elif isinstance(day, datetime):
        civil = to_brasilia_date(day)
    else:
        civil = day
    return datetime(civil.year, civil.month, civil.day, tzinfo=BRASILIA_TZ)


def end_of_brasilia_day(day: date | datetime | None = None) -> datetime:
    """
    Último milissegundo do dia civil em Brasília.

    Args:
        day: Data civil, instante ou ``None`` para hoje.

    Returns:
        Datetime timezone-aware em ``23:59:59.999999-03:00``.
    """
    return start_of_brasilia_day(day) + timedelta(days=1) - timedelta(microseconds=1)


def is_on_brasilia_day(value: datetime, day: date) -> bool:
    """
    Indica se o instante cai no dia civil de Brasília.

    Args:
        value: Instante da operação.
        day: Dia civil de Brasília.

    Returns:
        ``True`` se ``value`` pertence a ``day`` em Brasília.
    """
    return to_brasilia_date(value) == day


def is_brasilia_today(value: datetime, now: datetime | None = None) -> bool:
    """
    Indica se o instante é de hoje em Brasília.

    Args:
        value: Instante da operação.
        now: Relógio de referência.

    Returns:
        ``True`` se cair no dia civil atual de Brasília.
    """
    return is_on_brasilia_day(value, brasilia_today(now))


def history_cutoff(days: int, now: datetime | None = None) -> datetime:
    """
    Início do primeiro dia civil da janela trailing (inclusiva) em Brasília.

    ``days=1`` começa na meia-noite de hoje. ``days=7`` começa na meia-noite
    de 6 dias atrás (7 dias civis incluindo hoje).

    Args:
        days: Quantidade de dias civis (mínimo 1).
        now: Relógio de referência.

    Returns:
        Meia-noite de Brasília do primeiro dia da janela.
    """
    window = max(1, int(days))
    first = brasilia_today(now) - timedelta(days=window - 1)
    return start_of_brasilia_day(first)


def history_cutoff_iso(days: int, now: datetime | None = None) -> str:
    """
    Cutoff de histórico em ISO-8601 UTC, para SQL e PostgREST.

    Args:
        days: Quantidade de dias civis.
        now: Relógio de referência.

    Returns:
        Timestamp UTC do início da janela.
    """
    return history_cutoff(days, now).astimezone(timezone.utc).isoformat()


def parse_to_brasilia_date(value: Any) -> date | None:
    """
    Interpreta string ISO ou datetime e devolve a data civil de Brasília.

    Args:
        value: Datetime, ISO-8601 ou valor inválido.

    Returns:
        Data civil ou ``None`` se não der para parsear.
    """
    if isinstance(value, datetime):
        return to_brasilia_date(value)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return to_brasilia_date(parsed)
