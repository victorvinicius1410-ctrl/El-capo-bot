"""Mínimo de entrada por moeda do saldo (BRL R$ 5, USD US$ 1), sem teto."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from backend import main
from backend.auto_trader import AutoTrader


class EntryCurrencyLimitsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.auto_trader = AutoTrader()

    def test_normalize_and_minimums(self) -> None:
        self.assertEqual(main.normalize_account_currency("usd"), "USD")
        self.assertEqual(main.normalize_account_currency("BRL"), "BRL")
        self.assertEqual(main.normalize_account_currency(None), "BRL")
        self.assertEqual(main.min_real_entry_for_currency("USD"), 1.0)
        self.assertEqual(main.min_real_entry_for_currency("BRL"), 5.0)

    async def test_robot_config_accepts_brl_above_five(self) -> None:
        with (
            patch.object(main, "resolve_user_account_currency", return_value="BRL"),
            patch.object(main, "persist_robot"),
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response = await main.robot_config({"entryValue": 80}, {"user_id": "user-brl"})
        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"]["entry_value"], 80)

    async def test_robot_config_accepts_usd_one(self) -> None:
        with (
            patch.object(main, "resolve_user_account_currency", return_value="USD"),
            patch.object(main, "persist_robot"),
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response = await main.robot_config({"entryValue": 1}, {"user_id": "user-usd"})
        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"]["entry_value"], 1)

    async def test_robot_config_accepts_usd_above_one(self) -> None:
        with (
            patch.object(main, "resolve_user_account_currency", return_value="USD"),
            patch.object(main, "persist_robot"),
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response = await main.robot_config({"entryValue": 25}, {"user_id": "user-usd-high"})
        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"]["entry_value"], 25)

    async def test_robot_config_rejects_usd_below_one(self) -> None:
        with (
            patch.object(main, "resolve_user_account_currency", return_value="USD"),
            patch.object(main, "persist_robot"),
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response = await main.robot_config({"entryValue": 0.99}, {"user_id": "user-usd-low"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.body)["error"], "ENTRY_VALUE_TOO_LOW")

    async def test_robot_config_rejects_brl_one(self) -> None:
        with (
            patch.object(main, "resolve_user_account_currency", return_value="BRL"),
            patch.object(main, "persist_robot"),
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response = await main.robot_config({"entryValue": 1}, {"user_id": "user-brl-one"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.body)["error"], "ENTRY_VALUE_TOO_LOW")
        self.assertEqual(main.auto_trader.get("user-brl-one").entry_value, 5.0)

    async def test_unknown_currency_uses_brl_minimum(self) -> None:
        self.assertEqual(main.min_real_entry_for_currency(""), 5.0)
        self.assertEqual(main.min_real_entry_for_currency("EUR"), 5.0)


if __name__ == "__main__":
    unittest.main()
