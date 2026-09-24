import logging
import math
import os
from datetime import datetime, timezone
from typing import Any, Literal


SignalValue = Literal["CALL", "PUT", "WAIT"]
TrendValue = Literal["UP", "DOWN", "SIDEWAYS"]
StrategyMode = Literal["aggressive", "balanced", "conservative"]
OperationTimeframe = Literal["M1", "M5", "M15"]

logger = logging.getLogger("backend-gateway")

from backend.reversion_strategy import (  # noqa: E402
    REVZ_ENABLED,
    REVZ_NOMINATE_THRESHOLD,
    REVZ_THRESHOLD,
    STRATEGY_REVZ,
    is_otc_symbol,
    revz_confidence,
    revz_evaluate,
)
from backend.vertex_strategy import (  # noqa: E402
    VERTEX_ENABLED,
    VERTEX_PARALLEL,
    vertex_confidence,
    vertex_evaluate,
)
from backend.sr_respect import (  # noqa: E402
    RESPECT_CONFLICT,
    RESPECT_SEM_DIRECAO,
    RESPECT_VISIBLE_AHEAD,
    build_zone,
    evaluate_respect,
)

# O que REV-Z e Vertex recusam: entrada contra o nível e nível visível colado à
# frente (topo/fundo de 1 toque, máxima/mínima recente). A região a favor sem
# rejeição continua liberada para elas — o extremo do indicador é a tese.
NIVEL_A_FRENTE = frozenset({RESPECT_CONFLICT, RESPECT_VISIBLE_AHEAD})

from backend.wick_filter import WICK_FILTER_ENABLED, evaluate_wicks  # noqa: E402
from backend.sr_level_trade import (  # noqa: E402
    SR_LEVEL_TRADE_ENABLED,
    STRATEGY_SR_LEVEL,
    find_level_trade,
    level_text,
    level_wick_ok,
)
from backend.support_resistance_strategy import (  # noqa: E402
    SR_ENABLED,
    SR_MIN_CANDLES,
    sr_confidence,
    sr_evaluate,
)
from backend.live_demo_mode import apply_live_demo  # noqa: E402
from backend.open_rsi_strategy import (  # noqa: E402
    STRATEGY_RSI_OPEN,
    is_rsi_open_active,
    rsi_open_candle_timeframe,
    rsi_open_confidence,
    rsi_open_evaluate,
    rsi_open_text,
)
from backend.narrativa_analise import monta_narrativa  # noqa: E402
from backend.named_strategies import (  # noqa: E402
    STRATEGY_LABELS,
    STRATEGY_RETRACEMENT_SR,
    detect_named_strategies,
    pick_primary_strategy,
)

STRATEGY_PRICE_ACTION = "Price Action"
STRATEGY_CANDLE_PSYCHOLOGY = "Psicologia de velas"
STRATEGY_CANDLE_PATTERNS = "Padrões de vela"
ACTIVE_ENTRY_STRATEGIES = (
    STRATEGY_PRICE_ACTION,
    STRATEGY_CANDLE_PSYCHOLOGY,
    STRATEGY_CANDLE_PATTERNS,
)

# Duração da vela/ordem (minutos). A IA analisa continuamente a cada vela;
# este valor NÃO é mais um cooldown multiplicado (legado 5/15/45).
CYCLE_MINUTES_BY_TIMEFRAME: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
}
TIMEFRAME_SECONDS_BY_NAME: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
}
# Janela em que a análise técnica varre o mercado dentro de cada vela.
ANALYSIS_WINDOW_BOUNDS: tuple[int, int] = (5, 20)
# Backoff curto quando candles/payout/sessão falham (não espera ciclo legado).
OPERATIONAL_RETRY_SECONDS = 30
MIN_OPERATIONS_PER_HOUR_BY_TIMEFRAME: dict[str, int] = {
    "M1": 12,
    "M5": 6,
    "M15": 2,
}
DIRECTION_SCORE_EDGE_MIN = 8

# Política de suporte/resistência (2026-07-29): com o preço colado em suporte
# ou resistência o robô não entra, em nenhuma direção. Backtest walk-forward
# com candles reais (8 ativos OTC, 1000 velas M1 cada) mostrou que as entradas
# nessa região aprovadas pelo portão de confiança do usuário deram 0% de
# acerto, e removê-las elevou o resultado do período de +3,24 para +5,24
# (stake unitário). Ver `docs/ESTRATEGIA.md` §1.
# Desligar (`False`) volta ao comportamento clássico do backup, em que a
# região de nível era permitida com rejeição confirmada (`LEVEL_REJECTION`).
SR_ZONE_HARD_BLOCK = True

# Bloqueios anti-loss (2026-08-04): padrões tóxicos medidos no histórico real
# (WR bem abaixo do empate ~53% com payout 85–88%). Ver `docs/ESTRATEGIA.md`.
LAST_3_ALIGNMENT_HARD_BLOCK = True
CONTINUATION_DEAD_RSI_HARD_BLOCK = True
WEAK_CONTINUATION_PUT_HARD_BLOCK = True
# Cortes de 15/08 (WEAK PUT, vela 45%, DOJI, PUT chase, CALL chase, REPEAT)
# desligados em 16/08: no sábado/domingo o WR caiu (32–45%) vs ~50–51% da
# substitution 13–14/08. Flags False = estratégia anterior (backup 14/08 20:39).
WEAK_PUT_HARD_BLOCK = False
CANDLE_WEAK_HARD_BLOCK = False
CANDLE_MIN_BODY_RATIO = 0.45
DOJI_HARD_BLOCK = False
DOJI_MAX_BODY_RATIO = 0.10
# Staging 22/08 (sistema 02): PUT corpo <45% (auditoria 16–22/08, WR 26,1%).
# Limiar era 0.60 nos cortes 15/08 (off); aqui só o pedaço comprovadamente tóxico.
# Confirmado em 2026-09-03: PUT com corpo <45% deu 47,68% no treino e 41,35%
# no teste (n=711/133). Amostra de teste pequena, mas o efeito é grande. Fica.
PUT_BODY_HARD_BLOCK = True
PUT_MIN_BODY_RATIO = 0.45
PUT_CHASE_HARD_BLOCK = False
PUT_WICK_HARD_BLOCK = False
PUT_MAX_LOWER_WICK_RATIO = 0.15
CALL_CHASE_HARD_BLOCK = False
# Staging 22/08: CALL + GREEN-RED-GREEN (WR 40,1%, maior P&L negativo do recorte).
# Diferente de CALL_CHASE (GGG em CONTINUATION), que permanece off.
# Confirmado em 2026-09-03: 45,21% no treino e 43,46% no teste (n=626/451).
# Replica nas duas metades — fica.
CALL_GRG_HARD_BLOCK = True
REPEAT_ENTRY_HARD_BLOCK = False
TREND_CLEAR_HARD_BLOCK = True
# Staging 22/08: horas ruins BRT — WR 35–39% no recorte 16–22/08.
# DESLIGADO na reauditoria de 2026-09-03: contra as 10.730 ops M1 limpas, as
# horas {2,3,17,19} BRT deram 46,33% no treino e **50,40% no teste** — as piores
# de um período são as melhores do seguinte, que é a assinatura de ruído
# memorizado. Blacklist de horário já tinha sido reprovada uma vez em 30/08.
TOXIC_HOUR_HARD_BLOCK = False
TOXIC_HOURS_BRT = frozenset({2, 3, 17, 19})
# Staging 22/08: ban USDCHF (WR ~45% no recorte).
# DESLIGADO em 2026-09-03: 46,36% no treino e **50,00% no teste**. Não replica.
ASSET_BAN_HARD_BLOCK = False
BANNED_ASSETS = frozenset(
    {
        "USDCHF-OTC",
        "USDCHF",
    }
)
# Staging 22/08: WEAK em pares tóxicos (EURGBP CALL ~39%, AUDUSD PUT ~39%).
# Confirmado em 2026-09-03: 44,07% no treino e 47,20% no teste. Fica.
TOXIC_WEAK_PAIR_HARD_BLOCK = True
# Staging 30/08 (sistema 02): autópsia EC02 22–24/08 — GGG WR 31,6% (n=19),
# GRR WR 33,3% (n=12), WEAK 47,2% (n=53). Horas tóxicas S02 permanecem iguais.
# DESLIGADOS em 2026-09-03. Foram calibrados em n=19 (GGG) e n=12 (GRR) — na
# base limpa de 10.730 ops: GGG 50,73%/49,82%, GRR 48,51%/48,84%, WEAK
# 47,99%/49,62%. Nenhum dos três fica abaixo da média nas duas metades, e o
# WEAK_SETUP sozinho custava 65% do volume. O que de fato separa as entradas
# ruins é o recovery estrito (`RECOVERY_STRICT`), medido em +2,2 pp.
SEQ_GGG_HARD_BLOCK = False
SEQ_GRR_HARD_BLOCK = False
WEAK_SETUP_HARD_BLOCK = False
# RSI "meio morto" em CONTINUATION (amostra 7d: WR 28,6%).
CONTINUATION_DEAD_RSI_MIN = 50.0
CONTINUATION_DEAD_RSI_MAX = 60.0
# CONTINUATION+PUT com WR agregado < 43% no caderno global / histórico.
# 2026-08-13: EURGBP/AUDJPY incluídos (WR PUT ~29–35% na auditoria do dia).
WEAK_CONTINUATION_PUT_ASSETS = frozenset(
    {
        "EURUSD-OTC",
        "EURUSD",
        "AUDUSD-OTC",
        "AUDUSD",
        "USDCAD-OTC",
        "USDCAD",
        "USDCHF-OTC",
        "USDCHF",
        "EURGBP-OTC",
        "EURGBP",
        "AUDJPY-OTC",
        "AUDJPY",
    }
)

# Ativos com WR estruturalmente fraco no dia — só demote no ranking (não hard block),
# para o ciclo preferir outro par sem zerar a frequência.
RANK_DEMOTION_ASSETS = frozenset(
    {
        "AUDUSD-OTC",
        "AUDUSD",
    }
)


def is_doji_body(body_ratio: float | None) -> bool:
    """Indica DOJI / corpo miúdo (abertura ≈ fechamento).

    Args:
        body_ratio: Corpo da vela em fração do range (0–1).

    Returns:
        True quando o corpo é ≤ ``DOJI_MAX_BODY_RATIO``.
    """
    try:
        return float(body_ratio or 0) <= DOJI_MAX_BODY_RATIO
    except (TypeError, ValueError):
        return False


def is_weak_candle_body(body_ratio: float | None) -> bool:
    """Indica vela fraca ou sem força (corpo < 45%).

    Args:
        body_ratio: Corpo da vela em fração do range (0–1).

    Returns:
        True quando o corpo é < ``CANDLE_MIN_BODY_RATIO``.
    """
    try:
        return float(body_ratio or 0) < CANDLE_MIN_BODY_RATIO
    except (TypeError, ValueError):
        return False


def is_put_thin_body(body_ratio: float | None, direction: str | None) -> bool:
    """Indica PUT com corpo abaixo do mínimo de venda (staging: 45%).

    Auditoria sistema 01 (16–22/08): PUT com corpo <45% fez WR **26,1%**
    (n=88). CALL com corpo <45% ficou em 54,8% — por isso o corte é só PUT.
    (O corte 15/08 usava 60% e foi desligado; staging reativa só o limiar 45%.)

    Args:
        body_ratio: Corpo da vela em fração do range (0–1).
        direction: ``CALL`` ou ``PUT``.

    Returns:
        True quando a direção é PUT e o corpo é < ``PUT_MIN_BODY_RATIO``.
    """
    if str(direction or "").strip().upper() != "PUT":
        return False
    try:
        return float(body_ratio or 0) < PUT_MIN_BODY_RATIO
    except (TypeError, ValueError):
        return True


def is_call_green_red_green(
    direction: str | None,
    last_3_colors: list[str] | tuple[str, ...] | None,
) -> bool:
    """Indica CALL no padrão GREEN-RED-GREEN (alternância disfarçada de compra).

    Auditoria sistema 01 (16–22/08): 277 ops, WR **40,1%**, P&L ≈ −998.
    Aplica a **qualquer setup** (WEAK e CONTINUATION), não só CONTINUATION.

    Args:
        direction: ``CALL`` ou ``PUT``.
        last_3_colors: Cores das 3 velas, mais antiga primeiro.

    Returns:
        True quando é CALL com sequência GREEN-RED-GREEN.
    """
    if str(direction or "").strip().upper() != "CALL":
        return False
    colors = tuple(str(item).strip().upper() for item in (last_3_colors or []))
    return colors == ("GREEN", "RED", "GREEN")


def is_seq_green_green_green(
    last_3_colors: list[str] | tuple[str, ...] | None,
) -> bool:
    """Indica sequência GREEN-GREEN-GREEN (qualquer setup/direção).

    Autópsia EC02 (22–24/08): 19 ops, WR **31,6%**, P&L −779. Diferente de
    ``CALL_CHASE`` (só CONTINUATION+CALL); aqui bloqueia WEAK também.

    Args:
        last_3_colors: Cores das 3 velas, mais antiga primeiro.

    Returns:
        True quando as 3 últimas cores são GREEN-GREEN-GREEN.
    """
    colors = tuple(str(item).strip().upper() for item in (last_3_colors or []))
    return colors == ("GREEN", "GREEN", "GREEN")


def is_seq_green_red_red(
    last_3_colors: list[str] | tuple[str, ...] | None,
) -> bool:
    """Indica sequência GREEN-RED-RED (qualquer setup/direção).

    Autópsia EC02 (22–24/08): 12 ops, WR **33,3%**, P&L −455.

    Args:
        last_3_colors: Cores das 3 velas, mais antiga primeiro.

    Returns:
        True quando as 3 últimas cores são GREEN-RED-RED.
    """
    colors = tuple(str(item).strip().upper() for item in (last_3_colors or []))
    return colors == ("GREEN", "RED", "RED")


def is_weak_setup(setup: str | None) -> bool:
    """Indica setup de price action WEAK (fallback de frequência).

    Args:
        setup: Price action (``WEAK``, ``CONTINUATION``, …).

    Returns:
        True quando o setup é WEAK.
    """
    return str(setup or "").strip().upper() == "WEAK"


def is_toxic_hour_brt(now: datetime | None = None) -> bool:
    """Indica se o instante cai em hora tóxica de Brasília (02/03/17/19).

    Args:
        now: Instante de referência (UTC ou aware). Padrão: agora UTC.

    Returns:
        True quando a hora civil em ``America/Sao_Paulo`` está em
        ``TOXIC_HOURS_BRT``.
    """
    from backend.brasilia_time import BRASILIA_TZ

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(BRASILIA_TZ).hour in TOXIC_HOURS_BRT


def is_banned_asset(symbol: str | None) -> bool:
    """Indica se o ativo está na lista de ban hard (staging: USDCHF).

    Args:
        symbol: Código do ativo (ex.: ``USDCHF-OTC``).

    Returns:
        True quando o par não deve operar.
    """
    normalized = str(symbol or "").strip().upper()
    return normalized in BANNED_ASSETS


def is_toxic_weak_pair(
    setup: str | None,
    direction: str | None,
    symbol: str | None,
) -> bool:
    """Indica WEAK em par/direção tóxicos (EURGBP CALL, AUDUSD PUT).

    Args:
        setup: Price action (``WEAK``, ``CONTINUATION``, …).
        direction: ``CALL`` ou ``PUT``.
        symbol: Código do ativo.

    Returns:
        True quando o combo setup+direção+par deve ser bloqueado.
    """
    if str(setup or "").strip().upper() != "WEAK":
        return False
    direction_u = str(direction or "").strip().upper()
    base = str(symbol or "").strip().upper().replace("-OTC", "")
    if direction_u == "CALL" and base == "EURGBP":
        return True
    if direction_u == "PUT" and base == "AUDUSD":
        return True
    return False


def is_put_against_wick(lower_wick_ratio: float | None, direction: str | None) -> bool:
    """Indica PUT com pavio de baixo (contra a venda) longo demais.

    Args:
        lower_wick_ratio: Pavio inferior em fração do range (0–1).
        direction: ``CALL`` ou ``PUT``.

    Returns:
        True quando PUT e pavio de baixo > ``PUT_MAX_LOWER_WICK_RATIO``.
    """
    if str(direction or "").strip().upper() != "PUT":
        return False
    try:
        return float(lower_wick_ratio or 0) > PUT_MAX_LOWER_WICK_RATIO
    except (TypeError, ValueError):
        return False


def extract_last_3_colors(
    signal: dict[str, Any] | None,
    candles: list[dict[str, float]] | None = None,
) -> list[str]:
    """Cores das últimas 3 velas (mais antiga → atual).

    Args:
        signal: Sinal ou candidato com ``last_3_colors`` / metrics.
        candles: Velas fechadas (fallback se o sinal não trouxer cores).

    Returns:
        Lista de ``GREEN``/``RED``/``DOJI`` (pode ser vazia).
    """
    if isinstance(signal, dict):
        raw = signal.get("last_3_colors")
        if not raw and isinstance(signal.get("metrics"), dict):
            raw = signal["metrics"].get("last_3_colors")
        if isinstance(raw, (list, tuple)) and raw:
            return [str(item).strip().upper() for item in raw]
    if candles and len(candles) >= 3:
        return _candle_colors(candles[-3:])
    return []


def is_chasing_continuation(
    setup: str | None,
    direction: str | None,
    last_3_colors: list[str] | tuple[str, ...] | None,
) -> bool:
    """CONTINUATION perseguindo 3 velas iguais (CALL verdes ou PUT vermelhas).

    Args:
        setup: Price action.
        direction: ``CALL`` ou ``PUT``.
        last_3_colors: Cores das 3 velas, mais antiga primeiro.

    Returns:
        True quando a continuação está atrasada no movimento.
    """
    return is_chasing_continuation_put(
        setup, direction, last_3_colors
    ) or is_chasing_continuation_call(setup, direction, last_3_colors)


def is_chasing_continuation_call(
    setup: str | None,
    direction: str | None,
    last_3_colors: list[str] | tuple[str, ...] | None,
) -> bool:
    """CONTINUATION CALL nas 3 verdes seguidas (perseguir a alta).

    Auditoria 15/08 pós-deploy: 43 ops, WR ~30%. Espelho do PUT chase.

    Args:
        setup: Price action.
        direction: ``CALL`` ou ``PUT``.
        last_3_colors: Cores das 3 velas, mais antiga primeiro.

    Returns:
        True quando é compra de continuação em GREEN-GREEN-GREEN.
    """
    if str(setup or "").strip().upper() != "CONTINUATION":
        return False
    if str(direction or "").strip().upper() != "CALL":
        return False
    colors = tuple(str(item).strip().upper() for item in (last_3_colors or []))
    return colors == ("GREEN", "GREEN", "GREEN")


def is_chasing_continuation_put(
    setup: str | None,
    direction: str | None,
    last_3_colors: list[str] | tuple[str, ...] | None,
) -> bool:
    """CONTINUATION PUT nas 3 vermelhas seguidas (perseguir a queda).

    No recorte não-WEAK esse padrão fez 45,8% (abaixo do empate). GREEN-RED-RED
    fez 70,6% e permanece permitido.

    Args:
        setup: Price action.
        direction: ``CALL`` ou ``PUT``.
        last_3_colors: Cores das 3 velas, mais antiga primeiro.

    Returns:
        True quando é venda de continuação em RED-RED-RED.
    """
    if str(setup or "").strip().upper() != "CONTINUATION":
        return False
    if str(direction or "").strip().upper() != "PUT":
        return False
    colors = tuple(str(item).strip().upper() for item in (last_3_colors or []))
    return colors == ("RED", "RED", "RED")


def is_weak_put_setup(setup: str | None, direction: str | None) -> bool:
    """Indica se o contexto é WEAK + PUT (venda em setup fraco).

    Args:
        setup: Price action (ex.: ``WEAK``, ``CONTINUATION``).
        direction: ``CALL`` ou ``PUT``.

    Returns:
        True quando o setup é WEAK e a direção é PUT.
    """
    return str(setup or "").strip().upper() == "WEAK" and str(direction or "").strip().upper() == "PUT"


def is_weak_continuation_put_asset(symbol: str | None) -> bool:
    """
    Indica se o ativo está na lista tóxica de CONTINUATION+PUT.

    Args:
        symbol: Código do ativo (ex.: ``EURUSD-OTC``).

    Returns:
        True quando CONTINUATION PUT deve ser bloqueado nesse par.
    """
    normalized = str(symbol or "").strip().upper()
    return normalized in WEAK_CONTINUATION_PUT_ASSETS


def is_rank_demotion_asset(symbol: str | None) -> bool:
    """
    Indica se o ativo deve perder prioridade no ranking (sem hard block).

    Args:
        symbol: Código do ativo (ex.: ``AUDUSD-OTC``).

    Returns:
        True quando outro candidato estruturado deve ganhar o slot.
    """
    normalized = str(symbol or "").strip().upper()
    return normalized in RANK_DEMOTION_ASSETS


def cycle_minutes_for_timeframe(timeframe: str | None) -> int:
    """Retorna a duração da vela/ordem do timeframe (1 / 5 / 15 minutos)."""
    normalized = str(timeframe or "M1").strip().upper()
    return CYCLE_MINUTES_BY_TIMEFRAME.get(normalized, 1)


def timeframe_seconds(timeframe: str | None) -> int:
    """Retorna a duração da vela em segundos para o timeframe informado."""
    normalized = str(timeframe or "M1").strip().upper()
    return TIMEFRAME_SECONDS_BY_NAME.get(normalized, 60)


def seconds_until_next_analysis(
    timeframe: str | None,
    server_timestamp: float | None = None,
    *,
    force_next_candle: bool = False,
    operational_backoff: bool = False,
) -> int:
    """
    Calcula quantos segundos faltam para a próxima varredura contínua.

    A IA monitora o mercado a cada vela do timeframe da operação (M1/M5/M15),
    na janela de análise (segundos 5–20). Após um scan sem oportunidade,
    agenda a próxima vela (`force_next_candle=True`) para não martelar a API.
    Falhas operacionais usam backoff curto (`OPERATIONAL_RETRY_SECONDS`).

    Args:
        timeframe: Timeframe da operação (M1, M5 ou M15).
        server_timestamp: Relógio de referência (Unix). Se omitido, usa UTC local.
        force_next_candle: Se True, ignora a janela atual e vai para a próxima vela.
        operational_backoff: Se True, retorna o backoff curto de falha operacional.

    Returns:
        Segundos (>= 1) até a próxima análise permitida.
    """
    if operational_backoff:
        return int(OPERATIONAL_RETRY_SECONDS)

    expiration = timeframe_seconds(timeframe)
    start, end = ANALYSIS_WINDOW_BOUNDS
    if server_timestamp is None:
        server_timestamp = datetime.now(timezone.utc).timestamp()
    seconds_in_candle = float(server_timestamp) % expiration

    if force_next_candle or (start <= seconds_in_candle <= end):
        return max(1, int(math.ceil(expiration - seconds_in_candle + start)))
    if seconds_in_candle < start:
        return max(1, int(math.ceil(start - seconds_in_candle)))
    return max(1, int(math.ceil(expiration - seconds_in_candle + start)))


def minimum_operations_per_hour(timeframe: str | None) -> int:
    """Retorna a meta operacional horária do timeframe selecionado."""
    normalized = str(timeframe or "M1").strip().upper()
    return MIN_OPERATIONS_PER_HOUR_BY_TIMEFRAME.get(normalized, 12)


# Limiares da estratégia clássica (backup-classic) no score bruto aditivo.
# Após N ciclos sem entrada, libera filtros de "seca" (não os anti-loss tóxicos).
FREQUENCY_RECOVERY_AFTER_CYCLES = 2
FREQUENCY_RECOVERY_MIN_SCORE = 70
# Reauditoria 2026-09-03 (11.628 ops reais, contas de marketing excluídas): o
# recovery de frequência estava ligado em 95% das varreduras e, ao dispensar os
# hard blocks abaixo, respondia por **64,4% de todas as ordens executadas**.
# Essas ordens acertam 48,38% contra 50,60% das demais, e a diferença é o único
# efeito de todo o levantamento que sobreviveu ao holdout (50,74% no treino /
# 50,38% no teste). Com o recovery estrito o retorno por entrada sai de −9,65%
# para −0,85%; o custo é 64% menos volume (mediana 8 -> 3 ops por conta-dia).
#
# `RECOVERY_STRICT=false` restaura o comportamento antigo para comparação A/B.
# Estratégias nomeadas do sistema 01 (`named_strategies.py`), fora do pipeline
# desde 26/07/2026. Reativadas no sistema 02 em 2026-09-04 como **fontes de
# sinal adicionais**, não como substitutas do motor clássico: cada uma pode
# liberar uma entrada que o portão clássico barraria, e o `strategy_key` vai
# para o histórico para o resultado ser atribuível estratégia por estratégia.
#
# É a alavanca de VOLUME. Medido nas 281.910 velas OTC de 03/09, por ativo-dia:
#   SR-R (pivôs)          131,6 sinais   acerto 50,23% ± 0,61
#   RETRACEMENT_SR        74,7 sinais    acerto 49,89% ± 0,80
#   CANDLE_FLOW           59,1 sinais    acerto 49,54% ± 0,90
#   EXHAUSTION_REVERSAL   40,9 sinais    acerto 50,41% ± 1,09
# Sinal sobra; nenhuma delas bate o empate de 53,48% no OTC. Ligar isto compra
# frequência, não vantagem — a atribuição por `strategy_key` existe justamente
# para que algumas semanas de dados digam se alguma se sustenta.
NAMED_STRATEGIES_ENABLED = os.getenv("NAMED_STRATEGIES", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}

# Cortes que NENHUMA estratégia nomeada dispensa: são os três que replicaram nas
# duas metades do histórico limpo (reauditoria de 03/09).
NAMED_STRATEGY_NON_WAIVABLE = frozenset(
    {
        "CALL_GRG",
        "PUT_BODY",
        "TOXIC_WEAK_PAIR",
        "ACCOUNT_DISCONNECTED",
        "STOP_WIN_HIT",
        "STOP_LOSS_HIT",
        "ACTIVE_CLOSED",
        "ACTIVE_SUSPENDED",
        "PAYOUT_UNAVAILABLE",
        "OPERATION_IN_PROGRESS",
        "CANDLES_UNAVAILABLE",
        "MIN_PAYOUT",
        # Pavio excessivo (11/09): regra do dono, vale para toda estratégia.
        "WICK_EXCESS",
    }
)

# Chave da SR-R quando ela entra como estratégia nomeada (e não como override).
STRATEGY_SUPPORT_RESISTANCE_PIVOT = "SUPPORT_RESISTANCE_PIVOT"
# Chave da Vertex no histórico. Marcada de propósito: estratégia nova sem
# medição não pode se misturar com o resto na apuração de acerto.
STRATEGY_VERTEX = "VERTEX"

RECOVERY_STRICT = os.getenv("RECOVERY_STRICT", "true").strip().lower() in {"1", "true", "yes"}

# Bloqueios que o recovery de frequência dispensa. Em modo estrito ele só
# dispensa `TREND_CLEAR` — os dois que definem a qualidade da entrada
# (`PRICE_ACTION_SETUP` e `LEVEL_REJECTION`) continuam valendo.
FREQUENCY_RECOVERY_SOFT_BLOCKS = frozenset(
    {"TREND_CLEAR"}
    if RECOVERY_STRICT
    else {
        "TREND_CLEAR",
        "PRICE_ACTION_SETUP",
        "LEVEL_REJECTION",
    }
)
# Soft block libera trade_allowed, mas estes NÃO perdem a penalidade no score —
# senão WEAK compete em igualdade com CONTINUATION e ganha o slot (auditoria
# 2026-08-13: 64/100 losses eram WEAK liberados no recovery).
FREQUENCY_RECOVERY_KEEP_SCORE_PENALTIES = frozenset(
    {
        "PRICE_ACTION_SETUP",
    }
)

STRATEGY_PROFILES = {
    "aggressive": {"confidence": 70, "payout": 70, "strength": 8, "body_ratio": 0.25},
    "balanced": {"confidence": 80, "payout": 80, "strength": 12, "body_ratio": 0.40},
    # body 0.40 (era 0.55): M1 OTC raramente entrega corpo 55%+; zerava allowed=0.
    "conservative": {"confidence": 85, "payout": 85, "strength": 12, "body_ratio": 0.40},
}

# Gráficos sempre avaliados pela IA técnica, independentemente do timeframe da operação.
ANALYSIS_TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15")
MIN_MTF_CONFLUENCE = 2
MTF_VOTE_CONFIDENCE_MIN = 78
CALIBRATED_CONFIDENCE_FLOOR = 50
CALIBRATED_CONFIDENCE_CAP = 92
CONFIDENCE_MODEL_VERSION = "backup-classic"


def analyze_signal(
    symbol: str,
    candles: list[dict[str, Any]],
    timeframe: str = "M1",
    *,
    strategy_mode: StrategyMode = "conservative",
    payout: float | None = None,
    frequency_recovery: bool = False,
    m1_candles: list[dict[str, Any]] | None = None,
    live_demo: bool = False,
) -> dict[str, Any]:
    """Analisa velas e aplica o portão de qualidade.

    Args:
        symbol: Ativo.
        candles: Velas OHLCV.
        timeframe: Timeframe da operação.
        strategy_mode: Perfil aggressive/balanced/conservative.
        payout: Payout do ativo, se conhecido.
        frequency_recovery: Se True, suaviza filtros de seca (não anti-loss).
        m1_candles: Velas M1 do mesmo ativo, quando a operação é M5/M15. A
            REV-Z precisa delas: medido em 05/09, o z calculado nas velas do
            próprio timeframe dá 49,59% em M5 e 41,67% em M15, contra 60,56%
            em M1 — o efeito é de curto prazo e some numa janela de 10h/30h.
            A EXPIRAÇÃO maior continua valendo a pena (M5 mede 67,31%); é só o
            SINAL que tem de vir do M1.
        live_demo: Modo de transmissão ao vivo. Afrouxa o portão em OTC para dar
            cadência à live. Piora o resultado (mais volume em série aleatória);
            existe só para ritmo de demonstração. Ver `live_demo_mode.py`.
    """
    normalized = [_normalize_candle(candle) for candle in candles]
    normalized = [candle for candle in normalized if candle is not None]
    last_price = normalized[-1]["close"] if normalized else None
    logger.info(
        "[CANDLE_ANALYSIS] symbol=%s timeframe=%s candles=%s",
        symbol,
        timeframe,
        len(normalized),
    )

    if len(normalized) < 30:
        signal = _build_signal(
            symbol=symbol,
            signal="WAIT",
            confidence=0,
            reason="Menos de 30 candles disponiveis.",
            last_price=last_price,
            trend="SIDEWAYS",
            strength=0,
            timeframe=timeframe,
        )
        signal["insufficient_candles"] = True
        signal["raw_direction_score"] = 0
        signal["confidence_model_version"] = CONFIDENCE_MODEL_VERSION
        _attach_empty_candle_analysis(signal, symbol, timeframe, normalized)
        return _apply_quality_filters(
            signal, normalized, strategy_mode, payout, frequency_recovery=frequency_recovery
        )

    closes = [candle["close"] for candle in normalized]
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    rsi14 = _rsi(closes, 14)
    trend, trend_strength = _trend(ema9[-1], ema21[-1], closes[-1])
    last_candle = normalized[-1]
    avg_range = _average_range(normalized[-20:])
    atr_pct = (avg_range / abs(last_price)) if last_price else 0.0
    extreme_candle = _is_extreme_candle(last_candle, avg_range)

    call_score, call_reasons = _score_direction("CALL", normalized, ema9[-1], ema21[-1], rsi14)
    put_score, put_reasons = _score_direction("PUT", normalized, ema9[-1], ema21[-1], rsi14)

    if call_score >= put_score:
        selected_signal: SignalValue = "CALL"
        confidence = call_score
        reasons = call_reasons
    else:
        selected_signal = "PUT"
        confidence = put_score
        reasons = put_reasons

    if extreme_candle:
        reasons.append("Último candle exagerado reduz o score.")

    signal = _build_signal(
        symbol=symbol,
        signal=selected_signal,
        confidence=confidence,
        reason=" ".join(reasons),
        last_price=last_price,
        trend=trend,
        strength=trend_strength,
        timeframe=timeframe,
    )
    _attach_indicators(signal, normalized, ema9[-1], ema21[-1], rsi14, avg_range, atr_pct)
    _attach_candle_analysis(signal, symbol, timeframe, normalized, ema9[-1], ema21[-1], rsi14, avg_range, atr_pct)
    signal["extreme_candle"] = extreme_candle
    signal["raw_direction_score"] = max(0, int(round(confidence)))
    signal["confidence_model_version"] = CONFIDENCE_MODEL_VERSION
    resultado = _apply_quality_filters(
        signal, normalized, strategy_mode, payout, frequency_recovery=frequency_recovery
    )
    resultado = apply_named_strategies(resultado, symbol, normalized, timeframe)
    velas_z = normalized
    velas_m1: list[dict[str, float]] = []
    if m1_candles:
        convertidas = [_normalize_candle(c) for c in m1_candles]
        convertidas = [c for c in convertidas if c is not None]
        if convertidas:
            velas_z = convertidas
            velas_m1 = convertidas
    # A REV-Z só vale com o z das velas M1. Em M5/M15 sem as M1, cair para as
    # velas do próprio timeframe mediria outra coisa (49,59% em M5 e 41,67% em
    # M15, medido em 05/09) — melhor não operar a vela.
    velas_revz = normalized if str(timeframe).upper() == "M1" else velas_m1
    if is_rsi_open_active():
        # RSI (24/09): M1 e M5 leem as velas M1; M15 lê a própria vela M15 —
        # foi o que o backtest mediu melhor em cada timeframe.
        velas_tf = rsi_open_candle_timeframe(timeframe)
        velas_revz = normalized if velas_tf == str(timeframe).upper() else velas_m1
    resultado = apply_revz_override(resultado, symbol, velas_revz, timeframe=timeframe)
    resultado = apply_sr_override(resultado, symbol, normalized)
    resultado = apply_vertex_override(resultado, symbol, velas_z)
    # Depois de todas as estratégias: com o preço perto de um nível, quem manda
    # é o nível (regra do dono, 11/09). Ver `apply_level_trade`.
    resultado = apply_level_trade(resultado, symbol, normalized)
    # Por último de propósito: só age quando TODO o resto barrou.
    return apply_live_demo(resultado, symbol, live_enabled=live_demo)


def apply_level_trade(
    signal: dict[str, Any],
    symbol: str,
    normalized: list[dict[str, float]],
) -> dict[str, Any]:
    """Entrada a favor do nível: resistência vira venda, suporte vira compra.

    Pedido do dono em 11/09/2026. O suporte e a resistência passaram a ter dois
    papéis: filtro (nenhuma estratégia entra contra o nível, em `sr_respect`) e
    SINAL. Com o preço perto de um nível a direção é a do nível, mesmo que o
    motor clássico (ou a Vertex) estivesse apontando para o outro lado — era
    exatamente esse caso que o filtro cancelava no disparo, 66 vezes em 5h.

    Roda por último entre as estratégias, e não toca no mercado aberto: lá a
    decisão é da REV-Z (mesma regra do `apply_vertex_override`).

    O pavio aqui é direcional (`level_wick_ok`): o pavio de rejeição a favor da
    entrada é confirmação; pavio contra, ou vela indecisa com pavio grande dos
    dois lados, barra a entrada — e, como o nível manda na vela, barra a vela
    inteira, sem devolver a palavra ao clássico (ele entraria contra o nível).

    Args:
        signal: Sinal já montado pelo clássico (e pelas outras estratégias).
        symbol: Ativo analisado.
        normalized: Velas normalizadas; a última está em formação.

    Returns:
        O sinal, decidido pelo nível quando há nível perto.
    """
    if not SR_LEVEL_TRADE_ENABLED or not normalized:
        return signal
    if isinstance(signal.get("revz"), dict):
        return signal
    veredito = find_level_trade(normalized)
    signal["sr_level"] = veredito
    if not veredito:
        return signal

    direcao = veredito["direction"]
    bloqueados = [f for f in (signal.get("blocked_filters") or []) if not str(f).startswith("SR_NIVEL")]
    sem_pavio, motivo_pavio = level_wick_ok(normalized, direcao)
    signal["wick_reason"] = motivo_pavio
    signal["strategy_name"] = "S/R entrada a favor do nível"
    signal["strategy_key"] = STRATEGY_SR_LEVEL
    signal["confidence_model_version"] = "sr-nivel-v1"
    if not sem_pavio:
        signal["signal"] = "WAIT"
        signal["direction"] = "WAIT"
        signal["trade_allowed"] = False
        signal["confidence"] = 0
        signal["score"] = 0
        signal["strategy_score"] = 0
        bloqueados = [f for f in bloqueados if f != "WICK_EXCESS"] + ["WICK_EXCESS"]
        signal["blocked_filters"] = bloqueados
        signal["block_reasons"] = list(bloqueados)
        signal["quality_reason"] = motivo_pavio
        return signal

    conf = int(veredito["confidence"])
    signal["signal"] = direcao
    signal["direction"] = direcao
    signal["analyzed_direction"] = direcao
    signal["trade_allowed"] = True
    signal["confidence"] = conf
    signal["score"] = conf
    signal["strategy_score"] = conf
    signal["blocked_filters"] = []
    signal["block_reasons"] = []
    signal["quality_reason"] = "OK_SR_NIVEL"
    texto = level_text(symbol, veredito)
    for campo in ("reason", "entry_reason", "signal_explanation", "narrator_text", "analysis_detail"):
        signal[campo] = texto
    logger.info(
        "[SR_LEVEL_SIGNAL] symbol=%s direction=%s lado=%s nivel=%.5f toques=%s fonte=%s dist_atr=%s conf=%s",
        symbol,
        direcao,
        veredito["side"],
        veredito["level"],
        veredito["touches"],
        veredito["source"],
        veredito["distance_atr"],
        conf,
    )
    return signal

def apply_named_strategies(
    signal: dict[str, Any],
    symbol: str,
    normalized: list[dict[str, float]],
    timeframe: str,
) -> dict[str, Any]:
    """Roda as estratégias nomeadas como fontes de sinal adicionais.

    O motor clássico decide primeiro. Aqui, se alguma estratégia nomeada
    reconhece o setup, a entrada é liberada mesmo que o portão clássico a
    tenha barrado — exceto pelos cortes de ``NAMED_STRATEGY_NON_WAIVABLE``,
    que continuam valendo para todas.

    Inclui a SR-R (``support_resistance_strategy``) como estratégia nomeada
    ``SUPPORT_RESISTANCE_PIVOT``, ao lado da ``RETRACEMENT_SR`` que já existia
    no sistema 01. São duas leituras diferentes de suporte e resistência: a
    primeira monta o nível por pivôs e contagem de toques, a segunda exige
    retração recente e rejeição colada no nível.

    Com ``NAMED_STRATEGIES_ENABLED`` desligado (padrão) só anota os matches no
    sinal, sem mudar nenhuma decisão — assim dá para medir a frequência antes
    de ligar.

    Args:
        signal: Sinal já filtrado pelo motor clássico.
        symbol: Ativo analisado.
        normalized: Velas normalizadas em ordem cronológica.
        timeframe: Timeframe da operação.

    Returns:
        O sinal com ``strategy_key``, ``named_strategies`` e, quando ligado e
        houver match, ``trade_allowed`` liberado.
    """
    direction = str(signal.get("signal") or signal.get("direction") or "").upper()
    if len(normalized) < 6:
        return signal

    # A SR-R é reversão e o motor clássico segue a última vela em ~89% das
    # ordens: por construção os dois quase nunca apontam para o mesmo lado.
    # Por isso ela entra como fonte independente — só quando o clássico não
    # tem direção — e não como confirmação da direção dele.
    # Sem `allow_otc`: quem decide é `SR_ALLOW_OTC`. Forçar True aqui fazia a
    # SR-R liberar entradas em ativo sintético mesmo com a variável desligada,
    # que é o padrão justamente porque ela mediu 49,85% no holdout OTC.
    veredito_pivo = sr_evaluate(symbol, normalized[-(SR_MIN_CANDLES + 5):])
    # O motor clássico SEMPRE devolve uma direção (escolhe o maior entre
    # call_score e put_score); quem barra é o portão. Então a SR-R entra
    # quando o clássico foi BARRADO — aí a direção passa a ser a dela.
    classico_barrado = not signal.get("trade_allowed")
    if classico_barrado and veredito_pivo["direction"]:
        direction = str(veredito_pivo["direction"])
        signal["sr_pivot_direction"] = direction

    if direction not in {"CALL", "PUT"}:
        return signal

    # Os detectores do sistema 01 esperam velas com max/min.
    janela = [
        {
            "open": c["open"],
            "close": c["close"],
            "max": c.get("high", c.get("max")),
            "min": c.get("low", c.get("min")),
        }
        for c in normalized[-30:]
    ]
    matches = detect_named_strategies(
        direction,
        janela,
        timeframe=timeframe,
        rsi=float(signal.get("rsi") or 50.0),
        near_support=bool(signal.get("near_support")),
        near_resistance=bool(signal.get("near_resistance")),
    )

    veredito_sr = veredito_pivo
    if veredito_sr["direction"] == direction:
        matches.append(
            {
                "key": STRATEGY_SUPPORT_RESISTANCE_PIVOT,
                "label": "Suporte/Resistência por pivôs",
                "setup": STRATEGY_SUPPORT_RESISTANCE_PIVOT,
                "summary": (
                    f"Rejeição em nível de {veredito_sr['level']:.5f} "
                    f"com {veredito_sr['touches']} toques."
                ),
                "speech_preview": f"Vou de {direction} por rejeição no nível.",
                "detail": (
                    f"Nível montado por pivôs com {veredito_sr['touches']} toques; "
                    f"pavio de rejeição {veredito_sr['wick_ratio'] * 100:.0f}% do range."
                ),
                "sr_zone_exempt": True,
            }
        )

    signal["named_strategies"] = [m["key"] for m in matches]
    signal["named_strategy_keys"] = [m["key"] for m in matches]
    signal["matched_strategies"] = [m["key"] for m in matches]

    # Prioridade própria: `pick_primary_strategy` só conhece as três chaves do
    # sistema 01 e devolveria None sempre que o único match fosse a SR-R.
    # A ordem coloca as duas leituras de suporte/resistência antes das de
    # continuação, que é o que o dono pediu — e é por isso que o acerto de cada
    # uma vai para o histórico separado: RETRACEMENT_SR mediu 46,38% no
    # backtest de 04/09, o pior das quatro, e precisa ser vigiado.
    ordem = (
        STRATEGY_RETRACEMENT_SR,
        STRATEGY_SUPPORT_RESISTANCE_PIVOT,
    )
    primaria = next(
        (m for chave in ordem for m in matches if m["key"] == chave),
        None,
    ) or pick_primary_strategy(matches)
    if primaria is None and matches:
        primaria = matches[0]
    if primaria is not None:
        signal["strategy_key"] = primaria["key"]
        signal["strategy_summary"] = primaria["summary"]
        signal["analysis_detail"] = primaria["detail"]
        signal["speech_preview"] = primaria["speech_preview"]

    if not NAMED_STRATEGIES_ENABLED or primaria is None:
        return signal
    if signal.get("trade_allowed"):
        return signal

    bloqueados = list(signal.get("blocked_filters") or [])
    # `SR_ZONE` só cai para as estratégias que existem para operar NO nível —
    # é o que `sr_zone_exempt` marca em cada detector. CANDLE_FLOW é
    # continuação cega e continua barrada colada no suporte/resistência, que
    # era a razão de o bloqueio ter virado incondicional em 29/07.
    nao_dispensaveis = set(NAMED_STRATEGY_NON_WAIVABLE)
    if not primaria.get("sr_zone_exempt"):
        nao_dispensaveis.add("SR_ZONE")
    impeditivos = [b for b in bloqueados if b in nao_dispensaveis]
    if impeditivos:
        signal["quality_reason"] = ",".join(impeditivos)
        return signal

    signal["trade_allowed"] = True
    if signal.get("sr_pivot_direction"):
        signal["signal"] = direction
        signal["direction"] = direction
        signal["analyzed_direction"] = direction
    signal["quality_reason"] = f"OK_{primaria['key']}"
    signal["strategy_name"] = primaria["label"]
    signal["entry_reason"] = primaria["detail"]
    signal["signal_explanation"] = primaria["detail"]
    signal["narrator_text"] = primaria["detail"]
    logger.info(
        "[NAMED_STRATEGY_RELEASE] symbol=%s direction=%s strategy=%s barrados=%s",
        symbol,
        direction,
        primaria["key"],
        ",".join(bloqueados) if bloqueados else "-",
    )
    return signal


def apply_sr_override(
    signal: dict[str, Any],
    symbol: str,
    normalized: list[dict[str, float]],
) -> dict[str, Any]:
    """Sobrepõe a decisão pela estratégia de suporte e resistência (SR-R).

    Segue o mesmo desenho de ``apply_revz_override``: roda DEPOIS do pipeline
    clássico, então o sinal já chega com o contrato completo (métricas,
    filtros, score) e aqui só a direção e o operar/não-operar são trocados.

    Roda também DEPOIS da REV-Z, e sai na frente se a REV-Z já decidiu operar —
    duas estratégias de reversão disputando a mesma vela seria dobrar a aposta
    no mesmo palpite, não confluência. Com ``SR_ENABLED`` desligado (padrão) o
    sinal volta intacto.

    Args:
        signal: Sinal já montado pelo motor clássico (e pela REV-Z, se ligada).
        symbol: Ativo analisado.
        normalized: Velas normalizadas, em ordem cronológica.

    Returns:
        O sinal, sobreposto quando a SR-R está ligada e tem veredito.
    """
    if not SR_ENABLED:
        return signal
    if isinstance(signal.get("revz"), dict):
        # A REV-Z avaliou este ativo (mercado aberto): a decisão é dela, tenha
        # ou não disparado.
        return signal

    veredito = sr_evaluate(symbol, normalized[-(SR_MIN_CANDLES + 5):])
    bloqueados = [f for f in (signal.get("blocked_filters") or []) if not str(f).startswith("SR_")]
    signal["sr"] = veredito
    signal["strategy_name"] = "SR-R rejeição em suporte/resistência"
    signal["confidence_model_version"] = "sr-v1"

    if veredito["direction"] is None:
        signal["signal"] = "WAIT"
        signal["direction"] = "WAIT"
        signal["trade_allowed"] = False
        signal["confidence"] = 0
        signal["score"] = 0
        signal["strategy_score"] = 0
        bloqueados.append(str(veredito["blocked"]))
        signal["blocked_filters"] = bloqueados
        signal["block_reasons"] = list(bloqueados)
        signal["quality_reason"] = str(veredito["blocked"])
        return signal

    conf = sr_confidence(veredito)
    signal["signal"] = veredito["direction"]
    signal["direction"] = veredito["direction"]
    signal["analyzed_direction"] = veredito["direction"]
    signal["trade_allowed"] = True
    signal["confidence"] = conf
    signal["score"] = conf
    # Mesma razão da REV-Z: não há score aditivo aqui. Quem decide é a rejeição
    # no nível, e a confiança é só o reflexo da força dela.
    signal["strategy_score"] = conf
    signal["blocked_filters"] = []
    signal["block_reasons"] = []
    signal["quality_reason"] = "OK"
    lado = "suporte" if veredito["direction"] == "CALL" else "resistência"
    texto = (
        f"{symbol}: rejeição em {lado} de {veredito['level']:.5f} "
        f"({veredito['touches']} toques), pavio de {veredito['wick_ratio'] * 100:.0f}% "
        f"do range. Entrada {veredito['direction']}."
    )
    signal["reason"] = texto
    signal["entry_reason"] = texto
    signal["signal_explanation"] = texto
    signal["narrator_text"] = texto
    logger.info(
        "[SR_SIGNAL] symbol=%s direction=%s level=%.5f touches=%s wick=%.2f confidence=%s",
        symbol,
        veredito["direction"],
        veredito["level"],
        veredito["touches"],
        veredito["wick_ratio"],
        conf,
    )
    return signal


def apply_revz_override(
    signal: dict[str, Any],
    symbol: str,
    normalized: list[dict[str, float]],
    *,
    timeframe: str = "M1",
) -> dict[str, Any]:
    """Sobrepõe a decisão do motor clássico pela estratégia REV-Z.

    Com ``OPEN_MARKET_STRATEGY=RSI`` (24/09/2026) quem decide o mercado aberto
    é o RSI extremo por timeframe (``open_rsi_strategy``), pelo MESMO caminho:
    veredito no campo ``revz``, respeito ao nível, portões e confirmação no
    disparo. Só o cálculo e o texto mudam.

    Roda DEPOIS do pipeline clássico de propósito: o sinal já vem com o
    contrato completo (métricas, filtros, score), e aqui só a decisão de
    direção e de operar/não operar é trocada. Assim nenhum consumidor a jusante
    precisa saber que a estratégia mudou.

    Quando ``REVZ_ENABLED`` é False (padrão) devolve o sinal intacto.

    Desde 10/09/2026 a REV-Z é o motor do MERCADO ABERTO e só dele: em ativo
    ``-OTC`` o sinal do motor clássico volta intacto (o OTC segue com clássico e
    Vertex). No aberto ela decide sozinha — quando não há extremo a vela fica
    sem entrada, em vez de devolver a palavra ao clássico, que no aberto mediu
    49,1% em 53 operações (08–10/09).

    Aqui o limiar é o de INDICAÇÃO (``REVZ_NOMINATE_THRESHOLD``): a análise roda
    com a vela ainda em formação. Quem decide a ordem é a confirmação no
    disparo, com a vela fechada e ``REVZ_THRESHOLD`` — ver
    ``revz_confirm_at_entry``.

    Args:
        signal: Sinal já montado pelo motor clássico.
        symbol: Ativo analisado.
        normalized: Velas M1 normalizadas, em ordem cronológica. Vazio quando
            a operação é M5/M15 e as M1 não vieram — aí a vela fica sem entrada.

    Returns:
        O sinal, sobreposto quando a REV-Z está ligada e o ativo é de mercado aberto.
    """
    if not REVZ_ENABLED or is_otc_symbol(symbol):
        return signal

    usa_rsi = is_rsi_open_active()
    if usa_rsi:
        veredito = rsi_open_evaluate(
            symbol,
            [c["close"] for c in normalized],
            timeframe,
            nominate=True,
        )
    else:
        veredito = revz_evaluate(
            symbol,
            [c["close"] for c in normalized],
            threshold=REVZ_NOMINATE_THRESHOLD,
        )
        veredito["confirm_threshold"] = REVZ_THRESHOLD
    direcao = veredito["direction"]
    if direcao is not None and normalized:
        # "Nunca contra o nível" vale para toda estratégia (regra do dono,
        # 09/09). Mesma leitura da Vertex: só o conflito recusa — CALL colado
        # na resistência, PUT colado no suporte. Dentro da região a favor a
        # REV-Z não pede rejeição confirmada: o extremo do z é a tese dela.
        respeita, motivo = evaluate_respect(direcao, _support_resistance_context(normalized), normalized[-1])
        if not respeita and motivo in NIVEL_A_FRENTE:
            contra = "RSI_CONTRA_O_NIVEL" if usa_rsi else "REVZ_CONTRA_O_NIVEL"
            veredito = dict(veredito, direction=None, blocked=contra)
    bloqueados = [f for f in (signal.get("blocked_filters") or []) if not str(f).startswith("REVZ_")]
    signal["revz"] = veredito
    if usa_rsi:
        signal["strategy_name"] = "RSI extremo — reversão no mercado aberto"
        signal["strategy_key"] = STRATEGY_RSI_OPEN
        signal["confidence_model_version"] = "rsi-aberto-v1"
    else:
        signal["strategy_name"] = "REV-Z reversão em desvio extremo"
        signal["strategy_key"] = STRATEGY_REVZ
        signal["confidence_model_version"] = "revz-v1"

    if veredito["direction"] is None:
        signal["signal"] = "WAIT"
        signal["direction"] = "WAIT"
        signal["trade_allowed"] = False
        signal["confidence"] = 0
        signal["score"] = 0
        signal["strategy_score"] = 0
        bloqueados.append(str(veredito["blocked"]))
        signal["blocked_filters"] = bloqueados
        signal["block_reasons"] = list(bloqueados)
        signal["quality_reason"] = str(veredito["blocked"])
        # A leitura do motor clássico ("Vou de PUT — o desenho está claro")
        # não pode ficar na tela de uma vela em que o aberto não opera.
        if usa_rsi:
            texto = rsi_open_text(symbol, veredito)
        elif veredito.get("z") is not None:
            texto = (
                f"{symbol}: preço a {abs(veredito['z']):.1f} desvios da média das últimas "
                f"{veredito['lookback']} velas. Sem esticão suficiente para a reversão — "
                "aguardando."
            )
        else:
            texto = f"{symbol}: sem dados suficientes para medir o desvio da média — aguardando."
        for campo in ("reason", "signal_explanation", "narrator_text", "analysis_detail", "candle_reading"):
            signal[campo] = texto
        return signal

    if usa_rsi:
        conf = rsi_open_confidence(veredito["rsi"], timeframe)
    else:
        conf = revz_confidence(veredito["z"])
    signal["signal"] = veredito["direction"]
    signal["direction"] = veredito["direction"]
    signal["analyzed_direction"] = veredito["direction"]
    signal["trade_allowed"] = True
    signal["confidence"] = conf
    signal["score"] = conf
    # O portão do ciclo compara `strategy_score` com o mínimo do usuário. A
    # REV-Z não tem score aditivo: quem decide é o |z|, e a confiança já é o
    # reflexo dele.
    signal["strategy_score"] = conf
    signal["blocked_filters"] = []
    signal["block_reasons"] = []
    signal["quality_reason"] = "OK"
    if usa_rsi:
        texto = rsi_open_text(symbol, veredito)
        for campo in (
            "reason",
            "entry_reason",
            "signal_explanation",
            "narrator_text",
            "analysis_detail",
            "candle_reading",
        ):
            signal[campo] = texto
        return signal
    z = veredito["z"]
    lado = "abaixo" if veredito["direction"] == "CALL" else "acima"
    # Sem "a tendência é voltar" (convicção) nem "se o fechamento confirmar"
    # (soa como dúvida na voz): o texto só diz o que foi medido. A confirmação
    # no fechamento continua acontecendo em `revz_confirm_at_entry`.
    texto = (
        f"{symbol}: preço {abs(z):.1f} desvios {lado} da média das últimas "
        f"{veredito['lookback']} velas. Entrada de {veredito['direction']}."
    )
    # `candle_reading` também: é o texto que o cliente lê, e o do motor
    # clássico descreve a entrada A FAVOR da vela — o oposto desta.
    for campo in (
        "reason",
        "entry_reason",
        "signal_explanation",
        "narrator_text",
        "analysis_detail",
        "candle_reading",
    ):
        signal[campo] = texto
    return signal


def apply_vertex_override(
    signal: dict[str, Any],
    symbol: str,
    normalized: list[dict[str, float]],
) -> dict[str, Any]:
    """Sobrepõe a decisão do motor clássico pela estratégia Vertex.

    Mesmo desenho do ``apply_revz_override``: roda depois do pipeline clássico,
    quando o sinal já tem o contrato completo, e troca só a direção e o
    operar/não operar. Nenhum consumidor a jusante precisa saber que a
    estratégia mudou.

    A região de suporte e resistência continua valendo aqui. A Vertex é
    reversão e pode apontar CALL com o preço colado na resistência; nesse caso a
    entrada é recusada, porque "nunca contra o nível" vale para todas as
    estratégias, não só para o motor clássico. Dentro da região a favor a Vertex
    não precisa de rejeição confirmada — o extremo do indicador é a tese dela.

    Quando ``VERTEX_ENABLED`` é False (padrão) devolve o sinal intacto.

    Args:
        signal: Sinal já montado pelo motor clássico.
        symbol: Ativo analisado.
        normalized: Velas normalizadas, em ordem cronológica.

    Returns:
        O sinal, sobreposto quando a Vertex está ligada.
    """
    if not VERTEX_ENABLED:
        return signal
    if isinstance(signal.get("revz"), dict):
        # Mercado aberto é da REV-Z (10/09/2026). A Vertex continua valendo no
        # OTC; aqui ela sobrescreveria a vela com uma tese que o backtest de
        # 09/09 mediu nula no aberto (52,55%, abaixo do empate de 54,05%).
        return signal

    veredito = vertex_evaluate(symbol, normalized)
    bloqueados = [f for f in (signal.get("blocked_filters") or []) if not str(f).startswith("VERTEX_")]
    signal["vertex"] = veredito

    direcao = veredito["direction"]
    if direcao is not None and normalized:
        respeita, motivo = evaluate_respect(direcao, _support_resistance_context(normalized), normalized[-1])
        if not respeita and motivo in NIVEL_A_FRENTE:
            veredito = dict(veredito, direction=None, blocked="VERTEX_CONTRA_O_NIVEL")
            signal["vertex"] = veredito
            direcao = None

    if direcao is None:
        # PARALELO (10/09): a Vertex não tem tese nesta vela, e isso não é
        # motivo para calar o motor clássico. Até 09/09 este ramo sobrescrevia
        # o parecer dele com WAIT/confiança 0 — como o |vertex| só passa do
        # extremo em ~4% das velas, 96% das análises viravam "nada aqui" e as
        # contas pararam de operar sem erro nenhum no log. Medido em velas
        # reais: 21 análises com direção contra 480 com a flag desligada.
        #
        # Agora as duas estratégias correm lado a lado: a Vertex decide quando
        # dispara (piso próprio, `vertex_min_confidence`), e quando não dispara
        # quem decide é o clássico, com as regras e o piso dele. O nome
        # `VERTEX_FORA_DO_EXTREMO` fica registrado só para leitura — não
        # penaliza score e não é bloqueio crítico.
        if VERTEX_PARALLEL:
            bloqueados.append(str(veredito["blocked"]))
            signal["blocked_filters"] = bloqueados
            signal["block_reasons"] = list(bloqueados)
            return signal
        signal["strategy_name"] = "Vertex reversão em desvio extremo"
        signal["strategy_key"] = STRATEGY_VERTEX
        signal["confidence_model_version"] = "vertex-v1"
        signal["signal"] = "WAIT"
        signal["direction"] = "WAIT"
        signal["trade_allowed"] = False
        signal["confidence"] = 0
        signal["score"] = 0
        signal["strategy_score"] = 0
        bloqueados.append(str(veredito["blocked"]))
        signal["blocked_filters"] = bloqueados
        signal["block_reasons"] = list(bloqueados)
        signal["quality_reason"] = str(veredito["blocked"])
        return signal

    # A Vertex disparou: a partir daqui o sinal é dela, e só dela.
    signal["strategy_name"] = "Vertex reversão em desvio extremo"
    signal["strategy_key"] = STRATEGY_VERTEX
    signal["confidence_model_version"] = "vertex-v1"
    conf = vertex_confidence(veredito["vertex"])
    signal["signal"] = direcao
    signal["direction"] = direcao
    signal["analyzed_direction"] = direcao
    signal["trade_allowed"] = True
    signal["confidence"] = conf
    signal["score"] = conf
    # Escala própria e curta: quem decide operar é o |vertex| passar do nível
    # extremo, e a confiança só reflete o quanto passou. O portão do ciclo
    # precisa do rescale correspondente — ver `VERTEX_CONFIDENCE_MAX`.
    signal["strategy_score"] = conf
    signal["blocked_filters"] = []
    signal["block_reasons"] = []
    signal["quality_reason"] = "OK_VERTEX"
    valor = veredito["vertex"]
    limite = veredito["ext_top"] if direcao == "PUT" else veredito["ext_bot"]
    lado = "esticado para cima" if direcao == "PUT" else "esticado para baixo"
    texto = (
        f"{symbol}: Vertex em {valor:+.1f}, {lado} além do nível extremo de "
        f"{limite:+.0f}. Reversão esperada — entrada {direcao}."
    )
    for campo in ("reason", "entry_reason", "signal_explanation", "narrator_text", "analysis_detail"):
        signal[campo] = texto
    return signal


def _build_signal(
    *,
    symbol: str,
    signal: SignalValue,
    confidence: int,
    reason: str,
    last_price: float | None,
    trend: TrendValue,
    strength: int,
    timeframe: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "signal": signal,
        "confidence": max(0, min(100, int(round(confidence)))),
        "score": max(0, min(100, int(round(confidence)))),
        "reason": reason,
        "entry_reason": reason,
        "signal_explanation": reason,
        "narrator_text": reason,
        "timeframe": timeframe,
        "last_price": last_price,
        "trend": trend,
        "strength": max(0, min(100, int(round(strength)))),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _calibrate_confidence(raw_score: int | float) -> int:
    """
    Converte o score técnico aditivo para uma escala limitada de confiança.

    O score bruto soma evidências correlacionadas e, portanto, não representa
    probabilidade de acerto. A compressão impede que confluências técnicas
    saturem artificialmente em 100%.
    """
    normalized = max(0.0, min(120.0, float(raw_score))) / 120.0
    calibrated = CALIBRATED_CONFIDENCE_FLOOR + (
        CALIBRATED_CONFIDENCE_CAP - CALIBRATED_CONFIDENCE_FLOOR
    ) * normalized
    return max(0, min(CALIBRATED_CONFIDENCE_CAP, int(round(calibrated))))


def _normalize_candle(candle: dict[str, Any]) -> dict[str, float] | None:
    try:
        low = candle["min"] if "min" in candle else candle["low"]
        high = candle["max"] if "max" in candle else candle["high"]
        return {
            "open": float(candle["open"]),
            "close": float(candle["close"]),
            "min": float(low),
            "max": float(high),
            "volume": float(candle.get("volume") or 0),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def _rsi(values: list[float], period: int) -> float:
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, 0)
        loss = abs(min(change, 0))
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))


def _has_recent_pullback(direction: str, candles: list[dict[str, float]]) -> bool:
    """Confirma uma correção curta antes do candle que retoma a tendência."""
    if len(candles) < 4:
        return False
    previous = candles[-4:-1]
    if direction == "CALL":
        return any(candle["close"] < candle["open"] for candle in previous)
    if direction == "PUT":
        return any(candle["close"] > candle["open"] for candle in previous)
    return False


def _trend(ema9: float, ema21: float, last_price: float) -> tuple[TrendValue, int]:
    if last_price == 0:
        return "SIDEWAYS", 0
    distance_pct = abs(ema9 - ema21) / abs(last_price)
    strength = min(100, distance_pct * 100000)
    if strength < 8:
        return "SIDEWAYS", int(round(strength))
    return ("UP" if ema9 > ema21 else "DOWN"), int(round(strength))


def _average_range(candles: list[dict[str, float]]) -> float:
    ranges = [max(candle["max"] - candle["min"], 0) for candle in candles]
    return sum(ranges) / len(ranges) if ranges else 0


def _is_extreme_candle(candle: dict[str, float], avg_range: float) -> bool:
    candle_range = max(candle["max"] - candle["min"], 0)
    if avg_range <= 0:
        return False
    return candle_range > avg_range * 2.5


def _score_direction(
    direction: SignalValue,
    candles: list[dict[str, float]],
    ema9: float,
    ema21: float,
    rsi: float,
) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    if direction == "CALL":
        if ema9 > ema21:
            score += 30
            reasons.append("EMA9 acima da EMA21.")
        if 55 <= rsi <= 75:
            score += 22
            reasons.append(f"RSI favoravel ({rsi:.1f}).")
    else:
        if ema9 < ema21:
            score += 30
            reasons.append("EMA9 abaixo da EMA21.")
        if 25 <= rsi <= 45:
            score += 22
            reasons.append(f"RSI favoravel ({rsi:.1f}).")

    sequence_score = _sequence_score(direction, candles[-3:])
    score += sequence_score
    if sequence_score:
        reasons.append("Sequência dos últimos 3 candles confirma direção.")

    force_score = _current_candle_force_score(direction, candles[-1])
    score += force_score
    if force_score:
        reasons.append("Último candle tem força na direção.")

    price_action_score = _price_action_setup_score(direction, candles)
    score += price_action_score
    if price_action_score >= 18:
        reasons.append("Setup de reversao confirmado pelos candles.")
    elif price_action_score:
        reasons.append("Setup de continuacao confirmado pelos candles.")

    wick_score = _wick_score(direction, candles[-1])
    score += wick_score
    if wick_score:
        reasons.append("Pavio contra a entrada esta curto.")

    last_5_score = _last_5_score(direction, candles[-5:])
    score += last_5_score
    if last_5_score:
        reasons.append("Últimos 5 candles sustentam a direção.")

    return score, reasons or ["Sem confluencias suficientes."]


def _sequence_score(direction: SignalValue, candles: list[dict[str, float]]) -> int:
    if len(candles) < 3:
        return 0
    if direction == "CALL":
        directional = sum(1 for candle in candles if candle["close"] > candle["open"])
        closes_confirm = candles[-1]["close"] >= candles[0]["close"]
    else:
        directional = sum(1 for candle in candles if candle["close"] < candle["open"])
        closes_confirm = candles[-1]["close"] <= candles[0]["close"]
    if directional == 3 and closes_confirm:
        return 20
    if directional >= 2 and closes_confirm:
        return 14
    return 0


def _attach_indicators(
    signal: dict[str, Any],
    candles: list[dict[str, float]],
    ema9: float,
    ema21: float,
    rsi: float,
    atr: float,
    atr_pct: float,
) -> None:
    last = candles[-1]
    candle_range = max(last["max"] - last["min"], 0)
    body = abs(last["close"] - last["open"])
    upper_wick = last["max"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["min"]
    body_ratio = body / candle_range if candle_range else 0.0
    upper_wick_ratio = upper_wick / candle_range if candle_range else 0.0
    lower_wick_ratio = lower_wick / candle_range if candle_range else 0.0
    direction = signal.get("signal")
    directional_last_5 = _directional_count(str(direction), candles[-5:])
    price_action_setup = _price_action_setup(str(direction), candles)
    opposite_direction = "PUT" if direction == "CALL" else "CALL" if direction == "PUT" else "WAIT"
    level_context = _support_resistance_context(candles)

    signal.update(
        {
            "ema9": round(ema9, 6),
            "ema21": round(ema21, 6),
            "rsi": round(rsi, 2),
            "atr": round(atr, 6),
            "atr_pct": round(atr_pct, 8),
            "body_ratio": round(body_ratio, 4),
            "candle_body": round(body, 6),
            "upper_wick": round(upper_wick, 6),
            "lower_wick": round(lower_wick, 6),
            "upper_wick_ratio": round(upper_wick_ratio, 4),
            "lower_wick_ratio": round(lower_wick_ratio, 4),
            "directional_candles_5": directional_last_5,
            "pullback_confirmed": _has_recent_pullback(str(direction), candles),
            "alternating_last_3": _alternating_last_3(candles[-3:]),
            "current_candle_direction": _candle_direction(last),
            "price_action_setup": price_action_setup,
            "near_support_resistance": _near_support_resistance(str(direction), candles),
            "near_support": bool(level_context.get("near_support")),
            "near_resistance": bool(level_context.get("near_resistance")),
            "support_level": (
                round(float(level_context["support"]), 6)
                if level_context.get("support") is not None
                else None
            ),
            "resistance_level": (
                round(float(level_context["resistance"]), 6)
                if level_context.get("resistance") is not None
                else None
            ),
            "level_conflict": _has_level_conflict(str(direction), level_context),
            "level_rejection_confirmed": _level_rejection_confirmed(str(direction), candles, level_context),
            "zigzag_reversal": _is_reversal_setup(str(direction), candles),
            "reversal_against": _price_action_setup(opposite_direction, candles) == "REVERSAL",
        }
    )


def _apply_quality_filters(
    signal: dict[str, Any],
    candles: list[dict[str, float]],
    strategy_mode: StrategyMode,
    payout: float | None,
    *,
    frequency_recovery: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    mode = strategy_mode if strategy_mode in STRATEGY_PROFILES else "conservative"
    profile = STRATEGY_PROFILES[mode]
    blocked: list[str] = []
    approved: list[str] = []
    direction = str(signal.get("signal") or "WAIT")

    penalties = {
        "TREND_CLEAR": 10,
        "TREND_STRENGTH": 8,
        "SIDEWAYS_FILTER": 10,
        "EMA_TREND": 8,
        "RSI_RANGE": 8,
        "WICK_REJECTION": 8,
        "CANDLE_STRENGTH": 8,
        "DOJI_FILTER": 5,
        "VOLATILITY": 5,
        "LAST_5_CONFIRMATION": 5,
        "NO_ALTERNATING_LAST_3": 5,
        "PRICE_ACTION_SETUP": 12,
        "REVERSAL_AGAINST": 15,
        "SUPPORT_RESISTANCE": 8,
        "LEVEL_CONFLICT": 18,
        "LEVEL_REJECTION": 12,
        "SR_ZONE": 20,
        "WICK_EXCESS": 20,
        "LAST_3_ALIGNMENT": 20,
        "CONTINUATION_DEAD_RSI": 18,
        "WEAK_CONTINUATION_PUT": 20,
        "WEAK_PUT": 20,
        "PUT_CHASE": 20,
        "CALL_CHASE": 20,
        "CALL_GRG": 20,
        "SEQ_GGG": 20,
        "SEQ_GRR": 20,
        "WEAK_SETUP": 20,
        "PUT_BODY": 18,
        "PUT_WICK": 12,
        "TOXIC_HOUR": 20,
        "ASSET_BAN": 20,
        "TOXIC_WEAK_PAIR": 20,
    }

    def check(name: str, passed: bool) -> None:
        if passed:
            approved.append(name)
        else:
            blocked.append(name)

    if direction not in {"CALL", "PUT"}:
        blocked.append("CANDLES_UNAVAILABLE")
    if signal.get("insufficient_candles"):
        blocked.append("CANDLES_UNAVAILABLE")
    check("MIN_CONFIDENCE", int(signal.get("confidence") or 0) >= profile["confidence"])
    if payout is None:
        blocked.append("PAYOUT_UNAVAILABLE")
    else:
        check("MIN_PAYOUT", float(payout) >= profile["payout"])
    ema9 = float(signal.get("ema9") or 0)
    ema21 = float(signal.get("ema21") or 0)
    rsi = float(signal.get("rsi") or 50)
    price_action_setup = str(signal.get("price_action_setup") or "WEAK")
    has_reversal_setup = price_action_setup == "REVERSAL"
    is_continuation = price_action_setup == "CONTINUATION"
    level_context = _support_resistance_context(candles)
    level_conflict = _has_level_conflict(direction, level_context)
    needs_level_rejection = (
        price_action_setup in {"REVERSAL", "SUPPORT_RESISTANCE"}
        or bool(level_context.get("near_support"))
        or bool(level_context.get("near_resistance"))
    )
    level_rejection_confirmed = _level_rejection_confirmed(direction, candles, level_context)
    in_support_resistance_zone = bool(level_context.get("near_support")) or bool(
        level_context.get("near_resistance")
    )
    last_3_direction = str(signal.get("last_3_direction") or "").upper()
    if not last_3_direction and len(candles) >= 3:
        last_3_direction = _direction_label(candles[-3:])
    wanted_last_3 = "UP" if direction == "CALL" else "DOWN" if direction == "PUT" else ""
    last_3_aligned = (not is_continuation) or (last_3_direction == wanted_last_3)
    dead_rsi = is_continuation and CONTINUATION_DEAD_RSI_MIN <= rsi < CONTINUATION_DEAD_RSI_MAX
    weak_continuation_put = (
        is_continuation
        and direction == "PUT"
        and is_weak_continuation_put_asset(str(signal.get("symbol") or ""))
    )
    weak_put = is_weak_put_setup(price_action_setup, direction)
    last_3_colors = extract_last_3_colors(signal, candles)
    chasing_put = is_chasing_continuation_put(price_action_setup, direction, last_3_colors)
    chasing_call = is_chasing_continuation_call(price_action_setup, direction, last_3_colors)
    call_grg = is_call_green_red_green(direction, last_3_colors)
    seq_ggg = is_seq_green_green_green(last_3_colors)
    seq_grr = is_seq_green_red_red(last_3_colors)
    weak_setup = is_weak_setup(price_action_setup)
    put_thin = is_put_thin_body(float(signal.get("body_ratio") or 0), direction)
    put_wick = is_put_against_wick(signal.get("lower_wick_ratio"), direction)
    toxic_hour = is_toxic_hour_brt(now)
    banned_asset = is_banned_asset(str(signal.get("symbol") or ""))
    toxic_weak_pair = is_toxic_weak_pair(
        price_action_setup,
        direction,
        str(signal.get("symbol") or ""),
    )
    check(
        "TREND_CLEAR",
        has_reversal_setup or signal.get("trend") != "SIDEWAYS",
    )
    check(
        "TREND_STRENGTH",
        has_reversal_setup
        or signal.get("trend") == "SIDEWAYS"
        or int(signal.get("strength") or 0) >= profile["strength"],
    )
    check("SIDEWAYS_FILTER", has_reversal_setup or signal.get("trend") != "SIDEWAYS")
    if direction == "CALL":
        check("EMA_TREND", ema9 > ema21 or has_reversal_setup)
        check("RSI_RANGE", 55 <= rsi <= 75)
        check("WICK_REJECTION", float(signal.get("upper_wick_ratio") or 1) <= 0.45)
    elif direction == "PUT":
        check("EMA_TREND", ema9 < ema21 or has_reversal_setup)
        check("RSI_RANGE", 25 <= rsi <= 45)
        check("WICK_REJECTION", float(signal.get("lower_wick_ratio") or 1) <= 0.45)

    body_ratio = float(signal.get("body_ratio") or 0)
    min_body = CANDLE_MIN_BODY_RATIO if CANDLE_WEAK_HARD_BLOCK else float(profile["body_ratio"])
    if PUT_BODY_HARD_BLOCK and direction == "PUT":
        min_body = max(min_body, PUT_MIN_BODY_RATIO)
    check("CANDLE_STRENGTH", body_ratio >= min_body)
    check(
        "DOJI_FILTER",
        (not is_doji_body(body_ratio)) if DOJI_HARD_BLOCK else body_ratio >= float(profile["body_ratio"]),
    )
    check("VOLATILITY", float(signal.get("atr_pct") or 0) >= 0.0001)
    check("LAST_5_CONFIRMATION", int(signal.get("directional_candles_5") or 0) >= 3 or has_reversal_setup)
    check("NO_ALTERNATING_LAST_3", not bool(signal.get("alternating_last_3")))
    check("PRICE_ACTION_SETUP", price_action_setup in {"CONTINUATION", "REVERSAL", "SUPPORT_RESISTANCE"})
    check("SUPPORT_RESISTANCE", bool(signal.get("near_support_resistance")) or price_action_setup == "CONTINUATION")
    check("LEVEL_CONFLICT", not level_conflict)
    check("LEVEL_REJECTION", not needs_level_rejection or level_rejection_confirmed)
    # A regra inteira de respeito à região vive em `sr_respect`: contra o nível
    # nunca; dentro dela, só na rejeição confirmada e com espaço até o nível
    # oposto; fora dela, livre. Antes daqui `SR_ZONE` era veto cego e barrava
    # 383 de 383 setups `SUPPORT_RESISTANCE` — o motor somava os 15 pontos do
    # setup e matava a entrada na mesma passagem.
    # Sem direção não há o que respeitar: sinal WAIT já é barrado por
    # CANDLES_UNAVAILABLE, e marcar SR_ZONE aqui inventaria uma violação de
    # nível que não existe e sujaria o diagnóstico de quem lê o log.
    if direction in {"CALL", "PUT"} and candles:
        respeita_regiao, motivo_regiao = evaluate_respect(direction, level_context, candles[-1])
    else:
        respeita_regiao, motivo_regiao = True, RESPECT_SEM_DIRECAO
    signal["sr_zone_source"] = level_context.get("source")
    signal["sr_respect_reason"] = motivo_regiao
    check("SR_ZONE", (not SR_ZONE_HARD_BLOCK) or respeita_regiao)
    # Pavio (11/09): as velas fechadas antes da entrada e a vela em formação.
    # `WICK_REJECTION` acima olha só `candles[-1]` — na análise, a vela com 5-20s
    # de vida — e só tira pontos. Este é bloqueio; a mesma regra é reconferida
    # no disparo com a vela recém-fechada (`revalidate_level_before_entry`).
    sem_pavio, motivo_pavio, _ = evaluate_wicks(candles) if candles else (True, "PAVIO_SEM_DADOS", {})
    signal["wick_reason"] = motivo_pavio
    check("WICK_EXCESS", sem_pavio)
    check("REVERSAL_AGAINST", not bool(signal.get("reversal_against")))
    check(
        "LAST_3_ALIGNMENT",
        (not LAST_3_ALIGNMENT_HARD_BLOCK) or last_3_aligned,
    )
    check(
        "CONTINUATION_DEAD_RSI",
        (not CONTINUATION_DEAD_RSI_HARD_BLOCK) or (not dead_rsi),
    )
    check(
        "WEAK_CONTINUATION_PUT",
        (not WEAK_CONTINUATION_PUT_HARD_BLOCK) or (not weak_continuation_put),
    )
    check(
        "WEAK_PUT",
        (not WEAK_PUT_HARD_BLOCK) or (not weak_put),
    )
    check(
        "PUT_CHASE",
        (not PUT_CHASE_HARD_BLOCK) or (not chasing_put),
    )
    check(
        "CALL_CHASE",
        (not CALL_CHASE_HARD_BLOCK) or (not chasing_call),
    )
    check(
        "CALL_GRG",
        (not CALL_GRG_HARD_BLOCK) or (not call_grg),
    )
    check(
        "SEQ_GGG",
        (not SEQ_GGG_HARD_BLOCK) or (not seq_ggg),
    )
    check(
        "SEQ_GRR",
        (not SEQ_GRR_HARD_BLOCK) or (not seq_grr),
    )
    check(
        "WEAK_SETUP",
        (not WEAK_SETUP_HARD_BLOCK) or (not weak_setup),
    )
    check(
        "PUT_BODY",
        (not PUT_BODY_HARD_BLOCK) or (not put_thin),
    )
    check(
        "PUT_WICK",
        (not PUT_WICK_HARD_BLOCK) or (not put_wick),
    )
    check(
        "TOXIC_HOUR",
        (not TOXIC_HOUR_HARD_BLOCK) or (not toxic_hour),
    )
    check(
        "ASSET_BAN",
        (not ASSET_BAN_HARD_BLOCK) or (not banned_asset),
    )
    check(
        "TOXIC_WEAK_PAIR",
        (not TOXIC_WEAK_PAIR_HARD_BLOCK) or (not toxic_weak_pair),
    )

    confidence = int(signal.get("confidence") or 0)
    score_blocked = list(blocked)
    if frequency_recovery:
        # Em recovery a maioria dos soft-blocks não derruba o score abaixo do
        # portão — senão allowed=True no scan e NO_OPPORTUNITY no ciclo.
        # PRICE_ACTION_SETUP continua penalizando (KEEP_SCORE_PENALTIES).
        waived = FREQUENCY_RECOVERY_SOFT_BLOCKS - FREQUENCY_RECOVERY_KEEP_SCORE_PENALTIES
        score_blocked = [name for name in blocked if name not in waived]
    strategy_score = max(0, confidence - sum(penalties.get(name, 0) for name in score_blocked))
    hard_block_names = {
        "ACCOUNT_DISCONNECTED",
        "STOP_WIN_HIT",
        "STOP_LOSS_HIT",
        "ACTIVE_CLOSED",
        "ACTIVE_SUSPENDED",
        "PAYOUT_UNAVAILABLE",
        "OPERATION_IN_PROGRESS",
        "CANDLES_UNAVAILABLE",
        "PRICE_ACTION_SETUP",
        "REVERSAL_AGAINST",
        "LEVEL_CONFLICT",
        "LEVEL_REJECTION",
        "SR_ZONE",
    }
    if WICK_FILTER_ENABLED:
        hard_block_names.add("WICK_EXCESS")
    if TREND_CLEAR_HARD_BLOCK:
        hard_block_names.add("TREND_CLEAR")
    if LAST_3_ALIGNMENT_HARD_BLOCK:
        hard_block_names.add("LAST_3_ALIGNMENT")
    if WEAK_CONTINUATION_PUT_HARD_BLOCK:
        hard_block_names.add("WEAK_CONTINUATION_PUT")
    if WEAK_PUT_HARD_BLOCK:
        hard_block_names.add("WEAK_PUT")
    if PUT_CHASE_HARD_BLOCK:
        hard_block_names.add("PUT_CHASE")
    if CALL_CHASE_HARD_BLOCK:
        hard_block_names.add("CALL_CHASE")
    if CALL_GRG_HARD_BLOCK:
        hard_block_names.add("CALL_GRG")
    if SEQ_GGG_HARD_BLOCK:
        hard_block_names.add("SEQ_GGG")
    if SEQ_GRR_HARD_BLOCK:
        hard_block_names.add("SEQ_GRR")
    if WEAK_SETUP_HARD_BLOCK:
        hard_block_names.add("WEAK_SETUP")
    if PUT_BODY_HARD_BLOCK:
        hard_block_names.add("PUT_BODY")
    if PUT_WICK_HARD_BLOCK:
        hard_block_names.add("PUT_WICK")
    if TOXIC_HOUR_HARD_BLOCK:
        hard_block_names.add("TOXIC_HOUR")
    if ASSET_BAN_HARD_BLOCK:
        hard_block_names.add("ASSET_BAN")
    if TOXIC_WEAK_PAIR_HARD_BLOCK:
        hard_block_names.add("TOXIC_WEAK_PAIR")
    if CANDLE_WEAK_HARD_BLOCK:
        hard_block_names.add("CANDLE_STRENGTH")
    if DOJI_HARD_BLOCK:
        hard_block_names.add("DOJI_FILTER")
    if frequency_recovery:
        hard_block_names -= FREQUENCY_RECOVERY_SOFT_BLOCKS
        signal["frequency_recovery"] = True
    hard_blocks = [name for name in blocked if name in hard_block_names]
    quality_score = strategy_score
    trade_allowed = not hard_blocks
    signal.update(
        {
            "strategy_mode": mode,
            "payout": payout,
            "direction": direction,
            "strategy_score": strategy_score,
            "score": strategy_score,
            "quality_score": quality_score,
            "block_reasons": list(blocked),
            "blocked_filters": blocked,
            "approved_filters": approved,
            "trade_allowed": trade_allowed,
            "quality_reason": "OK" if trade_allowed else ",".join(hard_blocks),
            "in_support_resistance_zone": in_support_resistance_zone,
            "frequency_recovery": bool(frequency_recovery),
        }
    )
    if trade_allowed:
        # O nome interno do filtro (MIN_CONFIDENCE, PRICE_ACTION_SETUP...) não
        # diz nada para quem lê e fazia toda entrada parecer a mesma análise.
        # Ele continua em `blocked_filters` para diagnóstico; o cliente recebe
        # a narrativa montada a partir das MESMAS métricas.
        penalties_text = ", ".join(blocked)
        signal["technical_explanation"] = (signal.get("reason") or "Sinal aprovado.") + (
            f" Penalizacoes no score: {penalties_text}." if penalties_text else ""
        )
        narrativa = str(signal.get("candle_reading") or "").strip()
        signal["signal_explanation"] = narrativa or signal["technical_explanation"]
    else:
        filters = ", ".join(hard_blocks) if hard_blocks else "sem direção válida"
        signal["signal_explanation"] = f"Sinal sem entrada: {filters}."
    signal["entry_reason"] = signal["signal_explanation"]
    signal["narrator_text"] = signal["signal_explanation"]
    logger.info(
        "[CANDLE_SCORE_UPDATED] symbol=%s direction=%s score=%s confidence=%s block_reasons=%s",
        signal.get("symbol"),
        signal.get("direction"),
        signal.get("score"),
        signal.get("confidence"),
        signal.get("block_reasons"),
    )
    return signal

def _match_entry_strategies(
    direction: str,
    candles: list[dict[str, float]],
    signal: dict[str, Any],
) -> list[str]:
    """Retorna quais das 3 estratégias de entrada confirmaram a direção."""
    return list(_match_entry_strategy_setups(direction, candles, signal))


def _match_entry_strategy_setups(
    direction: str,
    candles: list[dict[str, float]],
    signal: dict[str, Any],
) -> dict[str, str]:
    """Relaciona cada estratégia aprovada ao setup de continuação ou reversão."""
    matched: dict[str, str] = {}
    if direction not in {"CALL", "PUT"} or len(candles) < 2:
        return matched

    price_action_setup = str(signal.get("price_action_setup") or _price_action_setup(direction, candles))
    if price_action_setup == "CONTINUATION":
        matched[STRATEGY_PRICE_ACTION] = "CONTINUATION"
    elif price_action_setup in {"REVERSAL", "SUPPORT_RESISTANCE"}:
        matched[STRATEGY_PRICE_ACTION] = "REVERSAL"

    psychology_setup = _candle_psychology_setup(direction, candles)
    if psychology_setup is not None:
        matched[STRATEGY_CANDLE_PSYCHOLOGY] = psychology_setup

    if _candle_pattern_match(direction, candles):
        matched[STRATEGY_CANDLE_PATTERNS] = "REVERSAL"

    return matched


def _candle_psychology_match(direction: str, candles: list[dict[str, float]]) -> bool:
    """Psicologia de velas: convicção (corpo forte) ou rejeição (pavio dominante)."""
    return _candle_psychology_setup(direction, candles) is not None


def _candle_psychology_setup(
    direction: str,
    candles: list[dict[str, float]],
) -> str | None:
    """Classifica a psicologia da vela sem misturar continuação e reversão."""
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    if direction == "CALL":
        decisive = last["close"] > last["open"] and body_ratio >= 0.58 and upper_wick_ratio <= 0.28
        rejection = (
            last["close"] >= last["open"]
            and lower_wick_ratio >= 0.42
            and body_ratio >= 0.18
            and upper_wick_ratio <= 0.3
        )
        if decisive:
            return "CONTINUATION"
        if rejection:
            return "REVERSAL"
        return None
    decisive = last["close"] < last["open"] and body_ratio >= 0.58 and lower_wick_ratio <= 0.28
    rejection = (
        last["close"] <= last["open"]
        and upper_wick_ratio >= 0.42
        and body_ratio >= 0.18
        and lower_wick_ratio <= 0.3
    )
    if decisive:
        return "CONTINUATION"
    if rejection:
        return "REVERSAL"
    return None


def _candle_pattern_match(direction: str, candles: list[dict[str, float]]) -> bool:
    """Padrões clássicos de vela alinhados à direção."""
    return (
        _is_engulfing(direction, candles)
        or _is_hammer_or_pin(direction, candles)
        or _is_shooting_star(direction, candles)
    )


def _is_engulfing(direction: str, candles: list[dict[str, float]]) -> bool:
    if len(candles) < 2:
        return False
    previous, last = candles[-2], candles[-1]
    prev_body = abs(previous["close"] - previous["open"])
    last_body = abs(last["close"] - last["open"])
    if last_body < max(prev_body * 1.05, 1e-9):
        return False
    if direction == "CALL":
        return (
            previous["close"] < previous["open"]
            and last["close"] > last["open"]
            and last["open"] <= previous["close"]
            and last["close"] >= previous["open"]
        )
    return (
        previous["close"] > previous["open"]
        and last["close"] < last["open"]
        and last["open"] >= previous["close"]
        and last["close"] <= previous["open"]
    )


def _is_hammer_or_pin(direction: str, candles: list[dict[str, float]]) -> bool:
    if not candles or direction != "CALL":
        return False
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    return (
        lower_wick_ratio >= 0.5
        and body_ratio <= 0.35
        and upper_wick_ratio <= 0.25
        and last["close"] >= last["open"]
    )


def _is_shooting_star(direction: str, candles: list[dict[str, float]]) -> bool:
    if not candles or direction != "PUT":
        return False
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    return (
        upper_wick_ratio >= 0.5
        and body_ratio <= 0.35
        and lower_wick_ratio <= 0.25
        and last["close"] <= last["open"]
    )


def _directional_count(direction: str, candles: list[dict[str, float]]) -> int:
    if direction == "CALL":
        return sum(1 for candle in candles if candle["close"] > candle["open"])
    if direction == "PUT":
        return sum(1 for candle in candles if candle["close"] < candle["open"])
    return 0


def _candle_direction(candle: dict[str, float]) -> str:
    if candle["close"] > candle["open"]:
        return "GREEN"
    if candle["close"] < candle["open"]:
        return "RED"
    return "DOJI"


def _candle_shape(candle: dict[str, float]) -> tuple[float, float, float]:
    candle_range = max(candle["max"] - candle["min"], 0)
    if candle_range <= 0:
        return 0.0, 0.0, 0.0
    body = abs(candle["close"] - candle["open"])
    upper_wick = max(0.0, candle["max"] - max(candle["open"], candle["close"]))
    lower_wick = max(0.0, min(candle["open"], candle["close"]) - candle["min"])
    return body / candle_range, upper_wick / candle_range, lower_wick / candle_range


def _price_action_setup(direction: str, candles: list[dict[str, float]]) -> str:
    if direction not in {"CALL", "PUT"} or len(candles) < 8:
        return "WEAK"
    if _is_reversal_setup(direction, candles):
        return "REVERSAL"
    if _near_support_resistance(direction, candles) and _current_candle_force_score(direction, candles[-1]) >= 14:
        return "SUPPORT_RESISTANCE"
    if _is_continuation_setup(direction, candles):
        return "CONTINUATION"
    return "WEAK"


def _price_action_setup_score(direction: SignalValue, candles: list[dict[str, float]]) -> int:
    setup = _price_action_setup(str(direction), candles)
    if setup == "REVERSAL":
        return 18
    if setup == "SUPPORT_RESISTANCE":
        return 15
    if setup == "CONTINUATION":
        return 12
    return 0


def _is_reversal_setup(direction: str, candles: list[dict[str, float]]) -> bool:
    if len(candles) < 8:
        return False
    previous = candles[-6:-1]
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    previous_direction = _direction_label(previous)
    previous_red = _directional_count("PUT", previous)
    previous_green = _directional_count("CALL", previous)
    near_level = _near_support_resistance(direction, candles)
    if direction == "CALL":
        return (
            previous_direction == "DOWN"
            and previous_red >= 3
            and last["close"] > last["open"]
            and body_ratio >= 0.4
            and lower_wick_ratio >= 0.2
            and upper_wick_ratio <= 0.35
            and near_level
        )
    return (
        previous_direction == "UP"
        and previous_green >= 3
        and last["close"] < last["open"]
        and body_ratio >= 0.4
        and upper_wick_ratio >= 0.2
        and lower_wick_ratio <= 0.35
        and near_level
    )


def _is_continuation_setup(direction: str, candles: list[dict[str, float]]) -> bool:
    if len(candles) < 5:
        return False
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    last_5 = candles[-5:]
    direction_label = _direction_label(last_5)
    directional = _directional_count(direction, last_5)
    if direction == "CALL":
        return (
            direction_label == "UP"
            and directional >= 3
            and last["close"] > last["open"]
            and body_ratio >= 0.55
            and upper_wick_ratio <= 0.3
        )
    return (
        direction_label == "DOWN"
        and directional >= 3
        and last["close"] < last["open"]
        and body_ratio >= 0.55
        and lower_wick_ratio <= 0.3
    )


def _near_support_resistance(direction: str, candles: list[dict[str, float]]) -> bool:
    context = _support_resistance_context(candles)
    if direction == "CALL":
        return bool(context.get("near_support"))
    if direction == "PUT":
        return bool(context.get("near_resistance"))
    return False


def _support_resistance_context(candles: list[dict[str, float]]) -> dict[str, Any]:
    """Região de suporte e resistência da série.

    Delega para ``sr_respect.build_zone``, que monta o nível por pivôs com
    contagem de toques. A construção antiga — extremo da janela de 23 velas —
    virou o fallback de lá, usado só quando não há velas ou pivôs suficientes.
    O contrato devolvido é o mesmo de antes, mais ``source`` e a contagem de
    toques de cada lado.

    Args:
        candles: Velas normalizadas em ordem cronológica.

    Returns:
        O dicionário da região; ver ``sr_respect.build_zone``.
    """
    return build_zone(candles)


def _has_level_conflict(direction: str, context: dict[str, Any]) -> bool:
    if direction == "CALL":
        return bool(context.get("near_resistance")) and not bool(context.get("near_support"))
    if direction == "PUT":
        return bool(context.get("near_support")) and not bool(context.get("near_resistance"))
    return False


def _level_rejection_confirmed(
    direction: str,
    candles: list[dict[str, float]],
    context: dict[str, Any] | None = None,
) -> bool:
    if direction not in {"CALL", "PUT"} or not candles:
        return False
    context = context or _support_resistance_context(candles)
    last = candles[-1]
    body_ratio, upper_wick_ratio, lower_wick_ratio = _candle_shape(last)
    if direction == "CALL":
        return (
            bool(context.get("near_support"))
            and last["close"] > last["open"]
            and body_ratio >= 0.35
            and lower_wick_ratio >= 0.2
            and upper_wick_ratio <= 0.4
        )
    return (
        bool(context.get("near_resistance"))
        and last["close"] < last["open"]
        and body_ratio >= 0.35
        and upper_wick_ratio >= 0.2
        and lower_wick_ratio <= 0.4
    )


def _alternating_last_3(candles: list[dict[str, float]]) -> bool:
    if len(candles) < 3:
        return False
    colors = []
    for candle in candles:
        if candle["close"] > candle["open"]:
            colors.append("GREEN")
        elif candle["close"] < candle["open"]:
            colors.append("RED")
        else:
            colors.append("DOJI")
    return colors[0] != "DOJI" and colors[1] != "DOJI" and colors[2] != "DOJI" and colors[0] != colors[1] and colors[1] != colors[2]


def _current_candle_force_score(direction: SignalValue, candle: dict[str, float]) -> int:
    candle_range = max(candle["max"] - candle["min"], 0)
    if candle_range <= 0:
        return 0
    body = abs(candle["close"] - candle["open"])
    body_ratio = body / candle_range
    if direction == "CALL" and candle["close"] <= candle["open"]:
        return 0
    if direction == "PUT" and candle["close"] >= candle["open"]:
        return 0
    if body_ratio >= 0.65:
        return 20
    if body_ratio >= 0.45:
        return 14
    if body_ratio >= 0.25:
        return 8
    return 0


def _wick_score(direction: SignalValue, candle: dict[str, float]) -> int:
    candle_range = max(candle["max"] - candle["min"], 0)
    if candle_range <= 0:
        return 0
    upper_wick = candle["max"] - max(candle["open"], candle["close"])
    lower_wick = min(candle["open"], candle["close"]) - candle["min"]
    against_ratio = (upper_wick if direction == "CALL" else lower_wick) / candle_range
    if against_ratio <= 0.2:
        return 10
    if against_ratio <= 0.35:
        return 6
    if against_ratio <= 0.45:
        return 3
    return 0


def _last_5_score(direction: SignalValue, candles: list[dict[str, float]]) -> int:
    if len(candles) < 5:
        return 0
    directional = _directional_count(direction, candles)
    if directional >= 4:
        return 10
    if directional == 3:
        return 6
    return 0


def _candle_colors(candles: list[dict[str, float]]) -> list[str]:
    colors = []
    for candle in candles:
        if candle["close"] > candle["open"]:
            colors.append("GREEN")
        elif candle["close"] < candle["open"]:
            colors.append("RED")
        else:
            colors.append("DOJI")
    return colors


def _direction_label(candles: list[dict[str, float]]) -> str:
    if not candles:
        return "NEUTRAL"
    first_open = candles[0]["open"]
    last_close = candles[-1]["close"]
    if last_close > first_open:
        return "UP"
    if last_close < first_open:
        return "DOWN"
    return "NEUTRAL"


def _is_sideways(candles: list[dict[str, float]], avg_range: float, last_price: float | None) -> bool:
    if len(candles) < 10 or not last_price:
        return True
    recent = candles[-10:]
    recent_range = max(candle["max"] for candle in recent) - min(candle["min"] for candle in recent)
    recent_range_pct = recent_range / abs(last_price) if last_price else 0.0
    avg_range_pct = avg_range / abs(last_price) if last_price else 0.0
    return recent_range_pct < 0.00035 or avg_range_pct < 0.00008


def _volatility_label(atr_pct: float) -> str:
    if atr_pct < 0.0001:
        return "LOW"
    if atr_pct > 0.0012:
        return "HIGH"
    return "NORMAL"


def _attach_empty_candle_analysis(
    signal: dict[str, Any],
    symbol: str,
    timeframe: str,
    candles: list[dict[str, float]],
) -> None:
    signal.update(
        {
            "used_strategies": ["Candle reading"],
            "candle_reading": "Candles insuficientes para leitura tecnica completa.",
            "entry_reason": signal.get("reason"),
            "block_reasons": ["CANDLES_UNAVAILABLE"],
            "metrics": {
                "symbol": symbol,
                "timeframe": timeframe,
                "candles_count": len(candles),
            },
        }
    )


def _attach_candle_analysis(
    signal: dict[str, Any],
    symbol: str,
    timeframe: str,
    candles: list[dict[str, float]],
    ema9: float,
    ema21: float,
    rsi: float,
    avg_range: float,
    atr_pct: float,
) -> None:
    last = candles[-1]
    candle_range = max(last["max"] - last["min"], 0)
    body = abs(last["close"] - last["open"])
    upper_wick = max(0.0, last["max"] - max(last["open"], last["close"]))
    lower_wick = max(0.0, min(last["open"], last["close"]) - last["min"])
    body_ratio = body / candle_range if candle_range else 0.0
    upper_wick_ratio = upper_wick / candle_range if candle_range else 0.0
    lower_wick_ratio = lower_wick / candle_range if candle_range else 0.0
    last_3 = candles[-3:]
    last_5 = candles[-5:]
    current_direction = "UP" if last["close"] > last["open"] else "DOWN" if last["close"] < last["open"] else "DOJI"
    direction = str(signal.get("signal") or "WAIT")
    price_action_setup = _price_action_setup(direction, candles)
    opposite_direction = "PUT" if direction == "CALL" else "CALL" if direction == "PUT" else "WAIT"
    level_context = _support_resistance_context(candles)
    metrics = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles_count": len(candles),
        "ema9": round(ema9, 6),
        "ema21": round(ema21, 6),
        "rsi14": round(rsi, 2),
        "current_candle_direction": current_direction,
        "current_candle_strength": round(body_ratio, 4),
        "candle_body": round(body, 6),
        "candle_range": round(candle_range, 6),
        "upper_wick": round(upper_wick, 6),
        "lower_wick": round(lower_wick, 6),
        "upper_wick_ratio": round(upper_wick_ratio, 4),
        "lower_wick_ratio": round(lower_wick_ratio, 4),
        "last_3_direction": _direction_label(last_3),
        "last_3_colors": _candle_colors(last_3),
        "last_5_direction": _direction_label(last_5),
        "last_5_colors": _candle_colors(last_5),
        "price_action_setup": price_action_setup,
        "near_support_resistance": _near_support_resistance(direction, candles),
        "near_support": bool(level_context.get("near_support")),
        "near_resistance": bool(level_context.get("near_resistance")),
        "support_level": (
            round(float(level_context["support"]), 6)
            if level_context.get("support") is not None
            else None
        ),
        "resistance_level": (
            round(float(level_context["resistance"]), 6)
            if level_context.get("resistance") is not None
            else None
        ),
        "support_distance": round(float(level_context.get("support_distance") or 0), 6),
        "resistance_distance": round(float(level_context.get("resistance_distance") or 0), 6),
        "level_conflict": _has_level_conflict(direction, level_context),
        "level_rejection_confirmed": _level_rejection_confirmed(direction, candles, level_context),
        "zigzag_reversal": _is_reversal_setup(direction, candles),
        "reversal_against": _price_action_setup(opposite_direction, candles) == "REVERSAL",
        "sideways": _is_sideways(candles, avg_range, signal.get("last_price")),
        "volatility": _volatility_label(atr_pct),
        "atr": round(avg_range, 6),
        "atr_pct": round(atr_pct, 8),
    }
    signal["metrics"] = metrics
    signal["price_action_setup"] = price_action_setup
    signal["last_3_direction"] = metrics["last_3_direction"]
    signal["last_3_colors"] = list(metrics["last_3_colors"])
    signal["last_5_direction"] = metrics["last_5_direction"]
    signal["level_conflict"] = metrics["level_conflict"]
    signal["reversal_against"] = metrics["reversal_against"]
    signal["sideways"] = metrics["sideways"]
    signal["volatility"] = metrics["volatility"]
    signal["used_strategies"] = list(ACTIVE_ENTRY_STRATEGIES)
    # Leitura técnica preservada para log e diagnóstico: o que o cliente vê é a
    # narrativa, mas quando algo dá errado é este texto que diz o que o motor
    # realmente mediu.
    signal["technical_reading"] = (
        f"{symbol} {timeframe}: price action {price_action_setup.lower()}, "
        f"candle atual {current_direction.lower()} com corpo de {body_ratio:.0%}, "
        f"últimas 3 velas {_direction_label(last_3).lower()}."
    )
    direcao_lida = str(signal.get("signal") or signal.get("direction") or "").upper()
    if direcao_lida in {"CALL", "PUT"}:
        # `_normalize_candle` não preserva o `from`, então semear pelo timestamp
        # deixava a abertura sempre igual. O fechamento da última vela muda a
        # cada vela e está sempre presente — serve de semente estável.
        ultimo = candles[-1] if candles and isinstance(candles[-1], dict) else {}
        marcador = ultimo.get("from") or f"{ultimo.get('close', timeframe)}:{len(candles)}"
        signal["candle_reading"] = monta_narrativa(
            symbol, direcao_lida, metrics, marcador=marcador
        )
    else:
        signal["candle_reading"] = signal["technical_reading"]
    signal["entry_reason"] = signal.get("reason")


def merge_multi_timeframe_signals(
    primary_timeframe: str,
    signals_by_tf: dict[str, dict[str, Any]],
    *,
    minimum_vote_confidence: int = MTF_VOTE_CONFIDENCE_MIN,
) -> dict[str, Any]:
    """
    Combina leituras de M1/M5/M15 e avalia a operação no timeframe configurado.

    Args:
        primary_timeframe: Timeframe da operação (M1, M5 ou M15).
        signals_by_tf: Mapa timeframe → sinal bruto de ``analyze_signal``.
        minimum_vote_confidence: Confiança mínima para um voto MTF qualificado.

    Returns:
        Sinal final com metadados de confluência multi-timeframe.

    Raises:
        ValueError: Se ``signals_by_tf`` estiver vazio.
    """
    if not signals_by_tf:
        raise ValueError("signals_by_tf nao pode ser vazio")

    normalized_primary = str(primary_timeframe or "M1").strip().upper()
    primary = dict(signals_by_tf.get(normalized_primary) or next(iter(signals_by_tf.values())))
    primary_direction = str(primary.get("signal") or "WAIT").upper()

    votes: dict[str, str] = {}
    for timeframe in ANALYSIS_TIMEFRAMES:
        signal = signals_by_tf.get(timeframe)
        if not isinstance(signal, dict):
            continue
        direction = str(signal.get("signal") or "WAIT").upper()
        if direction in {"CALL", "PUT", "WAIT"}:
            votes[timeframe] = direction

    for timeframe, signal in signals_by_tf.items():
        label = str(timeframe).strip().upper()
        if label in votes or not isinstance(signal, dict):
            continue
        direction = str(signal.get("signal") or "WAIT").upper()
        if direction in {"CALL", "PUT", "WAIT"}:
            votes[label] = direction

    qualified_votes = {
        timeframe: direction
        for timeframe, direction in votes.items()
        if direction in {"CALL", "PUT"}
        and bool((signals_by_tf.get(timeframe) or {}).get("trade_allowed"))
        and int((signals_by_tf.get(timeframe) or {}).get("confidence") or 0)
        >= minimum_vote_confidence
    }
    agreeing = [
        timeframe
        for timeframe, direction in qualified_votes.items()
        if primary_direction in {"CALL", "PUT"} and direction == primary_direction
    ]
    disagreeing = [
        timeframe
        for timeframe, direction in qualified_votes.items()
        if primary_direction in {"CALL", "PUT"}
        and direction in {"CALL", "PUT"}
        and direction != primary_direction
    ]
    confluence_count = len(agreeing)
    analyzed_tfs = [
        timeframe
        for timeframe in ANALYSIS_TIMEFRAMES
        if isinstance(signals_by_tf.get(timeframe), dict)
    ]
    mtf_data_complete = len(analyzed_tfs) >= 2

    summaries: list[str] = []
    for timeframe in ANALYSIS_TIMEFRAMES:
        signal = signals_by_tf.get(timeframe)
        if not isinstance(signal, dict):
            summaries.append(f"{timeframe}: indisponivel")
            continue
        direction = str(signal.get("signal") or "WAIT").upper()
        confidence = int(signal.get("confidence") or 0)
        summaries.append(f"{timeframe} {direction} ({confidence}%)")

    mtf_reading = (
        f"Analise multi-timeframe (operacao {normalized_primary}): "
        + "; ".join(summaries)
        + f". Confluencia {confluence_count}/{max(len(analyzed_tfs), 1)}."
    )

    result = dict(primary)
    result["timeframe"] = normalized_primary
    result["operation_timeframe"] = normalized_primary
    result["mtf_timeframes"] = list(ANALYSIS_TIMEFRAMES)
    result["mtf_votes"] = votes
    result["mtf_qualified_votes"] = qualified_votes
    result["mtf_agreeing"] = agreeing
    result["mtf_disagreeing"] = disagreeing
    result["mtf_confluence"] = confluence_count
    result["mtf_analyzed_count"] = len(analyzed_tfs)
    result["mtf_analysis"] = {
        timeframe: {
            "signal": str((signals_by_tf.get(timeframe) or {}).get("signal") or "WAIT"),
            "confidence": int((signals_by_tf.get(timeframe) or {}).get("confidence") or 0),
            "trend": (signals_by_tf.get(timeframe) or {}).get("trend"),
            "trade_allowed": bool((signals_by_tf.get(timeframe) or {}).get("trade_allowed")),
            "qualified_vote": timeframe in qualified_votes,
        }
        for timeframe in ANALYSIS_TIMEFRAMES
        if isinstance(signals_by_tf.get(timeframe), dict)
    }

    used = list(result.get("used_strategies") or [])
    if "Multi-timeframe 1m/5m/15m" not in used:
        used.append("Multi-timeframe 1m/5m/15m")
    result["used_strategies"] = used

    base_reading = str(result.get("candle_reading") or "").strip()
    result["candle_reading"] = f"{mtf_reading} {base_reading}".strip()

    blocked = [str(item) for item in (result.get("blocked_filters") or [])]
    approved = [str(item) for item in (result.get("approved_filters") or [])]

    if primary_direction not in {"CALL", "PUT"}:
        result["mtf_ready"] = False
        result["blocked_filters"] = blocked
        result["approved_filters"] = approved
        return result

    if not mtf_data_complete:
        if "MTF_DATA_UNAVAILABLE" not in blocked:
            blocked.append("MTF_DATA_UNAVAILABLE")
        result["trade_allowed"] = False
        result["mtf_ready"] = False
        result["blocked_filters"] = blocked
        result["approved_filters"] = approved
        return result

    m15_signal = signals_by_tf.get("M15") or {}
    m15_direction = str(m15_signal.get("signal") or "WAIT").upper()
    m15_trend = str(m15_signal.get("trend") or "").upper()
    opposite_direction = "PUT" if primary_direction == "CALL" else "CALL"
    opposite_trend = "DOWN" if primary_direction == "CALL" else "UP"
    raw_m15_conflict = (
        m15_direction == opposite_direction
        and (
            int(m15_signal.get("confidence") or 0) >= 60
            or m15_trend == opposite_trend
        )
    )
    strong_m15_conflict = "M15" in disagreeing or raw_m15_conflict
    if confluence_count >= MIN_MTF_CONFLUENCE and not strong_m15_conflict:
        if "MTF_CONFLUENCE" not in approved:
            approved.append("MTF_CONFLUENCE")
        blocked = [item for item in blocked if item != "MTF_CONFLUENCE"]
        result["mtf_ready"] = True
        confidence_weights = {"M1": 0.6, "M5": 0.25, "M15": 0.15}
        agreeing_weight = sum(confidence_weights.get(timeframe, 0.0) for timeframe in agreeing)
        if agreeing_weight > 0:
            result["confidence"] = min(
                CALIBRATED_CONFIDENCE_CAP,
                round(
                    sum(
                        int((signals_by_tf.get(timeframe) or {}).get("confidence") or 0)
                        * confidence_weights.get(timeframe, 0.0)
                        for timeframe in agreeing
                    )
                    / agreeing_weight
                ),
            )
            result["score"] = result["confidence"]
        if confluence_count == len(ANALYSIS_TIMEFRAMES):
            reason = str(result.get("reason") or "").strip()
            boost_note = "Confluencia total M1/M5/M15."
            result["reason"] = f"{reason} {boost_note}".strip()
            result["entry_reason"] = result["reason"]
            result["signal_explanation"] = result["reason"]
            result["narrator_text"] = result["reason"]
    else:
        if "MTF_CONFLUENCE" not in blocked:
            blocked.append("MTF_CONFLUENCE")
        approved = [item for item in approved if item != "MTF_CONFLUENCE"]
        result["trade_allowed"] = False
        result["mtf_ready"] = False
        result["quality_reason"] = (
            "Confluencia insuficiente entre graficos de 1m, 5m e 15m."
        )
        reason = str(result.get("reason") or "").strip()
        result["reason"] = (
            f"{reason} Bloqueado: precisa de pelo menos {MIN_MTF_CONFLUENCE} "
            f"timeframes qualificados alinhados (atual {confluence_count})."
        ).strip()
        result["entry_reason"] = result["reason"]
        result["signal_explanation"] = result["reason"]
        result["narrator_text"] = result["reason"]

    if strong_m15_conflict:
        if "MTF_HIGHER_TF_CONFLICT" not in blocked:
            blocked.append("MTF_HIGHER_TF_CONFLICT")
        result["trade_allowed"] = False
        result["mtf_ready"] = False
        if not result.get("quality_reason"):
            result["quality_reason"] = "Tendencia de 15m conflita com a operacao."

    result["blocked_filters"] = blocked
    result["approved_filters"] = approved
    return result
