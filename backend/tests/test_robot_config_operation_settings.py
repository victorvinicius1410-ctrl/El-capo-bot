"""Configuração enviada ao iniciar operação deve ser aceita e persistida."""

from __future__ import annotations

import unittest

from backend.auto_trader import AutoTrader, RobotConfigUpdate


class RobotConfigOperationSettingsTests(unittest.TestCase):
    """Garante que timeframe, mercado, entrada e stops sobrevivem ao /robot/config."""

    def setUp(self) -> None:
        self.trader = AutoTrader()

    def test_config_update_accepts_non_default_timeframe_and_market(self) -> None:
        update = RobotConfigUpdate.model_validate(
            {
                "timeframe": "M5",
                "market_mode": "OPEN",
                "entry_value": 12.5,
                "stop_win": 90,
                "stop_loss": 45,
                "martingale_enabled": True,
                "martingale_steps": 2,
                "martingale_multiplier": 2.5,
            }
        )
        state = self.trader.update_config("user-op", update)
        self.assertEqual(state.timeframe, "M5")
        self.assertEqual(state.market_mode, "OPEN")
        self.assertEqual(state.entry_value, 12.5)
        self.assertEqual(state.stop_win, 90)
        self.assertEqual(state.stop_loss, 45)
        self.assertTrue(state.martingale_enabled)
        self.assertEqual(state.martingale_steps, 2)
        self.assertEqual(state.martingale_multiplier, 2.5)
        self.assertEqual(state.cycle_minutes, 5)

    def test_config_update_accepts_both_market_and_m15(self) -> None:
        state = self.trader.update_config(
            "user-both",
            RobotConfigUpdate.model_validate(
                {
                    "timeframe": "M15",
                    "market_mode": "BOTH",
                    "entry_value": 7,
                    "stop_win": 100,
                    "stop_loss": 50,
                }
            ),
        )
        self.assertEqual(state.timeframe, "M15")
        self.assertEqual(state.market_mode, "BOTH")
        self.assertEqual(state.entry_value, 7)
        self.assertEqual(state.cycle_minutes, 15)

    def test_start_keeps_configured_operation_settings(self) -> None:
        self.trader.update_config(
            "user-start",
            RobotConfigUpdate.model_validate(
                {
                    "timeframe": "M5",
                    "market_mode": "OPEN",
                    "entry_value": 18,
                    "stop_win": 110,
                    "stop_loss": 55,
                    "martingale_enabled": True,
                    "martingale_steps": 3,
                }
            ),
        )
        state = self.trader.start("user-start")
        self.assertTrue(state.enabled)
        self.assertEqual(state.timeframe, "M5")
        self.assertEqual(state.market_mode, "OPEN")
        self.assertEqual(state.entry_value, 18)
        self.assertEqual(state.stop_win, 110)
        self.assertEqual(state.stop_loss, 55)
        self.assertTrue(state.martingale_enabled)
        self.assertEqual(state.martingale_steps, 3)
        self.assertEqual(state.cycle_minutes, 5)


if __name__ == "__main__":
    unittest.main()
