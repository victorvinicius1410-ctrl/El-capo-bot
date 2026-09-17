"""Filtro de pavio (11/09/2026).

Reproduz as três perdas da conta Sergio em 11/09, todas CALL M1 com as velas
reais da corretora: EURAUD-OTC 14:02 (velas anteriores com pavio de 77%, 78% e
54%), USDCHF-OTC 14:07 (última vela com 92% de pavio) e NZDUSD-OTC 14:20 (68% e
77% nas duas últimas). Pela regra antiga as três passavam: o motor só olhava a
vela em formação, e só tirava pontos.
"""

import unittest
from unittest import mock

from backend import main, wick_filter
from backend.wick_filter import (
    WICK_FORMING,
    WICK_HEAVY_SEQUENCE,
    WICK_LAST_CLOSED,
    WICK_OK,
    WICK_SEM_DADOS,
    evaluate_wicks,
    wick_ratio,
)


def _vela(o, c, h, l, t=0):
    return {"from": t, "open": o, "close": c, "max": h, "min": l}


def _base(n=30):
    """Velas de corpo cheio, range 10 (sem pavio): ATR = 10."""
    velas = []
    preco = 100.0
    for i in range(n):
        velas.append(_vela(preco, preco + 10, preco + 10, preco, t=i * 60))
        preco += 10
    return velas


def _pavio(base, pavio=0.8, faixa=10.0):
    """Vela com `pavio` de fração do range, range `faixa`."""
    corpo = faixa * (1 - pavio)
    return _vela(base, base + corpo, base + corpo + faixa * pavio / 2, base - faixa * pavio / 2)


def _atual(preco):
    return _vela(preco, preco, preco, preco)


class RazaoDePavioTest(unittest.TestCase):
    def test_corpo_cheio_nao_tem_pavio(self) -> None:
        self.assertEqual(wick_ratio(_vela(1, 2, 2, 1)), 0.0)

    def test_doji_e_todo_pavio(self) -> None:
        self.assertEqual(wick_ratio(_vela(1.5, 1.5, 2, 1)), 1.0)

    def test_vela_sem_range(self) -> None:
        self.assertEqual(wick_ratio(_vela(1, 1, 1, 1)), 0.0)

    def test_aceita_high_low_do_dataset(self) -> None:
        self.assertAlmostEqual(wick_ratio({"open": 1, "close": 1.5, "high": 2, "low": 1}), 0.5)


class RegrasDePavioTest(unittest.TestCase):
    def test_velas_limpas_liberam(self) -> None:
        velas = _base() + [_atual(400.0)]
        self.assertEqual(evaluate_wicks(velas)[:2], (True, WICK_OK))

    def test_ultima_fechada_com_muito_pavio_barra(self) -> None:
        velas = _base() + [_pavio(400.0, 0.7), _atual(401.0)]
        self.assertEqual(evaluate_wicks(velas, check_forming=False)[:2], (False, WICK_LAST_CLOSED))

    def test_duas_das_tres_anteriores_com_pavio_barra(self) -> None:
        velas = _base() + [_pavio(400.0, 0.55), _vela(401, 411, 411, 401), _pavio(411.0, 0.55), _atual(412.0)]
        self.assertEqual(evaluate_wicks(velas, check_forming=False)[:2], (False, WICK_HEAVY_SEQUENCE))

    def test_uma_so_com_pavio_moderado_libera(self) -> None:
        velas = _base() + [_pavio(400.0, 0.55), _vela(401, 411, 411, 401), _vela(411, 421, 421, 411), _atual(421.0)]
        self.assertEqual(evaluate_wicks(velas, check_forming=False)[:2], (True, WICK_OK))

    def test_vela_minuscula_nao_conta_como_pavio(self) -> None:
        """Doji de um tick "é 100% pavio" mas não é o pavio que o cliente vê."""
        velas = _base() + [_pavio(400.0, 1.0, faixa=1.0), _atual(400.0)]
        self.assertEqual(evaluate_wicks(velas, check_forming=False)[:2], (True, WICK_OK))

    def test_vela_em_formacao_com_pavio_barra_na_analise(self) -> None:
        velas = _base() + [_pavio(400.0, 0.7)]
        self.assertEqual(evaluate_wicks(velas)[:2], (False, WICK_FORMING))

    def test_vela_em_formacao_ignorada_no_disparo(self) -> None:
        velas = _base() + [_pavio(400.0, 0.7)]
        self.assertEqual(evaluate_wicks(velas, check_forming=False)[:2], (True, WICK_OK))

    def test_poucas_velas_libera_sem_dados(self) -> None:
        self.assertEqual(evaluate_wicks(_base(5))[:2], (True, WICK_SEM_DADOS))

    def test_desligado_libera(self) -> None:
        velas = _base() + [_pavio(400.0, 0.9), _atual(401.0)]
        with mock.patch.object(wick_filter, "WICK_FILTER_ENABLED", False):
            self.assertEqual(evaluate_wicks(velas)[:2], (True, WICK_OK))


# Velas reais (M1, corretora) antes de cada entrada perdida do Sergio em 11/09.
# (open, close, max, min) das 3 últimas fechadas; as anteriores são limpas com o
# ATR medido naquele momento.
CASOS_SERGIO = {
    # EURAUD-OTC CALL 14:02 — ATR 0.00106; pavios 77%, 78%, 54%.
    "EURAUD": (0.00106, [(1.616615, 1.616425, 1.617265, 1.616425), (1.616375, 1.616605, 1.617305, 1.616265),
                        (1.616655, 1.617145, 1.617665, 1.616605)]),
    # USDCHF-OTC CALL 14:07 — ATR 0.0002086; última vela com 92% de pavio.
    "USDCHF": (0.0002086, [(0.810425, 0.810305, 0.810445, 0.810145), (0.810335, 0.810465, 0.810565, 0.810335),
                          (0.810455, 0.810465, 0.810555, 0.810425)]),
    # NZDUSD-OTC CALL 14:20 — ATR 0.0011593; 68% e 77% nas duas últimas.
    "NZDUSD": (0.0011593, [(0.582705, 0.583495, 0.583895, 0.582705), (0.583535, 0.584045, 0.584515, 0.582925),
                          (0.584035, 0.583715, 0.584805, 0.583395)]),
}


def _serie_real(atr, ultimas):
    velas = []
    preco = ultimas[0][0] - 30 * atr
    for i in range(30):
        velas.append(_vela(preco, preco + atr, preco + atr, preco, t=i * 60))
    velas += [_vela(o, c, h, l, t=(30 + i) * 60) for i, (o, c, h, l) in enumerate(ultimas)]
    px = ultimas[-1][1]
    return velas + [_atual(px)]


class PerdasDoSergioTest(unittest.TestCase):
    def test_as_tres_perdas_seriam_barradas_no_disparo(self) -> None:
        for nome, (atr, ultimas) in CASOS_SERGIO.items():
            with self.subTest(nome):
                pode, motivo, _ = evaluate_wicks(_serie_real(atr, ultimas), check_forming=False)
                self.assertFalse(pode, motivo)
                self.assertIn(motivo, {WICK_LAST_CLOSED, WICK_HEAVY_SEQUENCE})


class PavioNoDisparoTest(unittest.IsolatedAsyncioTestCase):
    async def _rodar(self, velas):
        cand = {"symbol": "EURAUD-OTC", "direction": "CALL", "signal": "CALL", "wick_reason": WICK_OK}
        with mock.patch.object(
            main, "call_bullex_service", new=mock.AsyncMock(return_value=(200, {"ok": True, "candles": velas}))
        ), mock.patch.object(main, "extract_candles", return_value=velas), mock.patch.object(
            main, "build_zone", return_value={"support": None, "resistance": None}
        ), mock.patch.object(main, "evaluate_respect", return_value=(True, "OK_FORA_DA_REGIAO")):
            return await main.revalidate_level_before_entry("user-pavio", cand, "M1"), cand

    async def test_barra_com_pavio_na_vela_recem_fechada(self) -> None:
        atr, ultimas = CASOS_SERGIO["USDCHF"]
        motivo, cand = await self._rodar(_serie_real(atr, ultimas)[:-1])
        self.assertEqual(motivo, "PAVIO_NA_ENTRADA")
        self.assertEqual(cand["wick_entry_reason"], WICK_LAST_CLOSED)

    async def test_libera_velas_limpas(self) -> None:
        motivo, cand = await self._rodar(_base())
        self.assertIsNone(motivo)
        self.assertEqual(cand["wick_entry_reason"], WICK_OK)

    async def test_nivel_contra_continua_barrando_antes_do_pavio(self) -> None:
        velas = _base()
        cand = {"symbol": "EURAUD-OTC", "direction": "CALL", "signal": "CALL"}
        with mock.patch.object(
            main, "call_bullex_service", new=mock.AsyncMock(return_value=(200, {"ok": True, "candles": velas}))
        ), mock.patch.object(main, "extract_candles", return_value=velas), mock.patch.object(
            main, "build_zone", return_value={"support": None, "resistance": 1.0}
        ), mock.patch.object(main, "evaluate_respect", return_value=(False, "CONTRA_O_NIVEL")):
            self.assertEqual(
                await main.revalidate_level_before_entry("user-pavio", cand, "M1"), "SR_ZONE_NA_ENTRADA"
            )


class CamposDoVereditoTest(unittest.TestCase):
    def test_veredito_de_pavio_sobrevive_as_listas_fixas(self) -> None:
        from backend import robot_persistence

        self.assertIn("wick_reason", main.ANALYSIS_DETAIL_FIELDS)
        self.assertIn("wick_entry_reason", main.ANALYSIS_DETAIL_FIELDS)
        self.assertIn("wick_reason", robot_persistence.TRADE_ANALYSIS_FIELDS)
        self.assertIn("wick_entry_reason", robot_persistence.TRADE_ANALYSIS_FIELDS)

    def test_pavio_e_bloqueio_que_ninguem_dispensa(self) -> None:
        """Vale para todo mundo MENOS o modo LIVE — ver o teste seguinte."""
        from backend import reversion_strategy, signal_engine

        self.assertIn("WICK_EXCESS", main.CRITICAL_TRADE_BLOCKS)
        self.assertIn("WICK_EXCESS", main.RECOVERY_NON_RELAXABLE_TRADE_BLOCKS)
        self.assertIn("WICK_EXCESS", reversion_strategy.REVZ_NON_WAIVABLE)
        self.assertIn("WICK_EXCESS", signal_engine.NAMED_STRATEGY_NON_WAIVABLE)

    def test_modo_live_e_a_unica_excecao_ao_pavio(self) -> None:
        """Exceção aberta pelo dono em 15/09/2026, revertendo a regra de 11/09.

        A entrada do modo LIVE é curta (teto de
        ``LIVE_MAX_EXPIRATION_MINUTES``) e nessa escala o pavio não manda na
        vela. A exceção é SÓ do modo LIVE: o motor clássico, a REV-Z, as
        estratégias nomeadas e a recuperação continuam barrando.
        """
        from backend import live_demo_mode

        self.assertNotIn("WICK_EXCESS", live_demo_mode.LIVE_NON_WAIVABLE)


if __name__ == "__main__":
    unittest.main()
