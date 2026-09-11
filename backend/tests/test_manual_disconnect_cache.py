"""Desconexão manual não pode deixar cache REAL mascarando o painel (LEI 10)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from backend import main
from backend.main import (
    apply_manual_disconnect_session_state,
    auto_trader,
    build_success,
    cache_bullex_response,
    get_session_cache,
    recent_real_account_connection_payload,
    reset_session_connection_cache,
)


class ManualDisconnectCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user_id = "user-disconnect-cache"
        reset_session_connection_cache(self.user_id)
        auto_trader.disconnect_account(self.user_id)
        main.bullex_manual_disconnect.discard(self.user_id)
        self.addCleanup(main.bullex_manual_disconnect.discard, self.user_id)

    def test_manual_disconnect_purges_grace_account_cache(self) -> None:
        cache_bullex_response(
            self.user_id,
            "/account",
            200,
            build_success(
                {
                    "connected": True,
                    "active_mode": "REAL",
                    "mode": "REAL",
                    "balance": 250.0,
                    "currency": "BRL",
                    "email": "trader@example.com",
                }
            ),
        )
        auto_trader.sync_connection(
            self.user_id,
            connected=True,
            active_mode="REAL",
            source="test",
            align_status=True,
        )
        self.assertIsNotNone(recent_real_account_connection_payload(self.user_id))

        auto_trader.disconnect_account(self.user_id)
        apply_manual_disconnect_session_state(self.user_id)

        self.assertIsNone(recent_real_account_connection_payload(self.user_id))
        cache = get_session_cache(self.user_id)
        self.assertIsNone(cache.offline_until)
        self.assertEqual(cache.failure_count, 0)
        state = auto_trader.get(self.user_id)
        self.assertFalse(bool(state.connected))
        self.assertEqual(state.connection_status_source, "disconnected")

    def test_memory_fallback_none_after_manual_disconnect(self) -> None:
        """Memória/grace não podem devolver conta após Desconectar."""
        auto_trader.sync_connection(
            self.user_id,
            connected=True,
            active_mode="REAL",
            source="test",
            align_status=True,
        )
        auto_trader.disconnect_account(self.user_id)
        # Simula estado residual com active_mode ainda REAL (bug observado).
        state = auto_trader.get(self.user_id)
        state.active_mode = "REAL"
        main.bullex_manual_disconnect.add(self.user_id)
        self.assertIsNone(main.memory_account_fallback(self.user_id))
        self.assertIsNone(main.memory_status_fallback(self.user_id))

    def test_robot_snapshot_ignores_stale_redis_when_manual_disconnect(self) -> None:
        """Snapshot Redis connected=true não pode vencer o clique (bug 12/08)."""
        stale = build_success(
            {
                "enabled": True,
                "connected": True,
                "status": "ANALYZING",
                "connection_status_source": "bullex_service",
                "active_mode": "REAL",
            }
        )
        auto_trader.disconnect_account(self.user_id)
        main.bullex_manual_disconnect.add(self.user_id)
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main.robot_bus, "get_snapshot", return_value=stale),
        ):
            payload = main.build_robot_state_snapshot_payload(self.user_id)
        data = payload.get("data") if isinstance(payload, dict) else None
        self.assertIsInstance(data, dict)
        self.assertFalse(bool(data.get("connected")))
        self.assertEqual(data.get("connection_status_source"), "disconnected")

    def test_publish_manual_disconnect_overwrites_redis_snapshot(self) -> None:
        published: list[tuple[str, dict]] = []

        def _capture(user_id: str, payload: dict) -> None:
            published.append((user_id, payload))

        auto_trader.disconnect_account(self.user_id)
        main.bullex_manual_disconnect.add(self.user_id)
        with patch.object(main.robot_bus, "publish_snapshot", side_effect=_capture):
            main.publish_manual_disconnect_robot_snapshot(self.user_id)
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0][0], self.user_id)
        data = published[0][1].get("data")
        self.assertIsInstance(data, dict)
        self.assertFalse(bool(data.get("connected")))
        self.assertEqual(data.get("connection_status_source"), "disconnected")


class ManualDisconnectBalanceRecoveryTests(unittest.TestCase):
    """A recuperação de saldo REAL não pode desfazer o clique em Desconectar.

    Regressão real (09/08): o painel voltava para "Conectado" com saldo poucos
    segundos após o clique. O `/bullex/account` falhava com
    REAL_BALANCE_NOT_DETECTED (correto — a sessão morreu), mas o caminho de
    recuperação lia o snapshot em memória, que sobrevive à desconexão, e
    devolvia um contrato `connected=true` com o saldo antigo.
    """

    def setUp(self) -> None:
        self.user_id = "user-disconnect-balance-recovery"
        main.bullex_manual_disconnect.discard(self.user_id)
        self.addCleanup(main.bullex_manual_disconnect.discard, self.user_id)
        self.failed = {"ok": False, "error": "REAL_BALANCE_NOT_DETECTED", "data": None}

    def test_recovery_skipped_after_manual_disconnect(self) -> None:
        snapshot = {"mode": "REAL", "balance": 27409.24, "currency": "BRL"}
        with patch.object(main, "get_user_account_snapshot", return_value=snapshot):
            main.bullex_manual_disconnect.add(self.user_id)
            self.assertIsNone(
                main.recover_real_account_contract_for_start(self.user_id, self.failed)
            )
            self.assertIsNone(
                main.recover_real_account_contract_for_poll(self.user_id, self.failed)
            )

    def test_recovery_still_works_without_manual_disconnect(self) -> None:
        """Falha transitória da corretora continua recuperando (conta fantasma)."""
        snapshot = {"mode": "REAL", "balance": 27409.24, "currency": "BRL"}
        with (
            patch.object(main, "get_user_account_snapshot", return_value=snapshot),
            patch.object(
                main,
                "get_cached_account_snapshot",
                return_value={"mode": None, "balance": None},
            ),
        ):
            recovered = main.recover_real_account_contract_for_start(
                self.user_id, self.failed
            )
        self.assertIsNotNone(recovered)
        self.assertTrue(recovered["ok"])
        self.assertTrue(recovered["data"]["connected"])


if __name__ == "__main__":
    unittest.main()
