"""Região de suporte e resistência que o motor clássico passa a respeitar.

Substitui a leitura que existia embutida em ``signal_engine`` desde o começo do
projeto, e que tinha três defeitos medidos em 2026-09-09:

1. **Não era suporte e resistência.** ``_support_resistance_context`` chamava de
   suporte o menor ``min`` das 23 velas anteriores e de resistência o maior
   ``max``. Em M1 isso é o extremo de uma janela de 23 minutos: não conta
   toques, não valida o nível, não olha pivô. Medido em 50.700 transições, o
   valor do "suporte" mudava em 25,7% das velas — um nível que se move a cada
   quatro minutos não é um nível.
2. **A zona cobria metade do gráfico.** A tolerância era
   ``max(range * 0,12, ATR * 0,65)`` para cada lado; 55,0% das leituras caíam
   dentro dela.
3. **Contradição fatal.** ``_price_action_setup`` só devolvia
   ``"SUPPORT_RESISTANCE"`` quando o preço estava colado no nível, e o filtro
   ``SR_ZONE`` barrava exatamente essa condição como hard block. Medido em 8.000
   leituras: 383 setups de S/R, 383 bloqueados. Cem por cento — a implicação é
   lógica, não estatística. O motor somava os 15 pontos do setup no score e
   matava a entrada na mesma passagem.

Aqui a região vem de níveis de verdade — pivôs agrupados com contagem de toques,
a mesma construção da SR-R (``support_resistance_strategy``), reaproveitada
diretamente para não existirem duas definições de nível no projeto.

## O que "respeitar" passa a significar

Três decisões, nesta ordem:

- **Contra o nível: nunca.** CALL colado na resistência ou PUT colado no suporte
  é recusado, sempre. Isto já era o ``LEVEL_CONFLICT`` e continua igual.
- **Dentro da região, a favor: só com rejeição confirmada.** É a correção da
  contradição. Operar a região é entrar na rejeição do nível; barrar isso
  também, como o ``SR_ZONE`` fazia, é fugir da região e chamar de respeito.
- **Fora de qualquer região: livre.** O filtro não tem o que dizer.

Há ainda uma quarta condição, que a versão antiga não tinha como checar porque
não conhecia o nível oposto: **espaço para andar**. Rejeição no suporte com a
resistência a menos de ``SR_RESPECT_MIN_ROOM_ATR`` de distância não tem para
onde ir dentro da vela, e é recusada.

## Reversibilidade

``SR_RESPECT=false`` devolve o comportamento anterior por inteiro (extremo de
janela + veto cego), sem redeploy de código.
"""

from __future__ import annotations

import os
from typing import Any

from backend.support_resistance_strategy import (
    average_true_range,
    candle_high,
    candle_low,
    candle_shape,
    cluster_levels,
    find_pivots,
)

# Liga a região por níveis reais e a regra de respeito. Diferente de
# `SR_ENABLED`, isto NÃO troca a estratégia: o motor clássico continua decidindo
# a direção. Só muda de onde sai a região e o que o filtro faz com ela.
SR_RESPECT_ENABLED = os.getenv("SR_RESPECT", "true").strip().lower() in {"1", "true", "yes"}

# Velas fechadas usadas para montar os níveis. Em M1, 120 = 2 horas — a mesma
# janela da SR-R, que foi onde os níveis por pivô foram calibrados.
SR_RESPECT_LOOKBACK = int(os.getenv("SR_RESPECT_LOOKBACK", "120"))
# Raio do pivô: a vela tem que ser o extremo entre as 2 anteriores e as 2
# seguintes.
SR_RESPECT_PIVOT_RADIUS = 2
# Distância, em ATR, para dois pivôs contarem como o mesmo nível — e também a
# meia-largura da região. 0,5 ATR contra os ~12% do range da versão antiga.
SR_RESPECT_TOLERANCE_ATR = float(os.getenv("SR_RESPECT_TOLERANCE_ATR", "0.5"))
# Toques mínimos para o nível valer. 1 toque é um repique; 2 é um nível.
SR_RESPECT_MIN_TOUCHES = int(os.getenv("SR_RESPECT_MIN_TOUCHES", "2"))
# Espaço mínimo até o nível oposto, em ATR, para a entrada valer a pena.
SR_RESPECT_MIN_ROOM_ATR = float(os.getenv("SR_RESPECT_MIN_ROOM_ATR", "1.5"))

# Forma da vela de rejeição. Mesmos limiares do `_level_rejection_confirmed`
# clássico, de propósito: esta mudança já troca a origem do nível e o veto, e
# mexer nos três ao mesmo tempo tornaria o efeito impossível de atribuir.
SR_RESPECT_MIN_BODY_RATIO = 0.35
SR_RESPECT_MIN_WICK_RATIO = 0.20
SR_RESPECT_MAX_OPPOSITE_WICK = 0.40

# Velas mínimas para montar níveis por pivô. Abaixo disto a região sai do
# extremo da janela, como antes, e o veto volta a ser cego — a construção antiga
# é o piso, nunca o padrão.
SR_RESPECT_MIN_CANDLES = 40

# Motivos devolvidos por `evaluate_respect`, para o log e para os testes.
RESPECT_OK_CLEAR = "OK_FORA_DA_REGIAO"
RESPECT_OK_REJECTION = "OK_REJEICAO_NO_NIVEL"
RESPECT_CONFLICT = "CONTRA_O_NIVEL"
RESPECT_NO_REJECTION = "NA_REGIAO_SEM_REJEICAO"
RESPECT_NO_ROOM = "SEM_ESPACO_ATE_O_NIVEL_OPOSTO"
RESPECT_SEM_DIRECAO = "SEM_DIRECAO_A_AVALIAR"
RESPECT_VISIBLE_AHEAD = "NIVEL_VISIVEL_A_FRENTE"

# Nível VISÍVEL (10/09/2026, pedido do dono depois de três perdas do mesmo
# cliente): o nível acima exige 2 toques em 2 horas, então um topo novo — tocado
# uma vez, ou a máxima que o preço acabou de fazer — não existia para o robô.
# Ele comprava colado na máxima do gráfico registrando "resistência: nenhuma",
# e o cliente, olhando o mesmo gráfico, via a entrada contra a resistência.
# Medido nas ordens de 10/09: CHFJPY 0,20 ATR, EURGBP 0,15 e 0,38 ATR abaixo da
# máxima de 30 velas, as três perdidas.
# Agora conta como nível à frente da entrada: qualquer topo/fundo de pivô (1
# toque basta) nas últimas `SR_VISIBLE_PIVOT_LOOKBACK` velas fechadas e a
# máxima/mínima das últimas `SR_VISIBLE_EXTREME_WINDOW` velas. A entrada é
# recusada quando um deles está a menos de `SR_VISIBLE_MIN_ROOM_ATR` do preço,
# na direção da operação. `SR_VISIBLE_LEVELS=false` desliga sem redeploy.
SR_VISIBLE_LEVELS_ENABLED = os.getenv("SR_VISIBLE_LEVELS", "true").strip().lower() in {"1", "true", "yes"}
SR_VISIBLE_PIVOT_LOOKBACK = int(os.getenv("SR_VISIBLE_PIVOT_LOOKBACK", "60"))
SR_VISIBLE_EXTREME_WINDOW = int(os.getenv("SR_VISIBLE_EXTREME_WINDOW", "30"))
SR_VISIBLE_MIN_ROOM_ATR = float(os.getenv("SR_VISIBLE_MIN_ROOM_ATR", "0.5"))
# Topo/fundo que o preço já furou por mais que isto (em ATR) deixou de estar à
# frente — virou nível de trás.
SR_VISIBLE_BROKEN_ATR = 0.1
SR_VISIBLE_MIN_CANDLES = 20


def _window_zone(candles: list[dict[str, float]]) -> dict[str, Any]:
    """Região pelo extremo da janela — a construção antiga, agora só fallback.

    Args:
        candles: Velas normalizadas em ordem cronológica.

    Returns:
        O mesmo contrato de ``build_zone``, com ``source="WINDOW"``.
    """
    vazio = {
        "support": None,
        "resistance": None,
        "tolerance": 0.0,
        "near_support": False,
        "near_resistance": False,
        "support_touches": 0,
        "resistance_touches": 0,
        "source": "WINDOW",
    }
    if len(candles) < 12:
        return vazio
    recentes = candles[-24:]
    anteriores = recentes[:-1]
    ultima = recentes[-1]
    amplitude = max(candle_high(c) for c in recentes) - min(candle_low(c) for c in recentes)
    media = average_true_range(recentes, periodo=len(recentes))
    tolerancia = max(amplitude * 0.12, media * 0.65)
    if tolerancia <= 0:
        return vazio
    suporte = min(candle_low(c) for c in anteriores)
    resistencia = max(candle_high(c) for c in anteriores)
    dist_suporte = min(abs(candle_low(ultima) - suporte), abs(float(ultima["close"]) - suporte))
    dist_resistencia = min(
        abs(candle_high(ultima) - resistencia), abs(float(ultima["close"]) - resistencia)
    )
    return {
        "support": suporte,
        "resistance": resistencia,
        "tolerance": tolerancia,
        "near_support": dist_suporte <= tolerancia,
        "near_resistance": dist_resistencia <= tolerancia,
        "support_distance": dist_suporte,
        "resistance_distance": dist_resistencia,
        "support_touches": 0,
        "resistance_touches": 0,
        "source": "WINDOW",
    }


def visible_levels(candles: list[dict[str, float]]) -> dict[str, Any]:
    """Topos e fundos que qualquer um vê no gráfico, com ou sem 2 toques.

    Os pivôs saem só das velas fechadas (precisam das 2 velas seguintes para
    existir). A máxima e a mínima da janela INCLUEM a última vela: na análise
    ela está em formação e, se acabou de fazer a máxima do gráfico, o preço
    está exatamente nela; na reconferência do disparo a corretora pode ainda
    não ter aberto a vela nova, e a última é a que acabou de fechar.

    Args:
        candles: Velas normalizadas em ordem cronológica.

    Returns:
        ``{"visible_resistances", "visible_supports", "visible_atr"}`` —
        listas de preços e o ATR usado para medir distância. Vazio quando
        desligado ou sem velas suficientes.
    """
    if not SR_VISIBLE_LEVELS_ENABLED or len(candles) < SR_VISIBLE_MIN_CANDLES:
        return {}
    fechadas = candles[:-1]
    atr = average_true_range(fechadas[-SR_RESPECT_LOOKBACK:], periodo=14)
    if atr <= 0:
        return {}
    topos, fundos = find_pivots(fechadas[-SR_VISIBLE_PIVOT_LOOKBACK:], radius=SR_RESPECT_PIVOT_RADIUS)
    janela = candles[-SR_VISIBLE_EXTREME_WINDOW:]
    resistencias = [float(p) for p in topos] + [max(candle_high(c) for c in janela)]
    suportes = [float(p) for p in fundos] + [min(candle_low(c) for c in janela)]
    return {
        "visible_resistances": sorted(resistencias),
        "visible_supports": sorted(suportes),
        "visible_atr": atr,
    }


def visible_level_ahead(
    direction: str,
    zona: dict[str, Any],
    candle: dict[str, float],
) -> float | None:
    """Distância, em ATR, até o nível visível à frente da entrada.

    CALL olha os topos acima do preço; PUT, os fundos abaixo. Topo já furado por
    mais de ``SR_VISIBLE_BROKEN_ATR`` fica de fora; a máxima da janela nunca
    fica — se o preço está nela ou acima, a distância é zero ou negativa.

    Args:
        direction: ``"CALL"`` ou ``"PUT"``.
        zona: Saída de ``build_zone`` (com as chaves de ``visible_levels``).
        candle: Vela cujo fechamento é o preço da entrada.

    Returns:
        A menor distância em ATR, ou ``None`` quando a zona não traz níveis
        visíveis (desligado, poucas velas ou zona montada à mão).
    """
    atr = float(zona.get("visible_atr") or 0.0)
    if atr <= 0:
        return None
    preco = float(candle["close"])
    if direction == "CALL":
        niveis = list(zona.get("visible_resistances") or [])
        extremo = max(niveis) if niveis else None
        a_frente = [n for n in niveis if n >= preco - SR_VISIBLE_BROKEN_ATR * atr]
        if extremo is not None:
            a_frente.append(extremo)
        return min((n - preco) / atr for n in a_frente) if a_frente else None
    if direction == "PUT":
        niveis = list(zona.get("visible_supports") or [])
        extremo = min(niveis) if niveis else None
        a_frente = [n for n in niveis if n <= preco + SR_VISIBLE_BROKEN_ATR * atr]
        if extremo is not None:
            a_frente.append(extremo)
        return min((preco - n) / atr for n in a_frente) if a_frente else None
    return None


def build_zone(candles: list[dict[str, float]]) -> dict[str, Any]:
    """Região de S/R por níveis reais, mais os níveis visíveis do gráfico.

    Ver ``_build_zone_levels`` para a região e ``visible_levels`` para os
    topos/fundos de 1 toque e a máxima/mínima recente.
    """
    zona = _build_zone_levels(candles)
    zona.update(visible_levels(candles))
    return zona


def _build_zone_levels(candles: list[dict[str, float]]) -> dict[str, Any]:
    """Monta a região de suporte e resistência a partir de níveis reais.

    Pivôs de raio 2 sobre as últimas ``SR_RESPECT_LOOKBACK`` velas fechadas,
    agrupados em níveis por ``SR_RESPECT_TOLERANCE_ATR`` e filtrados por
    ``SR_RESPECT_MIN_TOUCHES``. O suporte é o nível mais forte abaixo do preço,
    a resistência o mais forte acima; empate de toques desempata pelo mais
    próximo, que é o que o preço encontra primeiro.

    A vela em formação não entra na montagem dos níveis — ela é o preço que se
    testa contra eles.

    Args:
        candles: Velas normalizadas em ordem cronológica. Aceita ``max``/``min``
            (formato da corretora) e ``high``/``low`` (formato dos datasets).

    Returns:
        ``{"support", "resistance", "tolerance", "near_support",
        "near_resistance", "support_touches", "resistance_touches", "source"}``.
        ``source`` é ``"PIVOT"`` quando saiu de níveis com toques e ``"WINDOW"``
        quando caiu no extremo da janela.
    """
    if not SR_RESPECT_ENABLED or len(candles) < SR_RESPECT_MIN_CANDLES:
        return _window_zone(candles)

    fechadas = candles[:-1][-SR_RESPECT_LOOKBACK:]
    ultima = candles[-1]
    atr = average_true_range(fechadas, periodo=14)
    tolerancia = atr * SR_RESPECT_TOLERANCE_ATR
    if tolerancia <= 0:
        return _window_zone(candles)

    topos, fundos = find_pivots(fechadas, radius=SR_RESPECT_PIVOT_RADIUS)
    resistencias = cluster_levels(
        topos, tolerancia=tolerancia, min_touches=SR_RESPECT_MIN_TOUCHES
    )
    suportes = cluster_levels(
        fundos, tolerancia=tolerancia, min_touches=SR_RESPECT_MIN_TOUCHES
    )
    if not resistencias and not suportes:
        return _window_zone(candles)

    fechamento = float(ultima["close"])
    baixa = candle_low(ultima)
    alta = candle_high(ultima)

    abaixo = [n for n in suportes if n["price"] <= fechamento + tolerancia]
    acima = [n for n in resistencias if n["price"] >= fechamento - tolerancia]
    suporte = max(abaixo, key=lambda n: (n["touches"], n["price"])) if abaixo else None
    resistencia = min(acima, key=lambda n: (-n["touches"], n["price"])) if acima else None

    zona: dict[str, Any] = {
        "support": suporte["price"] if suporte else None,
        "resistance": resistencia["price"] if resistencia else None,
        "tolerance": tolerancia,
        "support_touches": int(suporte["touches"]) if suporte else 0,
        "resistance_touches": int(resistencia["touches"]) if resistencia else 0,
        "near_support": False,
        "near_resistance": False,
        "source": "PIVOT",
    }
    if suporte is not None:
        distancia = min(abs(baixa - suporte["price"]), abs(fechamento - suporte["price"]))
        zona["support_distance"] = distancia
        zona["near_support"] = distancia <= tolerancia
    if resistencia is not None:
        distancia = min(abs(alta - resistencia["price"]), abs(fechamento - resistencia["price"]))
        zona["resistance_distance"] = distancia
        zona["near_resistance"] = distancia <= tolerancia
    return zona


def rejection_confirmed(direction: str, candle: dict[str, float]) -> bool:
    """Indica se a vela é uma rejeição do nível na direção pedida.

    CALL pede fechamento em alta com pavio inferior — o preço furou o suporte e
    voltou. PUT pede o espelho.

    Args:
        direction: ``"CALL"`` ou ``"PUT"``.
        candle: Vela em formação.

    Returns:
        ``True`` quando corpo e pavios confirmam a rejeição.
    """
    forma = candle_shape(candle)
    if forma["range"] <= 0 or forma["body_ratio"] < SR_RESPECT_MIN_BODY_RATIO:
        return False
    abertura = float(candle["open"])
    fechamento = float(candle["close"])
    if direction == "CALL":
        return (
            fechamento > abertura
            and forma["lower_wick_ratio"] >= SR_RESPECT_MIN_WICK_RATIO
            and forma["upper_wick_ratio"] <= SR_RESPECT_MAX_OPPOSITE_WICK
        )
    if direction == "PUT":
        return (
            fechamento < abertura
            and forma["upper_wick_ratio"] >= SR_RESPECT_MIN_WICK_RATIO
            and forma["lower_wick_ratio"] <= SR_RESPECT_MAX_OPPOSITE_WICK
        )
    return False


def _has_room(direction: str, zona: dict[str, Any], candle: dict[str, float]) -> bool:
    """Indica se há espaço até o nível oposto para a entrada valer a pena."""
    tolerancia = float(zona.get("tolerance") or 0.0)
    if tolerancia <= 0:
        return True
    minimo = tolerancia / SR_RESPECT_TOLERANCE_ATR * SR_RESPECT_MIN_ROOM_ATR
    fechamento = float(candle["close"])
    if direction == "CALL":
        alvo = zona.get("resistance")
        return alvo is None or (float(alvo) - fechamento) >= minimo
    alvo = zona.get("support")
    return alvo is None or (fechamento - float(alvo)) >= minimo


def evaluate_respect(
    direction: str,
    zona: dict[str, Any],
    candle: dict[str, float],
) -> tuple[bool, str]:
    """Decide se a entrada respeita a região de suporte e resistência.

    Args:
        direction: ``"CALL"`` ou ``"PUT"``.
        zona: Saída de ``build_zone``.
        candle: Vela em formação.

    Returns:
        Par ``(respeita, motivo)``. O motivo é uma das constantes ``RESPECT_*``
        e vai para o log e para a narração.
    """
    if direction not in {"CALL", "PUT"}:
        return False, RESPECT_CONFLICT

    no_suporte = bool(zona.get("near_support"))
    na_resistencia = bool(zona.get("near_resistance"))

    # Contra o nível: recusado sempre, com ou sem rejeição. Comprar colado na
    # resistência é exatamente o que o dono viu acontecer e mandou parar.
    contra = (direction == "CALL" and na_resistencia) or (direction == "PUT" and no_suporte)
    if contra:
        return False, RESPECT_CONFLICT

    # Nível visível logo à frente: vale dentro e fora da região. Rejeição no
    # suporte com um topo a 0,3 ATR acima também não tem para onde ir.
    distancia = visible_level_ahead(direction, zona, candle)
    if distancia is not None and distancia < SR_VISIBLE_MIN_ROOM_ATR:
        return False, RESPECT_VISIBLE_AHEAD

    if not no_suporte and not na_resistencia:
        return True, RESPECT_OK_CLEAR

    # Daqui para baixo o preço está na região, e a favor dela.
    if not rejection_confirmed(direction, candle):
        return False, RESPECT_NO_REJECTION
    if not _has_room(direction, zona, candle):
        return False, RESPECT_NO_ROOM
    return True, RESPECT_OK_REJECTION
