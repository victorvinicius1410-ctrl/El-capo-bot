"""Narrativa da análise: o que o robô viu, contado como gente conta.

## Por que este módulo existe

O texto que o cliente lia era a montagem literal do score:

    "EMA9 acima da EMA21. RSI favoravel (58.0). Último candle tem força na
     direção. Pavio contra a entrada esta curto. Últimos 5 candles sustentam a
     direção. Penalizacoes no score: MIN_CONFIDENCE, PRICE_ACTION_SETUP,
     SUPPORT_RESISTANCE."

Três problemas. Ele expõe nome interno de filtro, que não significa nada para
quem lê. Ele é sempre a mesma frase na mesma ordem, então duas entradas
seguidas parecem a mesma análise copiada. E ele soa a checklist de máquina, não
a alguém que olhou o gráfico.

Aqui o texto é montado a partir dos **mesmos números** — nada é inventado —, mas
escolhendo o que vale contar, traduzindo para linguagem de quem opera, e
variando abertura, ordem e conectivo.

## A regra que não pode ser quebrada

**A narrativa descreve o que foi medido e nada além — sem opinião.** Até
10/09/2026 cada texto terminava com um veredito: ou ressalva ("é um sinal
razoável, não uma certeza", "com a ressalva de que o setup não está perfeito")
ou convicção ("com margem confortável", "o desenho está claro"). O dono pediu
que o El Capo não soasse incerto; a saída foi tirar o veredito inteiro, e não
trocar a ressalva por convicção — o robô acerta perto de 50% em OTC, e
prometer no texto o que o resultado não entrega é o caminho mais curto para o
cliente perder a confiança na ferramenta.

Por isso: nenhuma frase avalia a entrada (nem "fraco", nem "claro"), e um fato
contrário à direção continua sendo dito, em tom neutro ("as médias curtas
apontam para o lado contrário"). Omitir o que pesa contra seria escolher os
números a dedo.

## Como a variação funciona

A escolha de frase é determinística, semeada por ativo + minuto da vela. Duas
entradas no mesmo ativo em minutos diferentes saem diferentes; a mesma análise
relida sai igual. Sem `random` global, que tornaria o texto irreprodutível e
quebraria teste.
"""

from __future__ import annotations

import hashlib
from typing import Any

# Aberturas. A variação começa aqui porque é a primeira coisa que o olho pega —
# duas entradas seguidas com a mesma abertura já parecem a mesma análise.
# Cada abertura já traz o conectivo que a liga à primeira observação, porque
# "X vinha arrastado, e" e "O que chama atenção em X" pedem pontuação diferente.
# Juntar tudo com vírgula fixa produzia "X vinha arrastado, e, o RSI...".
ABERTURAS = (
    ("Acompanhando {ativo} há alguns minutos", ", "),
    ("Na leitura de {ativo} agora", ", "),
    ("O que chama atenção em {ativo}", ": "),
    ("Olhando o gráfico de {ativo}", ": "),
    ("{ativo} vinha arrastado, e agora", " "),
    ("Fechou a vela em {ativo} e", " "),
    ("Peguei {ativo} num momento interessante", ": "),
)

# Reforço x contraste. Ligar uma ressalva com "No mesmo sentido" é o tipo de
# erro que denuncia texto montado por template — o leitor sente antes de saber
# explicar. Qual usar depende do sinal da observação seguinte.
LIGACOES_REFORCO = ("Somado a isso,", "E não é só isso:", "Junto disso,", "No mesmo sentido,", "Reforçando,")
LIGACOES_CONTRASTE = ("Só que", "Por outro lado,", "Mas", "Enquanto isso,")

# Fecho só nomeia a direção. Não há mais fecho graduado por convicção — ver a
# regra no topo do módulo.
FECHOS = (
    "Entrada de {direcao}.",
    "Vou de {direcao}.",
)


def _semente(simbolo: str, marcador: Any) -> int:
    """Número estável derivado do ativo e do instante, para variar sem sortear."""
    bruto = f"{simbolo}|{marcador}".encode("utf-8")
    return int(hashlib.sha256(bruto).hexdigest()[:8], 16)


def _escolhe(opcoes: tuple, semente: int, deslocamento: int = 0):
    return opcoes[(semente + deslocamento) % len(opcoes)]


def _achados(metricas: dict[str, Any], direcao: str) -> list[tuple[str, bool]]:
    """Frases das métricas com a marca de a favor (True) ou contra (False).

    A marca só escolhe o conectivo; ela nunca vira adjetivo no texto.
    """
    saida: list[tuple[str, bool]] = []
    subindo = direcao == "CALL"
    lado = "compra" if subindo else "venda"

    rsi = metricas.get("rsi14")
    if isinstance(rsi, (int, float)):
        if rsi >= 70:
            saida.append((f"o RSI em {rsi:.0f} mostra o ativo esticado para cima", True))
        elif rsi <= 30:
            saida.append((f"o RSI em {rsi:.0f} mostra o ativo esticado para baixo", True))
        elif (subindo and rsi >= 55) or (not subindo and rsi <= 45):
            saida.append((f"o RSI em {rsi:.0f} acompanha o lado da {lado}", True))

    ema9, ema21 = metricas.get("ema9"), metricas.get("ema21")
    if isinstance(ema9, (int, float)) and isinstance(ema21, (int, float)) and ema21:
        if (ema9 > ema21) == subindo:
            saida.append(("as médias curtas confirmam a direção", True))
        else:
            saida.append(("as médias curtas apontam para o lado contrário", False))

    corpo, faixa = metricas.get("candle_body"), metricas.get("candle_range")
    if isinstance(corpo, (int, float)) and isinstance(faixa, (int, float)) and faixa:
        proporcao = corpo / faixa
        if proporcao >= 0.7:
            saida.append((f"a última vela fechou com corpo cheio ({proporcao:.0%} do range)", True))
        elif proporcao <= 0.3:
            saida.append((f"a última vela fechou com corpo de {proporcao:.0%} do range", False))

    pavio = metricas.get("upper_wick_ratio" if subindo else "lower_wick_ratio")
    if isinstance(pavio, (int, float)) and pavio >= 0.35:
        saida.append((f"a última vela deixou um pavio de {pavio:.0%} contra a direção", False))

    cores = metricas.get("last_3_colors")
    if isinstance(cores, list) and len(cores) == 3:
        alvo = "GREEN" if subindo else "RED"
        if all(c == alvo for c in cores):
            saida.append(("as três últimas velas foram todas no mesmo sentido", True))
        elif cores[0] != cores[1] != cores[2]:
            saida.append(("as últimas velas vêm alternando de cor", False))

    # `WEAK` não gera frase: o rótulo não mede nada além de "não é continuação
    # nem reversão", e a única forma de dizê-lo era a ressalva que saiu.
    setup = str(metricas.get("price_action_setup") or "").upper()
    if setup == "CONTINUATION":
        saida.append(("o movimento tem continuidade", True))
    elif setup == "REVERSAL":
        saida.append(("o desenho é de reversão", True))

    volatilidade = str(metricas.get("volatility") or "").upper()
    if volatilidade == "HIGH":
        saida.append(("a volatilidade está alta, então o movimento tende a andar rápido", True))
    return saida


def observacoes(metricas: dict[str, Any], direcao: str) -> list[str]:
    """Traduz as métricas em frases de quem opera, só o que for verdade.

    Args:
        metricas: Bloco ``metrics`` do sinal.
        direcao: ``CALL`` ou ``PUT``.

    Returns:
        Frases, da mais relevante para a menos. Pode vir vazia.
    """
    return [frase for frase, _ in _achados(metricas, direcao)]


def monta_narrativa(
    simbolo: str,
    direcao: str,
    metricas: dict[str, Any],
    *,
    marcador: Any = "",
) -> str:
    """Monta o texto que o cliente lê.

    Args:
        simbolo: Ativo.
        direcao: ``CALL`` ou ``PUT``.
        metricas: Bloco ``metrics`` do sinal.
        marcador: Algo que muda por vela (timestamp) para variar o texto.

    Returns:
        Narrativa em uma a quatro frases. Não avalia a entrada: nem ressalva,
        nem convicção.

    Note:
        Não há caminho separado para o modo LIVE. Existia um: ele escrevia
        "entrada de demonstração, com o filtro de qualidade afrouxado" e o
        overlay lia isso em voz alta na transmissão. A entrada do modo LIVE
        agora usa esta mesma narrativa (ver `live_demo_mode.narracao_normal`),
        sem anunciar o modo.
    """
    semente = _semente(simbolo, marcador)
    achados = _achados(metricas, direcao)
    fecho = _escolhe(FECHOS, semente, 2).format(direcao=direcao)

    modelo, junta = _escolhe(ABERTURAS, semente)
    abertura = modelo.format(ativo=simbolo)
    if not achados:
        lado = "compra" if direcao == "CALL" else "venda"
        return f"{abertura}{junta}a leitura das últimas velas aponta para {lado}. {fecho}"

    corpo = achados[0][0]
    if len(achados) > 1:
        familia = LIGACOES_CONTRASTE if achados[1][1] != achados[0][1] else LIGACOES_REFORCO
        corpo += ". " + _escolhe(familia, semente, 1) + f" {achados[1][0]}"
    if len(achados) > 2:
        terceiro = achados[2][0]
        corpo += f". {terceiro[0].upper()}{terceiro[1:]}"

    return f"{abertura}{junta}{corpo}. {fecho}"
