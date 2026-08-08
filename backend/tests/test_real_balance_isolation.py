"""Regressão: balance_id global sob alta demanda / multi-sessão.

Sob carga o WS de outro usuário podia gravar ``global_value.balance_id``
enquanto a sessão ativa ainda estava com id None (login/activate). Isso
gerava ``get_balance_mode() -> None`` → contrato ``REAL_BALANCE_NOT_DETECTED``
e o painel/start travavam no erro de saldo real mesmo com dinheiro na conta.
"""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bullexapi.global_value as global_value
from bullexapi.ws.received import profile as profile_received
from bullex_service import main as bullex_main
from backend import main as gateway_main


class BalanceIdOwnerIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        global_value.balance_id = None
        global_value.balance_id_owner = None

    def tearDown(self) -> None:
        global_value.balance_id = None
        global_value.balance_id_owner = None

    def test_foreign_ws_profile_does_not_overwrite_active_owner(self) -> None:
        owner_api = SimpleNamespace(profile=SimpleNamespace(msg=False, balances=None))
        foreign_api = SimpleNamespace(profile=SimpleNamespace(msg=False, balances=None))
        global_value.balance_id_owner = id(owner_api)
        global_value.balance_id = None

        profile_received.profile(
            foreign_api,
            {
                "name": "profile",
                "msg": {
                    "balance": 999,
                    "balances": [
                        {"id": 111, "type": 1, "amount": 50},
                        {"id": 222, "type": 4, "amount": 10000},
                    ],
                    "balance_id": 222,
                    "balance_type": 4,
                },
            },
        )

        self.assertIsNone(global_value.balance_id)
        self.assertEqual(global_value.balance_id_owner, id(owner_api))

    def test_owner_ws_profile_sets_balance_when_empty(self) -> None:
        owner_api = SimpleNamespace(profile=SimpleNamespace(msg=False, balances=None))
        global_value.balance_id_owner = id(owner_api)
        global_value.balance_id = None

        profile_received.profile(
            owner_api,
            {
                "name": "profile",
                "msg": {
                    "balance": 10,
                    "balances": [
                        {"id": 501, "type": 1, "amount": 10},
                        {"id": 502, "type": 4, "amount": 10000},
                    ],
                    "balance_id": 501,
                    "balance_type": 1,
                },
            },
        )

        self.assertEqual(global_value.balance_id, 502)


class GetBalancesTimeoutTests(unittest.TestCase):
    def test_get_balances_returns_none_on_timeout_instead_of_hanging(self) -> None:
        from bullexapi.stable_api import Bullex

        client = Bullex.__new__(Bullex)
        client.api = SimpleNamespace(balances_raw=None, get_balances=Mock())

        started = time.monotonic()
        result = Bullex.get_balances(client, timeout_seconds=0.2)
        elapsed = time.monotonic() - started

        self.assertIsNone(result)
        self.assertLess(elapsed, 1.5)
        self.assertGreaterEqual(elapsed, 0.15)


class BuildAccountPayloadModeRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        global_value.balance_id = 999999  # id de outro usuário
        global_value.balance_id_owner = None

    def tearDown(self) -> None:
        global_value.balance_id = None
        global_value.balance_id_owner = None

    def test_build_account_recovers_real_when_balance_mode_is_none(self) -> None:
        session = bullex_main.ManagedSession(
            user_id="recover-mode-user",
            client=SimpleNamespace(
                check_connect=lambda: True,
                get_balance_mode=Mock(side_effect=[None, "REAL"]),
                get_balances=lambda: {
                    "msg": [
                        {"id": 7001, "type": 1, "amount": 150.0},
                        {"id": 7002, "type": 4, "amount": 10000.0},
                    ]
                },
                get_profile_ansyc=lambda: {
                    "balances": [
                        {"id": 7001, "type": 1, "amount": 150.0},
                        {"id": 7002, "type": 4, "amount": 10000.0},
                    ]
                },
                change_balance=Mock(
                    side_effect=lambda _mode: setattr(global_value, "balance_id", 7001)
                ),
                position_change_all=Mock(),
                get_currency=lambda: "BRL",
                get_balance=lambda: 150.0,
            ),
            email="recover@example.com",
            active_mode="REAL",
            real_mode_confirmed=True,
        )

        with self.assertLogs("bullex-service", level="INFO") as logs:
            payload = bullex_main.build_account_payload(session)

        self.assertTrue(payload["connected"])
        self.assertEqual(payload["active_mode"], "REAL")
        self.assertEqual(payload["balance_real"], 150.0)
        self.assertEqual(global_value.balance_id, 7001)
        self.assertIn("[ACTIVE_MODE_RECOVERED]", "\n".join(logs.output))


class RobotStartRealBalanceRecoveryTests(unittest.TestCase):
    def test_recover_start_contract_from_cached_real_balance(self) -> None:
        user_id = "start-cache-recover"
        failed = {
            "ok": False,
            "error": "REAL_BALANCE_NOT_DETECTED",
            "data": {"connected": False, "active_mode": None},
        }

        with patch.object(
            gateway_main,
            "get_cached_account_snapshot",
            return_value={
                "mode": "REAL",
                "balance": 88.5,
                "connected": True,
                "currency": "BRL",
                "email": "cache@example.com",
            },
        ):
            recovered = gateway_main.recover_real_account_contract_for_start(
                user_id, failed
            )

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["data"]["active_mode"], "REAL")
        self.assertEqual(recovered["data"]["balance_real"], 88.5)

    def test_recover_returns_none_without_cache(self) -> None:
        user_id = "start-no-cache"
        failed = {
            "ok": False,
            "error": "REAL_BALANCE_NOT_DETECTED",
            "data": {"connected": False, "active_mode": None},
        }
        with patch.object(
            gateway_main,
            "get_cached_account_snapshot",
            return_value={
                "mode": None,
                "balance": None,
                "connected": None,
            },
        ), patch.object(
            gateway_main,
            "memory_account_fallback",
            return_value=None,
        ), patch.object(
            gateway_main,
            "get_user_account_snapshot",
            return_value={
                "mode": None,
                "balance": None,
                "connected": None,
            },
        ):
            self.assertIsNone(
                gateway_main.recover_real_account_contract_for_start(user_id, failed)
            )


class AccountPollRealBalanceRecoveryTests(unittest.TestCase):
    """Poll de /account não deve devolver REAL_BALANCE_NOT_DETECTED ao painel
    quando já há evidência REAL (cache ou robô em operação)."""

    def tearDown(self) -> None:
        # Evita estado do auto_trader vazar entre testes.
        trader = gateway_main.auto_trader
        for uid in ("poll-robot-recover", "poll-cache-recover", "poll-no-evidence"):
            try:
                trader.stop(uid)
            except Exception:
                pass

    def test_poll_recovers_from_running_robot_real_state(self) -> None:
        user_id = "poll-robot-recover"
        state = gateway_main.auto_trader.start(user_id)
        state.connected = True
        state.active_mode = "REAL"
        state.account_mode = "REAL"
        failed = {
            "ok": False,
            "error": "REAL_BALANCE_NOT_DETECTED",
            "data": {"connected": True, "active_mode": None},
        }

        with patch.object(
            gateway_main,
            "get_cached_account_snapshot",
            return_value={
                "mode": None,
                "balance": None,
                "connected": None,
                "currency": None,
                "email": None,
            },
        ), patch.object(
            gateway_main,
            "memory_account_fallback",
            return_value=None,
        ), patch.object(
            gateway_main,
            "get_user_account_snapshot",
            return_value={
                "mode": "REAL",
                "balance": 250.0,
                "connected": True,
                "currency": "BRL",
                "email": "poll@example.com",
            },
        ), patch.object(
            gateway_main,
            "recover_real_account_contract_for_start",
            return_value=None,
        ):
            recovered = gateway_main.recover_real_account_contract_for_poll(
                user_id, failed
            )

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["data"]["active_mode"], "REAL")
        self.assertEqual(recovered["data"]["balance_real"], 250.0)
        self.assertNotEqual(recovered.get("error"), "REAL_BALANCE_NOT_DETECTED")

    def test_poll_recovers_from_start_cache_helper(self) -> None:
        user_id = "poll-cache-recover"
        failed = {
            "ok": False,
            "error": "REAL_BALANCE_NOT_DETECTED",
            "data": {"connected": False, "active_mode": None},
        }
        cached_contract = gateway_main.build_real_account_contract(
            gateway_main.build_success(
                {
                    "connected": True,
                    "active_mode": "REAL",
                    "active_mode_from_bullex": "REAL",
                    "mode": "REAL",
                    "balance": 99.0,
                    "balance_real": 99.0,
                    "currency": "BRL",
                }
            )
        )
        with patch.object(
            gateway_main,
            "recover_real_account_contract_for_start",
            return_value=cached_contract,
        ):
            recovered = gateway_main.recover_real_account_contract_for_poll(
                user_id, failed
            )

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["data"]["balance_real"], 99.0)

    def test_poll_returns_none_without_real_evidence(self) -> None:
        user_id = "poll-no-evidence"
        failed = {
            "ok": False,
            "error": "REAL_BALANCE_NOT_DETECTED",
            "data": {"connected": False, "active_mode": None},
        }
        with patch.object(
            gateway_main,
            "recover_real_account_contract_for_start",
            return_value=None,
        ), patch.object(
            gateway_main,
            "memory_account_fallback",
            return_value=None,
        ), patch.object(
            gateway_main,
            "get_cached_account_snapshot",
            return_value={
                "mode": None,
                "balance": None,
                "connected": None,
            },
        ), patch.object(
            gateway_main,
            "get_user_account_snapshot",
            return_value={
                "mode": None,
                "balance": None,
                "connected": None,
            },
        ):
            self.assertIsNone(
                gateway_main.recover_real_account_contract_for_poll(user_id, failed)
            )


if __name__ == "__main__":
    unittest.main()
