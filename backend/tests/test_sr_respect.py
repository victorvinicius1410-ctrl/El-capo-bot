"""Região de suporte e resistência e a regra de respeito.

Escrito junto com a correção de 2026-09-09, que atacou três defeitos medidos no
motor clássico: a região saía do extremo de uma janela de 23 velas (não era
nível), a tolerância cobria 55% das leituras, e o veto ``SR_ZONE`` barrava 383
de 383 setups ``SUPPORT_RESISTANCE`` — o único caso em que o motor de fato
reconhecia um nível.

O teste que mais importa aqui é o de formato de vela: em 2026-09-04 um módulo
de S/R foi para o ar lendo ``high``/``low`` enquanto o motor normaliza para
``max``/``min``, e nenhum teste pegou porque todos usavam o formato do dataset.
"""

import unittest

from backend import sr_respect
from backend.sr_respect import (
    RESPECT_CONFLICT,
    RESPECT_NO_REJECTION,
    RESPECT_NO_ROOM,
    RESPECT_OK_CLEAR,
    RESPECT_OK_REJECTION,
    build_zone,
    evaluate_respect,
    rejection_confirmed,
)


def vela(open_: float, high: float, low: float, close: float) -> dict[str, float]:
    """Vela no formato da corretora (``max``/``min``)."""
    return {"open": open_, "close": close, "max": high, "min": low}


def para_formato_dataset(candles: list[dict[str, float]]) -> list[dict[str, float]]:
    """Converte velas de ``max``/``min`` para ``high``/``low``."""
    return [
        {"open": c["open"], "close": c["close"], "high": c["max"], "low": c["min"]}
        for c in candles
    ]


def serie_com_niveis(n: int = 90) -> list[dict[str, float]]:
    """Série que oscila entre um piso e um teto repetidos, criando níveis reais.

    O preço bate quatro vezes em 1,10000 e quatro em 1,10400, o que dá pivôs
    agrupáveis e contagem de toques suficiente. Entre os extremos o preço anda
    pelo meio do canal.

    Returns:
        Velas em ordem cronológica, formato da corretora.
    """
    piso, teto = 1.10000, 1.10400
    candles: list[dict[str, float]] = []
    for i in range(n):
        fase = i % 20
        if fase < 10:
            base = piso + (teto - piso) * (fase / 10)
        else:
            base = teto - (teto - piso) * ((fase - 10) / 10)
        alta = base + 0.00020
        baixa = base - 0.00020
        candles.append(vela(base - 0.00005, alta, baixa, base + 0.00005))
    return candles


class BuildZoneTest(unittest.TestCase):
    """A região sai de níveis com toques, não do extremo da janela."""

    def test_serie_com_niveis_usa_pivos(self) -> None:
        zona = build_zone(serie_com_niveis())
        self.assertEqual(zona["source"], "PIVOT")
        self.assertTrue(
            zona["support_touches"] >= 2 or zona["resistance_touches"] >= 2,
            "um dos lados precisa ter nível com 2+ toques",
        )

    def test_serie_curta_cai_no_fallback_de_janela(self) -> None:
        zona = build_zone(serie_com_niveis(20))
        self.assertEqual(zona["source"], "WINDOW")

    def test_sem_velas_devolve_regiao_vazia(self) -> None:
        zona = build_zone([])
        self.assertIsNone(zona["support"])
        self.assertIsNone(zona["resistance"])
        self.assertFalse(zona["near_support"])
        self.assertFalse(zona["near_resistance"])

    def test_tolerancia_muito_menor_que_a_versao_antiga(self) -> None:
        """0,5 ATR contra os 12% do range que cobriam metade do gráfico."""
        candles = serie_com_niveis()
        zona = build_zone(candles)
        amplitude = max(c["max"] for c in candles[-24:]) - min(c["min"] for c in candles[-24:])
        self.assertLess(zona["tolerance"], amplitude * 0.12)

    def test_mesmo_resultado_nos_dois_formatos_de_vela(self) -> None:
        """A lição de 2026-09-04: testar no formato da corretora, não só no do dataset."""
        candles = serie_com_niveis()
        corretora = build_zone(candles)
        dataset = build_zone(para_formato_dataset(candles))
        for campo in ("support", "resistance", "tolerance", "near_support", "near_resistance"):
            self.assertEqual(corretora[campo], dataset[campo], campo)


class RejectionConfirmedTest(unittest.TestCase):
    """Forma da vela de rejeição, nas duas direções."""

    def test_call_pede_pavio_inferior_e_fechamento_em_alta(self) -> None:
        martelo = vela(1.09992, 1.10020, 1.09980, 1.10012)
        self.assertTrue(rejection_confirmed("CALL", martelo))
        self.assertFalse(rejection_confirmed("PUT", martelo))

    def test_put_pede_pavio_superior_e_fechamento_em_baixa(self) -> None:
        estrela = vela(1.10408, 1.10420, 1.10380, 1.10388)
        self.assertTrue(rejection_confirmed("PUT", estrela))
        self.assertFalse(rejection_confirmed("CALL", estrela))

    def test_vela_travada_nao_confirma(self) -> None:
        self.assertFalse(rejection_confirmed("CALL", vela(1.1, 1.1, 1.1, 1.1)))

    def test_corpo_fino_demais_nao_confirma(self) -> None:
        doji = vela(1.10000, 1.10030, 1.09970, 1.10001)
        self.assertFalse(rejection_confirmed("CALL", doji))


class EvaluateRespectTest(unittest.TestCase):
    """As quatro decisões da regra de respeito."""

    def setUp(self) -> None:
        self.zona_suporte = {
            "support": 1.10000,
            "resistance": 1.10400,
            "tolerance": 0.00020,
            "near_support": True,
            "near_resistance": False,
        }
        self.zona_resistencia = {
            "support": 1.10000,
            "resistance": 1.10400,
            "tolerance": 0.00020,
            "near_support": False,
            "near_resistance": True,
        }
        self.zona_livre = {
            "support": 1.10000,
            "resistance": 1.10400,
            "tolerance": 0.00020,
            "near_support": False,
            "near_resistance": False,
        }
        self.martelo = vela(1.09992, 1.10020, 1.09980, 1.10012)
        self.estrela = vela(1.10408, 1.10420, 1.10380, 1.10388)

    def test_put_colado_no_suporte_e_recusado(self) -> None:
        """A violação exata que o dono relatou: vender colado no suporte."""
        respeita, motivo = evaluate_respect("PUT", self.zona_suporte, self.estrela)
        self.assertFalse(respeita)
        self.assertEqual(motivo, RESPECT_CONFLICT)

    def test_call_colado_na_resistencia_e_recusado(self) -> None:
        respeita, motivo = evaluate_respect("CALL", self.zona_resistencia, self.martelo)
        self.assertFalse(respeita)
        self.assertEqual(motivo, RESPECT_CONFLICT)

    def test_call_no_suporte_com_rejeicao_e_liberado(self) -> None:
        """A correção da contradição: operar a região é entrar na rejeição."""
        respeita, motivo = evaluate_respect("CALL", self.zona_suporte, self.martelo)
        self.assertTrue(respeita)
        self.assertEqual(motivo, RESPECT_OK_REJECTION)

    def test_call_no_suporte_sem_rejeicao_e_recusado(self) -> None:
        sem_pavio = vela(1.10000, 1.10025, 1.09999, 1.10022)
        respeita, motivo = evaluate_respect("CALL", self.zona_suporte, sem_pavio)
        self.assertFalse(respeita)
        self.assertEqual(motivo, RESPECT_NO_REJECTION)

    def test_fora_de_qualquer_regiao_e_liberado(self) -> None:
        respeita, motivo = evaluate_respect("CALL", self.zona_livre, self.martelo)
        self.assertTrue(respeita)
        self.assertEqual(motivo, RESPECT_OK_CLEAR)

    def test_sem_espaco_ate_o_nivel_oposto_e_recusado(self) -> None:
        """Rejeição no suporte com a resistência colada não tem para onde ir."""
        apertada = dict(self.zona_suporte, resistance=1.10022)
        respeita, motivo = evaluate_respect("CALL", apertada, self.martelo)
        self.assertFalse(respeita)
        self.assertEqual(motivo, RESPECT_NO_ROOM)

    def test_direcao_invalida_e_recusada(self) -> None:
        respeita, _ = evaluate_respect("WAIT", self.zona_livre, self.martelo)
        self.assertFalse(respeita)


class DesligadoTest(unittest.TestCase):
    """``SR_RESPECT=false`` devolve a construção antiga por inteiro."""

    def test_desligado_volta_para_a_janela(self) -> None:
        original = sr_respect.SR_RESPECT_ENABLED
        sr_respect.SR_RESPECT_ENABLED = False
        try:
            zona = build_zone(serie_com_niveis())
            self.assertEqual(zona["source"], "WINDOW")
        finally:
            sr_respect.SR_RESPECT_ENABLED = original


if __name__ == "__main__":
    unittest.main()
