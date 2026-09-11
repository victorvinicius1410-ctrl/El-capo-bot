"""Entrada a favor do nível: encostou na resistência, vende; no suporte, compra.

Pedido do dono em 2026-09-11, depois de um dia em que o S/R só barrava:
66 de 101 entradas anunciadas foram canceladas na hora da compra porque o preço
tinha chegado num topo ou fundo. A regra passou a ter dois papéis:

- **filtro** — nenhuma estratégia entra contra o nível (``sr_respect``);
- **sinal** — com o preço perto de um nível, o El Capo entra A FAVOR dele:
  resistência → ``PUT``, suporte → ``CALL``. Sem esperar rejeição, sempre na
  abertura da vela, e "perto" basta, não precisa encostar.

As outras estratégias continuam como estavam: quando não há nível perto, quem
decide é o motor clássico (e Vertex/REV-Z onde valem).

## O que é nível aqui

Os mesmos níveis que o cliente vê no gráfico, montados só com velas FECHADAS:

- pivôs de raio 2 com 2+ toques nas últimas ``SR_RESPECT_LOOKBACK`` velas;
- pivôs de 1 toque nas últimas ``SR_LEVEL_VISIBLE_LOOKBACK`` velas;
- máxima e mínima das últimas ``SR_LEVEL_EXTREME_WINDOW`` velas.

## Quando NÃO entra

- **Passou da linha.** A última vela fechou do outro lado do nível: virou
  rompimento, não teste de nível (``SR_LEVEL_BREAK_ATR``, zero por decisão do
  dono em 11/09). Furar com o pavio e voltar continua valendo, porque quem diz
  de que lado o preço está é o fechamento.
- **Teto da hora.** No máximo ``SR_LEVEL_MAX_PER_HOUR`` (3) entradas de nível
  por conta por hora.
- **Espremido.** Suporte e resistência igualmente perto: o preço está entre os
  dois e não há lado a favor.
- **Pavio contra** (``level_wick_ok``). O pavio de rejeição a favor da entrada
  — de cima na resistência, de baixo no suporte — é confirmação e libera.
  Pavio contra a entrada, ou vela indecisa com pavio grande dos dois lados,
  continua barrando.

## Ressalva

Reversão em nível foi medida em 03/09 (SR-R) perto de 50% de acerto no OTC,
abaixo do empate de ~53,5%. Isto é regra de produto — coerência com o gráfico —
não vantagem medida. ``SR_LEVEL_TRADE=false`` desliga sem mexer no resto.
"""

from __future__ import annotations

import os
from typing import Any

from backend.sr_respect import SR_RESPECT_LOOKBACK, SR_RESPECT_PIVOT_RADIUS, SR_RESPECT_TOLERANCE_ATR
from backend.support_resistance_strategy import (
    average_true_range,
    candle_high,
    candle_low,
    cluster_levels,
    find_pivots,
)

SR_LEVEL_TRADE_ENABLED = os.getenv("SR_LEVEL_TRADE", "true").strip().lower() in {"1", "true", "yes"}
# "Perto" do nível, em ATR (amplitude média das últimas 14 velas).
SR_LEVEL_PROXIMITY_ATR = float(os.getenv("SR_LEVEL_PROXIMITY_ATR", "0.5"))
# Fechou além do nível por mais que isto: rompido, não é mais nível. Zero por
# decisão do dono em 11/09 (23h): "não operar quando a última vela da operação
# passou da linha". Era exatamente esse caso que produziu 9 perdas de um único
# sinal (CADCHF 23:15): a vela fechou do outro lado do nível e o robô comprou
# no meio do rompimento. Furar com o PAVIO e voltar continua valendo — isso é
# rejeição, e é o fechamento que diz de que lado o preço está.
SR_LEVEL_BREAK_ATR = float(os.getenv("SR_LEVEL_BREAK_ATR", "0"))
# Suporte e resistência com distâncias a menos disto uma da outra: espremido.
SR_LEVEL_SQUEEZE_ATR = float(os.getenv("SR_LEVEL_SQUEEZE_ATR", "0.1"))
SR_LEVEL_VISIBLE_LOOKBACK = 60
SR_LEVEL_EXTREME_WINDOW = 30
SR_LEVEL_MIN_CANDLES = 40
# Teto de entradas de nível por conta por hora (dono, 11/09 23h). Sem ele, um
# nível que o preço não larga virava metralhada: 15 ordens em 12 minutos, 13
# com perda, e duas contas no stop loss.
SR_LEVEL_MAX_PER_HOUR = int(os.getenv("SR_LEVEL_MAX_PER_HOUR", "3"))
SR_LEVEL_HOUR_SECONDS = 3600.0

# Pavio na entrada de nível (frações do range da vela).
SR_LEVEL_WICK_AGAINST_RATIO = float(os.getenv("SR_LEVEL_WICK_AGAINST_RATIO", "0.40"))
SR_LEVEL_WICK_TWO_SIDED_RATIO = float(os.getenv("SR_LEVEL_WICK_TWO_SIDED_RATIO", "0.30"))
SR_LEVEL_WICK_MIN_RANGE_ATR = float(os.getenv("SR_LEVEL_WICK_MIN_RANGE_ATR", "0.30"))

# Confiança na escala 0–100 do motor clássico, de propósito: uma escala própria
# obrigaria a rebaixar o piso nos dois portões, e é essa a armadilha que já
# calou a REV-Z, a SR-R e a Vertex. O mínimo do painel continua valendo.
SR_LEVEL_CONFIDENCE_BASE = 85
SR_LEVEL_CONFIDENCE_TOUCHES_BONUS = 5
SR_LEVEL_CONFIDENCE_CLOSE_BONUS = 5
SR_LEVEL_CLOSE_ATR = 0.15

STRATEGY_SR_LEVEL = "SR_NIVEL"

LEVEL_WICK_OK = "OK_PAVIO_NIVEL"
LEVEL_WICK_AGAINST = "PAVIO_CONTRA_NO_NIVEL"
LEVEL_WICK_TWO_SIDED = "PAVIO_DOS_DOIS_LADOS_NO_NIVEL"


def _levels(fechadas: list[dict[str, Any]], atr: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resistências e suportes vistos no gráfico, com a origem de cada um."""
    tolerancia = atr * SR_RESPECT_TOLERANCE_ATR
    topos_longos, fundos_longos = find_pivots(fechadas[-SR_RESPECT_LOOKBACK:], radius=SR_RESPECT_PIVOT_RADIUS)
    topos_curtos, fundos_curtos = find_pivots(fechadas[-SR_LEVEL_VISIBLE_LOOKBACK:], radius=SR_RESPECT_PIVOT_RADIUS)
    resistencias = [
        {"price": n["price"], "touches": n["touches"], "source": "PIVO_2_TOQUES"}
        for n in cluster_levels(topos_longos, tolerancia=tolerancia, min_touches=2)
    ] + [
        {"price": n["price"], "touches": n["touches"], "source": "TOPO_VISIVEL"}
        for n in cluster_levels(topos_curtos, tolerancia=tolerancia, min_touches=1)
    ]
    suportes = [
        {"price": n["price"], "touches": n["touches"], "source": "PIVO_2_TOQUES"}
        for n in cluster_levels(fundos_longos, tolerancia=tolerancia, min_touches=2)
    ] + [
        {"price": n["price"], "touches": n["touches"], "source": "FUNDO_VISIVEL"}
        for n in cluster_levels(fundos_curtos, tolerancia=tolerancia, min_touches=1)
    ]
    janela = fechadas[-SR_LEVEL_EXTREME_WINDOW:]
    resistencias.append({"price": max(candle_high(c) for c in janela), "touches": 1, "source": "MAXIMA_RECENTE"})
    suportes.append({"price": min(candle_low(c) for c in janela), "touches": 1, "source": "MINIMA_RECENTE"})
    return resistencias, suportes


def _nearest(niveis: list[dict[str, Any]], distancia) -> dict[str, Any] | None:
    """Nível válido mais perto (não rompido e dentro da distância de "perto")."""
    melhor = None
    for nivel in niveis:
        d = distancia(nivel["price"])
        if -SR_LEVEL_BREAK_ATR <= d <= SR_LEVEL_PROXIMITY_ATR:
            if melhor is None or abs(d) < abs(melhor["distance_atr"]) or (
                abs(d) == abs(melhor["distance_atr"]) and nivel["touches"] > melhor["touches"]
            ):
                melhor = {**nivel, "distance_atr": d}
    return melhor


def find_level_trade(candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Diz se o preço está perto de um nível e, se estiver, o lado a favor.

    A última vela da lista é a vela atual: o preço é o fechamento dela. Na
    análise ela está em formação; no disparo é a vela que acabou de abrir (ou,
    se a corretora ainda não a abriu, a síntese no fechamento da anterior —
    ver ``candles_with_current_candle``).

    Args:
        candles: Velas em ordem cronológica; a última é a atual.

    Returns:
        ``{"direction", "side", "level", "touches", "source", "distance_atr",
        "atr", "confidence"}`` ou ``None`` quando não há nível perto, quando o
        preço está espremido entre os dois lados ou quando está desligado.
    """
    if not SR_LEVEL_TRADE_ENABLED or len(candles) < SR_LEVEL_MIN_CANDLES:
        return None
    fechadas = candles[:-1]
    atr = average_true_range(fechadas[-SR_RESPECT_LOOKBACK:], periodo=14)
    if atr <= 0:
        return None
    preco = float(candles[-1]["close"])
    resistencias, suportes = _levels(fechadas, atr)
    resistencia = _nearest(resistencias, lambda nivel: (nivel - preco) / atr)
    suporte = _nearest(suportes, lambda nivel: (preco - nivel) / atr)
    if resistencia and suporte:
        if abs(abs(resistencia["distance_atr"]) - abs(suporte["distance_atr"])) < SR_LEVEL_SQUEEZE_ATR:
            return None
        if abs(resistencia["distance_atr"]) < abs(suporte["distance_atr"]):
            suporte = None
        else:
            resistencia = None
    nivel = resistencia or suporte
    if nivel is None:
        return None
    direcao = "PUT" if resistencia else "CALL"
    confianca = SR_LEVEL_CONFIDENCE_BASE
    if nivel["touches"] >= 2:
        confianca += SR_LEVEL_CONFIDENCE_TOUCHES_BONUS
    if abs(nivel["distance_atr"]) <= SR_LEVEL_CLOSE_ATR:
        confianca += SR_LEVEL_CONFIDENCE_CLOSE_BONUS
    return {
        "direction": direcao,
        "side": "RESISTENCIA" if resistencia else "SUPORTE",
        "level": float(nivel["price"]),
        "touches": int(nivel["touches"]),
        "source": nivel["source"],
        "distance_atr": round(float(nivel["distance_atr"]), 3),
        "atr": atr,
        "confidence": confianca,
    }


def _wick_parts(candle: dict[str, Any]) -> tuple[float, float, float]:
    """(range, pavio de cima, pavio de baixo) em preço."""
    alta, baixa = candle_high(candle), candle_low(candle)
    abertura, fechamento = float(candle["open"]), float(candle["close"])
    return alta - baixa, alta - max(abertura, fechamento), min(abertura, fechamento) - baixa


def _level_wick_problem(candle: dict[str, Any], direction: str, atr: float) -> str | None:
    faixa, cima, baixo = _wick_parts(candle)
    if faixa <= 0 or faixa < SR_LEVEL_WICK_MIN_RANGE_ATR * atr:
        return None
    contra = (baixo if direction == "PUT" else cima) / faixa
    if contra >= SR_LEVEL_WICK_AGAINST_RATIO:
        return LEVEL_WICK_AGAINST
    if cima / faixa >= SR_LEVEL_WICK_TWO_SIDED_RATIO and baixo / faixa >= SR_LEVEL_WICK_TWO_SIDED_RATIO:
        return LEVEL_WICK_TWO_SIDED
    return None


def level_wick_ok(
    candles: list[dict[str, Any]],
    direction: str,
    *,
    check_forming: bool = True,
) -> tuple[bool, str]:
    """Filtro de pavio da entrada de nível.

    Olha a vela atual (só na análise, ``check_forming``), a última fechada e a
    sequência das 3 últimas fechadas — as mesmas três leituras do filtro geral
    (``wick_filter``), mas contando só o pavio que vai CONTRA a entrada.

    Args:
        candles: Velas em ordem cronológica; a última é a atual.
        direction: ``"CALL"`` ou ``"PUT"`` da entrada de nível.
        check_forming: Se True, avalia também a vela em formação.

    Returns:
        ``(pode_entrar, motivo)``.
    """
    from backend.wick_filter import WICK_FILTER_ENABLED

    if not WICK_FILTER_ENABLED or len(candles) < SR_LEVEL_MIN_CANDLES:
        return True, LEVEL_WICK_OK
    fechadas = candles[:-1]
    atr = average_true_range(fechadas[-SR_RESPECT_LOOKBACK:], periodo=14)
    if atr <= 0:
        return True, LEVEL_WICK_OK
    if check_forming:
        problema = _level_wick_problem(candles[-1], direction, atr)
        if problema:
            return False, problema
    problema = _level_wick_problem(fechadas[-1], direction, atr)
    if problema:
        return False, problema
    problemas = [p for p in (_level_wick_problem(c, direction, atr) for c in fechadas[-3:]) if p]
    if len(problemas) >= 2:
        return False, problemas[-1]
    return True, LEVEL_WICK_OK


# Quando cada conta entrou por nível (epoch), para o teto por hora. Vive no
# processo, como os cooldowns de ativo: o robot-runtime é um processo só por
# instalação, e um restart zerar o contador é aceitável — o pior caso é uma
# hora com até 3 entradas a mais.
_entradas_de_nivel: dict[str, list[float]] = {}


def registrar_entrada_de_nivel(user_id: str, agora: float | None = None) -> None:
    """Marca uma entrada de nível executada, para o teto por hora."""
    import time

    momento = float(agora if agora is not None else time.time())
    janela = [t for t in _entradas_de_nivel.get(str(user_id), []) if momento - t < SR_LEVEL_HOUR_SECONDS]
    janela.append(momento)
    _entradas_de_nivel[str(user_id)] = janela


def entradas_de_nivel_na_hora(user_id: str, agora: float | None = None) -> int:
    """Quantas entradas de nível a conta fez nos últimos 60 minutos."""
    import time

    momento = float(agora if agora is not None else time.time())
    janela = [t for t in _entradas_de_nivel.get(str(user_id), []) if momento - t < SR_LEVEL_HOUR_SECONDS]
    _entradas_de_nivel[str(user_id)] = janela
    return len(janela)


def teto_de_nivel_atingido(user_id: str, agora: float | None = None) -> bool:
    """Indica se a conta já usou o teto de entradas de nível da hora."""
    if SR_LEVEL_MAX_PER_HOUR <= 0:
        return False
    return entradas_de_nivel_na_hora(user_id, agora) >= SR_LEVEL_MAX_PER_HOUR


def is_level_candidate(candidate: dict[str, Any] | None) -> bool:
    """Indica se a direção do candidato foi decidida pelo nível."""
    if not isinstance(candidate, dict):
        return False
    return candidate.get("strategy_key") == STRATEGY_SR_LEVEL and isinstance(candidate.get("sr_level"), dict)


def level_text(symbol: str, veredito: dict[str, Any]) -> str:
    """Explicação curta da entrada de nível, para o painel e a narração."""
    lado = "resistência" if veredito["side"] == "RESISTENCIA" else "suporte"
    acao = "venda" if veredito["direction"] == "PUT" else "compra"
    return f"{symbol}: preço na {lado} em {veredito['level']:.5f}. Entrada de {acao} a favor do nível."
