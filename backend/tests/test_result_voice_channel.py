"""Canal de voz do placar (`result_voice`) — narração sem mexer no ciclo.

Regressão de 2026-07-29: para fazer o El Capo falar o placar em todas as
operações, o display de resultado tinha subido para 12s e `unseen_result`
passara a ser marcado sempre. Isso atrasava `prepare_cycle` dentro da vela e
deixava o payload preso em WIN/LOSS (overlay anunciava entradas que o robô
nunca enviava). A fala agora usa `result_voice`, isolado do ciclo.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from backend.auto_trader import (
    AutoTrader,
    RESULT_VOICE_TTL_SECONDS,
    STATUS_WAITING_ENTRY,
    STATUS_WAITING_NEXT_CYCLE,
    STATUS_WIN,
    utc_now,
)


def pending_trade(order_id: str, amount: float = 50.0) -> dict:
    return {
        "order_id": order_id,
        "active": "EURUSD-OTC",
        "direction": "CALL",
        "amount": amount,
        "result": "PENDING_RESULT",
    }


class ResultVoiceChannelTests(unittest.TestCase):
    def test_display_window_stays_five_seconds(self) -> None:
        """Janela operacional não pode crescer: ela bloqueia `prepare_cycle`."""
        trader = AutoTrader()
        trader.start("user-display")
        trader.record_trade("user-display", pending_trade("v-1"))

        _, state = trader.finish_trade("user-display", "v-1", "WIN", 44.0)

        self.assertEqual(
            state.result_display_until,
            state.result_received_at + timedelta(seconds=5),
        )

    def test_finish_trade_publishes_result_voice(self) -> None:
        trader = AutoTrader()
        trader.start("user-voice")
        trader.record_trade("user-voice", pending_trade("v-2"))

        _, state = trader.finish_trade("user-voice", "v-2", "WIN", 44.0)

        voice = state.result_voice
        self.assertIsNotNone(voice)
        self.assertEqual(voice["order_id"], "v-2")
        self.assertEqual(voice["result"], "WIN")
        self.assertEqual(voice["wins"], 1)
        self.assertEqual(voice["losses"], 0)
        self.assertEqual(voice["profit"], state.profit)

    def test_result_voice_survives_next_cycle_and_expires_by_ttl(self) -> None:
        """A fala do placar continua disponível após o ciclo seguir."""
        trader = AutoTrader()
        trader.start("user-voice-ttl")
        trader.record_trade("user-voice-ttl", pending_trade("v-3"))
        trader.finish_trade("user-voice-ttl", "v-3", "LOSS", 0)

        state = trader.get("user-voice-ttl")
        state.status = STATUS_WAITING_NEXT_CYCLE
        state.result_display_until = None

        payload = state.to_dict()
        self.assertEqual(payload["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(payload["result_voice"]["result"], "LOSS")

        state.result_voice["at"] = (
            utc_now() - timedelta(seconds=RESULT_VOICE_TTL_SECONDS + 1)
        ).isoformat()
        expired = state.to_dict()

        self.assertIsNone(expired["result_voice"])
        self.assertIsNone(state.result_voice)

    def test_unseen_result_does_not_mask_new_entry(self) -> None:
        """Com sinal travado, o payload mostra a entrada, não o WIN anterior."""
        trader = AutoTrader()
        trader.start("user-mask")
        trader.record_trade("user-mask", pending_trade("v-4"))
        trader.finish_trade("user-mask", "v-4", "WIN", 44.0)

        state = trader.get("user-mask")
        state.unseen_result = True
        state.result_display_until = utc_now() - timedelta(seconds=1)
        state.status = STATUS_WAITING_ENTRY
        state.pending_signal = {"symbol": "GBPUSD-OTC", "signal": "PUT"}

        payload = state.to_dict()

        self.assertEqual(payload["status"], STATUS_WAITING_ENTRY)
        self.assertIsNotNone(payload["pending_signal"])

    def test_unseen_result_still_shows_win_when_idle(self) -> None:
        """Sem operação nova, o comportamento de tela fechada é preservado."""
        trader = AutoTrader()
        trader.start("user-idle")
        trader.record_trade("user-idle", pending_trade("v-5"))
        trader.finish_trade("user-idle", "v-5", "WIN", 44.0)

        state = trader.get("user-idle")
        state.result_display_until = utc_now() - timedelta(seconds=1)
        state.status = STATUS_WAITING_NEXT_CYCLE
        state.unseen_result = True
        state.cycle_result = "WIN"

        payload = state.to_dict()

        self.assertTrue(payload["unseen_result"])
        self.assertEqual(payload["status"], STATUS_WIN)

    def test_reset_score_clears_result_voice(self) -> None:
        trader = AutoTrader()
        trader.start("user-reset")
        trader.record_trade("user-reset", pending_trade("v-6"))
        trader.finish_trade("user-reset", "v-6", "WIN", 44.0)

        state = trader.reset_score("user-reset")

        self.assertIsNone(state.result_voice)


if __name__ == "__main__":
    unittest.main()
