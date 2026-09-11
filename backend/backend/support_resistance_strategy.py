"""Estratégia SR-R: rejeição em nível de suporte ou resistência.

Criada em 2026-09-03 a pedido do dono, junto com a reauditoria das 12.848
operações do sistema 01. É a primeira implementação de suporte/resistência de
verdade no projeto: o motor clássico já tinha um campo ``near_support_resistance``,
mas ele **nunca aparece verdadeiro em nenhuma ordem executada** (12.848 linhas,
zero ocorrências) — o nível ali é só o mínimo/máximo das 24 velas anteriores,
sem contagem de toques e sem confirmação de rejeição.

## A regra

A cada vela FECHADA, olhando ``SR_LOOKBACK`` velas para trás:

1. **Pivôs.** Vela cuja máxima é a maior de uma janela de ``±SR_PIVOT_RADIUS``
   vira pivô de topo; a menor, pivô de fundo.
2. **Níveis.** Pivôs a menos de ``SR_TOLERANCE_ATR × ATR`` um do outro formam um
   nível. O preço do nível é a média do grupo, e a força é quantos toques ele tem.
   Nível só vale com ``SR_MIN_TOUCHES`` toques ou mais — é isso que separa um
   nível de um repique qualquer.
3. **Gatilho.** A última vela fechada entrou na zona do nível e **voltou**:
   - fundo perfurado e fechamento de volta acima do suporte, com pavio inferior
     de pelo menos ``SR_MIN_WICK_RATIO`` do range → ``CALL``;
   - topo perfurado e fechamento de volta abaixo da resistência, com pavio
     superior equivalente → ``PUT``.
4. **Recusa** quando o fechamento ficou do lado errado do nível (nível rompido,
   não rejeitado), quando a vela é doji, e quando não há nível válido por perto.

É reversão em nível, não continuação. Aposta contra quem furou o nível e não
conseguiu sustentar — a mesma família da REV-Z, que é a única vantagem que
sobreviveu à varredura de 2026-08-31 (ver ``reversion_strategy.py``).

## Ressalva obrigatória

Os números medidos desta estratégia estão em ``docs/ESTRATEGIA_SR.md``. Enquanto
esse arquivo não disser que a medição foi feita para a frente, com amostra
suficiente, **isto é hipótese**, não vantagem. A auditoria de 03/09/2026 mostrou
que 11.397 regras testadas no histórico produziram duas com 60% de acerto no
treino e nenhuma que sobrevivesse ao período seguinte — qualquer regra nova
começa culpada até prova em contrário.
"""

from __future__ import annotations

import logging
import os
from statistics import fmean
from typing import Any

logger = logging.getLogger("backend-gateway")

# Liga a SR-R no lugar do motor clássico. Como a REV-Z, é CONFIGURAÇÃO DE
# AMBIENTE e não constante de código: ligar no código muda o significado de
# `analyze_signal` para o processo inteiro e quebra os testes do motor clássico.
SR_ENABLED = os.getenv("SR_ENABLED", "false").strip().lower() in {"1", "true", "yes"}

# Permite operar ativo sintético (-OTC). Padrão desligado: a série OTC é passeio
# aleatório em 164.000 velas medidas, e a auditoria de 03/09 não achou nenhuma
# regra que sobrevivesse fora da amostra. Ligar isto é apostar, não operar.
SR_ALLOW_OTC = os.getenv("SR_ALLOW_OTC", "false").strip().lower() in {"1", "true", "yes"}

# Velas fechadas usadas para montar os níveis (M1: 120 = 2 horas).
SR_LOOKBACK = 120
# Raio da janela que define um pivô. 2 = a vela tem que ser o extremo entre as
# 2 anteriores e as 2 seguintes.
SR_PIVOT_RADIUS = 2
# Distância máxima, em ATR, para dois pivôs contarem como o mesmo nível.
SR_TOLERANCE_ATR = 0.5
# Toques mínimos para o nível valer. 1 toque é um repique; 2 é um nível.
SR_MIN_TOUCHES = 2
# Pavio mínimo de rejeição, como fração do range da vela.
SR_MIN_WICK_RATIO = 0.45
# Corpo máximo da vela de rejeição, como fração do range. Vela de corpo cheio
# que fecha longe do nível é rompimento, não rejeição.
SR_MAX_BODY_RATIO = 0.55
# Velas mínimas para sequer calcular.
SR_MIN_CANDLES = SR_LOOKBACK + SR_PIVOT_RADIUS * 2 + 2

# Escala própria de confiança, curta e deliberadamente longe de 100 — mesma
# decisão da REV-Z. Quem compara o mínimo do usuário (escala 0-100 do motor
# clássico) contra estes valores barra 100% dos sinais; ver o portão em
# `apply_strategy_guard` e a nota de `REVZ_CONFIDENCE_MAX`.
SR_CONFIDENCE_FLOOR = 55
SR_CONFIDENCE_MAX = 75


def is_otc_symbol(symbol: str) -> bool:
    """Indica se o ativo é sintético da corretora (sufixo ``-OTC``)."""
    return str(symbol or "").strip().upper().endswith("-OTC")


def candle_high(candle: dict[str, float]) -> float:
    """Máxima da vela, aceitando os dois formatos que circulam no projeto.

    O ``GET /candles`` da corretora devolve ``max``/``min`` e é assim que
    ``_normalize_candle`` do motor mantém as velas; os datasets de backtest e
    os testes usam ``high``/``low``. Ler só um dos dois quebra em produção sem
    quebrar nenhum teste — foi o que aconteceu em 2026-09-04.

    Args:
        candle: Vela com ``high`` ou ``max``.

    Returns:
        A máxima como float.

    Raises:
        KeyError: Se a vela não tiver nenhuma das duas chaves.
    """
    if "high" in candle:
        return float(candle["high"])
    return float(candle["max"])


def candle_low(candle: dict[str, float]) -> float:
    """Mínima da vela, aceitando ``low`` ou ``min``. Ver ``candle_high``."""
    if "low" in candle:
        return float(candle["low"])
    return float(candle["min"])


def average_true_range(candles: list[dict[str, float]], periodo: int = 14) -> float:
    """
    Amplitude média das últimas velas, usada como unidade de distância.

    Args:
        candles: Velas em ordem cronológica, com ``high``/``low``.
        periodo: Quantas velas entram na média.

    Returns:
        A amplitude média, ou ``0.0`` quando não há velas.
    """
    janela = candles[-periodo:] if periodo > 0 else candles
    if not janela:
        return 0.0
    return fmean(candle_high(c) - candle_low(c) for c in janela)


def find_pivots(
    candles: list[dict[str, float]],
    *,
    radius: int = SR_PIVOT_RADIUS,
) -> tuple[list[float], list[float]]:
    """
    Localiza os pivôs de topo e de fundo de uma série.

    Um pivô de topo é a vela cuja máxima é a maior da janela ``[i-radius,
    i+radius]``; o de fundo, a menor mínima. As ``radius`` velas de cada ponta
    não podem ser avaliadas e ficam de fora.

    Args:
        candles: Velas fechadas em ordem cronológica.
        radius: Quantas velas de cada lado a vela precisa superar.

    Returns:
        Par ``(topos, fundos)`` com os preços dos pivôs, em ordem cronológica.
    """
    topos: list[float] = []
    fundos: list[float] = []
    if radius < 1 or len(candles) < radius * 2 + 1:
        return topos, fundos
    for i in range(radius, len(candles) - radius):
        janela = candles[i - radius : i + radius + 1]
        alta = candle_high(candles[i])
        baixa = candle_low(candles[i])
        if alta >= max(candle_high(c) for c in janela):
            topos.append(alta)
        if baixa <= min(candle_low(c) for c in janela):
            fundos.append(baixa)
    return topos, fundos


def cluster_levels(
    precos: list[float],
    *,
    tolerancia: float,
    min_touches: int = SR_MIN_TOUCHES,
) -> list[dict[str, Any]]:
    """
    Agrupa pivôs próximos em níveis com contagem de toques.

    Args:
        precos: Preços dos pivôs, em qualquer ordem.
        tolerancia: Distância máxima entre dois pivôs do mesmo nível.
        min_touches: Toques mínimos para o nível entrar no resultado.

    Returns:
        Níveis ``{"price", "touches"}`` ordenados por preço. Lista vazia quando
        a tolerância não é positiva ou nenhum grupo alcança ``min_touches``.
    """
    if tolerancia <= 0 or not precos:
        return []
    grupos: list[list[float]] = []
    for preco in sorted(float(p) for p in precos):
        if grupos and preco - grupos[-1][0] <= tolerancia:
            grupos[-1].append(preco)
        else:
            grupos.append([preco])
    return [
        {"price": fmean(g), "touches": len(g)}
        for g in grupos
        if len(g) >= min_touches
    ]


def candle_shape(candle: dict[str, float]) -> dict[str, float]:
    """
    Mede corpo e pavios de uma vela como fração do range.

    Args:
        candle: Vela com ``open``/``high``/``low``/``close``.

    Returns:
        ``{"range", "body_ratio", "upper_wick_ratio", "lower_wick_ratio"}``.
        Com range zero (vela travada) todas as frações saem ``0.0``.
    """
    abertura = float(candle["open"])
    fechamento = float(candle["close"])
    alta = candle_high(candle)
    baixa = candle_low(candle)
    amplitude = alta - baixa
    if amplitude <= 0:
        return {"range": 0.0, "body_ratio": 0.0, "upper_wick_ratio": 0.0, "lower_wick_ratio": 0.0}
    corpo_alto = max(abertura, fechamento)
    corpo_baixo = min(abertura, fechamento)
    return {
        "range": amplitude,
        "body_ratio": abs(fechamento - abertura) / amplitude,
        "upper_wick_ratio": (alta - corpo_alto) / amplitude,
        "lower_wick_ratio": (corpo_baixo - baixa) / amplitude,
    }


def sr_evaluate(
    symbol: str,
    candles: list[dict[str, float]],
    *,
    lookback: int = SR_LOOKBACK,
    min_touches: int = SR_MIN_TOUCHES,
    tolerance_atr: float = SR_TOLERANCE_ATR,
    min_wick_ratio: float = SR_MIN_WICK_RATIO,
    max_body_ratio: float = SR_MAX_BODY_RATIO,
    allow_otc: bool | None = None,
) -> dict[str, Any]:
    """
    Avalia a última vela fechada e devolve o veredito da SR-R.

    Args:
        symbol: Ativo analisado.
        candles: Velas FECHADAS em ordem cronológica; a última é o gatilho.
        lookback: Velas usadas para montar os níveis (sem contar o gatilho).
        min_touches: Toques mínimos para o nível valer.
        tolerance_atr: Largura da zona do nível, em ATR.
        min_wick_ratio: Pavio de rejeição mínimo, como fração do range.
        max_body_ratio: Corpo máximo da vela de rejeição.
        allow_otc: Sobrepõe ``SR_ALLOW_OTC``; usado pelo backtest.

    Returns:
        ``direction`` (``None`` quando não opera), ``level``, ``touches``,
        ``wick_ratio``, ``blocked`` com o motivo da recusa e os parâmetros.
    """
    permite_otc = SR_ALLOW_OTC if allow_otc is None else bool(allow_otc)
    resultado: dict[str, Any] = {
        "strategy": "SR-R",
        "direction": None,
        "level": None,
        "touches": 0,
        "wick_ratio": None,
        "distance_atr": None,
        "blocked": None,
        "lookback": lookback,
        "min_touches": min_touches,
    }
    if is_otc_symbol(symbol) and not permite_otc:
        resultado["blocked"] = "SR_OTC_SEM_VANTAGEM"
        return resultado
    if len(candles) < lookback + SR_PIVOT_RADIUS * 2 + 2:
        resultado["blocked"] = "SR_VELAS_INSUFICIENTES"
        return resultado

    gatilho = candles[-1]
    historico = candles[-(lookback + 1) : -1]
    atr = average_true_range(historico)
    if atr <= 0:
        resultado["blocked"] = "SR_SERIE_SEM_VARIACAO"
        return resultado
    tolerancia = atr * tolerance_atr

    forma = candle_shape(gatilho)
    if forma["range"] <= 0:
        resultado["blocked"] = "SR_VELA_TRAVADA"
        return resultado
    if forma["body_ratio"] > max_body_ratio:
        # Corpo cheio no nível é rompimento, não rejeição.
        resultado["blocked"] = "SR_CORPO_DE_ROMPIMENTO"
        return resultado

    topos, fundos = find_pivots(historico)
    resistencias = cluster_levels(topos, tolerancia=tolerancia, min_touches=min_touches)
    suportes = cluster_levels(fundos, tolerancia=tolerancia, min_touches=min_touches)
    if not resistencias and not suportes:
        resultado["blocked"] = "SR_SEM_NIVEL_VALIDO"
        return resultado

    baixa = candle_low(gatilho)
    alta = candle_high(gatilho)
    fechamento = float(gatilho["close"])

    # Suporte: a mínima entrou na zona e o fechamento voltou para cima dela.
    candidatos_suporte = [
        nivel
        for nivel in suportes
        if baixa <= nivel["price"] + tolerancia and fechamento > nivel["price"]
    ]
    # Resistência: a máxima entrou na zona e o fechamento voltou para baixo dela.
    candidatos_resistencia = [
        nivel
        for nivel in resistencias
        if alta >= nivel["price"] - tolerancia and fechamento < nivel["price"]
    ]

    if candidatos_suporte and candidatos_resistencia:
        # Preso entre os dois — não há lado favorecido.
        resultado["blocked"] = "SR_NIVEL_CONFLITANTE"
        return resultado

    if candidatos_suporte:
        nivel = max(candidatos_suporte, key=lambda n: (n["touches"], n["price"]))
        if forma["lower_wick_ratio"] < min_wick_ratio:
            resultado["blocked"] = "SR_SEM_REJEICAO"
            resultado["level"] = nivel["price"]
            resultado["touches"] = nivel["touches"]
            return resultado
        resultado.update(
            {
                "direction": "CALL",
                "level": nivel["price"],
                "touches": nivel["touches"],
                "wick_ratio": forma["lower_wick_ratio"],
                "distance_atr": (fechamento - nivel["price"]) / atr,
            }
        )
        return resultado

    if candidatos_resistencia:
        nivel = min(candidatos_resistencia, key=lambda n: (-n["touches"], n["price"]))
        if forma["upper_wick_ratio"] < min_wick_ratio:
            resultado["blocked"] = "SR_SEM_REJEICAO"
            resultado["level"] = nivel["price"]
            resultado["touches"] = nivel["touches"]
            return resultado
        resultado.update(
            {
                "direction": "PUT",
                "level": nivel["price"],
                "touches": nivel["touches"],
                "wick_ratio": forma["upper_wick_ratio"],
                "distance_atr": (nivel["price"] - fechamento) / atr,
            }
        )
        return resultado

    resultado["blocked"] = "SR_LONGE_DO_NIVEL"
    return resultado


def sr_confidence(veredito: dict[str, Any]) -> int:
    """
    Confiança exibida, derivada da força do nível e do tamanho da rejeição.

    Curta de propósito (55 a 75): o acerto esperado de uma estratégia de nível
    não justifica número perto de 100, e "confiança 100 num sinal de moeda" foi
    um dos defeitos apontados na auditoria do sistema 01.

    Args:
        veredito: Saída de ``sr_evaluate``.

    Returns:
        Inteiro entre 0 (sem direção) e ``SR_CONFIDENCE_MAX``.
    """
    if not veredito.get("direction"):
        return 0
    toques_extras = max(0, int(veredito.get("touches") or 0) - SR_MIN_TOUCHES)
    pavio_extra = max(0.0, float(veredito.get("wick_ratio") or 0.0) - SR_MIN_WICK_RATIO)
    bruto = SR_CONFIDENCE_FLOOR + toques_extras * 4.0 + pavio_extra * 30.0
    return int(round(min(float(SR_CONFIDENCE_MAX), bruto)))
