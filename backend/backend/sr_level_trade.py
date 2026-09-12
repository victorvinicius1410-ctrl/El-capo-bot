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

- **Nível atrás do movimento.** Só vale o nível À FRENTE: preço subindo opera a
  resistência acima (venda), preço descendo opera o suporte abaixo (compra).
  Suporte atrás de um preço que sobe não é compra (``SR_LEVEL_TREND_ATR``).
- **Nível recém-criado.** O último toque tem de ter pelo menos
  ``SR_LEVEL_MIN_AGE`` velas: o extremo que a vela que acabou de fechar criou é
  o movimento em curso, não um nível que se provou.
- **Passou da linha.** A última vela fechou do outro lado do nível: virou
  rompimento, não teste de nível (``SR_LEVEL_BREAK_ATR``, zero por decisão do
  dono em 11/09). Furar com o pavio e voltar continua valendo, porque quem diz
  de que lado o preço está é o fechamento.
- **Teto da hora.** No máximo ``SR_LEVEL_MAX_PER_HOUR`` (3) entradas de nível
  por conta por hora.
- **Espremido.** Suporte e resistência igualmente perto: o preço está entre os
  dois e não há lado a favor.
- **Pavio** (``level_wick_ok``). Um pavio único de rejeição a favor da entrada
  — de cima na resistência, de baixo no suporte — é confirmação e libera. Mas
  barra: pavio contra a entrada, vela indecisa com pavio grande dos dois lados,
  e **2 das 3 últimas velas deixando 50%+ de pavio** (mercado indeciso; é o
  "cheio de pavio" que o cliente vê no gráfico).

## Ressalva

Reversão em nível foi medida em 03/09 (SR-R) perto de 50% de acerto no OTC,
abaixo do empate de ~53,5%. Isto é regra de produto — coerência com o gráfico —
não vantagem medida. ``SR_LEVEL_TRADE=false`` desliga sem mexer no resto.
"""

from __future__ import annotations

import os
from statistics import fmean
from typing import Any

from backend.sr_respect import SR_RESPECT_LOOKBACK, SR_RESPECT_PIVOT_RADIUS, SR_RESPECT_TOLERANCE_ATR
from backend.support_resistance_strategy import (
    average_true_range,
    candle_high,
    candle_low,
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
# Movimento das últimas velas, em ATR, a partir do qual o preço "está indo"
# para um lado. Subindo, só a resistência à frente vale; descendo, só o
# suporte. Um suporte ATRÁS de um preço que sobe não é compra — foi por ignorar
# isso que o robô comprou na CHFJPY 12/09 15:44 com resistência 0,42 ATR acima
# (vídeo do cliente; a venda teria ganho).
SR_LEVEL_TREND_CANDLES = 3
SR_LEVEL_TREND_ATR = float(os.getenv("SR_LEVEL_TREND_ATR", "0.3"))
# Velas mínimas desde o último toque do nível. Zero significaria aceitar como
# "nível" o extremo que a própria vela recém-fechada criou — comprar na mínima
# nova, que é o caso EURAUD 12/09 00:37 (preço caindo 2,8 ATR, CALL, LOSS).
SR_LEVEL_MIN_AGE_CANDLES = int(os.getenv("SR_LEVEL_MIN_AGE", "3"))
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
# Mercado indeciso: velas seguidas deixando muito pavio, para qualquer lado.
# Vale também no nível (dono, 12/09: "o El Capo acabou de pegar operação em uma
# vela que deixou bastante pavio" — USDCAD 15:58, três velas anteriores com
# 57%, 84% e 49% de pavio; o pavio CONTRA era pequeno e a regra direcional
# sozinha liberava). Um pavio único de rejeição a favor continua liberado.
SR_LEVEL_WICK_SEQUENCE_RATIO = float(os.getenv("SR_LEVEL_WICK_SEQUENCE_RATIO", "0.50"))
SR_LEVEL_WICK_SEQUENCE_COUNT = int(os.getenv("SR_LEVEL_WICK_SEQUENCE_COUNT", "2"))
SR_LEVEL_WICK_SEQUENCE_WINDOW = 3

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
LEVEL_WICK_SEQUENCE = "PAVIO_NAS_VELAS_ANTERIORES_NO_NIVEL"


def _pivos_com_indice(
    fechadas: list[dict[str, Any]],
    radius: int,
    inicio: int,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Pivôs como ``(índice na lista completa, preço)``.

    ``find_pivots`` devolve só os preços, e sem o índice não há como saber se o
    nível é antigo ou se a vela que acabou de fechar o inventou.
    """
    topos: list[tuple[int, float]] = []
    fundos: list[tuple[int, float]] = []
    if radius < 1 or len(fechadas) < radius * 2 + 1:
        return topos, fundos
    for i in range(radius, len(fechadas) - radius):
        janela = fechadas[i - radius : i + radius + 1]
        alta, baixa = candle_high(fechadas[i]), candle_low(fechadas[i])
        if alta >= max(candle_high(c) for c in janela):
            topos.append((inicio + i, alta))
        if baixa <= min(candle_low(c) for c in janela):
            fundos.append((inicio + i, baixa))
    return topos, fundos


def _agrupa(
    pivos: list[tuple[int, float]],
    *,
    tolerancia: float,
    min_touches: int,
    source: str,
) -> list[dict[str, Any]]:
    """Agrupa pivôs em níveis, guardando o índice do toque mais recente."""
    if tolerancia <= 0 or not pivos:
        return []
    grupos: list[list[tuple[int, float]]] = []
    for idx, preco in sorted(pivos, key=lambda p: p[1]):
        if grupos and preco - grupos[-1][0][1] <= tolerancia:
            grupos[-1].append((idx, preco))
        else:
            grupos.append([(idx, preco)])
    return [
        {
            "price": fmean(p for _, p in g),
            "touches": len(g),
            "source": source,
            "last_touch": max(i for i, _ in g),
        }
        for g in grupos
        if len(g) >= min_touches
    ]


def _levels(fechadas: list[dict[str, Any]], atr: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resistências e suportes vistos no gráfico, com a origem de cada um."""
    tolerancia = atr * SR_RESPECT_TOLERANCE_ATR
    longas = fechadas[-SR_RESPECT_LOOKBACK:]
    curtas = fechadas[-SR_LEVEL_VISIBLE_LOOKBACK:]
    topos_longos, fundos_longos = _pivos_com_indice(
        longas, SR_RESPECT_PIVOT_RADIUS, len(fechadas) - len(longas)
    )
    topos_curtos, fundos_curtos = _pivos_com_indice(
        curtas, SR_RESPECT_PIVOT_RADIUS, len(fechadas) - len(curtas)
    )
    resistencias = _agrupa(topos_longos, tolerancia=tolerancia, min_touches=2, source="PIVO_2_TOQUES")
    resistencias += _agrupa(topos_curtos, tolerancia=tolerancia, min_touches=1, source="TOPO_VISIVEL")
    suportes = _agrupa(fundos_longos, tolerancia=tolerancia, min_touches=2, source="PIVO_2_TOQUES")
    suportes += _agrupa(fundos_curtos, tolerancia=tolerancia, min_touches=1, source="FUNDO_VISIVEL")
    janela = fechadas[-SR_LEVEL_EXTREME_WINDOW:]
    base = len(fechadas) - len(janela)
    topo = max(range(len(janela)), key=lambda i: candle_high(janela[i]))
    fundo = min(range(len(janela)), key=lambda i: candle_low(janela[i]))
    resistencias.append({
        "price": candle_high(janela[topo]), "touches": 1, "source": "MAXIMA_RECENTE",
        "last_touch": base + topo,
    })
    suportes.append({
        "price": candle_low(janela[fundo]), "touches": 1, "source": "MINIMA_RECENTE",
        "last_touch": base + fundo,
    })
    return resistencias, suportes


def _nearest(niveis: list[dict[str, Any]], distancia, idade_minima: int) -> dict[str, Any] | None:
    """Nível válido mais perto: à frente do preço, não rompido e não recém-criado.

    ``idade_minima`` é o índice máximo de toque aceito: nível cujo último toque
    é mais novo que isso foi criado pela vela que acabou de fechar — é o extremo
    do movimento em curso, não um nível que já se provou.
    """
    melhor = None
    for nivel in niveis:
        if int(nivel.get("last_touch", 0)) > idade_minima:
            continue
        d = distancia(nivel["price"])
        if -SR_LEVEL_BREAK_ATR <= d <= SR_LEVEL_PROXIMITY_ATR:
            if melhor is None or abs(d) < abs(melhor["distance_atr"]) or (
                abs(d) == abs(melhor["distance_atr"]) and nivel["touches"] > melhor["touches"]
            ):
                melhor = {**nivel, "distance_atr": d}
    return melhor


def _movimento_atr(fechadas: list[dict[str, Any]], atr: float) -> float:
    """Quanto o preço andou nas últimas velas, em ATR (positivo = subindo)."""
    if len(fechadas) <= SR_LEVEL_TREND_CANDLES or atr <= 0:
        return 0.0
    inicio = float(fechadas[-1 - SR_LEVEL_TREND_CANDLES]["close"])
    return (float(fechadas[-1]["close"]) - inicio) / atr


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
    idade_minima = len(fechadas) - 1 - SR_LEVEL_MIN_AGE_CANDLES
    resistencia = _nearest(resistencias, lambda nivel: (nivel - preco) / atr, idade_minima)
    suporte = _nearest(suportes, lambda nivel: (preco - nivel) / atr, idade_minima)
    # O nível que vale é o que está À FRENTE do movimento. Preço subindo só
    # opera a resistência acima (venda); descendo, só o suporte abaixo (compra).
    # Um suporte atrás de um preço que sobe não é compra: é o nível que o preço
    # já deixou. Sem isto, o robô comprou na CHFJPY 12/09 15:44 com o preço
    # vindo de +1,94 ATR e resistência 0,42 ATR acima — o cliente mandou vídeo.
    movimento = _movimento_atr(fechadas, atr)
    if movimento >= SR_LEVEL_TREND_ATR:
        suporte = None
    elif movimento <= -SR_LEVEL_TREND_ATR:
        resistencia = None
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
        "movement_atr": round(movimento, 2),
        "level": float(nivel["price"]),
        "touches": int(nivel["touches"]),
        "source": nivel["source"],
        "distance_atr": round(float(nivel["distance_atr"]), 3),
        "atr": atr,
        "confidence": confianca,
    }


def _range(candle: dict[str, Any]) -> float:
    return max(0.0, candle_high(candle) - candle_low(candle))


def wick_ratio(candle: dict[str, Any]) -> float:
    """Pavios (cima + baixo) em fração do range. Igual ao do filtro geral."""
    faixa = _range(candle)
    if faixa <= 0:
        return 0.0
    corpo = abs(float(candle["close"]) - float(candle["open"]))
    return max(0.0, min(1.0, (faixa - corpo) / faixa))


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
    # Mercado indeciso: velas seguidas deixando muito pavio, de qualquer lado.
    # Aqui o lado não importa — é o gráfico que o cliente vê "cheio de pavio".
    janela = fechadas[-SR_LEVEL_WICK_SEQUENCE_WINDOW:]
    cabeludas = sum(
        1
        for c in janela
        if _range(c) >= SR_LEVEL_WICK_MIN_RANGE_ATR * atr
        and wick_ratio(c) >= SR_LEVEL_WICK_SEQUENCE_RATIO
    )
    if cabeludas >= SR_LEVEL_WICK_SEQUENCE_COUNT:
        return False, LEVEL_WICK_SEQUENCE
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
