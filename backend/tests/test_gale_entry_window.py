"""O sono pós-resultado não pode comer a janela de entrada do gale.

`seconds_until_next_candle_after_trade` volta a ~60s exatamente na virada da
vela. Como o laço do worker dormia `min(esse valor, 5s)` e fazia `continue`,
ele pulava `execute_robot_worker_cycle` — que é quem envia a ordem — e acordava
no segundo ~5, depois da janela de compra (0-3s). Resultado em produção: 38
gales disparados em 7 dias e nenhuma ordem de gale enviada.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend import main
from backend.auto_trader import RobotState

# Virada de vela M1 exata: `timestamp() % 60` == 0.
CANDLE_OPEN = datetime(2026, 9, 15, 14, 27, 0, tzinfo=timezone.utc)


def _state_after_loss(*, seconds_ago: float, timeframe: str = "M1") -> RobotState:
    """Estado logo depois de um LOSS que fechou na vela anterior."""
    state = RobotState()
    state.enabled = True
    state.timeframe = timeframe
    state.last_trade = {
        "order_id": "14264468112",
        "result": "LOSS",
        "finished_at": (CANDLE_OPEN - timedelta(seconds=seconds_ago)).isoformat(),
    }
    return state


def _simulate_worker_ticks(*, use_fix: bool) -> list[float]:
    """Roda o laço do worker em relógio virtual, com um gale pendente.

    Reproduz a ordem real das decisões de ``robot_worker``: primeiro o guard
    pós-trade (que pode dormir e pular o ciclo), depois o tick que envia a
    ordem, depois o sleep fino de ``robot_worker_entry_wait_seconds``.

    Args:
        use_fix: True usa ``post_trade_analysis_wait`` (com o conserto);
            False usa o guard cru, como era antes.

    Returns:
        O segundo da vela de cada tick que chegou a executar o ciclo.
    """
    interval = 60.0
    # LOSS fechou no segundo ~1,7 da vela anterior — o caso de produção.
    finished_at = CANDLE_OPEN - timedelta(seconds=58.3)
    state = RobotState()
    state.enabled = True
    state.timeframe = "M1"
    state.gale_pending = True
    state.gale_step = 1
    state.gale_amount = 20.0
    state.pending_signal = {"symbol": "GBPUSD-OTC", "direction": "PUT", "is_gale": True}
    state.last_trade = {
        "order_id": "14264468112",
        "result": "LOSS",
        "finished_at": finished_at.isoformat(),
    }

    now = finished_at + timedelta(seconds=0.5)
    deadline = CANDLE_OPEN + timedelta(seconds=10)
    ticks: list[float] = []
    while now < deadline:
        with patch.object(main, "utc_now", return_value=now):
            wait = (
                main.post_trade_analysis_wait(state)
                if use_fix
                else main.seconds_until_next_candle_after_trade(state)
            )
        if wait is not None:
            now += timedelta(seconds=max(0.5, min(float(wait), 5.0)))
            continue
        ticks.append(now.timestamp() % interval)
        seconds_until_entry = max(0.0, (CANDLE_OPEN - now).total_seconds())
        now += timedelta(
            seconds=max(0.25, main.robot_worker_entry_wait_seconds(seconds_until_entry))
        )
    return ticks


class GaleEntryWindowSimulationTests(unittest.TestCase):
    """A ordem do gale tem que cair na janela de compra (0-3s)."""

    def test_com_o_conserto_o_gale_pega_a_abertura_da_vela(self) -> None:
        ticks = _simulate_worker_ticks(use_fix=True)
        na_janela = [second for second in ticks if second <= 3.0]
        self.assertTrue(
            na_janela,
            f"nenhum tick na janela 0-3s; ticks perto da virada: {ticks[-6:]}",
        )

    def test_sem_o_conserto_o_gale_perdia_a_janela(self) -> None:
        """Documenta o bug: o sono de 5s caía exatamente na virada."""
        ticks = _simulate_worker_ticks(use_fix=False)
        self.assertFalse(
            [second for second in ticks if second <= 3.0],
            "o guard cru deveria pular a janela (era o bug)",
        )
        # O primeiro tick da vela nova só acontecia depois do segundo 3.
        pos_virada = [second for second in ticks if second < 30.0]
        self.assertTrue(pos_virada)
        self.assertGreater(min(pos_virada), 3.0)


class PostTradeAnalysisWaitTests(unittest.TestCase):
    def test_sem_sinal_pendente_o_sono_continua_valendo(self) -> None:
        """Comportamento preservado: sem nada para enviar, pula análise pesada."""
        state = _state_after_loss(seconds_ago=58)
        with patch.object(main, "utc_now", return_value=CANDLE_OPEN):
            wait = main.post_trade_analysis_wait(state)
        self.assertIsNotNone(wait)
        self.assertAlmostEqual(float(wait or 0), 60.0, delta=1.0)

    def test_gale_pendente_na_virada_da_vela_nao_dorme(self) -> None:
        """O caso exato que matava o gale em produção."""
        state = _state_after_loss(seconds_ago=58)
        state.gale_pending = True
        state.gale_step = 1
        state.gale_amount = 20.0
        state.pending_signal = {"symbol": "GBPUSD-OTC", "direction": "PUT", "is_gale": True}
        with patch.object(main, "utc_now", return_value=CANDLE_OPEN):
            # O guard cru ainda manda esperar a vela inteira: era esse valor
            # que virava `sleep(5)` em cima da janela 0-3s.
            self.assertAlmostEqual(
                float(main.seconds_until_next_candle_after_trade(state) or 0),
                60.0,
                delta=1.0,
            )
            self.assertIsNone(
                main.post_trade_analysis_wait(state),
                "com gale pendente o ciclo tem que rodar na abertura da vela",
            )

    def test_sinal_normal_pendente_tambem_nao_dorme(self) -> None:
        """Entrada normal agendada para esta vela sofria do mesmo corte."""
        state = _state_after_loss(seconds_ago=58)
        state.pending_signal = {"symbol": "EURUSD-OTC", "direction": "CALL"}
        with patch.object(main, "utc_now", return_value=CANDLE_OPEN):
            self.assertIsNone(main.post_trade_analysis_wait(state))

    def test_gale_pendente_dispensa_o_sono_em_qualquer_ponto_da_vela(self) -> None:
        for seconds_ago in (0.5, 10, 30, 58):
            with self.subTest(seconds_ago=seconds_ago):
                state = _state_after_loss(seconds_ago=seconds_ago)
                state.gale_pending = True
                with patch.object(main, "utc_now", return_value=CANDLE_OPEN):
                    self.assertIsNone(main.post_trade_analysis_wait(state))

    def test_sem_operacao_anterior_nao_ha_sono(self) -> None:
        state = RobotState()
        state.enabled = True
        with patch.object(main, "utc_now", return_value=CANDLE_OPEN):
            self.assertIsNone(main.post_trade_analysis_wait(state))

    def test_resultado_pendente_nao_aciona_o_sono(self) -> None:
        """Ordem ainda aberta não é 'pós-trade' — quem cuida disso é outro guard."""
        state = _state_after_loss(seconds_ago=5)
        state.last_trade["result"] = "PENDING_RESULT"
        with patch.object(main, "utc_now", return_value=CANDLE_OPEN):
            self.assertIsNone(main.post_trade_analysis_wait(state))


if __name__ == "__main__":
    unittest.main()
