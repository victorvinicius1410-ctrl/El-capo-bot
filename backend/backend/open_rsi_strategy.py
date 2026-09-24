"""RSI extremo: estratégia do MERCADO ABERTO por timeframe (24/09/2026).

Substitui a REV-Z como motor do mercado aberto quando
``OPEN_MARKET_STRATEGY=RSI``. O OTC não é tocado: em ativo ``-OTC`` nada daqui
roda.

## Por que trocar a REV-Z

Medido em 24/09 com as velas M1 reais de 14/09 a 24/09 — dias que nenhuma
escolha de parâmetro tinha visto. A REV-Z (|z| >= 4,5 em M1) deu **49,2%** em
65 operações, abaixo do empate de 54,05% (payout 85%). Já tinha dado 51,7%
depois de 05/09. A vantagem de agosto não se sustentou.

A reversão pelo RSI(14) sobreviveu nas duas semanas e na pesquisa de 10/09
(com dados anteriores). Backtest com as regras REAIS do robô — espera de 3
velas entre entradas, cooldown de 30 min no ativo que perdeu, pausa de 10 min
depois de 2 perdas, respeito ao nível na análise e reconferência de nível e
pavio no disparo — uma conta, 9 pares, 8,66 dias úteis:

    M1  RSI M1  20/80  exp 1  -> 17,4/dia  55,6%   (22/78: 26/dia 52,0%; 24/76: 37/dia 50,2%)
    M5  RSI M1  24/76  exp 5  -> 15,9/dia  56,5%   (22/78: 12/dia 61,4%; 26/74: 22/dia 54,4%)
    M15 RSI M15 28/72  exp 15 ->  6,4/dia  60,0%   (30/70: 8/dia 53,6%; amostra pequena)

Os limites de cada timeframe vêm do ``.env`` (``RSI_OPEN_M1_LOW`` etc.): a
escolha entre volume e acerto é do dono, e trocá-la não pede redeploy de
código. ``HIGH`` é sempre ``100 - LOW``.

## Ressalvas que não podem sumir

- 8,66 dias úteis: a margem de erro é de ±7 a ±13 pontos. Nada aqui chega
  perto de 65%, e nenhum número acima pode ser prometido a cliente.
- No M5 o RSI é das velas **M1**, lido na virada de 5 min; no M15 é da própria
  vela **M15**. Foi o que o backtest mediu melhor em cada um (M5 com o RSI da
  vela M5 e M15 com o das M1 ficaram piores).

## Os dois tempos (mesmo desenho da REV-Z)

O robô analisa no segundo 5-20 da vela N, enxergando a vela N ainda em
formação, e entra na abertura da N+1. O backtest decide no FECHAMENTO da vela e
entra na seguinte. Por isso:

1. na análise, o limite é afrouxado em ``RSI_OPEN_NOMINATE_MARGIN`` pontos e só
   INDICA o candidato;
2. no disparo, ``rsi_open_confirm_at_entry`` refaz o RSI com a vela que acabou
   de FECHAR e exige o limite cheio, na mesma direção.

O veredito viaja no campo ``revz`` do candidato, com ``strategy="RSI"``. É de
propósito: esse campo já atravessa as quatro listas fixas do caminho até a
compra (``ANALYSIS_DETAIL_FIELDS``, ``set_pending_signal``, o registro da
operação e ``TRADE_ANALYSIS_FIELDS``). Um campo novo teria de ser posto nas
quatro, e esquecer uma já custou duas horas sem ordem em 10/09.
"""

from __future__ import annotations

import os
from typing import Any

from backend.reversion_strategy import (
    REVZ_CONFIDENCE_FLOOR,
    REVZ_CONFIDENCE_MAX,
    REVZ_ENABLED,
    is_otc_symbol,
)

# "REVZ" (padrão) mantém o motor de 10/09; "RSI" liga este. Só vale com
# ``REVZ_ENABLED=true``, que é quem liga o encanamento do mercado aberto.
OPEN_MARKET_STRATEGY = os.getenv("OPEN_MARKET_STRATEGY", "REVZ").strip().upper()

STRATEGY_RSI_OPEN = "RSI_ABERTO"
RSI_PERIOD = 14
# Wilder converge em poucas dezenas de velas; abaixo disto o valor ainda
# depende demais da semente.
RSI_MIN_CLOSES = 60


def _limite(nome: str, padrao: float) -> float:
    try:
        valor = float(os.getenv(nome, str(padrao)))
    except ValueError:
        return padrao
    # Limite inferior válido fica entre 1 e 49: 50 operaria toda vela.
    return min(max(valor, 1.0), 49.0)


# Limite INFERIOR por timeframe da conta (o superior é 100 - inferior).
# Padrões = o ajuste de maior acerto medido em cada um.
RSI_OPEN_LOW: dict[str, float] = {
    "M1": _limite("RSI_OPEN_M1_LOW", 20),
    "M5": _limite("RSI_OPEN_M5_LOW", 24),
    "M15": _limite("RSI_OPEN_M15_LOW", 28),
}

# Timeframe da vela em que o RSI é calculado, por timeframe da conta.
RSI_OPEN_CANDLE_TIMEFRAME: dict[str, str] = {"M1": "M1", "M5": "M1", "M15": "M15"}

# Folga da indicação na análise (vela em formação). Quem decide é a
# confirmação no disparo com o limite cheio.
RSI_OPEN_NOMINATE_MARGIN = float(os.getenv("RSI_OPEN_NOMINATE_MARGIN", "6"))


def is_rsi_open_active() -> bool:
    """Indica se o mercado aberto é decidido pelo RSI (e não pela REV-Z)."""
    return REVZ_ENABLED and OPEN_MARKET_STRATEGY == "RSI"


def rsi_open_candle_timeframe(timeframe: str) -> str | None:
    """Timeframe das velas do RSI para a conta, ou ``None`` se não há regra."""
    return RSI_OPEN_CANDLE_TIMEFRAME.get(str(timeframe or "").strip().upper())


def rsi_open_limits(timeframe: str, *, nominate: bool = False) -> tuple[float, float] | None:
    """Limites ``(baixo, alto)`` do timeframe; afrouxados quando ``nominate``."""
    baixo = RSI_OPEN_LOW.get(str(timeframe or "").strip().upper())
    if baixo is None:
        return None
    if nominate:
        baixo = min(baixo + RSI_OPEN_NOMINATE_MARGIN, 49.0)
    return baixo, 100.0 - baixo


def rsi_wilder(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """RSI de Wilder do último fechamento.

    Semente = média simples dos ``period`` primeiros ganhos e perdas; depois
    suavização de Wilder. É exatamente o cálculo do backtest de 24/09.

    Returns:
        O RSI (0-100), ou ``None`` com fechamentos insuficientes.
    """
    valores = [float(c) for c in closes]
    if len(valores) < period + 1:
        return None
    ganhos: list[float] = []
    perdas: list[float] = []
    media_ganho = media_perda = 0.0
    for anterior, atual in zip(valores, valores[1:]):
        delta = atual - anterior
        ganho, perda = max(delta, 0.0), max(-delta, 0.0)
        if len(ganhos) < period:
            ganhos.append(ganho)
            perdas.append(perda)
            if len(ganhos) == period:
                media_ganho = sum(ganhos) / period
                media_perda = sum(perdas) / period
            continue
        media_ganho = (media_ganho * (period - 1) + ganho) / period
        media_perda = (media_perda * (period - 1) + perda) / period
    if media_perda == 0:
        return 100.0 if media_ganho > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + media_ganho / media_perda)


def rsi_direction(rsi: float | None, limites: tuple[float, float]) -> str | None:
    """Reversão: RSI baixo compra, RSI alto vende; ``None`` fora do extremo."""
    if rsi is None:
        return None
    baixo, alto = limites
    if rsi < baixo:
        return "CALL"
    if rsi > alto:
        return "PUT"
    return None


def rsi_open_evaluate(
    symbol: str,
    closes: list[float],
    timeframe: str,
    *,
    nominate: bool = True,
) -> dict[str, Any]:
    """Veredito do RSI para um ativo de mercado aberto.

    Args:
        symbol: Ativo analisado.
        closes: Fechamentos das velas de ``rsi_open_candle_timeframe``.
        timeframe: Timeframe da CONTA (M1/M5/M15).
        nominate: ``True`` na análise (limite afrouxado); ``False`` no disparo.

    Returns:
        Mesmo formato do veredito da REV-Z: ``direction`` (``None`` quando não
        opera), ``blocked`` e os parâmetros usados; ``z`` fica ``None``.
    """
    tf = str(timeframe or "").strip().upper()
    limites = rsi_open_limits(tf, nominate=nominate)
    confirmacao = rsi_open_limits(tf)
    resultado: dict[str, Any] = {
        "strategy": "RSI",
        "direction": None,
        "z": None,
        "rsi": None,
        "blocked": None,
        "timeframe": tf,
        "candle_timeframe": rsi_open_candle_timeframe(tf),
        "period": RSI_PERIOD,
        "low": None if limites is None else limites[0],
        "high": None if limites is None else limites[1],
        "confirm_low": None if confirmacao is None else confirmacao[0],
        "confirm_high": None if confirmacao is None else confirmacao[1],
    }
    if is_otc_symbol(symbol):
        resultado["blocked"] = "RSI_OTC_SEM_VANTAGEM"
        return resultado
    if limites is None:
        resultado["blocked"] = "RSI_TIMEFRAME_SEM_REGRA"
        return resultado
    if len(closes) < RSI_MIN_CLOSES:
        resultado["blocked"] = "RSI_VELAS_INSUFICIENTES"
        return resultado
    rsi = rsi_wilder(closes)
    resultado["rsi"] = rsi
    direcao = rsi_direction(rsi, limites)
    if direcao is None:
        resultado["blocked"] = "RSI_SEM_EXTREMO"
        return resultado
    resultado["direction"] = direcao
    return resultado


def rsi_open_confidence(rsi: float | None, timeframe: str) -> int:
    """Confiança exibida, na mesma escala curta da REV-Z (55 a 75).

    Cresce com a distância além do limite de CONFIRMAÇÃO. Indicação que ainda
    não chegou ao limite cheio fica no chão (55), que é o piso do portão.
    """
    limites = rsi_open_limits(timeframe)
    if rsi is None or limites is None:
        return 0
    baixo, alto = limites
    excesso = max(0.0, baixo - rsi, rsi - alto)
    return int(round(min(float(REVZ_CONFIDENCE_MAX), REVZ_CONFIDENCE_FLOOR + excesso * 2.0)))


def rsi_open_confirm_at_entry(
    closes: list[float] | None,
    direction: str,
    timeframe: str,
) -> tuple[bool, float | None, str]:
    """Confirma, no disparo, o RSI da vela que acabou de fechar.

    Args:
        closes: Fechamentos das velas FECHADAS (de ``closes_of_closed_candles``).
        direction: Direção do candidato.
        timeframe: Timeframe da conta.

    Returns:
        ``(confirmado, rsi, motivo)``.
    """
    limites = rsi_open_limits(timeframe)
    if not closes or limites is None or len(closes) < RSI_MIN_CLOSES:
        return False, None, "RSI_CONFIRMACAO_SEM_DADOS"
    rsi = rsi_wilder(list(closes))
    direcao = rsi_direction(rsi, limites)
    if direcao is None:
        return False, rsi, "RSI_SEM_EXTREMO_NO_FECHAMENTO"
    if direcao != str(direction or "").strip().upper():
        return False, rsi, "RSI_DIRECAO_VIROU"
    return True, rsi, "RSI_CONFIRMADO"


def rsi_open_text(symbol: str, veredito: dict[str, Any]) -> str:
    """Texto da análise para o painel e a narração.

    Só diz o que foi medido — sem "certeza", "tendência de voltar" ou
    "margem confortável" (regra da fala sem veredito, 10/09).
    """
    rsi = veredito.get("rsi")
    velas = veredito.get("candle_timeframe") or veredito.get("timeframe") or ""
    if rsi is None:
        return f"{symbol}: sem velas suficientes para medir o RSI — aguardando."
    direcao = veredito.get("direction")
    if direcao is None:
        return f"{symbol}: RSI {rsi:.0f} nas velas {velas}, fora do extremo — aguardando."
    lado = "vendido demais" if direcao == "CALL" else "comprado demais"
    return f"{symbol}: RSI {rsi:.0f} nas velas {velas}, preço {lado}. Entrada de {direcao}."
