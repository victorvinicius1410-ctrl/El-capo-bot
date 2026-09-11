"""Política SR_ZONE: o robô respeita a região de suporte e resistência.

Pedido original do cliente em 2026-07-29 ("não pegar operações em momentos que
o ativo está em suporte e resistência"). O backtest walk-forward com candles
reais (8 ativos OTC, 1000 velas M1) mostrou que as entradas dentro da zona de
nível aprovadas pelo portão de confiança do usuário deram 0% de acerto, então
o bloqueio virou crítico (reprova a entrada) e não uma penalidade de score.

**Política revista em 2026-09-09, a pedido do mesmo dono.** O veto era cego:
barrava qualquer entrada dentro da região, inclusive a que existe justamente
para operá-la. Como ``_price_action_setup`` só classifica um setup como
``SUPPORT_RESISTANCE`` quando o preço está colado no nível, o veto anulava esse
setup por completo — medido em 8.000 leituras: 383 de 383 bloqueados, 100%. O
motor somava os 15 pontos do setup no score e matava a entrada na mesma
passagem.

Respeitar a região passou a significar três coisas, e é o que estes testes
afirmam:

- contra o nível (CALL na resistência, PUT no suporte): recusado sempre;
- dentro da região a favor: liberado só com rejeição confirmada e espaço até o
  nível oposto;
- fora da região: o filtro não tem o que dizer.

A regra vive em ``backend/sr_respect.py``; ``SR_RESPECT=false`` devolve o veto
cego. Documentação: `docs/ESTRATEGIA.md` §"Política de suporte / resistência".
"""

import unittest
from unittest import mock

from backend import main, signal_engine, sr_respect

_VERTEX_ORIGINAL = signal_engine.VERTEX_ENABLED


def setUpModule() -> None:
    """Desliga a Vertex: aqui se testa o motor clássico.

    `VERTEX_ENABLED` é lido do ambiente na importação, então com a estratégia
    ligada no `.env` estes testes passariam a exercitar o override — que
    devolve WAIT sempre que o indicador não está no extremo — em vez do portão
    clássico que eles se propõem a medir.
    """
    signal_engine.VERTEX_ENABLED = False


def tearDownModule() -> None:
    signal_engine.VERTEX_ENABLED = _VERTEX_ORIGINAL
from backend.auto_trader import AutoTrader
from backend.signal_engine import analyze_signal
from tests.test_candle_analysis import make_support_reversal_candles


def make_mid_range_candles() -> list[dict[str, float]]:
    """
    Série com range definido (1.1000–1.1114) e preço subindo pelo meio dele.

    O último fechamento fica a mais de 40 pips do topo e do fundo, bem acima da
    tolerância de zona (12% do range), então nenhum nível está "colado".

    Returns:
        Lista de velas em ordem cronológica pronta para `analyze_signal`.
    """
    candles: list[dict[str, float]] = []
    for index in range(12):
        going_up = index % 2 == 0
        low = 1.10000 + 0.00090 * index
        high = low + 0.00150
        candles.append(
            {
                "open": round(low if going_up else high, 6),
                "close": round(high if going_up else low, 6),
                "max": round(high, 6),
                "min": round(low, 6),
                "volume": 1,
            }
        )
    price = 1.10450
    for _ in range(12):
        open_price = price
        close_price = open_price + 0.00018
        candles.append(
            {
                "open": round(open_price, 6),
                "close": round(close_price, 6),
                "max": round(close_price + 0.00002, 6),
                "min": round(open_price - 0.00002, 6),
                "volume": 1,
            }
        )
        price = close_price
    return candles


class SrZoneSignalTests(unittest.TestCase):
    def test_rejeicao_confirmada_no_suporte_e_liberada(self) -> None:
        """Operar a região é entrar na rejeição do nível, não fugir dela.

        Esta é a série de reversão no suporte: CALL com pavio inferior, corpo
        cheio e a resistência longe. Até 2026-09-09 o veto cego a barrava — o
        único caso em que o motor de fato reconhecia um nível era também o
        único que ele nunca deixava operar.
        """
        signal = analyze_signal(
            "EURUSD-OTC",
            make_support_reversal_candles(),
            timeframe="M1",
            strategy_mode="conservative",
            payout=90,
        )

        self.assertTrue(signal["in_support_resistance_zone"])
        self.assertEqual(signal["signal"], "CALL")
        self.assertEqual(signal["sr_respect_reason"], "OK_REJEICAO_NO_NIVEL")
        self.assertIn("SR_ZONE", signal["approved_filters"])
        self.assertNotIn("SR_ZONE", signal["blocked_filters"])

    def test_entrada_contra_o_nivel_continua_barrada(self) -> None:
        """A violação que o dono relatou: vender colado no suporte.

        Mesma série de suporte, mas forçando PUT. É contra o nível e é recusada
        com ou sem rejeição — nenhuma estratégia dispensa isto.
        """
        candles = make_support_reversal_candles()
        contexto = signal_engine._support_resistance_context(candles)
        self.assertTrue(contexto["near_support"])

        respeita, motivo = signal_engine.evaluate_respect("PUT", contexto, candles[-1])
        self.assertFalse(respeita)
        self.assertEqual(motivo, "CONTRA_O_NIVEL")

    def test_veto_cego_volta_com_a_flag_desligada(self) -> None:
        """``SR_RESPECT=false`` devolve o comportamento anterior por inteiro."""
        with mock.patch.object(sr_respect, "SR_RESPECT_ENABLED", False):
            contexto = signal_engine._support_resistance_context(
                make_support_reversal_candles()
            )
        self.assertEqual(contexto["source"], "WINDOW")

    def test_price_outside_sr_zone_keeps_filter_approved(self) -> None:
        signal = analyze_signal(
            "EURUSD-OTC",
            make_mid_range_candles(),
            timeframe="M1",
            strategy_mode="conservative",
            payout=90,
        )

        self.assertFalse(signal["in_support_resistance_zone"])
        self.assertIn("SR_ZONE", signal["approved_filters"])
        self.assertNotIn("SR_ZONE", signal["blocked_filters"])

    def test_disabling_flag_restores_classic_behaviour(self) -> None:
        with mock.patch.object(signal_engine, "SR_ZONE_HARD_BLOCK", False):
            signal = analyze_signal(
                "EURUSD-OTC",
                make_support_reversal_candles(),
                timeframe="M1",
                strategy_mode="conservative",
                payout=90,
            )

        self.assertTrue(signal["in_support_resistance_zone"])
        self.assertNotIn("SR_ZONE", signal["blocked_filters"])
        self.assertTrue(signal["trade_allowed"])


class SrZoneGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trader = AutoTrader()
        self.state = self.trader.start("user-sr-zone")
        self.state.min_confidence = 80
        self.state.min_payout = 80.0

    def test_sr_zone_is_a_critical_block(self) -> None:
        self.assertIn("SR_ZONE", main.CRITICAL_TRADE_BLOCKS)

    def test_sr_zone_is_not_relaxed_in_recovery_mode(self) -> None:
        self.assertIn("SR_ZONE", main.RECOVERY_NON_RELAXABLE_TRADE_BLOCKS)

    def test_candidate_in_sr_zone_never_reaches_order(self) -> None:
        candidate = {
            "symbol": "EURUSD-OTC",
            "signal": "CALL",
            "direction": "CALL",
            "confidence": 100,
            "payout": 90.0,
            "strategy_score": 80,
            "trade_allowed": True,
            "blocked_filters": ["SR_ZONE"],
            "in_support_resistance_zone": True,
        }

        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=int(self.state.min_confidence),
            )
        )


if __name__ == "__main__":
    unittest.main()
