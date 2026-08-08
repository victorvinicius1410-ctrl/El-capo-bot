"""Contrato REAL sem saldo não pode derrubar o robô no poll de /account."""

from __future__ import annotations

import unittest

from backend import main


class RealAccountContractKeepRobotTests(unittest.TestCase):
    def test_real_mode_without_balance_is_ok(self) -> None:
        contract = main.build_real_account_contract(
            {
                "ok": True,
                "data": {
                    "connected": True,
                    "active_mode": "REAL",
                    "balance": None,
                    "balance_real": None,
                },
            }
        )
        self.assertTrue(contract["ok"])
        self.assertEqual(contract["data"]["active_mode"], "REAL")
        self.assertIsNone(contract["data"]["balance_real"])

    def test_real_mode_with_balance_still_ok(self) -> None:
        contract = main.build_real_account_contract(
            {
                "ok": True,
                "data": {
                    "connected": True,
                    "active_mode": "REAL",
                    "balance": 120.5,
                    "balance_real": 120.5,
                },
            }
        )
        self.assertTrue(contract["ok"])
        self.assertEqual(contract["data"]["balance_real"], 120.5)

    def test_practice_mode_still_rejected(self) -> None:
        contract = main.build_real_account_contract(
            {
                "ok": True,
                "data": {
                    "connected": True,
                    "active_mode": "PRACTICE",
                    "balance": 50,
                    "balance_practice": 50,
                },
            }
        )
        self.assertFalse(contract["ok"])
        self.assertEqual(contract["error"], "BULLEX_ACTIVE_MODE_NOT_REAL")

    def test_keep_robot_when_running_real_and_balance_omitted_error(self) -> None:
        user_id = "keep-robot-real-user"
        state = main.auto_trader.start(user_id)
        state.connected = True
        state.active_mode = "REAL"
        contract = {
            "ok": False,
            "error": "REAL_BALANCE_NOT_DETECTED",
            "data": {"connected": True, "active_mode": None},
        }
        self.assertFalse(main.should_stop_robot_for_account_contract(user_id, contract))
        self.assertTrue(main.auto_trader.get(user_id).enabled)

    def test_stop_robot_on_explicit_practice(self) -> None:
        user_id = "stop-robot-practice-user"
        main.auto_trader.start(user_id)
        contract = {
            "ok": False,
            "error": "BULLEX_ACTIVE_MODE_NOT_REAL",
            "data": {"connected": True, "active_mode": "PRACTICE"},
        }
        self.assertTrue(main.should_stop_robot_for_account_contract(user_id, contract))


class UpdateConfigDoesNotDisableRobotTests(unittest.TestCase):
    def test_update_config_keeps_enabled_robot_running(self) -> None:
        from backend.auto_trader import AutoTrader, RobotConfigUpdate

        trader = AutoTrader()
        trader.start("cfg-keep-user")
        state = trader.update_config(
            "cfg-keep-user",
            RobotConfigUpdate.model_validate(
                {
                    "timeframe": "M5",
                    "market_mode": "BOTH",
                    "entry_value": 25,
                    "stop_win": 100,
                    "stop_loss": 80,
                }
            ),
        )
        self.assertTrue(state.enabled)
        self.assertEqual(state.timeframe, "M5")
        self.assertEqual(state.entry_value, 25)


if __name__ == "__main__":
    unittest.main()
