"""Gerenciamento Consistente: o Algoritmo de Masaniello, sem dependências.

O cliente informa um **capital do ciclo**, um número de operações ``N`` e
quantos acertos ``W`` precisa. O valor de cada entrada deixa de ser fixo: sai
de uma matriz calculada uma vez por ciclo, de modo que ``W`` acertos em até
``N`` operações terminam sempre na mesma meta, em qualquer ordem.

O ciclo termina em ``W`` acertos (meta batida) ou em ``N - W + 1`` erros
(o capital do ciclo acabou). Não há stop à parte: o plano É o stop.

Regras que este módulo garante (ver ``docs/GERENCIAMENTO_CONSISTENTE.md``):

- A matriz usa um payout **fixo** por ciclo (``payout_ref``, o piso que o robô
  já exige). O capital anda pelo lucro REAL devolvido pela corretora; payout
  real maior só adianta o capital em relação ao plano.
- Entrada calculada abaixo do mínimo da corretora sobe para o mínimo e a
  linha fica marcada (``adjusted_to_min``).
- Empate devolve o valor e não conta como operação.
- Toda mudança do ciclo incrementa ``rev``: é por ela que gateway e runtime
  decidem qual cópia é a mais nova (:func:`pick_freshest_cycle`).

Módulo puro de propósito: não importa ``main`` nem ``auto_trader``, e tem um
gêmeo em ``frontend/src/lib/masaniello.ts`` testado contra os MESMOS vetores
(``tests/fixtures/masaniello_vectors.json``). Mudou a conta aqui, muda lá.

Nenhuma chave do ciclo pode começar com ``ai``: ``strip_ai_fields`` apaga.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

STATUS_ACTIVE = "ACTIVE"
STATUS_TARGET_HIT = "TARGET_HIT"
STATUS_BUST = "BUST"
STATUS_ABANDONED = "ABANDONED"

# Motivo de encerramento (``end_reason``), para o texto do painel.
REASON_TARGET = "TARGET"
REASON_ERRORS = "ERRORS"
REASON_NO_CAPITAL = "NO_CAPITAL"
REASON_RESULT_UNKNOWN = "RESULT_UNKNOWN"
REASON_CONFIG_CHANGED = "CONFIG_CHANGED"
REASON_USER = "USER"

PROFILE_CUSTOM = "personalizado"
# perfil -> (operações, acertos necessários)
PROFILES: dict[str, tuple[int, int]] = {
    "conservador": (10, 4),
    "moderado": (10, 5),
    "agressivo": (10, 6),
}
DEFAULT_PROFILE = "conservador"

# As linhas do ciclo viajam no snapshot publicado a cada segundo.
MAX_OPERATIONS = 100
DEFAULT_PAYOUT_REF = 80.0


def cents(value: float) -> float:
    """Arredonda para centavos (meio para cima). Igual ao ``cents`` do TS."""
    return math.floor(float(value) * 100 + 0.5) / 100


def floor_cents(value: float) -> float:
    """Trunca para centavos, sem nunca passar do valor."""
    return math.floor(float(value) * 100 + 1e-6) / 100


def tenths(value: float) -> float:
    """Arredonda para uma casa (percentual de acerto). Igual ao TS."""
    return math.floor(float(value) * 10 + 0.5) / 10


def _power(base: float, exponent: int) -> float:
    """Potência por multiplicação repetida: mesmo resultado bit a bit no TS."""
    result = 1.0
    for _ in range(int(exponent)):
        result *= base
    return result


def odds_from_payout(payout_percent: Any) -> float:
    """Converte payout em percentual (80) na cotação da matriz (1,80)."""
    try:
        payout = float(payout_percent)
    except (TypeError, ValueError):
        payout = DEFAULT_PAYOUT_REF
    if not math.isfinite(payout) or payout <= 0:
        payout = DEFAULT_PAYOUT_REF
    return 1.0 + payout / 100.0


def normalize_profile(profile: Any) -> str:
    """Devolve um perfil conhecido; o que não reconhece vira personalizado."""
    text = str(profile or "").strip().lower()
    if text in PROFILES or text == PROFILE_CUSTOM:
        return text
    return PROFILE_CUSTOM if text else DEFAULT_PROFILE


def valid_plan(operations: Any, wins: Any) -> bool:
    """Plano aceito: ``1 <= W < N <= MAX_OPERATIONS`` (ao menos 1 erro aceito)."""
    try:
        n = int(operations)
        w = int(wins)
    except (TypeError, ValueError):
        return False
    return 1 <= w < n <= MAX_OPERATIONS


def matrix(operations: int, wins: int, odds: float) -> list[list[float | None]]:
    """Matriz do Masaniello: ``M[m][w]`` com ``m`` operações feitas e ``w`` acertos.

    ``None`` marca posição impossível (já não dá para chegar em ``W``).
    """
    n, w_target, q = int(operations), int(wins), float(odds)
    table: list[list[float | None]] = [[None] * (w_target + 2) for _ in range(n + 2)]
    for done in range(n, -1, -1):
        for won in range(w_target + 1):
            needed = w_target - won
            left = n - done
            if needed == 0:
                table[done][won] = 1.0
            elif needed == left:
                table[done][won] = _power(q, left)
            elif needed > left:
                table[done][won] = None
            else:
                a = table[done + 1][won]
                b = table[done + 1][won + 1]
                table[done][won] = q * a * b / (a + (q - 1) * b)  # type: ignore[operator]
    return table


def stake_fraction(table: list[list[float | None]], done: int, won: int, odds: float) -> float:
    """Fração do capital atual que entra na próxima operação.

    Vale 1 (capital inteiro) quando um erro a mais encerra o ciclo.
    """
    a = table[done + 1][won]
    b = table[done + 1][won + 1]
    if a is None or b is None:
        return 1.0
    return 1.0 - odds * b / (a + (odds - 1) * b)


def stake_range(operations: int, wins: int, payout_ref: float) -> tuple[float, float, float]:
    """Primeira, menor e maior entrada do plano, como fração do capital inicial.

    No payout de referência o capital de cada posição da matriz é fixo
    (``capital * M[0][0] / M[m][w]``), então dá para varrer as posições sem
    percorrer os caminhos.
    """
    n, w = int(operations), int(wins)
    q = odds_from_payout(payout_ref)
    table = matrix(n, w, q)
    start = float(table[0][0] or 1.0)
    smallest = math.inf
    largest = 0.0
    for done in range(n):
        for won in range(min(done, w - 1) + 1):
            node = table[done][won]
            if node is None or done - won > n - w:
                continue
            fraction = stake_fraction(table, done, won, q) * start / node
            smallest = min(smallest, fraction)
            largest = max(largest, fraction)
    return stake_fraction(table, 0, 0, q), smallest, largest


def _ceil_cents(value: float) -> float:
    return math.ceil(float(value) * 100 - 1e-6) / 100


def min_capital(operations: int, wins: int, payout_ref: float, min_entry: float) -> float:
    """Menor capital em que a 1ª entrada já alcança o mínimo da corretora."""
    first, _, _ = stake_range(operations, wins, payout_ref)
    if first <= 0:
        return float(min_entry)
    return _ceil_cents(float(min_entry) / first)


def full_plan_capital(operations: int, wins: int, payout_ref: float, min_entry: float) -> float:
    """Capital a partir do qual NENHUMA entrada precisa ser puxada ao mínimo."""
    _, smallest, _ = stake_range(operations, wins, payout_ref)
    if smallest <= 0 or not math.isfinite(smallest):
        return float(min_entry)
    return _ceil_cents(float(min_entry) / smallest)


def plan_summary(
    capital: float,
    operations: int,
    wins: int,
    payout_ref: float,
    min_entry: float = 0.0,
) -> dict[str, Any]:
    """Números do plano antes da 1ª ordem (meta, limites e tamanho das entradas).

    Args:
        capital: Capital do ciclo.
        operations: ``N``.
        wins: ``W``.
        payout_ref: Payout de referência em percentual (80 = 80%).
        min_entry: Mínimo da corretora na moeda da conta.

    Returns:
        ``target`` (capital final na meta), ``target_profit``,
        ``target_percent``, ``max_errors``, ``first_stake``, ``min_stake`` e
        ``max_stake`` (menor e maior entrada possíveis, antes do ajuste ao
        mínimo), ``adjusts_to_min`` (alguma entrada cai abaixo do mínimo),
        ``min_capital`` e ``full_plan_capital``.
    """
    n, w = int(operations), int(wins)
    q = odds_from_payout(payout_ref)
    start = float(matrix(n, w, q)[0][0] or 1.0)
    first, smallest, largest = stake_range(n, w, payout_ref)
    minimum = float(min_entry or 0)
    return {
        "target": cents(capital * start),
        "target_profit": cents(capital * start - capital),
        "target_percent": cents((start - 1) * 100),
        "max_errors": n - w,
        "first_stake": cents(first * capital),
        "min_stake": cents(smallest * capital),
        "max_stake": cents(largest * capital),
        "adjusts_to_min": bool(minimum > 0 and cents(smallest * capital) < minimum),
        "min_capital": min_capital(n, w, payout_ref, minimum) if minimum > 0 else 0.0,
        "full_plan_capital": full_plan_capital(n, w, payout_ref, minimum) if minimum > 0 else 0.0,
    }


def signature(capital: Any, operations: Any, wins: Any, payout_ref: Any) -> tuple[float, int, int, float]:
    """Identidade do plano: mudou qualquer item, é outro ciclo."""
    return (
        cents(float(capital or 0)),
        int(operations or 0),
        int(wins or 0),
        cents(float(payout_ref or 0)),
    )


def cycle_signature(cycle: dict[str, Any] | None) -> tuple[float, int, int, float] | None:
    """Assinatura de um ciclo existente (``None`` se não há ciclo)."""
    if not isinstance(cycle, dict):
        return None
    return signature(
        cycle.get("capital_inicial"),
        cycle.get("n"),
        cycle.get("w"),
        cycle.get("payout_ref"),
    )


def is_active(cycle: dict[str, Any] | None) -> bool:
    """True se há ciclo em andamento."""
    return isinstance(cycle, dict) and cycle.get("status") == STATUS_ACTIVE


def _refresh_next_stake(cycle: dict[str, Any]) -> None:
    """Recalcula ``next_stake`` (o que o painel mostra como próxima entrada)."""
    proxima = next_stake(cycle)
    cycle["next_stake"] = proxima["stake"] if proxima else None
    cycle["next_stake_adjusted"] = bool(proxima and proxima["adjusted_to_min"])


def new_cycle(
    capital: float,
    operations: int,
    wins: int,
    payout_ref: float,
    *,
    min_entry: float = 0.0,
    at: str | None = None,
    cycle_id: str | None = None,
) -> dict[str, Any]:
    """Abre um ciclo novo.

    Args:
        capital: Capital do ciclo.
        operations: ``N``.
        wins: ``W``.
        payout_ref: Payout de referência em percentual.
        min_entry: Mínimo da corretora na moeda da conta.
        at: Instante ISO da abertura (default: agora, UTC).
        cycle_id: Identificador (default: UUID novo).

    Returns:
        O ciclo, já com ``target``, ``max_errors`` e ``next_stake``.
    """
    n, w = int(operations), int(wins)
    summary = plan_summary(capital, n, w, payout_ref)
    cycle: dict[str, Any] = {
        "id": cycle_id or uuid.uuid4().hex,
        "rev": 1,
        "status": STATUS_ACTIVE,
        "end_reason": None,
        "started_at": at or datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "capital_inicial": cents(capital),
        "capital_atual": cents(capital),
        "n": n,
        "w": w,
        "payout_ref": cents(float(payout_ref)),
        "min_entry": float(min_entry or 0),
        "target": summary["target"],
        "max_errors": summary["max_errors"],
        "wins": 0,
        "losses": 0,
        "pending": None,
        "rows": [],
    }
    _refresh_next_stake(cycle)
    return cycle


def next_stake(cycle: dict[str, Any] | None, min_entry: float | None = None) -> dict[str, Any] | None:
    """Valor da próxima ordem do ciclo.

    Args:
        cycle: Ciclo em andamento.
        min_entry: Mínimo da corretora (default: o gravado no ciclo).

    Returns:
        ``{"stake", "stake_planned", "adjusted_to_min"}``, ou ``None`` quando
        o ciclo não está ativo ou o capital que sobrou não paga o mínimo.
    """
    if not is_active(cycle):
        return None
    assert cycle is not None
    minimum = float(cycle.get("min_entry") or 0) if min_entry is None else float(min_entry or 0)
    capital = float(cycle.get("capital_atual") or 0)
    available = floor_cents(capital)
    if available <= 0 or available < minimum:
        return None
    n, w = int(cycle["n"]), int(cycle["w"])
    won, lost = int(cycle.get("wins") or 0), int(cycle.get("losses") or 0)
    if won >= w or lost > n - w:
        return None
    q = odds_from_payout(cycle.get("payout_ref"))
    planned = cents(stake_fraction(matrix(n, w, q), won + lost, won, q) * capital)
    stake = planned
    adjusted = False
    if stake < minimum:
        stake = minimum
        adjusted = True
    if stake > available:
        stake = available
    return {"stake": stake, "stake_planned": planned, "adjusted_to_min": adjusted}


def mark_pending(
    cycle: dict[str, Any],
    *,
    order_id: Any,
    stake: float,
    stake_planned: float | None = None,
    adjusted_to_min: bool = False,
    asset: Any = None,
    payout: Any = None,
    at: str | None = None,
) -> dict[str, Any]:
    """Registra a ordem enviada e ainda sem resultado. Devolve um ciclo novo."""
    updated = dict(cycle)
    updated["pending"] = {
        "order_id": str(order_id),
        "stake": cents(stake),
        "stake_planned": cents(stake if stake_planned is None else stake_planned),
        "adjusted_to_min": bool(adjusted_to_min),
        "asset": asset,
        "payout": payout,
        "at": at or datetime.now(timezone.utc).isoformat(),
        "unknown": False,
    }
    updated["rev"] = int(cycle.get("rev") or 0) + 1
    return updated


def flag_pending_unknown(cycle: dict[str, Any], order_id: Any) -> dict[str, Any]:
    """Marca que o resultado da ordem pendente não chegou no prazo."""
    pending = cycle.get("pending")
    if not isinstance(pending, dict) or str(pending.get("order_id")) != str(order_id):
        return cycle
    if pending.get("unknown"):
        return cycle
    updated = dict(cycle)
    updated["pending"] = {**pending, "unknown": True}
    updated["rev"] = int(cycle.get("rev") or 0) + 1
    return updated


def has_order(cycle: dict[str, Any] | None, order_id: Any) -> bool:
    """True se o resultado dessa ordem já está nas linhas do ciclo."""
    if not isinstance(cycle, dict):
        return False
    wanted = str(order_id)
    return any(str(row.get("order_id")) == wanted for row in cycle.get("rows") or [])


def apply_result(
    cycle: dict[str, Any],
    *,
    order_id: Any,
    result: str,
    profit: float,
    stake: float | None = None,
    stake_planned: float | None = None,
    adjusted_to_min: bool | None = None,
    asset: Any = None,
    payout: Any = None,
    at: str | None = None,
) -> dict[str, Any]:
    """Lança o resultado de uma ordem no ciclo. Idempotente por ``order_id``.

    Args:
        cycle: Ciclo ao qual a ordem pertence.
        order_id: Id da ordem na corretora.
        result: ``WIN``, ``LOSS`` ou ``DRAW``.
        profit: Lucro REAL da ordem (negativo no LOSS, zero no empate).
        stake: Valor que entrou (default: o da ordem pendente).
        stake_planned: Valor que o plano pedia (default: o da pendente).
        adjusted_to_min: Se a entrada foi puxada para o mínimo.
        asset: Ativo da ordem.
        payout: Payout cotado na ordem.
        at: Instante ISO do resultado.

    Returns:
        Um ciclo novo, com a linha, o capital e o status atualizados. Devolve
        o mesmo ciclo quando a ordem já estava lançada.
    """
    if has_order(cycle, order_id):
        return cycle
    normalized = str(result or "").strip().upper()
    if normalized not in {"WIN", "LOSS", "DRAW"}:
        return cycle
    pending = cycle.get("pending") if isinstance(cycle.get("pending"), dict) else {}
    same_order = str(pending.get("order_id")) == str(order_id)
    source = pending if same_order else {}
    used = float(stake if stake is not None else source.get("stake") or 0)
    planned = float(stake_planned if stake_planned is not None else source.get("stake_planned") or used)
    adjusted = bool(source.get("adjusted_to_min") if adjusted_to_min is None else adjusted_to_min)

    updated = dict(cycle)
    won = int(cycle.get("wins") or 0)
    lost = int(cycle.get("losses") or 0)
    capital = float(cycle.get("capital_atual") or 0)
    counted = normalized in {"WIN", "LOSS"}
    if normalized == "WIN":
        gain = float(profit) if float(profit) > 0 else 0.0
        capital = cents(capital + gain)
        won += 1
        row_profit = cents(gain)
    elif normalized == "LOSS":
        loss = float(profit) if float(profit) < 0 else -used
        capital = cents(max(0.0, capital + loss))
        lost += 1
        row_profit = cents(loss)
    else:
        row_profit = 0.0

    n, w = int(cycle["n"]), int(cycle["w"])
    total = won + lost
    row = {
        "seq": total if counted else None,
        "order_id": str(order_id),
        "at": at or datetime.now(timezone.utc).isoformat(),
        "asset": asset if asset is not None else source.get("asset"),
        "result": normalized,
        "stake": cents(used),
        "stake_planned": cents(planned),
        "adjusted_to_min": adjusted,
        "payout": payout if payout is not None else source.get("payout"),
        "profit": row_profit,
        "capital_after": capital,
        "hit_rate": tenths(won / total * 100) if total else 0.0,
        "errors_left": max(0, n - w - lost),
    }
    updated["rows"] = [*(cycle.get("rows") or []), row]
    updated["wins"] = won
    updated["losses"] = lost
    updated["capital_atual"] = capital
    if same_order:
        updated["pending"] = None
    updated["rev"] = int(cycle.get("rev") or 0) + 1
    if updated.get("status") == STATUS_ACTIVE:
        if won >= w:
            updated = close(updated, STATUS_TARGET_HIT, REASON_TARGET, at=row["at"], bump=False)
        elif lost > n - w:
            updated = close(updated, STATUS_BUST, REASON_ERRORS, at=row["at"], bump=False)
    _refresh_next_stake(updated)
    return updated


def close(
    cycle: dict[str, Any],
    status: str,
    reason: str,
    *,
    at: str | None = None,
    bump: bool = True,
) -> dict[str, Any]:
    """Encerra o ciclo (meta, perda ou abandono). Devolve um ciclo novo."""
    updated = dict(cycle)
    updated["status"] = status
    updated["end_reason"] = reason
    updated["ended_at"] = at or datetime.now(timezone.utc).isoformat()
    if bump:
        updated["rev"] = int(cycle.get("rev") or 0) + 1
    _refresh_next_stake(updated)
    return updated


def _instant(value: Any) -> float:
    """ISO -> epoch, para comparar ``started_at`` (0 quando não dá para ler)."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def pick_freshest_cycle(
    a: dict[str, Any] | None,
    b: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Escolhe a cópia mais nova do ciclo entre duas (gateway x runtime x banco).

    Ids diferentes: ganha o que começou depois. Mesmo id: ganha o ``rev``
    maior; no empate de ``rev``, ganha o encerrado; senão fica com ``a``.
    """
    if not isinstance(a, dict):
        return b if isinstance(b, dict) else None
    if not isinstance(b, dict):
        return a
    if a.get("id") != b.get("id"):
        return b if _instant(b.get("started_at")) > _instant(a.get("started_at")) else a
    rev_a, rev_b = int(a.get("rev") or 0), int(b.get("rev") or 0)
    if rev_a != rev_b:
        return b if rev_b > rev_a else a
    # Mesmo `rev` vindo de processos diferentes: o encerramento ganha de um
    # ciclo que ainda se diz ativo (não se reabre ciclo encerrado).
    if a.get("status") == STATUS_ACTIVE and b.get("status") != STATUS_ACTIVE:
        return b
    return a


def simulate(
    capital: float,
    operations: int,
    wins: int,
    payout_ref: float,
    results: list[str],
    *,
    min_entry: float = 0.0,
    payout_real: float | None = None,
) -> dict[str, Any]:
    """Roda um ciclo inteiro em cima de uma sequência de resultados.

    É a prévia do painel e a base dos vetores de teste. O lucro de cada WIN
    usa ``payout_real`` (default: o de referência).

    Args:
        capital: Capital do ciclo.
        operations: ``N``.
        wins: ``W``.
        payout_ref: Payout de referência em percentual.
        results: Sequência de ``"W"``/``"L"``/``"D"`` (ou WIN/LOSS/DRAW).
        min_entry: Mínimo da corretora.
        payout_real: Payout usado para pagar os WINs, em percentual.

    Returns:
        O ciclo final (com ``rows``).
    """
    cycle = new_cycle(
        capital,
        operations,
        wins,
        payout_ref,
        min_entry=min_entry,
        at="1970-01-01T00:00:00+00:00",
        cycle_id="simulacao",
    )
    paid = float(payout_ref if payout_real is None else payout_real) / 100.0
    aliases = {"W": "WIN", "L": "LOSS", "D": "DRAW"}
    for index, raw in enumerate(results):
        order = next_stake(cycle)
        if order is None:
            if is_active(cycle):
                cycle = close(cycle, STATUS_BUST, REASON_NO_CAPITAL, at=cycle["started_at"])
            break
        result = aliases.get(str(raw).strip().upper(), str(raw).strip().upper())
        stake = order["stake"]
        if result == "WIN":
            profit = cents(stake * paid)
        elif result == "LOSS":
            profit = -stake
        else:
            profit = 0.0
        cycle = apply_result(
            cycle,
            order_id=f"sim-{index + 1}",
            result=result,
            profit=profit,
            stake=stake,
            stake_planned=order["stake_planned"],
            adjusted_to_min=order["adjusted_to_min"],
            payout=cents(paid * 100),
            at=cycle["started_at"],
        )
    if is_active(cycle) and next_stake(cycle) is None:
        cycle = close(cycle, STATUS_BUST, REASON_NO_CAPITAL, at=cycle["started_at"])
    return cycle
