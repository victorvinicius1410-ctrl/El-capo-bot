"""Volume do M5/M15: varre o mercado inteiro e não pula vela à toa.

Em 27/09/2026 a conta M5 do Sergio ficou 5 h ligada: 12 sinais, 0 ordens.
Medido nas contas M5 de 25-27/09: 66% das varreduras paradas pelo early stop
terminavam sem sinal (parava em par com confiança 44 que o ciclo reprovava),
só 10 de 21 pares eram vistos por ciclo e, depois de cada resultado, o robô
dormia a vela inteira (5 min no M5, 15 no M15). O M1 fica como estava.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from backend import auto_trader as auto_trader_module
from backend import main
from backend.auto_trader import AutoTrader
from backend.signal_engine import seconds_until_analysis_after_result

# 27/09/2026 13:15:00 UTC — abertura de vela M1, M5 e M15.
VIRADA = datetime(2026, 9, 27, 13, 15, 0, tzinfo=timezone.utc)


def _estado(timeframe: str, finished_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        timeframe=timeframe,
        last_trade={"result": "WIN", "finished_at": finished_at.isoformat()},
        gale_pending=False,
        pending_signal=None,
    )


class VarreduraCompletaTest(unittest.TestCase):
    def test_m5_e_m15_varrem_tudo_m1_nao(self) -> None:
        self.assertTrue(main.timeframe_scans_full_market("M5"))
        self.assertTrue(main.timeframe_scans_full_market("m15"))
        self.assertFalse(main.timeframe_scans_full_market("M1"))
        self.assertFalse(main.timeframe_scans_full_market(None))

    def test_m5_analisa_todos_os_pares_otc(self) -> None:
        total = len(main.resolve_analysis_assets("OTC"))
        self.assertGreater(total, main.ROBOT_ANALYSIS_MAX_ASSETS)
        self.assertEqual(main.cycle_analysis_max_assets("M5", "OTC"), total)
        self.assertEqual(main.cycle_analysis_max_assets("M15", "OTC"), total)

    def test_m1_continua_com_o_teto_do_ciclo(self) -> None:
        self.assertEqual(
            main.cycle_analysis_max_assets("M1", "OTC"),
            main.ROBOT_ANALYSIS_MAX_ASSETS,
        )


class SonoDepoisDoResultadoTest(unittest.TestCase):
    def test_m5_nao_dorme_a_vela_em_que_o_resultado_chegou(self) -> None:
        agora = VIRADA + timedelta(seconds=2)
        estado = _estado("M5", VIRADA + timedelta(seconds=1))
        with patch.object(main, "utc_now", return_value=agora):
            self.assertIsNone(main.post_trade_analysis_wait(estado))

    def test_m15_nao_dorme_a_vela_em_que_o_resultado_chegou(self) -> None:
        agora = VIRADA + timedelta(seconds=3)
        estado = _estado("M15", VIRADA + timedelta(seconds=1))
        with patch.object(main, "utc_now", return_value=agora):
            self.assertIsNone(main.post_trade_analysis_wait(estado))

    def test_m1_continua_dormindo_ate_a_proxima_vela(self) -> None:
        agora = VIRADA + timedelta(seconds=2)
        estado = _estado("M1", VIRADA + timedelta(seconds=1))
        with patch.object(main, "utc_now", return_value=agora):
            self.assertAlmostEqual(main.post_trade_analysis_wait(estado), 58.0, places=3)

    def test_m5_resultado_depois_da_janela_ainda_dorme(self) -> None:
        # Resultado fora da virada (ex.: TIMEOUT no meio da vela): a janela de
        # análise desta vela já passou, então espera a próxima como antes.
        agora = VIRADA + timedelta(seconds=121)
        estado = _estado("M5", VIRADA + timedelta(seconds=120))
        with patch.object(main, "utc_now", return_value=agora):
            self.assertAlmostEqual(main.post_trade_analysis_wait(estado), 179.0, places=3)


class ReagendaDepoisDoResultadoTest(unittest.TestCase):
    """O resultado sai da tela no segundo ~6 da vela, com a janela aberta."""

    def _reagenda(self, timeframe: str, fechou: datetime, agora: datetime) -> float:
        trader = AutoTrader()
        state = trader.start("00000000-0000-4000-8000-000000000027")
        state.timeframe = timeframe
        state.last_trade = {"result": "WIN", "finished_at": fechou.isoformat()}
        with patch.object(auto_trader_module, "utc_now", return_value=agora):
            state = trader.reset_cycle_after_result("00000000-0000-4000-8000-000000000027")
        return (state.next_cycle_at - agora).total_seconds()

    def test_m5_analisa_a_vela_em_que_o_resultado_chegou(self) -> None:
        espera = self._reagenda("M5", VIRADA + timedelta(seconds=0.6), VIRADA + timedelta(seconds=5.9))
        self.assertEqual(espera, 0)

    def test_m15_analisa_a_vela_em_que_o_resultado_chegou(self) -> None:
        espera = self._reagenda("M15", VIRADA + timedelta(seconds=0.6), VIRADA + timedelta(seconds=5.9))
        self.assertEqual(espera, 0)

    def test_m1_continua_indo_para_a_proxima_vela(self) -> None:
        espera = self._reagenda("M1", VIRADA + timedelta(seconds=0.6), VIRADA + timedelta(seconds=5.9))
        self.assertAlmostEqual(espera, 60 - 5.9 + 5, delta=1)

    def test_m5_espera_a_janela_abrir_se_ainda_nao_abriu(self) -> None:
        self.assertEqual(
            seconds_until_analysis_after_result("M5", VIRADA.timestamp() + 0.5, VIRADA.timestamp() + 2),
            3,
        )

    def test_m5_janela_ja_fechada_vai_para_a_proxima_vela(self) -> None:
        espera = seconds_until_analysis_after_result(
            "M5", VIRADA.timestamp() + 30, VIRADA.timestamp() + 35
        )
        self.assertEqual(espera, 300 - 35 + 5)

    def test_m5_operacao_da_vela_anterior_segue_a_regra_antiga(self) -> None:
        espera = seconds_until_analysis_after_result(
            "M5", VIRADA.timestamp() - 10, VIRADA.timestamp() + 6
        )
        self.assertEqual(espera, 300 - 6 + 5)


class FimDaExibicaoNoSnapshotTest(unittest.TestCase):
    """Caminho real (28/09/2026): o snapshot do painel reagenda antes do worker.

    WIN às 11:35:00,9, resultado na tela até 11:35:05,9, snapshot às 11:35:06,2
    marcou a próxima análise para 11:40:05 — a vela inteira perdida.
    """

    def _snapshot(self, timeframe: str) -> float:
        from backend.auto_trader import STATUS_WIN

        trader = AutoTrader()
        state = trader.start("00000000-0000-4000-8000-000000000028")
        state.timeframe = timeframe
        state.status = STATUS_WIN
        fechou = VIRADA + timedelta(seconds=0.94)
        state.result_display_until = fechou + timedelta(seconds=5)
        state.last_trade = {"result": "WIN", "finished_at": fechou.isoformat()}
        agora = VIRADA + timedelta(seconds=6.2)
        with patch.object(auto_trader_module, "utc_now", return_value=agora):
            state.to_dict()
        return (state.next_cycle_at - agora).total_seconds()

    def test_m5_analisa_a_mesma_vela(self) -> None:
        self.assertEqual(self._snapshot("M5"), 0)

    def test_m15_analisa_a_mesma_vela(self) -> None:
        self.assertEqual(self._snapshot("M15"), 0)

    def test_m1_segue_para_a_proxima_vela(self) -> None:
        self.assertAlmostEqual(self._snapshot("M1"), 60 - 6.2 + 5, delta=1)


if __name__ == "__main__":
    unittest.main()
