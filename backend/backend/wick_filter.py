"""Filtro de pavio: não entrar quando as velas estão deixando pavio demais.

Pedido do dono em 2026-09-11, depois de clientes reclamarem que o robô entrava
"onde as velas anteriores deixaram muito pavio". A leitura de pavio que existia
no motor clássico (``_wick_score`` e ``WICK_REJECTION`` em ``signal_engine``)
tinha dois defeitos:

1. **Olhava a vela errada.** Usava ``candles[-1]``, que no momento da análise
   (segundo 5–20 da vela) é a vela **em formação**, com poucos segundos de vida.
   As velas fechadas antes da entrada nunca eram olhadas.
2. **Não barrava nada.** ``WICK_REJECTION`` só tirava 8 pontos do score.

Aqui entram três regras, todas bloqueio:

- **Vela em formação** (só na análise): se ela já anda com pavios somando
  ``WICK_FORMING_RATIO`` do range e o range já é de pelo menos
  ``WICK_MIN_RANGE_ATR`` ATR. O piso de range existe porque, com 5 segundos de
  vida, uma vela de um tick "tem 100% de pavio" e não diz nada.
- **Última vela fechada** — a que se formou durante a espera pela entrada: o
  mesmo corte, aplicado à vela já fechada.
- **Velas anteriores**: ``WICK_HEAVY_MIN_COUNT`` das últimas
  ``WICK_HEAVY_WINDOW`` velas fechadas com pavios somando ``WICK_HEAVY_RATIO``
  do range. É mercado indeciso, puxando e devolvendo.

Medido em 526 ordens reais de 10–11/09 (reconstruídas com as velas da
corretora): as duas regras de velas fechadas juntas barrariam 56,8% das ordens,
com acerto de 43,8% nas barradas contra 49,3% nas que sobram. A diferença vai
na mesma direção nas duas metades da amostra, mas não é estatisticamente
provada. É regra de produto (o cliente vê o pavio no gráfico), não promessa de
acerto.

## Reversibilidade

``WICK_FILTER=false`` desliga tudo. Cada limite tem sua variável de ambiente.
"""

from __future__ import annotations

import os
from typing import Any

from backend.support_resistance_strategy import average_true_range, candle_high, candle_low

WICK_FILTER_ENABLED = os.getenv("WICK_FILTER", "true").strip().lower() in {"1", "true", "yes"}
# Pavios (de cima + de baixo) em fração do range da vela.
WICK_FORMING_RATIO = float(os.getenv("WICK_FORMING_RATIO", "0.60"))
WICK_LAST_CLOSED_RATIO = float(os.getenv("WICK_LAST_CLOSED_RATIO", "0.60"))
WICK_HEAVY_RATIO = float(os.getenv("WICK_HEAVY_RATIO", "0.50"))
WICK_HEAVY_WINDOW = int(os.getenv("WICK_HEAVY_WINDOW", "3"))
WICK_HEAVY_MIN_COUNT = int(os.getenv("WICK_HEAVY_MIN_COUNT", "2"))
# Uma vela só "deixa pavio" se tiver tamanho: abaixo disso é ruído de tick.
WICK_MIN_RANGE_ATR = float(os.getenv("WICK_MIN_RANGE_ATR", "0.30"))
WICK_MIN_CANDLES = 20

WICK_OK = "OK_PAVIO"
WICK_FORMING = "PAVIO_NA_VELA_EM_FORMACAO"
WICK_LAST_CLOSED = "PAVIO_NA_ULTIMA_VELA"
WICK_HEAVY_SEQUENCE = "PAVIO_NAS_VELAS_ANTERIORES"
WICK_SEM_DADOS = "PAVIO_SEM_DADOS"


def wick_ratio(candle: dict[str, Any]) -> float:
    """Pavio de cima + pavio de baixo, em fração do range (0–1).

    Args:
        candle: Vela com ``open``/``close`` e ``max``/``min`` ou ``high``/``low``.

    Returns:
        A fração do range que é pavio; ``0.0`` para vela sem range.
    """
    alta, baixa = candle_high(candle), candle_low(candle)
    faixa = alta - baixa
    if faixa <= 0:
        return 0.0
    corpo = abs(float(candle["close"]) - float(candle["open"]))
    return max(0.0, min(1.0, (faixa - corpo) / faixa))


def _range(candle: dict[str, Any]) -> float:
    return max(0.0, candle_high(candle) - candle_low(candle))


def evaluate_wicks(candles: list[dict[str, Any]], *, check_forming: bool = True) -> tuple[bool, str, dict[str, Any]]:
    """Diz se as velas permitem entrar sem pavio demais.

    A última vela da lista é tratada como a vela atual (em formação). As
    anteriores são as fechadas. Na reconferência do disparo, a atual tem 0–3
    segundos; ``check_forming=False`` a ignora e a "última fechada" é a vela
    que se formou durante a espera.

    Args:
        candles: Velas em ordem cronológica; a última é a atual.
        check_forming: Se True, aplica a regra da vela em formação.

    Returns:
        ``(pode_entrar, motivo, detalhe)``. Sem velas suficientes, libera com
        ``PAVIO_SEM_DADOS`` — quem garante dado fresco é a reconferência.
    """
    if not WICK_FILTER_ENABLED:
        return True, WICK_OK, {}
    if len(candles) < WICK_MIN_CANDLES:
        return True, WICK_SEM_DADOS, {}
    atual = candles[-1]
    fechadas = candles[:-1]
    atr = average_true_range(fechadas[-120:], periodo=14)
    if atr <= 0:
        return True, WICK_SEM_DADOS, {}

    def pesada(vela: dict[str, Any], limite: float) -> bool:
        return _range(vela) >= WICK_MIN_RANGE_ATR * atr and wick_ratio(vela) >= limite

    ultimas = fechadas[-WICK_HEAVY_WINDOW:]
    detalhe = {
        "wick_forming": round(wick_ratio(atual), 3),
        "wick_closed": [round(wick_ratio(v), 3) for v in ultimas],
        "wick_closed_range_atr": [round(_range(v) / atr, 2) for v in ultimas],
    }
    if check_forming and pesada(atual, WICK_FORMING_RATIO):
        return False, WICK_FORMING, detalhe
    if fechadas and pesada(fechadas[-1], WICK_LAST_CLOSED_RATIO):
        return False, WICK_LAST_CLOSED, detalhe
    if sum(1 for v in ultimas if pesada(v, WICK_HEAVY_RATIO)) >= WICK_HEAVY_MIN_COUNT:
        return False, WICK_HEAVY_SEQUENCE, detalhe
    return True, WICK_OK, detalhe
