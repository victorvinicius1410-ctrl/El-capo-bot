"""Nível visível: o topo/fundo que o cliente vê no gráfico, com ou sem 2 toques.

Caso real de 10/09/2026, cliente Sergio (R$ 200 por entrada): três compras
perdidas colado na máxima do gráfico — CHFJPY-OTC 17:10, EURGBP-OTC 17:33 e
18:36 (UTC). A região de S/R só aceitava nível com 2+ toques em 2 horas; um topo
novo não existia, e as três saíram com "resistência: nenhuma" →
``OK_FORA_DA_REGIAO``. As séries abaixo são as velas M1 reais da corretora
(45 fechadas antes de cada entrada), com a vela em formação no último fechamento
— exatamente o que a reconferência do disparo vê.
"""

import unittest

from backend import sr_respect
from backend.sr_respect import (
    RESPECT_OK_CLEAR,
    RESPECT_VISIBLE_AHEAD,
    build_zone,
    evaluate_respect,
    visible_level_ahead,
)


def _series(tuplas):
    return [{"open": o, "max": h, "min": l, "close": c} for (o, h, l, c) in tuplas]


def _com_vela_em_formacao(velas):
    preco = velas[-1]["close"]
    return velas + [{"open": preco, "max": preco, "min": preco, "close": preco}]


EURGBP_1836 = _series([
    (0.862475, 0.862545, 0.862325, 0.862495),
    (0.862505, 0.863005, 0.862505, 0.862925),
    (0.862915, 0.863125, 0.862905, 0.863095),
    (0.863085, 0.863455, 0.862985, 0.863365),
    (0.863355, 0.863495, 0.863305, 0.863405),
    (0.863375, 0.863495, 0.863305, 0.863305),
    (0.863285, 0.863675, 0.863285, 0.863505),
    (0.863455, 0.863485, 0.863035, 0.863185),
    (0.863175, 0.863305, 0.863015, 0.863015),
    (0.863045, 0.863065, 0.862745, 0.862845),
    (0.862825, 0.863055, 0.862705, 0.863025),
    (0.863005, 0.863135, 0.862985, 0.863025),
    (0.863015, 0.863085, 0.862875, 0.863085),
    (0.863075, 0.863135, 0.862895, 0.862905),
    (0.862895, 0.863455, 0.862895, 0.863435),
    (0.863425, 0.863525, 0.863255, 0.863275),
    (0.863265, 0.863595, 0.863265, 0.863595),
    (0.863585, 0.863625, 0.863485, 0.863595),
    (0.863655, 0.863655, 0.863255, 0.863325),
    (0.863285, 0.863695, 0.863285, 0.863675),
    (0.863655, 0.863705, 0.863515, 0.863555),
    (0.863545, 0.863795, 0.863545, 0.863665),
    (0.863645, 0.864125, 0.863635, 0.863985),
    (0.864005, 0.864035, 0.863815, 0.863885),
    (0.863865, 0.864005, 0.863675, 0.863745),
    (0.863685, 0.864055, 0.863685, 0.863965),
    (0.863975, 0.863975, 0.863625, 0.863705),
    (0.863725, 0.863915, 0.863705, 0.863915),
    (0.863905, 0.863965, 0.863805, 0.863885),
    (0.863895, 0.864115, 0.863865, 0.863865),
    (0.863895, 0.863995, 0.863665, 0.863825),
    (0.863835, 0.863905, 0.863645, 0.863895),
    (0.863905, 0.864035, 0.863895, 0.863905),
    (0.863915, 0.863975, 0.863715, 0.863855),
    (0.863865, 0.864165, 0.863865, 0.864005),
    (0.864005, 0.864325, 0.864005, 0.864315),
    (0.864335, 0.864485, 0.864295, 0.864465),
    (0.864455, 0.864465, 0.864185, 0.864215),
    (0.864185, 0.864195, 0.864035, 0.864125),
    (0.864115, 0.864255, 0.864005, 0.864205),
    (0.864185, 0.864255, 0.863905, 0.863945),
    (0.863975, 0.864125, 0.863945, 0.864055),
    (0.864035, 0.864215, 0.863935, 0.864105),
    (0.864115, 0.864405, 0.864005, 0.864385),
    (0.864395, 0.864475, 0.864175, 0.864385),
])

CHFJPY_1710 = _series([
    (189.4925, 189.5415, 189.4235, 189.4235),
    (189.4265, 189.4355, 189.3545, 189.3915),
    (189.3905, 189.4005, 189.3415, 189.3895),
    (189.3955, 189.4455, 189.3685, 189.4005),
    (189.4065, 189.4205, 189.3725, 189.4135),
    (189.4175, 189.5255, 189.4125, 189.5045),
    (189.5015, 189.5285, 189.4405, 189.4425),
    (189.4405, 189.4965, 189.4095, 189.4465),
    (189.4395, 189.4545, 189.3685, 189.4065),
    (189.4095, 189.4235, 189.3335, 189.3335),
    (189.3225, 189.4425, 189.3055, 189.4365),
    (189.4375, 189.4375, 189.3695, 189.3985),
    (189.4145, 189.4315, 189.3535, 189.3535),
    (189.3485, 189.3645, 189.2885, 189.3095),
    (189.3065, 189.4065, 189.3055, 189.3555),
    (189.3595, 189.3965, 189.3305, 189.3695),
    (189.3715, 189.3925, 189.2925, 189.3195),
    (189.3145, 189.3155, 189.2175, 189.2675),
    (189.2655, 189.3605, 189.2395, 189.3565),
    (189.3585, 189.3615, 189.2835, 189.3165),
    (189.3115, 189.4105, 189.3105, 189.3715),
    (189.3685, 189.3725, 189.2615, 189.2795),
    (189.2845, 189.4105, 189.2625, 189.4095),
    (189.4075, 189.4605, 189.3725, 189.4595),
    (189.4585, 189.4745, 189.3805, 189.4235),
    (189.4255, 189.5005, 189.4045, 189.4875),
    (189.4885, 189.5045, 189.4465, 189.4755),
    (189.4775, 189.4885, 189.4045, 189.4545),
    (189.4565, 189.4765, 189.3825, 189.4645),
    (189.4665, 189.5565, 189.4495, 189.5565),
    (189.5545, 189.6475, 189.5385, 189.6475),
    (189.6465, 189.7215, 189.6365, 189.6755),
    (189.6775, 189.6835, 189.6145, 189.6425),
    (189.6395, 189.6765, 189.5775, 189.6755),
    (189.6735, 189.7185, 189.6105, 189.6295),
    (189.6355, 189.6955, 189.6165, 189.6885),
    (189.6905, 189.7445, 189.6875, 189.7045),
    (189.7025, 189.7885, 189.6815, 189.7355),
    (189.7385, 189.8365, 189.6915, 189.8365),
    (189.8335, 189.9475, 189.8255, 189.9345),
    (189.9285, 190.0405, 189.9175, 189.9865),
    (189.9795, 189.9825, 189.9185, 189.9365),
    (189.9385, 189.9485, 189.8875, 189.9275),
    (189.9295, 189.9605, 189.8715, 189.9605),
    (189.9585, 190.0275, 189.9435, 190.0225),
])

EURGBP_1733 = _series([
    (0.860415, 0.860555, 0.860285, 0.860495),
    (0.860485, 0.860585, 0.860355, 0.860585),
    (0.860575, 0.860655, 0.860295, 0.860355),
    (0.860315, 0.860315, 0.860045, 0.860135),
    (0.860125, 0.860445, 0.860125, 0.860385),
    (0.860395, 0.860395, 0.860005, 0.860015),
    (0.860025, 0.860225, 0.859955, 0.860155),
    (0.860165, 0.860165, 0.859855, 0.860025),
    (0.860065, 0.860225, 0.859985, 0.860195),
    (0.860195, 0.860215, 0.859935, 0.859965),
    (0.859955, 0.860075, 0.859895, 0.860055),
    (0.860045, 0.860235, 0.860015, 0.860205),
    (0.860195, 0.860505, 0.860135, 0.860445),
    (0.860435, 0.860905, 0.860435, 0.860805),
    (0.860785, 0.860975, 0.860775, 0.860945),
    (0.860955, 0.861145, 0.860815, 0.861085),
    (0.861085, 0.861115, 0.860825, 0.860885),
    (0.860895, 0.861385, 0.860895, 0.861355),
    (0.861325, 0.861375, 0.860995, 0.861085),
    (0.861075, 0.861415, 0.861065, 0.861305),
    (0.861315, 0.861465, 0.861235, 0.861295),
    (0.861295, 0.861505, 0.861295, 0.861465),
    (0.861455, 0.861485, 0.861135, 0.861165),
    (0.861155, 0.861185, 0.860995, 0.861075),
    (0.861085, 0.861395, 0.861085, 0.861315),
    (0.861325, 0.861605, 0.861315, 0.861525),
    (0.861535, 0.861705, 0.861425, 0.861525),
    (0.861505, 0.861735, 0.861485, 0.861565),
    (0.861575, 0.861575, 0.861345, 0.861425),
    (0.861385, 0.861745, 0.861385, 0.861735),
    (0.861735, 0.861745, 0.861465, 0.861605),
    (0.861615, 0.861635, 0.861475, 0.861565),
    (0.861585, 0.861805, 0.861535, 0.861775),
    (0.861785, 0.861805, 0.861555, 0.861585),
    (0.861595, 0.861705, 0.861515, 0.861595),
    (0.861585, 0.861965, 0.861575, 0.861955),
    (0.861945, 0.862065, 0.861845, 0.861855),
    (0.861845, 0.861905, 0.861725, 0.861835),
    (0.861825, 0.862055, 0.861745, 0.861745),
    (0.861735, 0.862075, 0.861695, 0.862065),
    (0.862055, 0.862185, 0.861995, 0.862175),
    (0.862195, 0.862235, 0.862025, 0.862235),
    (0.862245, 0.862465, 0.862185, 0.862455),
    (0.862445, 0.862595, 0.862335, 0.862505),
    (0.862495, 0.862895, 0.862495, 0.862855),
])


class CasoRealSergioTest(unittest.TestCase):
    """As três compras de 10/09 passam a ser recusadas; a venda no topo, não."""

    def _veredito(self, velas, direcao):
        serie = _com_vela_em_formacao(velas)
        return evaluate_respect(direcao, build_zone(serie), serie[-1])

    def test_eurgbp_1836_compra_colada_no_topo_e_recusada(self) -> None:
        self.assertEqual(self._veredito(EURGBP_1836, "CALL"), (False, RESPECT_VISIBLE_AHEAD))

    def test_chfjpy_1710_compra_colada_no_topo_e_recusada(self) -> None:
        self.assertEqual(self._veredito(CHFJPY_1710, "CALL"), (False, RESPECT_VISIBLE_AHEAD))

    def test_eurgbp_1733_compra_colada_no_topo_e_recusada(self) -> None:
        self.assertEqual(self._veredito(EURGBP_1733, "CALL"), (False, RESPECT_VISIBLE_AHEAD))

    def test_distancia_medida_bate_com_a_auditoria(self) -> None:
        serie = _com_vela_em_formacao(CHFJPY_1710)
        distancia = visible_level_ahead("CALL", build_zone(serie), serie[-1])
        self.assertIsNotNone(distancia)
        self.assertLess(distancia, 0.5)
        self.assertGreaterEqual(distancia, 0.0)


class RegraTest(unittest.TestCase):
    def _subida(self, n=60, passo=0.0001):
        """Subida em escada com recuos: cria topos de 1 toque pelo caminho."""
        velas, preco = [], 1.1000
        for i in range(n):
            delta = passo if i % 5 != 4 else -2 * passo
            abertura, preco = preco, preco + delta
            velas.append({"open": abertura, "close": preco,
                          "max": max(abertura, preco) + passo / 4,
                          "min": min(abertura, preco) - passo / 4})
        return velas

    def test_compra_longe_do_topo_e_liberada(self) -> None:
        velas = self._subida()
        # derruba o preço bem abaixo de tudo: topo mais próximo fica longe
        ultimo = velas[-1]["close"]
        for _ in range(3):
            abertura, ultimo = ultimo, ultimo - 0.0008
            velas.append({"open": abertura, "close": ultimo, "max": abertura, "min": ultimo})
        serie = _com_vela_em_formacao(velas)
        zona = build_zone(serie)
        distancia = visible_level_ahead("CALL", zona, serie[-1])
        self.assertGreater(distancia, 0.5)

    def test_preco_acima_da_maxima_conta_como_colado(self) -> None:
        velas = self._subida()
        serie = velas + [{"open": velas[-1]["close"], "close": velas[-1]["close"] + 0.0005,
                          "max": velas[-1]["close"] + 0.0005, "min": velas[-1]["close"]}]
        zona = build_zone(serie)
        self.assertLessEqual(visible_level_ahead("CALL", zona, serie[-1]), 0.0)
        self.assertEqual(evaluate_respect("CALL", zona, serie[-1])[1], RESPECT_VISIBLE_AHEAD)

    def test_mesmo_resultado_nos_dois_formatos_de_vela(self) -> None:
        serie = _com_vela_em_formacao(EURGBP_1836)
        dataset = [{"open": c["open"], "close": c["close"], "high": c["max"], "low": c["min"]} for c in serie]
        self.assertEqual(
            visible_level_ahead("CALL", build_zone(serie), serie[-1]),
            visible_level_ahead("CALL", build_zone(dataset), dataset[-1]),
        )

    def test_zona_sem_niveis_visiveis_nao_decide(self) -> None:
        """Zona montada à mão (testes antigos, chamadas externas) não ganha veto novo."""
        zona = {"support": None, "resistance": None, "near_support": False, "near_resistance": False}
        vela = {"open": 1.0, "close": 1.0, "max": 1.0, "min": 1.0}
        self.assertIsNone(visible_level_ahead("CALL", zona, vela))
        self.assertEqual(evaluate_respect("CALL", zona, vela), (True, RESPECT_OK_CLEAR))


class FlagTest(unittest.TestCase):
    def test_flag_desligada_volta_ao_comportamento_anterior(self) -> None:
        original = sr_respect.SR_VISIBLE_LEVELS_ENABLED
        sr_respect.SR_VISIBLE_LEVELS_ENABLED = False
        try:
            serie = _com_vela_em_formacao(EURGBP_1836)
            zona = build_zone(serie)
            self.assertNotIn("visible_resistances", zona)
            self.assertTrue(evaluate_respect("CALL", zona, serie[-1])[0])
        finally:
            sr_respect.SR_VISIBLE_LEVELS_ENABLED = original


if __name__ == "__main__":
    unittest.main()
