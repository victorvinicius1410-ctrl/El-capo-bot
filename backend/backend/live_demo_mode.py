"""Modo LIVE: cadência de demonstração para transmissão ao vivo.

## O que é, e o que não é

O El Capo, na configuração normal, entra poucas vezes por hora — o portão de
qualidade barra a maioria das velas. Numa live isso é ruim de assistir: o
apresentador fica minutos olhando "analisando" sem nada acontecer.

Este modo existe só para resolver esse problema de **ritmo**. Ele afrouxa o
portão para o robô entrar com muito mais frequência em OTC. É uma estratégia
deliberadamente **fraca**: entra quase sempre que há uma direção computável.

**Ele não melhora o resultado — piora.** Os ativos OTC da BullEx foram medidos
em 05/09/2026 sobre 282.714 velas e são indistinguíveis de ruído (entropia de
4,0000 bits de 4,0000 possíveis; teste de corridas z = −0,29). Qualquer
estratégia ali acerta ~50%, e com payout de 87% isso é −6% do valor apostado.
**Mais operações = perder mais rápido.** Quem ligar isto numa live vai ver mais
entradas e mais derrotas, na mesma proporção de sempre.

Por isso três decisões deliberadas:

1. **Só conta de marketing.** `apply_live_demo` exige o flag, e o endpoint que
   liga o modo recusa quem não é `account_type=marketing`.
2. **Só OTC.** Em mercado aberto existe vantagem real (a REV-Z) e afrouxar o
   portão ali seria jogar fora a única coisa que funciona.
3. **Marcado no histórico** com o booleano `live_demo`. A auditoria de 03/09
   mostrou que 1.220 operações de contas de marketing estavam misturadas com
   as reais e inflavam todo relatório. Nenhuma operação deste modo pode entrar
   numa medição de estratégia sem estar identificada.

   A marca ficava também em `strategy_key = "LIVE_DEMO"`, e essa **saiu**: a
   chave é campo de tela. Ela aparecia como badge no Histórico do cliente e,
   pior, `build_strategy_narration` (em `main.py`) abre a lista de estratégias
   faladas com ela — o robô dizia em voz alta *"Estratégia utilizada:
   LIVE_DEMO, EMA9/EMA21, RSI…"*. Agora a chave é a real do setup medido e a
   auditoria filtra por `live_demo = true`, que não vai a lugar nenhum na
   interface. Toda consulta que precisar separar essas operações usa o
   booleano — ver `ESTRATEGIA.md`.

## A marca é interna, a narração não é

A marca do item 3 existe para a auditoria, não para quem assiste. A versão
anterior escrevia "entrada de demonstração — Modo LIVE ligado, portão
afrouxado" nos MESMOS campos que o overlay lê em voz alta (`narrator_text`,
`entry_reason`, `analysis_detail`) e batizava a estratégia de "Modo LIVE
(demonstração)" no painel e no histórico. Na transmissão isso aparecia como o
robô anunciando que aquilo não era operação de verdade.

Agora a narração da entrada do modo LIVE é montada como a de qualquer outra:
sai da leitura que o motor já fez das velas (`candle_reading`, produzido por
`narrativa_analise` a partir das métricas reais) e leva um nome de estratégia
do mesmo vocabulário das nomeadas. **Nada é inventado** — o texto continua
descrevendo só o que foi medido, e como a narrativa gradua o fecho pelo que
achou, um setup magro continua saindo com texto de setup magro.

O que NÃO muda: o booleano `live_demo`, o `quality_reason` ``OK_LIVE_DEMO``
(que não é lido por nenhuma tela) e a linha `[LIVE_DEMO_RELEASE]` no log. A
auditoria continua conseguindo separar essas operações das reais.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.named_strategies import (
    STRATEGY_CANDLE_FLOW,
    STRATEGY_CONTINUATION,
    STRATEGY_EXHAUSTION_REVERSAL,
    STRATEGY_LABELS,
)

logger = logging.getLogger("backend-gateway")

STRATEGY_LIVE_DEMO = "LIVE_DEMO"

# Bloqueios que o modo LIVE nunca dispensa: são os que protegem a execução em
# si, não a qualidade do sinal. Afrouxar qualquer um destes não gera cadência,
# gera ordem recusada pela corretora.
LIVE_NON_WAIVABLE = frozenset(
    {
        "ACCOUNT_DISCONNECTED",
        "STOP_WIN_HIT",
        "STOP_LOSS_HIT",
        "ACTIVE_CLOSED",
        "ACTIVE_SUSPENDED",
        "PAYOUT_UNAVAILABLE",
        "OPERATION_IN_PROGRESS",
        "CANDLES_UNAVAILABLE",
        "MIN_PAYOUT",
        # A região de suporte e resistência NÃO é filtro de qualidade e o modo
        # LIVE não a dispensa. Foi a violação relatada pelo dono em 09/09/2026:
        # `20:52:24 LIVE_DEMO_RELEASE NZDUSD-OTC PUT
        #  barrados=LEVEL_CONFLICT,LEVEL_REJECTION,SR_ZONE` seguido de
        # `20:53:06 ORDER_ACCEPTED order_id=14247169254` — PUT colado no
        # suporte, ordem real 42 s depois. Em 48 h foram 4 dessas em 6
        # liberações. Cadência de transmissão não justifica operar contra o
        # nível; o modo continua afrouxando tudo o que é qualidade de setup.
        "SR_ZONE",
        "LEVEL_CONFLICT",
        "LEVEL_REJECTION",
        # Pavio excessivo (11/09): regra do dono, também não é cadência.
        "WICK_EXCESS",
    }
)

# Confiança exibida. Curta e honesta: não há convicção nenhuma por trás de uma
# entrada de demonstração em série aleatória.
LIVE_CONFIDENCE = 60


def live_min_confidence(minimo_usuario: int, candidato: dict[str, Any] | None) -> int:
    """Rebaixa o piso de confiança quando a entrada veio do modo LIVE.

    Mesma armadilha das duas escalas que já pegou a REV-Z: o modo LIVE pontua
    ``LIVE_CONFIDENCE`` (60, e de propósito baixo — não há convicção nenhuma
    numa entrada de demonstração), enquanto o ``min_confidence`` do painel vem
    80 por padrão. ``60 >= 80`` é sempre falso, então **toda** entrada do modo
    LIVE era descartada em ``NO_OPPORTUNITY`` e o robô continuava lento — que
    é exatamente o problema que o modo existe para resolver.

    Args:
        minimo_usuario: ``state.min_confidence`` configurado no painel.
        candidato: Sinal avaliado. Só entradas marcadas ``live_demo`` mudam.

    Returns:
        O piso a aplicar: ``LIVE_CONFIDENCE`` para entrada de demonstração,
        senão o mínimo do usuário intacto.
    """
    if isinstance(candidato, dict) and candidato.get("live_demo") is True:
        return min(int(minimo_usuario), LIVE_CONFIDENCE)
    return int(minimo_usuario)


def is_otc_symbol(symbol: str) -> bool:
    """Indica se o ativo é sintético da corretora (sufixo ``-OTC``)."""
    return str(symbol or "").strip().upper().endswith("-OTC")


def live_demo_allows(signal: dict[str, Any], symbol: str) -> tuple[bool, str | None]:
    """Diz se o modo LIVE pode liberar esta entrada, e por que não quando não pode.

    Args:
        signal: Sinal já filtrado pelo motor clássico.
        symbol: Ativo analisado.

    Returns:
        Par ``(pode, motivo)``. ``motivo`` é ``None`` quando pode.
    """
    if not is_otc_symbol(symbol):
        return False, "LIVE_SOMENTE_OTC"
    direction = str(signal.get("signal") or signal.get("direction") or "").upper()
    if direction not in {"CALL", "PUT"}:
        return False, "LIVE_SEM_DIRECAO"
    bloqueados = [b for b in (signal.get("blocked_filters") or []) if b in LIVE_NON_WAIVABLE]
    if bloqueados:
        return False, ",".join(bloqueados)
    return True, None


def narracao_normal(
    signal: dict[str, Any],
    symbol: str,
    direction: str,
) -> tuple[str, str, str, str, str]:
    """Monta a narração da entrada do modo LIVE como a de qualquer outra.

    Nada aqui é inventado: o rótulo e o resumo saem do ``price_action_setup``
    que o motor já mediu, e a leitura das velas que o cliente ouve continua
    sendo a de sempre — ``candle_reading``, produzido por ``narrativa_analise``
    a partir das métricas, que esta função nem toca. Só o que denunciava o modo
    (o "entrada de demonstração", o "portão afrouxado", o nome "Modo LIVE") sai
    de cena; a marca de auditoria fica no booleano ``live_demo`` e no log.

    Args:
        signal: Sinal já montado e filtrado, com ``metrics``.
        symbol: Ativo analisado.
        direction: ``CALL`` ou ``PUT``.

    Returns:
        ``(strategy_key, strategy_name, strategy_summary, texto, speech_preview)``.
        A chave é a do setup real, não ``LIVE_DEMO``: ela é campo de tela (badge
        do Histórico) e abre a lista de estratégias faladas.
    """
    metricas = signal.get("metrics") if isinstance(signal.get("metrics"), dict) else {}
    setup = str(metricas.get("price_action_setup") or signal.get("price_action_setup") or "").upper()
    tf = str(signal.get("timeframe") or "").strip()
    sufixo_tf = f" ({tf})" if tf else ""
    lado = "compra" if direction == "CALL" else "venda"

    if setup == "REVERSAL":
        chave = STRATEGY_EXHAUSTION_REVERSAL
        resumo = f"Desenho de reversão na última vela{sufixo_tf}."
        fala = f"Vou de {direction} pelo desenho de reversão."
    elif setup == "CONTINUATION":
        chave = STRATEGY_CONTINUATION
        resumo = f"Continuação do movimento em andamento{sufixo_tf}."
        fala = f"Vou de {direction} seguindo a continuação do movimento."
    else:
        chave = STRATEGY_CANDLE_FLOW
        resumo = f"Fluxo das últimas velas do lado da {lado}{sufixo_tf}."
        fala = f"Vou de {direction} seguindo o fluxo das velas."

    rotulo = STRATEGY_LABELS[chave]

    # Mesma forma que `named_strategies._write_strategy_fields` dá a uma entrada
    # nomeada: o detalhe é "Estratégia: X. Direção Y. <resumo>", e a leitura das
    # velas (`candle_reading`, montada por `narrativa_analise`) fica onde
    # sempre esteve. Repetir a narrativa nos dois campos, como esta função fazia
    # antes, produzia na voz "Motivo da entrada: <narrativa>. Leitura das velas:
    # <a mesma narrativa>." — duplicação que nenhuma outra entrada tem, e que
    # por isso mesmo denunciava a origem.
    texto = f"Estratégia: {rotulo}. Direção {direction}. {resumo}"
    return chave, rotulo, resumo, texto, fala


def apply_live_demo(
    signal: dict[str, Any],
    symbol: str,
    *,
    live_enabled: bool,
) -> dict[str, Any]:
    """Libera a entrada para dar cadência à transmissão, quando o modo está ligado.

    Roda por último, depois de todas as estratégias: se alguma delas já aprovou
    a entrada, não há nada a fazer. Só age quando o portão barrou — que é
    justamente o caso chato numa live.

    Args:
        signal: Sinal já montado e filtrado.
        symbol: Ativo analisado.
        live_enabled: Flag do usuário (só conta de marketing consegue ligar).

    Returns:
        O sinal, liberado e marcado como ``LIVE_DEMO`` quando aplicável.
    """
    if not live_enabled or signal.get("trade_allowed"):
        return signal

    pode, motivo = live_demo_allows(signal, symbol)
    if not pode:
        signal["live_demo_blocked"] = motivo
        return signal

    direction = str(signal.get("signal") or signal.get("direction") or "").upper()
    chave, rotulo, resumo, texto, fala = narracao_normal(signal, symbol, direction)
    signal.update(
        {
            "trade_allowed": True,
            # `quality_reason` é log e diagnóstico — nenhuma tela lê. É aqui que
            # a marca do modo continua visível para quem depura.
            "quality_reason": f"OK_{STRATEGY_LIVE_DEMO}",
            "strategy_key": chave,
            "strategy_name": rotulo,
            "strategy_summary": resumo,
            "confidence": LIVE_CONFIDENCE,
            "score": LIVE_CONFIDENCE,
            "strategy_score": LIVE_CONFIDENCE,
            "live_demo": True,
            "entry_reason": texto,
            "signal_explanation": texto,
            "analysis_detail": texto,
            "narrator_text": texto,
            "speech_preview": fala,
        }
    )
    logger.info(
        "[LIVE_DEMO_RELEASE] symbol=%s direction=%s barrados=%s",
        symbol,
        direction,
        ",".join(signal.get("blocked_filters") or []) or "-",
    )
    return signal


def is_live_demo(candidato: dict[str, Any] | None) -> bool:
    """Indica se o candidato foi liberado pelo modo LIVE."""
    return isinstance(candidato, dict) and candidato.get("live_demo") is True


def live_demo_passa_portao(
    candidato: dict[str, Any],
    *,
    min_payout: float,
    minimo_confianca: int,
) -> tuple[bool, str | None]:
    """Portão da entrada de demonstração: só o que impede a ordem de existir.

    O motor já dispensou os filtros de qualidade em ``apply_live_demo``. O
    portão do ciclo, porém, refazia esse mesmo veto por conta própria: ele
    cruza ``blocked_filters`` com o conjunto crítico e recalcula os
    ``*_HARD_BLOCK`` a partir do corpo da vela e das últimas cores. Como
    ``apply_live_demo`` deixa ``blocked_filters`` intacto (de propósito, para
    a auditoria saber o que foi dispensado), toda entrada do modo morria ali.

    Medido em produção em 07/09/2026 sobre 24h de log: **138 de 138**
    liberações LIVE bloqueadas, nenhuma virou ordem. Quem mais matava:
    PRICE_ACTION_SETUP (107), SR_ZONE (83), LEVEL_REJECTION (81),
    LEVEL_CONFLICT (79) — todos filtros de qualidade, exatamente o que o modo
    existe para afrouxar.

    Aqui ficam só os bloqueios de execução: os que fariam a corretora recusar
    a ordem ou que protegem a banca (stop, ativo fechado, payout, operação em
    andamento). Esses o modo LIVE nunca dispensa.

    Args:
        candidato: Sinal já marcado ``live_demo``.
        min_payout: Payout mínimo configurado pelo usuário.
        minimo_confianca: Piso já rebaixado por ``live_min_confidence``.

    Returns:
        Par ``(passa, motivo)``. ``motivo`` é ``None`` quando passa.
    """
    bloqueados = {str(item) for item in (candidato.get("blocked_filters") or [])}
    impeditivos = sorted(bloqueados & LIVE_NON_WAIVABLE)
    if impeditivos:
        return False, ",".join(impeditivos)

    try:
        payout = float(candidato.get("payout") or 0)
    except (TypeError, ValueError):
        return False, "PAYOUT_INVALIDO"
    if payout < float(min_payout):
        return False, f"PAYOUT_ABAIXO_DO_MINIMO({payout:g}<{float(min_payout):g})"

    bruto = (
        candidato.get("strategy_score")
        if candidato.get("strategy_score") is not None
        else candidato.get("score")
        if candidato.get("score") is not None
        else candidato.get("confidence")
    )
    try:
        pontuacao = int(bruto or 0)
    except (TypeError, ValueError):
        return False, "PONTUACAO_INVALIDA"
    if pontuacao < int(minimo_confianca):
        return False, f"PONTUACAO_ABAIXO_DO_PISO({pontuacao}<{int(minimo_confianca)})"

    return True, None


def live_demo_restaura(sinal: dict[str, Any]) -> dict[str, Any]:
    """Reafirma a liberação do modo LIVE depois de qualquer reavaliação.

    O sinal atravessa vários estágios que **recalculam** ``trade_allowed`` a
    partir de ``blocked_filters``, sem saber que o modo LIVE já dispensou
    aqueles filtros. Cada um deles derrubava a entrada de novo:

    - ``apply_strategy_guard`` cruza os filtros com o conjunto crítico
    - ``confirm_ranked_candidates_multi_timeframe`` barra por confluência
    - o portão do ciclo (tratado em ``live_demo_passa_portao``)

    Em 08/09/2026 o sintoma era `[LIVE_DEMO_RELEASE]` seguido de
    `[NO_OPPORTUNITY]` **sem** nenhum `[LIVE_DEMO_GATE_BLOCK]`: a entrada
    morria antes do portão, com ``trade_allowed`` já em ``False``.

    Esta função é idempotente e só age em sinal marcado. Ela nunca dispensa
    ``LIVE_NON_WAIVABLE`` — bloqueio de execução continua derrubando.

    Args:
        sinal: Sinal ou candidato, possivelmente marcado ``live_demo``.

    Returns:
        O mesmo dicionário, com a liberação e a escala do modo reafirmadas.
    """
    if not is_live_demo(sinal):
        return sinal

    impeditivos = sorted(
        {str(item) for item in (sinal.get("blocked_filters") or [])} & LIVE_NON_WAIVABLE
    )
    if impeditivos:
        sinal["trade_allowed"] = False
        sinal["quality_reason"] = ",".join(impeditivos)
        return sinal

    sinal["trade_allowed"] = True
    sinal["quality_reason"] = f"OK_{STRATEGY_LIVE_DEMO}"
    # A escala do modo é própria e curta. Os estágios acima descontam
    # penalidades da confiança e devolvem um número da escala 0–100 do motor
    # clássico, que o piso de `live_min_confidence` não reconhece.
    sinal["confidence"] = LIVE_CONFIDENCE
    sinal["score"] = LIVE_CONFIDENCE
    sinal["strategy_score"] = LIVE_CONFIDENCE
    return sinal
