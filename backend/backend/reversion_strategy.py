"""Estratégia REV-Z: reversão à média em desvio extremo.

Descoberta em 2026-08-31 medindo 540.000 velas M1 reais de 9 pares de forex
(52 dias, 4 janelas independentes). É a única vantagem que sobreviveu a uma
varredura ampla; tudo o mais testado deu 50%.

## O efeito

Distância do fechamento até a média das últimas velas, em desvios-padrão
(``z``), contra a cor da vela SEGUINTE — gradiente monotônico, estável nas
quatro janelas:

    z = -3  ->  53,67% verde        z = +1  ->  49,76% verde
    z = -2  ->  51,80% verde        z = +2  ->  49,00% verde
    z = -1  ->  51,11% verde        z = +3  ->  47,78% verde
    z =  0  ->  50,49% verde

Preço esticado para baixo tende a voltar para cima e vice-versa. É reversão de
curto prazo, um efeito conhecido em forex — não é padrão gráfico.

## Por que só no mercado ABERTO

A mesma varredura sobre 164.000 velas OTC não achou nada: 50% em todas as
famílias (cor, pavio, corpo, volatilidade, sequência, hora, gap de abertura,
autocorrelação nos lags 1..120, dígitos do preço). A série sintética OTC se
comporta como passeio aleatório. **Esta estratégia não tem vantagem em OTC** e
o portão recusa ativos ``-OTC`` de propósito.

## Números honestos da configuração padrão (média 60, |z| >= 4,0)

n=1024, acerto **56,54% ± 3,06**, ~20 operações/dia sobre 9 pares. Por janela:
60,6% / 53,1% / 55,0% / 57,1%. Empate no mercado aberto (payout ~85%): 54,05%.

**Ressalvas que não podem ser perdidas:**
- Os parâmetros foram escolhidos testando 12 combinações nos MESMOS dados. Parte
  do 56,5% é seleção. O valor real esperado é mais baixo.
- A corretora só guarda ~55 dias de histórico, então não existe holdout externo.
  A única validação honesta é para a frente, em demo.
- Não chega perto de 65%. Ver ``docs/ESTRATEGIA_REVZ.md``.
"""

from __future__ import annotations

import logging
import os
from statistics import fmean, pstdev
from typing import Any

logger = logging.getLogger("backend-gateway")

# Liga a REV-Z no lugar do motor clássico. É CONFIGURAÇÃO DE AMBIENTE, não
# constante de código: ligar altera o significado de `analyze_signal` para todo
# o processo, então o padrão da biblioteca é off e quem liga é o `.env` do
# ambiente (`REVZ_ENABLED=true` no sistema 02).
#
# Desde 10/09/2026 ela vale SÓ para ativo de mercado aberto: em `-OTC` o
# override devolve o sinal do motor clássico intacto (ver `apply_revz_override`
# em `signal_engine.py`), então ligar a REV-Z não mexe em conta OTC. Antes disso
# ela recusava OTC com WAIT e parava qualquer conta que não fosse OPEN.
REVZ_ENABLED = os.getenv("REVZ_ENABLED", "false").strip().lower() in {"1", "true", "yes"}

# Nº de velas fechadas da média de referência.
# Medido em 05/09 sobre 477.316 velas (9 pares, 52,9 dias — praticamente todo o
# histórico que a corretora guarda), variando a janela com |z| >= 4,5:
#   janela  30 -> 54,12% no holdout,  3,2 ops/dia
#   janela  60 -> 60,00% no holdout,  7,5 ops/dia   (era o valor antigo)
#   janela  90 -> 60,33% no holdout,  9,8 ops/dia
#   janela 120 -> 60,70% no holdout, 11,3 ops/dia
# Janela maior dá MAIS operações e MAIS acerto — o contrário do que se esperava.
# Em 2 horas o preço se afasta muito mais da média do que em 1, então o
# numerador do z cresce mais rápido que o desvio no denominador.
#
# Decisivo: com janela 120 as QUATRO células do holdout duplo (ativo × período)
# ficam acima do empate de 54,05% — 56,82 / 69,85 / 57,96 / 64,06. Com janela 60
# uma delas dava 47,11%. Geral: 61,64% IC95[57,74–65,54] em 597 operações.
REVZ_LOOKBACK = int(os.getenv("REVZ_LOOKBACK", "120"))
# Desvios-padrão para disparar. Escolhido por ter a maior amostra entre as
# configurações com as quatro janelas acima do empate.
# Confirmado fora da amostra em 2026-09-03, com 220.574 velas M1 novas de 9
# pares (24 dias), separando os 4 pares de ajuste dos 5 de holdout:
#   |z| >= 3,0 -> 52,36% / 51,92%   (abaixo do empate de 54,05%)
#   |z| >= 3,5 -> 55,01% / 56,12%   (~45 ops/dia nos 9 pares)
#   |z| >= 4,0 -> 59,49% / 58,64%   (~15 ops/dia nos 9 pares)
# O gradiente é monotônico e replica nos pares que não foram usados para
# escolher nada — é a mesma vantagem medida em 31/08, agora reconfirmada em
# dados coletados depois. 4,0 continua o padrão por ser o mais conservador;
# `REVZ_THRESHOLD=3.5` triplica o volume e ainda fica acima do empate.
REVZ_THRESHOLD = float(os.getenv("REVZ_THRESHOLD", "4.0"))
# Mínimo de velas para sequer calcular.
REVZ_MIN_CANDLES = REVZ_LOOKBACK + 5

# Escala de confiança da estratégia. É ESCALA PRÓPRIA, deliberadamente curta e
# NÃO comparável ao 0–100 do motor clássico: quem decide operar é o |z|, e a
# confiança só reflete quão fundo na cauda o preço está. Quem compara o mínimo
# do usuário (calibrado para o motor clássico) contra estes valores barra 100%
# dos sinais — ver `apply_strategy_guard` em `main.py`.
REVZ_CONFIDENCE_FLOOR = 55
REVZ_CONFIDENCE_MAX = 75


def is_otc_symbol(symbol: str) -> bool:
    """Indica se o ativo é sintético da corretora (sufixo ``-OTC``)."""
    return str(symbol or "").strip().upper().endswith("-OTC")


def reversion_zscore(
    closes: list[float],
    *,
    lookback: int = REVZ_LOOKBACK,
) -> float | None:
    """
    Distância do último fechamento até a média, em desvios-padrão.

    Args:
        closes: Fechamentos em ordem cronológica, o último sendo o mais recente.
        lookback: Quantas velas entram na média.

    Returns:
        O ``z``, ou ``None`` se não há velas suficientes ou o desvio é zero
        (série travada — acontece em feriado e em ativo parado).
    """
    if len(closes) < lookback + 1:
        return None
    janela = [float(x) for x in closes[-(lookback + 1):]]
    media = fmean(janela)
    desvio = pstdev(janela)
    if desvio <= 0:
        return None
    return (janela[-1] - media) / desvio


def revz_direction(
    z: float | None,
    *,
    threshold: float = REVZ_THRESHOLD,
) -> str | None:
    """
    Direção da aposta a partir do ``z``.

    Preço esticado para BAIXO (``z`` negativo) aposta na volta para cima
    (``CALL``), e vice-versa. É reversão, não continuação — o oposto do que o
    motor clássico faz.

    Args:
        z: Desvio em desvios-padrão, de ``reversion_zscore``.
        threshold: Módulo mínimo para operar.

    Returns:
        ``"CALL"``, ``"PUT"``, ou ``None`` se o preço não está esticado.
    """
    if z is None:
        return None
    if z <= -threshold:
        return "CALL"
    if z >= threshold:
        return "PUT"
    return None


def revz_evaluate(
    symbol: str,
    closes: list[float],
    *,
    lookback: int = REVZ_LOOKBACK,
    threshold: float = REVZ_THRESHOLD,
) -> dict[str, Any]:
    """
    Avalia o ativo e devolve o veredito da REV-Z.

    Args:
        symbol: Ativo analisado.
        closes: Fechamentos das velas FECHADAS, em ordem cronológica.
        lookback: Velas na média de referência.
        threshold: Módulo de ``z`` para operar.

    Returns:
        Dicionário com ``direction`` (``None`` quando não opera), ``z``,
        ``blocked`` (motivo da recusa) e os parâmetros usados.
    """
    resultado: dict[str, Any] = {
        "strategy": "REV-Z",
        "direction": None,
        "z": None,
        "blocked": None,
        "lookback": lookback,
        "threshold": threshold,
    }
    if is_otc_symbol(symbol):
        # Medido: zero vantagem em 164.000 velas OTC. Operar aqui é apostar.
        resultado["blocked"] = "REVZ_OTC_SEM_VANTAGEM"
        return resultado
    if len(closes) < lookback + 1:
        resultado["blocked"] = "REVZ_VELAS_INSUFICIENTES"
        return resultado
    z = reversion_zscore(closes, lookback=lookback)
    resultado["z"] = z
    if z is None:
        resultado["blocked"] = "REVZ_SERIE_SEM_VARIACAO"
        return resultado
    direcao = revz_direction(z, threshold=threshold)
    if direcao is None:
        resultado["blocked"] = "REVZ_SEM_DESVIO_EXTREMO"
        return resultado
    resultado["direction"] = direcao
    return resultado


def revz_confidence(z: float | None, *, threshold: float = REVZ_THRESHOLD) -> int:
    """
    Confiança exibida, derivada de quão fundo na cauda o preço está.

    Deliberadamente conservadora: o acerto medido no disparo é ~56%, então a
    escala vai de 55 a 75 e nunca chega perto de 100. Confiança 100 num sinal
    de moeda foi um dos defeitos apontados na auditoria do sistema 01.
    """
    if z is None:
        return 0
    excesso = max(0.0, abs(z) - threshold)
    return int(round(min(float(REVZ_CONFIDENCE_MAX), REVZ_CONFIDENCE_FLOOR + excesso * 10.0)))


# ---------------------------------------------------------------------------
# REV-Z como motor do MERCADO ABERTO (10/09/2026)
#
# Pedido do dono: o mercado aberto passa a ser analisado pela REV-Z, e o OTC
# fica exatamente como está (motor clássico + Vertex). As peças abaixo são o
# que faltava para a estratégia simulada ser a estratégia executada.
# ---------------------------------------------------------------------------

STRATEGY_REVZ = "REVZ"

# O robô NÃO entra do jeito que a simulação assume. A simulação calcula o z no
# fechamento da vela i e entra na abertura da i+1. O robô analisa no segundo
# 5-20 da vela N — enxergando a vela N ainda em formação como se fosse a última
# fechada — e só entra na abertura da N+1, uns 50 s depois (medido em 10/09 no
# EURJPY das 11:26: `last_3_colors` RED,RED,RED com corpo 0,0045 e range
# 0,0055 só batem com a vela 11:26 aos 7 s; fechada ela foi VERDE).
#
# Por isso a decisão é em dois tempos:
# 1. na análise, `REVZ_NOMINATE_THRESHOLD` só INDICA o candidato;
# 2. no disparo, `revz_confirm_at_entry` refaz o z com a vela que acabou de
#    FECHAR e exige `REVZ_THRESHOLD` na mesma direção.
# A ordem que sai satisfaz exatamente a condição simulada: |z| no fechamento da
# vela anterior >= limiar, entrada na abertura da seguinte.
REVZ_NOMINATE_THRESHOLD = float(os.getenv("REVZ_NOMINATE_THRESHOLD", "3.5"))

# Bloqueios que a REV-Z respeita. São os de EXECUÇÃO e de proteção de banca —
# os que fariam a corretora recusar a ordem ou que existem por causa do stop.
# Os filtros de QUALIDADE do motor clássico (EMA, RSI, alinhamento das 3
# últimas velas, corpo da vela, memória de padrões por hora...) ficam de fora de
# propósito: o clássico segue a vela e a REV-Z aposta contra ela, então quase
# todo filtro dele é, por construção, um veto à tese dela. A simulação dos 60%
# não passou por nenhum deles.
REVZ_NON_WAIVABLE = frozenset(
    {
        "DISCONNECTED",
        "ACCOUNT_DISCONNECTED",
        "STOP_WIN_HIT",
        "STOP_LOSS_HIT",
        "ACTIVE_CLOSED",
        "ACTIVE_SUSPENDED",
        "ACTIVE_COOLDOWN",
        "ASSET_COOLDOWN",
        "PAYOUT_COOLDOWN",
        "PAYOUT_UNAVAILABLE",
        "OPERATION_IN_PROGRESS",
        "CANDLES_UNAVAILABLE",
        "STALE_MARKET_DATA",
        "MIN_PAYOUT",
        "MIN_CONFIDENCE",
        "GLOBAL_LOSS_COOLDOWN",
        "REPEAT_ENTRY",
        "SR_ZONE",
        "LEVEL_CONFLICT",
        "WICK_EXCESS",
    }
)


def is_revz_candidate(candidato: dict[str, Any] | None) -> bool:
    """Indica se a direção do candidato foi decidida pela REV-Z."""
    if not isinstance(candidato, dict):
        return False
    veredito = candidato.get("revz")
    return isinstance(veredito, dict) and str(veredito.get("direction") or "").upper() in {"CALL", "PUT"}


def revz_min_confidence(minimo_usuario: int) -> int:
    """Piso de confiança para um candidato da REV-Z.

    Desce até o CHÃO da escala própria (55), não até o teto (75). A confiança
    típica de um disparo fica perto do chão — com o piso no teto, 100% dos
    sinais morriam em silêncio. É a mesma armadilha que pegou a Vertex em 09/09.
    Nunca AUMENTA o piso de quem configurou menos.
    """
    return min(int(minimo_usuario), REVZ_CONFIDENCE_FLOOR)


def revz_passa_portao(
    candidato: dict[str, Any],
    *,
    min_payout: float,
    minimo_confianca: int,
) -> tuple[bool, str | None]:
    """Portão do ciclo para um candidato da REV-Z: só o que impede a ordem.

    Args:
        candidato: Candidato com veredito REV-Z com direção.
        min_payout: Payout mínimo configurado pelo usuário.
        minimo_confianca: Mínimo do painel (escala do motor clássico).

    Returns:
        ``(liberado, motivo)``; ``motivo`` é ``None`` quando liberado.
    """
    bloqueados = {str(item) for item in (candidato.get("blocked_filters") or [])}
    impeditivos = sorted(bloqueados & REVZ_NON_WAIVABLE)
    if impeditivos:
        return False, impeditivos[0]
    try:
        payout = float(candidato.get("payout") or 0)
        score = int(
            candidato.get("strategy_score")
            if candidato.get("strategy_score") is not None
            else candidato.get("confidence")
            or 0
        )
    except (TypeError, ValueError):
        return False, "REVZ_CANDIDATO_INVALIDO"
    if payout < float(min_payout):
        return False, "MIN_PAYOUT"
    if score < revz_min_confidence(minimo_confianca):
        return False, "MIN_CONFIDENCE"
    return True, None


def closes_of_closed_candles(
    candles: list[dict[str, Any]],
    *,
    current_candle_start: int,
    interval_seconds: int = 60,
) -> list[float] | None:
    """Fechamentos só das velas já FECHADAS, e só se a última acabou de fechar.

    A corretora devolve a vela em formação junto (a que começa em
    ``current_candle_start``). Ela é descartada. Se a vela imediatamente
    anterior não estiver na resposta, o dado está velho para decidir a entrada
    e a função devolve ``None``.

    Args:
        candles: Velas da corretora (``from``/``close``), qualquer ordem.
        current_candle_start: Início (unix) da vela em curso.
        interval_seconds: Duração da vela.

    Returns:
        Fechamentos em ordem cronológica, ou ``None`` quando falta a vela recém-fechada.
    """
    fechadas = []
    for vela in candles or []:
        try:
            inicio = int(vela.get("from") if vela.get("from") is not None else vela.get("time"))
            fechamento = float(vela["close"])
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        if inicio < int(current_candle_start):
            fechadas.append((inicio, fechamento))
    if not fechadas:
        return None
    fechadas.sort()
    if fechadas[-1][0] != int(current_candle_start) - int(interval_seconds):
        return None
    return [fechamento for _, fechamento in fechadas]


def revz_confirm_at_entry(
    closes: list[float] | None,
    direction: str,
    *,
    lookback: int = REVZ_LOOKBACK,
    threshold: float = REVZ_THRESHOLD,
) -> tuple[bool, float | None, str]:
    """Confirma, no disparo, que a vela recém-fechada ainda está no extremo.

    Args:
        closes: Fechamentos das velas fechadas (de ``closes_of_closed_candles``).
        direction: Direção do candidato (``CALL``/``PUT``).
        lookback: Velas na média.
        threshold: |z| exigido para operar.

    Returns:
        ``(confirmado, z, motivo)``.
    """
    if not closes:
        return False, None, "REVZ_CONFIRMACAO_SEM_DADOS"
    z = reversion_zscore(list(closes), lookback=lookback)
    if z is None:
        return False, None, "REVZ_CONFIRMACAO_SEM_DADOS"
    direcao = revz_direction(z, threshold=threshold)
    if direcao is None:
        return False, z, "REVZ_SEM_EXTREMO_NO_FECHAMENTO"
    if direcao != str(direction or "").strip().upper():
        return False, z, "REVZ_DIRECAO_VIROU"
    return True, z, "REVZ_CONFIRMADO"
