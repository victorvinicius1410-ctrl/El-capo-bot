"""Estratégia Vertex: reversão em desvio extremo na escala do Value Chart.

Porte fiel do indicador ``Reversion`` entregue pelo dono em 2026-09-09, escrito
na linguagem de indicadores da corretora. O cálculo é o do **Value Chart**
clássico — distância do fechamento até a média do meio-preço, dividida por uma
unidade de volatilidade própria — mais duas confirmações leves de momentum e de
cruzamento de médias, com peso 0,15 cada para não dominarem a escala base.

## O cálculo, linha a linha

    mba     = SMA((high + low) / 2, length)
    lrange  = unidade de volatilidade × 0,2      (ver `_lrange`)
    vclose  = (close - mba) / lrange
    momDev  = (close - close[momLength]) / lrange
    maDev   = (SMA(close, 9) - SMA(close, 21)) / lrange
    vertex  = vclose + 0,15 × momDev + 0,15 × maDev

``lrange`` tem dois branches no original, escolhidos por ``length > 7``. Com o
padrão ``length = 5`` vale o branch curto (média de 5 ranges corrigidos); o
branch longo, com blocos de ``varp`` velas, está implementado igual — inclusive
a assimetria de índice de ``prev(highest(high,varp), -varp+1)``, que é do script
original e foi mantida de propósito para o valor bater com o gráfico do dono.

## A leitura

É reversão à média: ``vertex >= +12`` diz que o preço esticou para cima e a
entrada é **PUT**; ``vertex <= -12`` diz o contrário e a entrada é **CALL**. Os
níveis são os ``extTop``/``extBot`` do indicador.

## O que ainda NÃO se sabe

Este módulo é o porte do script, não uma medição dele. **Nenhum backtest foi
rodado contra as velas da corretora até aqui** — nada permite afirmar acerto,
frequência ou vantagem, e a semelhança de família com a REV-Z (que mede ~56% em
mercado aberto e nada em OTC) não transfere resultado: a unidade de
volatilidade, a média de referência e o gatilho são outros.

Vale especialmente a ressalva de OTC. Em 164.000 velas sintéticas a série se
comporta como passeio aleatório em todas as famílias testadas, e a BullEx
praticamente só vende OTC. ``VERTEX_ALLOW_OTC`` vem ligado para a estratégia
poder operar de fato, mas operar em OTC é apostar na forma do indicador, não
numa vantagem medida.
"""

from __future__ import annotations

import logging
import os
from statistics import fmean
from typing import Any

from backend.support_resistance_strategy import candle_high, candle_low, is_otc_symbol

logger = logging.getLogger("backend-gateway")

# Liga a Vertex. Como a REV-Z e a SR-R, é CONFIGURAÇÃO DE AMBIENTE e não
# constante de código: ligar muda o significado de `analyze_signal` para o
# processo inteiro.
VERTEX_ENABLED = os.getenv("VERTEX_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
# Modo PARALELO (padrão desde 10/09): quando a Vertex NÃO dispara, o parecer do
# motor clássico é preservado e ele decide sozinho, com o piso dele. Com `false`
# volta o comportamento de sobreposição total, em que uma vela sem disparo
# vira WAIT/confiança 0 e cala o clássico — foi o que parou as contas em 09/09.
# `VERTEX_PARALLEL=false` reverte sem redeploy.
VERTEX_PARALLEL = os.getenv("VERTEX_PARALLEL", "true").strip().lower() in {"1", "true", "yes"}
# Ao contrário da REV-Z, não recusa OTC por padrão — recusar equivale a não
# operar nunca nesta corretora. Ver a ressalva no topo do módulo.
VERTEX_ALLOW_OTC = os.getenv("VERTEX_ALLOW_OTC", "true").strip().lower() in {"1", "true", "yes"}

# Os seis `input()` do script, na mesma ordem e com os mesmos padrões.
VERTEX_LENGTH = int(os.getenv("VERTEX_LENGTH", "5"))
VERTEX_MOM_LENGTH = int(os.getenv("VERTEX_MOM_LENGTH", "14"))
VERTEX_MA_FAST = int(os.getenv("VERTEX_MA_FAST", "9"))
VERTEX_MA_SLOW = int(os.getenv("VERTEX_MA_SLOW", "21"))
VERTEX_EXT_TOP = float(os.getenv("VERTEX_EXT_TOP", "12"))
VERTEX_EXT_BOT = float(os.getenv("VERTEX_EXT_BOT", "-12"))

# Peso das duas confirmações. No script são literais 0.15; ficam aqui como
# constante nomeada para o teste de fidelidade poder citá-los.
VERTEX_MOM_WEIGHT = 0.15
VERTEX_MA_WEIGHT = 0.15

# Escala de confiança própria, curta e longe de 100 — mesma decisão da REV-Z e
# da SR-R. Quem compara o mínimo do usuário (escala 0–100 do motor clássico)
# contra estes valores barra 100% dos sinais: o rescale vive em
# `apply_strategy_guard` E em `candidate_meets_cycle_threshold`, e faltar em um
# dos dois já custou três estratégias mudas neste projeto.
VERTEX_CONFIDENCE_FLOOR = 55
VERTEX_CONFIDENCE_MAX = 75

# Em OTC o piso desce até o CHÃO da escala, e não até o teto: assim todo disparo
# de |vertex| >= 12 vira ordem, em vez de só os |vertex| >= 20 que saturam a
# confiança em 75. Pedido do dono em 2026-09-09, para OTC apenas — mercado
# aberto fica como está. `VERTEX_OTC_FLOOR=false` devolve o comportamento
# anterior em OTC, sem redeploy.
VERTEX_OTC_FLOOR_ENABLED = os.getenv("VERTEX_OTC_FLOOR", "true").strip().lower() in {
    "1",
    "true",
    "yes",
}
# Piso do PORTÃO em OTC, separado de propósito de `VERTEX_CONFIDENCE_FLOOR`.
# Os dois valiam 55 e pareciam a mesma coisa, mas têm papéis diferentes: o
# `_FLOOR` é o chão da ESCALA (o menor valor que `vertex_confidence` emite) e
# este é o mínimo que o portão do ciclo exige. Mexer no `_FLOOR` para afrouxar o
# portão desloca a escala junto e não afrouxa nada — a tolerância a penalidades
# no disparo-limite continua zero.
#
# Baixado para 50 a pedido do dono em 2026-09-09, **só em OTC**: com a escala
# começando em 55, isso passa a tolerar até 5 pontos de penalidade no disparo
# mínimo (|vertex| = 12), onde antes só passava setup sem penalidade nenhuma.
# Mercado aberto continua em `VERTEX_CONFIDENCE_MAX` (75), intocado.
# `VERTEX_OTC_SCORE_FLOOR=55` devolve o comportamento anterior, sem redeploy.
VERTEX_OTC_SCORE_FLOOR = int(os.getenv("VERTEX_OTC_SCORE_FLOOR", "50"))
# |vertex| que satura a confiança no teto, contado a partir do nível extremo.
VERTEX_CONFIDENCE_SPAN = 8.0

# Velas mínimas para o valor existir. O branch longo de `lrange` olha até
# `varp * 5 + varp` velas atrás, e as médias pedem `ma_slow`.
def _min_candles() -> int:
    """Velas necessárias para calcular o Vertex com os parâmetros atuais."""
    varp = max(1, round(VERTEX_LENGTH / 5))
    fundo = varp * 6 if VERTEX_LENGTH > 7 else VERTEX_LENGTH + 5
    return max(VERTEX_MA_SLOW, VERTEX_MOM_LENGTH + 1, VERTEX_LENGTH, fundo) + 2


VERTEX_MIN_CANDLES = _min_candles()


def _prev(serie: list[float], atras: int) -> float:
    """``prev(s, i)`` do script: o valor ``|round(i)|`` barras atrás.

    O script chama sempre com argumento negativo (``prev(close,-varp)``) e a
    própria função tira o módulo, então o sinal não importa.

    Args:
        serie: Série em ordem cronológica, a última posição é a barra atual.
        atras: Quantas barras voltar; o módulo é aplicado, como no original.

    Returns:
        O valor na barra pedida, ou o mais antigo disponível quando a série é
        curta demais.
    """
    indice = len(serie) - 1 - abs(int(round(atras)))
    return float(serie[max(0, indice)])


def _sma(serie: list[float], periodo: int) -> float:
    """Média simples das últimas ``periodo`` posições."""
    if periodo <= 0 or not serie:
        return 0.0
    janela = serie[-periodo:]
    return float(fmean(janela))


def _highest(serie: list[float], periodo: int, atras: int = 0) -> float:
    """``prev(highest(s, periodo), -atras)``: máximo da janela, ``atras`` barras atrás."""
    fim = len(serie) - abs(int(round(atras)))
    inicio = max(0, fim - max(1, periodo))
    janela = serie[inicio:fim] or serie[:1]
    return float(max(janela))


def _lowest(serie: list[float], periodo: int, atras: int = 0) -> float:
    """``prev(lowest(s, periodo), -atras)``: mínimo da janela, ``atras`` barras atrás."""
    fim = len(serie) - abs(int(round(atras)))
    inicio = max(0, fim - max(1, periodo))
    janela = serie[inicio:fim] or serie[:1]
    return float(min(janela))


def _lrange(
    altas: list[float],
    baixas: list[float],
    fechamentos: list[float],
    length: int,
) -> float:
    """Unidade de volatilidade do Value Chart, nos dois branches do script.

    Args:
        altas: Máximas em ordem cronológica.
        baixas: Mínimas em ordem cronológica.
        fechamentos: Fechamentos em ordem cronológica.
        length: O ``length`` do indicador; ``> 7`` escolhe o branch longo.

    Returns:
        O ``lrange``, sempre já multiplicado por 0,2 como no original.
    """
    varp = max(1, round(length / 5))
    if length > 7:
        blocos: list[float] = []
        for k in range(5):
            if k == 0:
                bruto = _highest(altas, varp) - _lowest(baixas, varp)
                queda = abs(fechamentos[-1] - _prev(fechamentos, -varp))
            else:
                # Assimetria do script original, mantida: o topo volta
                # `varp*k - 1` barras e o fundo `varp*k`.
                bruto = _highest(altas, varp, atras=varp * k - 1) - _lowest(
                    baixas, varp, atras=varp * k
                )
                queda = abs(_prev(fechamentos, -varp * k) - _prev(fechamentos, -varp * (k + 1)))
            blocos.append(queda if (bruto == 0 and varp == 1) else bruto)
        return (sum(blocos) / 5) * 0.2

    var0: list[float] = []
    for i in range(len(fechamentos)):
        amplitude = altas[i] - baixas[i]
        anterior = fechamentos[i - 1] if i > 0 else fechamentos[i]
        cdelta = abs(fechamentos[i] - anterior)
        var0.append(cdelta if (cdelta > amplitude or altas[i] == baixas[i]) else amplitude)
    return _sma(var0, 5) * 0.2


def vertex_value(candles: list[dict[str, float]]) -> float | None:
    """Calcula o Vertex da última vela da série.

    Args:
        candles: Velas em ordem cronológica. Aceita ``max``/``min`` (formato da
            corretora) e ``high``/``low`` (formato dos datasets) — ler só um dos
            dois já quebrou uma estratégia em produção sem quebrar teste nenhum.

    Returns:
        O valor do indicador, ou ``None`` quando faltam velas ou a volatilidade
        medida é zero (série travada).
    """
    if len(candles) < VERTEX_MIN_CANDLES:
        return None
    altas = [candle_high(c) for c in candles]
    baixas = [candle_low(c) for c in candles]
    fechamentos = [float(c["close"]) for c in candles]

    lrange = _lrange(altas, baixas, fechamentos, VERTEX_LENGTH)
    if lrange <= 0:
        return None

    medios = [(altas[i] + baixas[i]) / 2 for i in range(len(candles))]
    mba = _sma(medios, VERTEX_LENGTH)

    vclose = (fechamentos[-1] - mba) / lrange
    mom_dev = (fechamentos[-1] - _prev(fechamentos, -VERTEX_MOM_LENGTH)) / lrange
    ma_dev = (_sma(fechamentos, VERTEX_MA_FAST) - _sma(fechamentos, VERTEX_MA_SLOW)) / lrange
    return vclose + VERTEX_MOM_WEIGHT * mom_dev + VERTEX_MA_WEIGHT * ma_dev


def vertex_direction(valor: float | None) -> str | None:
    """Traduz o valor do indicador em direção de entrada.

    Reversão: esticado para cima entra PUT, esticado para baixo entra CALL.

    Args:
        valor: Saída de ``vertex_value``.

    Returns:
        ``"CALL"``, ``"PUT"`` ou ``None`` quando o valor não é extremo.
    """
    if valor is None:
        return None
    if valor >= VERTEX_EXT_TOP:
        return "PUT"
    if valor <= VERTEX_EXT_BOT:
        return "CALL"
    return None


def vertex_confidence(valor: float | None) -> int:
    """Confiança na escala própria da estratégia (55–75).

    Cresce com o quanto o indicador passou do nível extremo e satura em
    ``VERTEX_CONFIDENCE_SPAN`` além dele.

    Args:
        valor: Saída de ``vertex_value``.

    Returns:
        Inteiro entre ``VERTEX_CONFIDENCE_FLOOR`` e ``VERTEX_CONFIDENCE_MAX``.
    """
    if valor is None:
        return VERTEX_CONFIDENCE_FLOOR
    if valor >= VERTEX_EXT_TOP:
        excesso = valor - VERTEX_EXT_TOP
    elif valor <= VERTEX_EXT_BOT:
        excesso = VERTEX_EXT_BOT - valor
    else:
        return VERTEX_CONFIDENCE_FLOOR
    fracao = min(1.0, max(0.0, excesso / VERTEX_CONFIDENCE_SPAN))
    faixa = VERTEX_CONFIDENCE_MAX - VERTEX_CONFIDENCE_FLOOR
    return int(round(VERTEX_CONFIDENCE_FLOOR + fracao * faixa))


def vertex_evaluate(symbol: str, candles: list[dict[str, float]]) -> dict[str, Any]:
    """Avalia a Vertex para um ativo.

    Args:
        symbol: Ativo analisado.
        candles: Velas em ordem cronológica.

    Returns:
        ``{"direction", "vertex", "ext_top", "ext_bot", "blocked"}``.
        ``direction`` é ``None`` quando não há entrada, e ``blocked`` diz por quê.
    """
    veredito: dict[str, Any] = {
        "direction": None,
        "vertex": None,
        "ext_top": VERTEX_EXT_TOP,
        "ext_bot": VERTEX_EXT_BOT,
        "blocked": None,
    }
    if not VERTEX_ALLOW_OTC and is_otc_symbol(symbol):
        veredito["blocked"] = "VERTEX_OTC_RECUSADO"
        return veredito
    if len(candles) < VERTEX_MIN_CANDLES:
        veredito["blocked"] = "VERTEX_VELAS_INSUFICIENTES"
        return veredito

    valor = vertex_value(candles)
    veredito["vertex"] = valor
    if valor is None:
        veredito["blocked"] = "VERTEX_SEM_VOLATILIDADE"
        return veredito

    direcao = vertex_direction(valor)
    if direcao is None:
        veredito["blocked"] = "VERTEX_FORA_DO_EXTREMO"
        return veredito

    veredito["direction"] = direcao
    return veredito


def is_vertex_candidate(candidato: dict[str, Any] | None) -> bool:
    """Indica se a entrada foi decidida pela Vertex."""
    if not isinstance(candidato, dict):
        return False
    veredito = candidato.get("vertex")
    return (
        isinstance(veredito, dict)
        and str(veredito.get("direction") or "").upper() in {"CALL", "PUT"}
    )


def vertex_min_confidence(minimo_usuario: int, candidato: dict[str, Any] | None) -> int:
    """Rebaixa o piso de confiança quando quem decidiu foi a Vertex.

    A escala da Vertex vai de ``VERTEX_CONFIDENCE_FLOOR`` a
    ``VERTEX_CONFIDENCE_MAX``; o mínimo do painel é calibrado para a escala
    0–100 do motor clássico e o padrão é 80. Comparar os dois barra todo sinal
    da estratégia, sem erro nenhum no log — foi o que aconteceu com a REV-Z, com
    a SR-R e com o modo LIVE, cada um custando uma rodada de diagnóstico.

    **Quanto o piso desce depende do mercado**, por decisão do dono em
    2026-09-09:

    - **OTC** desce até ``VERTEX_OTC_SCORE_FLOOR`` (50 desde 09/09), que fica
      ABAIXO do chão da escala (55): todo disparo de ``|vertex| >= 12`` vira
      ordem, e ainda sobram 5 pontos de folga para as penalidades do motor
      clássico. Descer só até o chão da escala não dava folga nenhuma.
    - **Mercado aberto** desce só até o teto (``VERTEX_CONFIDENCE_MAX``), que na
      prática exige ``|vertex| >= 20`` — a confiança satura 8 pontos além do
      nível extremo. É o comportamento que já estava no ar, e foi mantido de
      propósito: o pedido foi para não mexer no aberto.

    O backtest de 225.010 velas M1 reais diz que o lado OTC é o pior dos dois
    (50,25% de acerto contra os 53,48% que o payout exige, −6,03% por operação;
    no aberto, −2,78%), e que nenhum corte de ``|vertex|`` sobrevive ao holdout.
    Ligar o piso baixo em OTC é decisão do dono, tomada com esses números à
    vista — está aqui para que ninguém leia este código depois e ache que foi
    descuido. Ver `docs/ESTRATEGIA_VERTEX.md`.

    Nunca AUMENTA o piso: quem configurou menos que o teto continua com o seu.

    Args:
        minimo_usuario: Piso configurado no painel.
        candidato: Sinal a ser avaliado; o ativo sai de ``symbol``/``active``.

    Returns:
        O piso a aplicar neste candidato.
    """
    if not is_vertex_candidate(candidato):
        return int(minimo_usuario)
    simbolo = str(candidato.get("symbol") or candidato.get("active") or "")
    teto = VERTEX_CONFIDENCE_MAX
    if VERTEX_OTC_FLOOR_ENABLED and is_otc_symbol(simbolo):
        teto = VERTEX_OTC_SCORE_FLOOR
    return min(int(minimo_usuario), teto)
