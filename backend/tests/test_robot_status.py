import unittest
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.auto_trader import AutoTrader, RobotState, utc_now
from backend.status import (
    STATUS_ANALYZING,
    STATUS_BUYING,
    STATUS_ERROR,
    STATUS_LOSS,
    STATUS_OPERATION_OPEN,
    STATUS_PENDING_RESULT,
    STATUS_RUNNING,
    STATUS_SIGNAL_FOUND,
    STATUS_SHOW_RESULT,
    STATUS_STOPPED,
    STATUS_WAITING_ENTRY,
    STATUS_WAITING_NEXT_CANDLE_ENTRY,
    STATUS_WAITING_RESULT,
    STATUS_WIN,
    VALID_ROBOT_STATUSES,
    normalize_robot_status,
)


class RobotStatusContractTests(unittest.TestCase):
    def test_required_frontend_statuses_are_valid(self) -> None:
        for status in (
            STATUS_STOPPED,
            STATUS_RUNNING,
            STATUS_ANALYZING,
            STATUS_SIGNAL_FOUND,
            STATUS_WAITING_ENTRY,
            STATUS_BUYING,
            STATUS_OPERATION_OPEN,
            STATUS_WAITING_RESULT,
            STATUS_WIN,
            STATUS_LOSS,
            STATUS_SHOW_RESULT,
            STATUS_ERROR,
        ):
            with self.subTest(status=status):
                self.assertIn(status, VALID_ROBOT_STATUSES)

    def test_legacy_statuses_normalize_to_current_backend_states(self) -> None:
        self.assertEqual(normalize_robot_status("WAITING_ENTRY"), STATUS_WAITING_ENTRY)
        self.assertEqual(normalize_robot_status("WAITING_ENTRY_WINDOW"), STATUS_WAITING_NEXT_CANDLE_ENTRY)
        self.assertEqual(normalize_robot_status("OPERATION_OPEN"), STATUS_OPERATION_OPEN)
        self.assertEqual(normalize_robot_status("WAITING_RESULT"), STATUS_WAITING_RESULT)
        self.assertEqual(normalize_robot_status("UNKNOWN_STATUS"), STATUS_STOPPED)

    def test_build_robot_payload_always_returns_valid_status(self) -> None:
        cases = (
            (STATUS_STOPPED, STATUS_STOPPED),
            (STATUS_RUNNING, STATUS_RUNNING),
            (STATUS_ANALYZING, STATUS_ANALYZING),
            (STATUS_SIGNAL_FOUND, STATUS_SIGNAL_FOUND),
            (STATUS_WAITING_ENTRY, STATUS_WAITING_ENTRY),
            (STATUS_BUYING, STATUS_BUYING),
            (STATUS_OPERATION_OPEN, STATUS_OPERATION_OPEN),
            (STATUS_WAITING_RESULT, STATUS_WAITING_RESULT),
            (STATUS_WIN, STATUS_WIN),
            (STATUS_LOSS, STATUS_LOSS),
            (STATUS_SHOW_RESULT, STATUS_SHOW_RESULT),
            (STATUS_ERROR, STATUS_ERROR),
            ("MISSING_CONSTANT", STATUS_STOPPED),
        )

        for raw_status, expected_status in cases:
            with self.subTest(status=raw_status):
                state = RobotState(status=raw_status)
                payload = main.build_robot_payload(state)
                self.assertEqual(payload["data"]["status"], expected_status)
                self.assertIn(payload["data"]["status"], VALID_ROBOT_STATUSES)

    def test_pending_signal_payload_does_not_return_analyzing(self) -> None:
        state = RobotState(status=STATUS_ANALYZING, enabled=True)
        state.pending_signal = {
            "symbol": "GBPJPY-OTC",
            "direction": "CALL",
            "signal": "CALL",
            "confidence": 82,
            "payout": 90,
        }
        state.best_candidate = dict(state.pending_signal)

        payload = main.build_robot_payload(state, user_id="status-signal-user")["data"]

        self.assertEqual(payload["status"], STATUS_WAITING_ENTRY)
        self.assertIsNone(payload["analysis_message"])
        self.assertEqual(payload["status_message"], "Melhor ativo encontrado")

    def test_waiting_next_cycle_payload_hides_stale_signal(self) -> None:
        state = RobotState(status="WAITING_NEXT_CYCLE", enabled=True)
        state.pending_signal = {
            "symbol": "EURUSD-OTC",
            "direction": "CALL",
            "signal": "CALL",
            "confidence": 95,
            "payout": 90,
        }
        state.best_candidate = dict(state.pending_signal)

        payload = main.build_robot_payload(state, user_id="status-waiting-cycle-user")["data"]

        self.assertEqual(payload["status"], "WAITING_NEXT_CYCLE")
        self.assertIsNone(payload["pending_signal"])
        self.assertIsNone(payload["best_candidate"])
        self.assertEqual(payload["analysis_message"], "Buscando melhor oportunidade")
        self.assertEqual(payload["status_message"], "Buscando melhor oportunidade")
        self.assertEqual(payload["display_countdown_label"], "Buscando melhor oportunidade")
        self.assertEqual(payload["display_countdown_seconds"], 0)

    def test_operation_payload_does_not_return_analyzing(self) -> None:
        state = RobotState(status=STATUS_ANALYZING, enabled=True)
        state.operation_in_progress = True
        state.last_trade = {"order_id": "order-1", "result": STATUS_PENDING_RESULT}

        payload = main.build_robot_payload(state, user_id="status-operation-user")["data"]

        self.assertEqual(payload["status"], STATUS_WAITING_RESULT)
        self.assertTrue(payload["result_waiting"])
        self.assertIsNone(payload["analysis_message"])

    def test_finished_trade_payload_shows_result_for_display_window(self) -> None:
        trader = AutoTrader()
        user_id = "status-result-user"
        state = trader.get(user_id)
        state.enabled = True
        state.status = STATUS_BUYING
        state.operation_in_progress = True
        state.last_trade = {"order_id": "order-1", "amount": 10, "result": STATUS_PENDING_RESULT}

        finalized, state = trader.finish_trade(user_id, "order-1", STATUS_WIN, 8.5)
        payload = main.build_robot_payload(state, user_id=user_id)["data"]

        self.assertTrue(finalized)
        self.assertEqual(payload["status"], STATUS_WIN)
        self.assertEqual(payload["cycle_result"], STATUS_WIN)
        self.assertEqual(payload["operation_message"], STATUS_WIN)
        self.assertFalse(payload["operation_in_progress"])
        self.assertFalse(payload["result_waiting"])
        self.assertIsNotNone(payload["result_display_until"])

    def test_expired_result_does_not_hide_following_market_analysis(self) -> None:
        trader = AutoTrader()
        user_id = "status-expired-result-user"
        state = trader.start(user_id)
        state.operation_in_progress = True
        state.last_trade = {"order_id": "order-expired", "amount": 10, "result": STATUS_PENDING_RESULT}
        finalized, state = trader.finish_trade(user_id, "order-expired", STATUS_WIN, 8.5)
        state.result_display_until = utc_now() - timedelta(seconds=1)

        payload = main.build_robot_payload(state, user_id=user_id)["data"]

        self.assertTrue(finalized)
        self.assertEqual(payload["status"], "WAITING_NEXT_CYCLE")
        self.assertIsNone(payload["cycle_result"])
        self.assertIsNone(payload["result_received_at"])
        self.assertIsNone(payload["result_display_until"])
        self.assertEqual(payload["analysis_message"], "Buscando melhor oportunidade")

    def test_analyzing_with_best_candidate_does_not_promote_signal_found(self) -> None:
        """Regressão: best_candidate na análise não pode virar visual de entrada."""
        state = RobotState(status=STATUS_ANALYZING, enabled=True)
        state.best_candidate = {
            "symbol": "USDJPY-OTC",
            "direction": "PUT",
            "signal": "PUT",
            "confidence": 84,
            "payout": 87,
            "trade_allowed": False,
            "blocked_filters": ["ACTIVE_CLOSED"],
            "is_open": False,
        }
        state.cycle_best_candidate = dict(state.best_candidate)
        state.pending_signal = None

        payload = main.build_robot_payload(state, user_id="status-visual-only-user")["data"]

        self.assertEqual(payload["status"], STATUS_ANALYZING)
        self.assertIsNone(payload["pending_signal"])
        self.assertNotEqual(payload.get("status_message"), "Melhor ativo encontrado")

    def test_best_candidate_alone_does_not_prepare_entry_voice(self) -> None:
        """Regressão 2026-07-31: voz 'Entrada preparada' só com pending_signal."""
        trader = AutoTrader()
        state = trader.start("voice-best-only-user")
        state.status = STATUS_WAITING_ENTRY
        state.pending_signal = None
        state.best_candidate = {
            "symbol": "USDJPY-OTC",
            "direction": "PUT",
            "signal": "PUT",
            "confidence": 88,
            "payout": 87,
            "strategy_score": 40,
            "speech_preview": "Vou de PUT em USDJPY.",
            "trade_allowed": False,
            "is_open": False,
        }

        data = state.to_dict()

        self.assertIsNone(data.get("voice_message"))
        self.assertNotIn("Entrada preparada", str(data.get("voice_message") or ""))
        self.assertNotIn("Vou de PUT", str(data.get("voice_message") or ""))


class RobotStateEndpointStatusFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_api_key = main.config.panel_api_key
        self.old_trader = main.auto_trader
        main.config.panel_api_key = "test-key"
        main.auto_trader = AutoTrader()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.config.panel_api_key = self.old_api_key
        main.auto_trader = self.old_trader

    def test_robot_state_never_500s_when_state_impl_raises(self) -> None:
        user_id = "status-fallback-user"
        main.auto_trader.get(user_id).status = "MISSING_CONSTANT"

        with patch.object(
            main,
            "recover_sync_timeout_if_needed",
            side_effect=RuntimeError("unexpected state failure"),
        ):
            response = self.client.get(
                "/robot/state",
                headers={"x-api-key": "test-key", "x-user-id": user_id},
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertIn(payload["data"]["status"], VALID_ROBOT_STATUSES)
        self.assertEqual(payload["data"]["status"], STATUS_STOPPED)


if __name__ == "__main__":
    unittest.main()
