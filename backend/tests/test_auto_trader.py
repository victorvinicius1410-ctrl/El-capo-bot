import asyncio
import json
import unittest
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

from backend.auto_trader import (
    AutoTrader,
    STATUS_ACCOUNT_DISCONNECTED,
    STATUS_BUYING,
    STATUS_GALE_RESULT_RECEIVED,
    STATUS_ORDER_REJECTED,
    STATUS_PENDING_GALE_RESULT,
    STATUS_PENDING_RESULT,
    STATUS_RESULT_RECEIVED,
    STATUS_SENDING_GALE_ORDER,
    STATUS_SENDING_ORDER,
    STATUS_SIGNAL_FOUND,
    STATUS_SIGNAL_EXPIRED,
    STATUS_STOPPED,
    STATUS_WAITING_ENTRY,
    STATUS_WAITING_GALE_ENTRY,
    STATUS_WAITING_RESULT,
    STATUS_WAITING_NEXT_CYCLE,
    STATUS_WIN,
    utc_now,
)
from backend import main
from backend.robot_persistence import build_trade_history_item

# Estes testes exercitam a MECÂNICA de compra com uma corretora falsa que não
# devolve velas. Desde 10/09/2026 a reconferência de nível no disparo é fechada
# (`SR_ZONE_SEM_VERIFICACAO`: sem velas, não opera), e toda compra daqui caía
# nela. A regra de S/R tem os testes dela (`test_sr_entry_recheck`); aqui a
# flag é fixada para medir só o que o módulo mede. Mesma lição da Vertex:
# teste que não é de estratégia fixa as flags, não herda do ambiente.
_FAIL_CLOSED_ORIGINAL = main.SR_ENTRY_RECHECK_FAIL_CLOSED


def setUpModule() -> None:
    main.SR_ENTRY_RECHECK_FAIL_CLOSED = False


def tearDownModule() -> None:
    main.SR_ENTRY_RECHECK_FAIL_CLOSED = _FAIL_CLOSED_ORIGINAL



# Atualizado 2026-08-07: extract_server_timestamp() trata server_time <= 0
# como ausente (fallback para VPS), então 0.0 não é um epoch válido para
# simular "segundo 0 da vela" nos mocks de /sessions/status. 60.0 também cai
# no segundo 0 da vela M1 (60 % 60 == 0) mas é um epoch positivo válido.
SERVER_TIME_M1_OPEN = 60.0


def make_cycle_due(user_id: str) -> None:
    main.auto_trader.get(user_id).next_cycle_at = utc_now() - timedelta(seconds=1)


class AutoTraderStateTests(unittest.TestCase):
    def test_execution_direction_follows_analysis_direction(self) -> None:
        """A execução deve seguir a análise (CALL/PUT) sem aceitar inválidas."""
        self.assertEqual(main.opposite_execution_direction("CALL"), "PUT")
        self.assertEqual(main.opposite_execution_direction("put"), "CALL")
        self.assertEqual(
            main.resolve_robot_execution_direction("CALL", is_gale_order=False),
            "CALL",
        )
        self.assertEqual(
            main.resolve_robot_execution_direction("PUT", is_gale_order=False),
            "PUT",
        )
        self.assertEqual(
            main.resolve_robot_execution_direction("PUT", is_gale_order=True),
            "PUT",
        )

        with self.assertRaisesRegex(ValueError, "INVALID_TRADE_DIRECTION"):
            main.resolve_robot_execution_direction("WAIT", is_gale_order=False)
        with self.assertRaisesRegex(ValueError, "INVALID_TRADE_DIRECTION"):
            main.opposite_execution_direction("WAIT")

    def test_trade_history_preserves_analyzed_and_executed_directions(self) -> None:
        """O histórico deve permitir auditar direção analisada e executada."""
        item = build_trade_history_item(
            "user-aligned-history",
            {
                "order_id": "aligned-1",
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "analyzed_direction": "CALL",
                "execution_direction_inverted": False,
                "result": "WIN",
                "sent_at": "2026-08-05T15:00:00+00:00",
                "finished_at": "2026-08-05T15:01:00+00:00",
            },
        )

        self.assertEqual(item["direction"], "CALL")
        self.assertEqual(item["analysis_json"]["analyzed_direction"], "CALL")
        self.assertFalse(item["analysis_json"]["execution_direction_inverted"])

    def test_new_users_receive_independent_default_states(self) -> None:
        trader = AutoTrader()

        user_a = trader.update_config(
            "user-a",
            main.RobotConfigUpdate(entry_value=15),
        )
        user_b = trader.get("user-b")

        self.assertIsNot(user_a, user_b)
        self.assertEqual(user_a.entry_value, 15)
        # Atualizado 2026-08-07: entry_value padrão passou de 2 para 5.
        self.assertEqual(user_b.entry_value, 5)
        self.assertEqual(user_b.cycle_minutes, 1)
        self.assertEqual(user_b.min_confidence, 80)
        self.assertEqual(user_b.min_payout, 80)
        self.assertEqual(user_b.stop_win, 50)
        self.assertEqual(user_b.stop_loss, 30)
        self.assertEqual(user_b.strategy_mode, "conservative")
        self.assertEqual(user_b.account_mode, "REAL")
        self.assertTrue(user_b.allow_real)
        self.assertTrue(user_b.confirm_real)
        self.assertFalse(user_b.enabled)
        self.assertEqual(trader.source("user-a"), "memory")
        self.assertEqual(trader.source("user-b"), "default")

    def test_no_opportunity_streak_enables_next_cycle_and_trade_resets_it(self) -> None:
        trader = AutoTrader()
        user_id = "user-no-opportunity-streak"
        state = trader.start(user_id)

        state = trader.schedule_next_analysis_session(
            user_id,
            analysis_result="NO_OPPORTUNITY_FOUND",
            last_rejection_reason="NO_PATTERN_FOUND",
        )

        self.assertEqual(state.consecutive_no_opportunity_cycles, 1)

        state = trader.record_trade(
            user_id,
            {
                "order_id": "recovery-order",
                "sent_at": utc_now().isoformat(),
                "result": STATUS_PENDING_RESULT,
            },
        )

        self.assertEqual(state.consecutive_no_opportunity_cycles, 0)

    def test_operational_data_failure_does_not_enable_confidence_recovery(self) -> None:
        trader = AutoTrader()
        user_id = "user-operational-failure-streak"
        trader.start(user_id)

        state = trader.schedule_next_analysis_session(
            user_id,
            analysis_result="NO_OPPORTUNITY_FOUND",
            last_rejection_reason="CANDLES_UNAVAILABLE",
        )

        self.assertEqual(state.consecutive_no_opportunity_cycles, 0)

    def test_analysis_countdown_does_not_reveal_entry_before_cycle_ends(self) -> None:
        user_id = "user-analysis-countdown-contract"
        state = main.auto_trader.start(user_id)
        state.connected = True
        main.auto_trader.set_analysis_candidates(
            user_id,
            [
                {
                    "symbol": "EURUSD-OTC",
                    "signal": "CALL",
                    "direction": "CALL",
                    "confidence": 94,
                    "payout": 90,
                    "strategy_score": 94,
                    "trade_allowed": True,
                }
            ],
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "direction": "CALL",
                "confidence": 94,
                "payout": 90,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )

        payload = main.build_robot_payload(state)["data"]

        self.assertEqual(payload["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(payload["display_countdown_label"], "Buscando melhor oportunidade")
        self.assertEqual(payload["display_countdown_seconds"], 0)
        self.assertIsNone(payload["pending_signal"])
        self.assertEqual(payload["analysis_message"], "Buscando melhor oportunidade")
        self.assertEqual(payload["status_message"], "Buscando melhor oportunidade")

    def test_updating_user_b_does_not_change_user_a(self) -> None:
        trader = AutoTrader()
        trader.update_config(
            "user-a",
            main.RobotConfigUpdate(entry_value=15, stop_loss=40),
        )

        trader.update_config("user-b", main.RobotConfigUpdate(stop_loss=12))

        self.assertEqual(trader.get("user-a").entry_value, 15)
        self.assertEqual(trader.get("user-a").stop_loss, 40)
        # Atualizado 2026-08-07: entry_value padrão passou de 2 para 5.
        self.assertEqual(trader.get("user-b").entry_value, 5)
        self.assertEqual(trader.get("user-b").stop_loss, 12)

    def test_new_users_do_not_expose_ai_state(self) -> None:
        trader = AutoTrader()

        state = trader.get("user-ai-default")

        self.assertFalse(hasattr(state, "ai_analysis_enabled"))
        self.assertFalse(hasattr(state, "ai_confirmation_required"))
        self.assertFalse(hasattr(state, "ai_min_confidence"))

    def test_sending_order_never_falls_back_to_analyzing(self) -> None:
        trader = AutoTrader()
        trader.start("user-order-transition")
        trader.set_pending_signal(
            "user-order-transition",
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 92,
                "payout": 90,
            },
        )

        sending = trader.start_sending_order("user-order-transition")
        sending_status = sending.status
        rejected = trader.reject_order("user-order-transition", "active suspended")

        self.assertEqual(sending_status, STATUS_BUYING)
        self.assertEqual(rejected.status, STATUS_ORDER_REJECTED)
        self.assertNotEqual(rejected.status, "ANALYZING")
        self.assertFalse(rejected.operation_in_progress)

    def test_pending_signal_preserves_final_trade_approval(self) -> None:
        trader = AutoTrader()
        trader.start("user-approved-signal")

        state = trader.set_pending_signal(
            "user-approved-signal",
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 94,
                # Atualizado 2026-08-07: strategy_score é obrigatório — o portão
                # final não abre ordem só com confidence bruta.
                "strategy_score": 94,
                "payout": 90,
                "trade_allowed": True,
                "matched_strategies": ["Price Action"],
                "strategy_setup": "CONTINUATION",
                "strategy_setups": {"Price Action": "CONTINUATION"},
                "pullback_confirmed": True,
                "near_support": True,
                "support_level": 1.099,
                "body_ratio": 0.62,
                "rsi": 58.2,
                "confidence_model_version": "backup-classic",
                "raw_direction_score": 106,
                "mtf_ready": True,
                "mtf_confluence": 2,
                "mtf_votes": {"CALL": 2, "PUT": 0, "WAIT": 1},
                "mtf_qualified_votes": {"M1": "CALL", "M5": "CALL"},
                "mtf_analysis": {"M1": {"signal": "CALL"}, "M5": {"signal": "CALL"}},
            },
        )

        pending = state.pending_signal
        self.assertTrue(pending["trade_allowed"])
        self.assertEqual(pending["matched_strategies"], ["Price Action"])
        self.assertEqual(pending["strategy_setup"], "CONTINUATION")
        self.assertTrue(pending["pullback_confirmed"])
        self.assertTrue(pending["near_support"])
        self.assertEqual(pending["support_level"], 1.099)
        self.assertEqual(pending["body_ratio"], 0.62)
        self.assertEqual(pending["rsi"], 58.2)
        self.assertEqual(pending["confidence_model_version"], "backup-classic")
        self.assertEqual(pending["raw_direction_score"], 106)
        self.assertTrue(pending["mtf_ready"])
        self.assertEqual(pending["mtf_confluence"], 2)
        self.assertEqual(pending["mtf_votes"]["CALL"], 2)
        self.assertEqual(pending["mtf_qualified_votes"]["M5"], "CALL")
        self.assertEqual(pending["mtf_analysis"]["M5"]["signal"], "CALL")
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                pending,
                state,
                minimum_confidence=90,
            )
        )

    def test_gale_uses_dedicated_waiting_and_sending_statuses(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-gale-status")
        state.gale_pending = True
        state.pending_signal = {
            "symbol": "EURUSD-OTC",
            "signal": "PUT",
            "direction": "PUT",
            "confidence": 92,
            "payout": 90,
            "is_gale": True,
            "gale_amount": 4.0,
        }

        can_run, waiting = trader.prepare_cycle("user-gale-status")
        # Atualizado 2026-08-07: waiting e sending são o mesmo objeto de estado
        # mutável — o status precisa ser capturado antes de start_sending_order
        # sobrescrevê-lo, senão a asserção compara o estado já mutado.
        waiting_status = waiting.status
        sending = trader.start_sending_order("user-gale-status")

        self.assertTrue(can_run)
        self.assertEqual(waiting_status, STATUS_WAITING_GALE_ENTRY)
        self.assertEqual(sending.status, STATUS_SENDING_GALE_ORDER)

    def test_disabled_robot_never_opens_cycle(self) -> None:
        trader = AutoTrader()

        can_run, state = trader.prepare_cycle("user-disabled")

        self.assertFalse(can_run)
        self.assertEqual(state.status, STATUS_STOPPED)

    def test_stop_clears_worker_and_gale_flags(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-stop")
        state.operation_in_progress = True
        state.pending_signal = {"symbol": "EURUSD-OTC"}
        state.gale_pending = True
        state.gale_active = True
        state.analysis_result = "RUNNING"
        state.last_analysis_result = "RUNNING"

        stopped = trader.stop("user-stop")
        payload = stopped.to_dict()

        self.assertFalse(stopped.enabled)
        self.assertEqual(stopped.status, STATUS_STOPPED)
        self.assertFalse(stopped.operation_in_progress)
        self.assertIsNone(stopped.pending_signal)
        self.assertFalse(stopped.gale_pending)
        self.assertFalse(stopped.gale_active)
        self.assertFalse(payload["result_waiting"])

    def test_disabled_robot_aborts_before_sending_order(self) -> None:
        trader = AutoTrader()
        trader.start("user-disabled-send")
        trader.set_pending_signal(
            "user-disabled-send",
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 92,
                "payout": 90,
            },
        )
        trader.stop("user-disabled-send")

        with self.assertRaisesRegex(RuntimeError, "ROBOT_STOPPED"):
            trader.start_sending_order("user-disabled-send")

    def test_signal_expired_waits_before_releasing_cycle(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-signal-expired")
        state.status = STATUS_SIGNAL_EXPIRED
        state.next_cycle_at = utc_now() + timedelta(seconds=5)

        can_run, waiting = trader.prepare_cycle("user-signal-expired")

        self.assertFalse(can_run)
        self.assertEqual(waiting.status, STATUS_SIGNAL_EXPIRED)

    def test_stopped_robot_finishes_trade_without_triggering_gale(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-stop-finish")
        state.martingale_enabled = True
        trader.record_trade(
            "user-stop-finish",
            {
                "order_id": "stop-order-1",
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 2,
                "confidence": 90,
                "payout": 88,
                "result": "PENDING_RESULT",
                "sent_at": "2026-06-18T12:00:00+00:00",
            },
        )

        trader.stop("user-stop-finish")
        finalized, stopped = trader.finish_trade("user-stop-finish", "stop-order-1", "LOSS", -2)

        self.assertTrue(finalized)
        self.assertFalse(stopped.enabled)
        self.assertEqual(stopped.status, STATUS_STOPPED)
        self.assertFalse(stopped.gale_pending)
        self.assertFalse(stopped.gale_active)
        self.assertIsNone(stopped.pending_signal)

    def test_draw_result_does_not_count_as_loss_or_trigger_gale(self) -> None:
        """Empate Bullex (equal) devolve stake e segue o ciclo sem LOSS/gale."""
        trader = AutoTrader()
        state = trader.start("user-draw")
        state.martingale_enabled = True
        state.martingale_steps = 2
        trader.record_trade(
            "user-draw",
            {
                "order_id": "draw-order-1",
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 100,
                "confidence": 90,
                "payout": 88,
                "result": "PENDING_RESULT",
            },
        )
        state.operation_in_progress = True
        state.wins = 1
        state.losses = 0
        state.profit = 87.0

        finalized, finished = trader.finish_trade("user-draw", "draw-order-1", "DRAW", 0)

        self.assertTrue(finalized)
        self.assertTrue(finished.enabled)
        self.assertEqual(finished.wins, 1)
        self.assertEqual(finished.losses, 0)
        self.assertEqual(finished.profit, 87.0)
        self.assertEqual(finished.cycle_result, "DRAW")
        self.assertEqual(finished.status, "DRAW")
        self.assertFalse(finished.operation_in_progress)
        self.assertFalse(finished.gale_pending)
        self.assertEqual((finished.last_trade or {}).get("result"), "DRAW")
        self.assertEqual((finished.last_trade or {}).get("profit"), 0.0)

    # Atualizado 2026-08-07: não existe mais reserva fixa de 5 minutos —
    # prepare_cycle apenas libera o ciclo (True) sem agendar next_cycle_at
    # por si só; a reserva de fato acontece quando o chamador real
    # (execute_robot_cycle) processa o ciclo e agenda a próxima janela via
    # schedule_next_analysis_session (cadência contínua: próxima vela do
    # timeframe, não um cooldown fixo).
    def test_cycle_is_reserved_until_next_analysis_session_is_scheduled(self) -> None:
        trader = AutoTrader()
        trader.start("user-cycle")
        trader.get("user-cycle").next_cycle_at = utc_now() - timedelta(seconds=1)

        first_run, state = trader.prepare_cycle("user-cycle")
        self.assertTrue(first_run)
        self.assertEqual(state.status, STATUS_WAITING_NEXT_CYCLE)

        trader.schedule_next_analysis_session(
            "user-cycle",
            analysis_result="NO_OPPORTUNITY_FOUND",
            last_rejection_reason="NO_PATTERN_FOUND",
        )
        second_run, waiting_state = trader.prepare_cycle("user-cycle")

        self.assertFalse(second_run)
        self.assertEqual(waiting_state.status, STATUS_WAITING_NEXT_CYCLE)
        self.assertGreater(waiting_state.next_cycle_at, utc_now())
        self.assertLessEqual(
            waiting_state.next_cycle_at,
            utc_now() + timedelta(seconds=65),
        )

    # Atualizado 2026-08-07: com analysis_result="RUNNING", update_entry_window
    # não força a saída de ANALYZING só por posição de janela — uma análise
    # de fato em andamento não deve ser interrompida (isso é papel exclusivo
    # do timeout de 10s, ver test_running_analysis_over_10_seconds_recovers_with_timeout).
    # A cadência contínua também aboliu o timer "Análise em Xs" da UI
    # (ANALISE_CONTINUA.md §2): enquanto ANALYZING, o display é sempre
    # "Buscando melhor oportunidade" / 0s, dentro ou fora da antiga janela 5-20s.
    def test_second_2_keeps_analyzing_while_analysis_is_running(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-second-2")
        state.connected = True
        state.status = "ANALYZING"
        state.analysis_result = "RUNNING"
        state.last_analysis_result = "RUNNING"

        trader.update_entry_window("user-second-2", main.get_entry_window("M1", 2.0))
        payload = state.to_dict()

        self.assertEqual(payload["status"], "ANALYZING")
        self.assertEqual(payload["analysis_result"], "RUNNING")
        self.assertEqual(payload["display_countdown_label"], "Buscando melhor oportunidade")
        self.assertEqual(payload["display_countdown_seconds"], 0)

    def test_second_7_allows_analyzing(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-second-7")
        state.connected = True
        state.status = "WAITING_ANALYSIS_WINDOW"

        trader.update_entry_window("user-second-7", main.get_entry_window("M1", 7.0))
        trader.start_analysis("user-second-7")
        payload = state.to_dict()

        self.assertEqual(payload["status"], "ANALYZING")
        # Atualizado 2026-08-07: analysis_window_open saiu do payload de to_dict()
        # (campo interno deprecated para exibição); valida direto no estado.
        self.assertTrue(state.analysis_window_open)
        self.assertEqual(payload["display_countdown_seconds"], 0)

    def test_second_21_keeps_analyzing_while_analysis_is_running(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-second-21")
        state.connected = True
        state.status = "ANALYZING"
        state.analysis_result = "RUNNING"
        state.last_analysis_result = "RUNNING"

        trader.update_entry_window("user-second-21", main.get_entry_window("M1", 21.0))
        payload = state.to_dict()

        self.assertEqual(payload["status"], "ANALYZING")
        self.assertEqual(payload["analysis_result"], "RUNNING")
        self.assertEqual(payload["display_countdown_label"], "Buscando melhor oportunidade")
        self.assertEqual(payload["display_countdown_seconds"], 0)

    def test_display_countdown_seconds_is_always_an_integer(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-countdown-integer")

        for status in ("WAITING_ANALYSIS_WINDOW", "ANALYZING", "STOPPED"):
            state.status = status
            payload = state.to_dict()
            self.assertIsInstance(payload["display_countdown_seconds"], int)
            self.assertGreaterEqual(payload["display_countdown_seconds"], 0)

    def test_start_schedules_immediate_continuous_analysis(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-delayed-start")

        payload = state.to_dict()

        self.assertTrue(state.enabled)
        self.assertEqual(state.status, STATUS_WAITING_NEXT_CYCLE)
        self.assertIsNone(state.pending_signal)
        self.assertIsNone(state.last_signal)
        self.assertIsNone(state.rejection_reason)
        self.assertIsNotNone(state.cycle_id)
        self.assertIsNotNone(payload["current_cycle_started_at"])
        self.assertLessEqual(payload["seconds_until_next_cycle"], 2)
        self.assertEqual(payload["display_countdown_label"], "Buscando melhor oportunidade")
        self.assertEqual(payload["display_countdown_seconds"], 0)

    # Atualizado 2026-08-07: to_dict() normaliza STATUS_WAITING_ANALYSIS_WINDOW
    # (interno) para STATUS_WAITING_NEXT_CYCLE e o campo seconds_until_analysis_window
    # saiu do payload externo (ANALISE_CONTINUA.md §2: sem timer "próxima análise").
    def test_waiting_analysis_window_uses_analysis_countdown_for_display(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-analysis-countdown")
        window = main.get_entry_window("M1", 30.0)

        trader.wait_analysis_window(
            "user-analysis-countdown",
            window,
            analysis_result="WAITING_NEXT_ANALYSIS_WINDOW",
            rejection_reason="WAITING_NEXT_ANALYSIS_WINDOW",
        )

        payload = state.to_dict()
        self.assertEqual(payload["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertNotIn("seconds_until_analysis_window", payload)
        self.assertAlmostEqual(payload["seconds_until_next_cycle"], 35, delta=2)
        self.assertEqual(payload["display_countdown_label"], "Buscando melhor oportunidade")
        self.assertEqual(payload["display_countdown_seconds"], 0)

    def test_config_keeps_selected_five_minute_cycle(self) -> None:
        trader = AutoTrader()

        state = trader.update_config(
            "user-five-minute-config",
            main.RobotConfigUpdate(cycle_minutes=5),
        )

        self.assertEqual(state.cycle_minutes, 5)

    def test_start_never_reuses_previous_cycle(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-new-cycle")
        first_cycle_id = state.cycle_id
        state.pending_signal = {"symbol": "EURUSD-OTC"}
        state.last_signal = {"symbol": "EURUSD-OTC"}
        state.rejection_reason = "OLD_REASON"
        state.current_cycle_started_at = utc_now() - timedelta(minutes=30)
        state.next_cycle_at = utc_now() - timedelta(minutes=20)

        restarted = trader.start("user-new-cycle")

        self.assertIsNotNone(restarted.cycle_id)
        self.assertNotEqual(restarted.cycle_id, first_cycle_id)
        self.assertIsNone(restarted.pending_signal)
        self.assertIsNone(restarted.last_signal)
        self.assertIsNone(restarted.rejection_reason)
        self.assertLessEqual(
            (restarted.next_cycle_at - utc_now()).total_seconds(),
            2,
        )

    def test_entry_windows_match_each_timeframe(self) -> None:
        # Janela apertada de 0-8s para 0-3s em 2026-08-30: a auditoria mediu
        # que só 33,6% das ordens pegavam o início da vela. Ver
        # tests/test_entry_candle_timing.py.
        cases = {
            "M1": (0, 59, 3, 4),
            "M5": (0, 299, 3, 4),
            "M15": (0, 899, 3, 4),
            "M30": (0, 1799, 3, 4),
        }
        for timeframe, (open_at, before_at, end_at, missed_at) in cases.items():
            with self.subTest(timeframe=timeframe):
                self.assertTrue(main.get_entry_window(timeframe, open_at)["entry_window_open"])
                self.assertTrue(main.get_entry_window(timeframe, end_at)["entry_window_open"])
                before = main.get_entry_window(timeframe, before_at)
                self.assertFalse(before["entry_window_open"])
                self.assertEqual(before["seconds_until_entry_window"], 1)
                missed = main.get_entry_window(timeframe, missed_at)
                self.assertFalse(missed["entry_window_open"])
                self.assertTrue(missed["missed_entry_window"])
                self.assertEqual(missed["seconds_until_entry_window"], int(main.TIMEFRAME_SECONDS[timeframe]) - missed_at)

    def test_entry_window_contract_exposes_target_seconds(self) -> None:
        window = main.get_entry_window("M1", 59)

        self.assertFalse(window["entry_window_open"])
        self.assertEqual(window["seconds_until_entry_window"], 1)
        self.assertFalse(window["analysis_window_open"])
        # Em M1 no segundo 59: falta 1s para fechar a vela + 5s até a janela de análise.
        self.assertEqual(window["seconds_until_analysis_window"], 6)
        self.assertEqual(window["analysis_window_start_second"], 5)
        self.assertEqual(window["analysis_window_end_second"], 20)
        self.assertEqual(window["entry_window_start_second"], 0)
        self.assertEqual(window["entry_window_end_second"], 3)
        self.assertEqual(window["buy_target_second"], 0)

    def test_robot_worker_entry_wait_polls_near_window(self) -> None:
        """Worker não dorme a espera inteira — acorda cedo e faz poll fino."""
        self.assertEqual(main.robot_worker_entry_wait_seconds(0), 0.15)
        self.assertEqual(main.robot_worker_entry_wait_seconds(1), 0.15)
        self.assertEqual(main.robot_worker_entry_wait_seconds(4), 0.35)
        self.assertEqual(main.robot_worker_entry_wait_seconds(50), 4.0)
        self.assertEqual(main.robot_worker_entry_wait_seconds(8), 0.35)
        self.assertLess(main.robot_worker_entry_wait_seconds(10), 10)

    def test_analysis_window_contract_for_m1(self) -> None:
        window_at_10 = main.get_entry_window("M1", 10)
        window_at_20 = main.get_entry_window("M1", 20)
        window_at_30 = main.get_entry_window("M1", 30)

        self.assertTrue(window_at_10["analysis_window_open"])
        self.assertEqual(window_at_10["seconds_until_analysis_window"], 0)
        self.assertTrue(window_at_20["analysis_window_open"])
        self.assertFalse(window_at_30["analysis_window_open"])
        self.assertEqual(window_at_30["seconds_until_analysis_window"], 35)

    def test_cached_entry_window_refresh_does_not_compound_clock_drift(self) -> None:
        """Regressão: gravar server_time estimado sem novo âncora adianta a compra.

        Relato 2026-08-21: ordem ~45s antes do fechamento da vela. O worker
        reaplicava ``estimate = server_time + (now - checked_at)`` e
        ``update_entry_window`` sobrescrevia ``server_time`` mantendo o
        ``connection_checked_at`` antigo — cada poll compostava o elapsed.
        """
        user_id = "user-clock-drift-entry"
        state = main.auto_trader.start(user_id)
        state.timeframe = "M1"
        state.connected = True
        state.active_mode = "REAL"
        state.account_mode = "REAL"
        # Âncora Bullex: segundo 10 da vela M1 (70 % 60 == 10).
        anchor_ts = 70.0
        t0 = datetime.fromtimestamp(anchor_ts, timezone.utc)
        state.server_time = t0.isoformat()
        state.server_time_source = "bullex"
        state.connection_checked_at = t0
        state.server_time_sampled_at = t0

        async def _run_polls() -> None:
            nonlocal state
            last_seconds = 10.0
            with (
                patch.object(main, "call_bullex_service", new=AsyncMock()) as service_call,
                patch.object(main, "robot_has_recent_real_cache", return_value=True),
                patch.object(main, "fresh_robot_connection", return_value=True),
            ):
                for step in range(1, 9):
                    wall = t0 + timedelta(seconds=5 * step)
                    with patch.object(main, "utc_now", return_value=wall):
                        status, _payload, window = await main.refresh_entry_window(
                            user_id,
                            state,
                        )
                    self.assertEqual(status, 200)
                    self.assertIsNotNone(window)
                    assert window is not None
                    expected_seconds = 10.0 + (5 * step)
                    self.assertAlmostEqual(
                        float(window["current_candle_seconds"]),
                        expected_seconds,
                        delta=0.6,
                        msg=f"step={step} drift composto adiantaria a janela",
                    )
                    self.assertGreaterEqual(
                        float(window["current_candle_seconds"]),
                        last_seconds - 0.1,
                    )
                    self.assertLess(
                        float(window["current_candle_seconds"]),
                        expected_seconds + 2.0,
                        msg="relógio estimado não pode disparar além do wall-clock",
                    )
                    last_seconds = float(window["current_candle_seconds"])
                    state = main.auto_trader.get(user_id)
                service_call.assert_not_awaited()

        asyncio.run(_run_polls())
        # Após 40s reais: ainda no segundo ~50 da mesma vela — NÃO na próxima (0–8).
        self.assertFalse(state.entry_window_open)
        self.assertGreaterEqual(state.current_candle_seconds, 45.0)
        self.assertLess(state.current_candle_seconds, 55.0)

    def test_entry_window_is_refreshed_after_scan_crosses_candle_boundary(self) -> None:
        with patch.object(main, "monotonic", side_effect=[100.0, 125.0, 125.0]):
            window_before_scan = main.get_entry_window("M5", 290.0)
            window_after_scan = main.refresh_entry_window_after_analysis(window_before_scan)

        self.assertEqual(window_before_scan["seconds_until_entry_window"], 10)
        self.assertEqual(window_after_scan["server_timestamp"], 315.0)
        self.assertEqual(window_after_scan["current_candle_seconds"], 15.0)
        self.assertEqual(window_after_scan["seconds_until_entry_window"], 285)
        self.assertGreater(
            window_after_scan["server_timestamp"] + window_after_scan["seconds_until_entry_window"],
            window_after_scan["server_timestamp"],
        )

    def test_active_cycle_applies_refreshed_window_to_robot_state(self) -> None:
        user_id = "user-refreshed-cycle-window"
        state = main.auto_trader.start(user_id)
        state.timeframe = "M5"

        with patch.object(main, "monotonic", side_effect=[100.0, 125.0, 125.0]):
            window_before_scan = main.get_entry_window("M5", 290.0)
            refreshed_window = main.refresh_cycle_entry_window(
                user_id,
                state,
                window_before_scan,
            )

        self.assertEqual(refreshed_window["server_timestamp"], 315.0)
        self.assertEqual(state.current_candle_seconds, 15.0)
        self.assertEqual(state.seconds_until_entry_window, 285)
        self.assertFalse(state.entry_window_open)

    def test_waiting_entry_window_keeps_complete_time_contract(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-waiting-contract")
        state.timeframe = "M5"
        trader.set_pending_signal(
            "user-waiting-contract",
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 92,
                "payout": 88,
            },
        )
        trader.update_entry_window(
            "user-waiting-contract",
            main.get_entry_window("M5", 120.0),
        )

        payload = state.to_dict()

        # Atualizado 2026-08-07: to_dict() normaliza STATUS_WAITING_NEXT_CANDLE_ENTRY
        # para STATUS_WAITING_ENTRY (rótulo de exibição unificado).
        self.assertEqual(payload["status"], STATUS_WAITING_ENTRY)
        self.assertIn("seconds_until_next_cycle", payload)
        self.assertEqual(payload["seconds_until_entry_window"], 180)
        self.assertEqual(payload["seconds_until_entry"], 180)
        self.assertEqual(payload["display_countdown_label"], "Entrada no início da próxima vela em")
        self.assertEqual(payload["display_countdown_seconds"], 180)
        self.assertEqual(payload["entry_window_start_second"], 0)
        # Janela apertada para 0-3s em 2026-08-30 (entrada no início da vela).
        self.assertEqual(payload["entry_window_end_second"], 3)
        self.assertEqual(payload["buy_target_second"], 0)
        self.assertEqual(payload["entry_target"], "NEXT_CANDLE_OPEN")
        self.assertEqual(payload["expiration_seconds"], 300)
        self.assertFalse(payload["entry_window_open"])
        self.assertFalse(payload["operation_in_progress"])
        self.assertEqual(payload["pending_signal"]["symbol"], "EURUSD-OTC")
        self.assertEqual(payload["pending_signal"]["signal"], "CALL")
        self.assertEqual(payload["pending_signal"]["direction"], "CALL")
        self.assertEqual(payload["pending_signal"]["confidence"], 92)
        self.assertEqual(payload["pending_signal"]["payout"], 88)
        self.assertIsNone(payload["last_trade"])

    def test_open_operation_returns_real_remaining_expiration(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-open-expiration")
        state.timeframe = "M5"
        sent_at = utc_now() - timedelta(seconds=42)
        trader.record_trade(
            "user-open-expiration",
            {
                "order_id": "open-1",
                "sent_at": sent_at.isoformat(),
            },
        )

        payload = state.to_dict()

        self.assertTrue(payload["operation_in_progress"])
        self.assertEqual(payload["last_trade"]["result"], "PENDING_RESULT")
        self.assertGreaterEqual(payload["expiration_seconds"], 257)
        self.assertLessEqual(payload["expiration_seconds"], 258)
        self.assertTrue(payload["result_waiting"])
        self.assertTrue(payload["show_expiration_countdown"])
        # Atualizado 2026-08-07: com expiração futura a mensagem passou a ser
        # "Operação aberta" (mm:ss fica em expiration_display, não mais embutido
        # em "Expira em ...").
        self.assertEqual(payload["operation_message"], "Operação aberta")
        self.assertRegex(payload["expiration_display"], r"^\d{2}:\d{2}$")

    def test_expired_open_operation_waits_for_final_result(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-result-waiting")
        expired_at = utc_now() - timedelta(seconds=2)
        trader.record_trade(
            "user-result-waiting",
            {
                "order_id": "waiting-result-1",
                "expected_expire_at": expired_at.isoformat(),
                "result": STATUS_PENDING_RESULT,
            },
        )

        payload = state.to_dict()
        can_run, waiting_state = trader.prepare_cycle("user-result-waiting")

        # Atualizado 2026-08-07: to_dict() exibe STATUS_PENDING_RESULT como
        # STATUS_WAITING_RESULT (mesma normalização usada em PENDING_GALE_RESULT).
        self.assertEqual(payload["status"], STATUS_WAITING_RESULT)
        self.assertEqual(payload["expiration_seconds"], 0)
        self.assertTrue(payload["result_waiting"])
        self.assertTrue(payload["operation_in_progress"])
        self.assertEqual(payload["operation_message"], "Aguardando resultado...")
        self.assertEqual(payload["expiration_display"], "Aguardando resultado...")
        self.assertFalse(payload["show_expiration_countdown"])
        self.assertNotEqual(payload["operation_message"], "Expira em 00:00")
        self.assertFalse(can_run)
        self.assertEqual(waiting_state.status, STATUS_WAITING_RESULT)

    def test_restore_keeps_pending_signal_waiting_for_entry_window(self) -> None:
        trader = AutoTrader()
        state = trader.start("user-pending-restore")
        trader.set_pending_signal(
            "user-pending-restore",
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 93,
                "payout": 90,
            },
        )

        restored = AutoTrader().restore("user-pending-restore", state.to_dict())

        # Atualizado 2026-08-07: restore() usa STATUS_WAITING_ENTRY (não o
        # antigo WAITING_NEXT_CANDLE_ENTRY) para sinal pendente aguardando janela.
        self.assertEqual(restored.status, STATUS_WAITING_ENTRY)
        self.assertEqual(restored.pending_signal["symbol"], "EURUSD-OTC")
        self.assertEqual(restored.pending_signal["signal"], "CALL")
        self.assertEqual(restored.last_signal, restored.pending_signal)

    # Atualizado 2026-08-07: a cadência contínua substituiu o cooldown legado
    # (finished_at + cycle_minutes minutos). _schedule_next_cycle recalcula
    # cycle_minutes a partir do timeframe (M1 -> 1 min de vela) e agenda a
    # próxima análise dentro da janela da PRÓXIMA vela (até ~60s após
    # finished_at para M1), não mais um cooldown de N minutos configurável.
    def test_restore_rebuilds_next_cycle_from_finished_at(self) -> None:
        finished_at = utc_now() - timedelta(minutes=2)
        trader = AutoTrader()

        restored = trader.restore(
            "user-finished-restore",
            {
                "enabled": True,
                "cycle_minutes": 10,
                "last_entry_at": (finished_at - timedelta(minutes=1)).isoformat(),
                "last_trade": {
                    "order_id": "finished-1",
                    "result": "WIN",
                    "finished_at": finished_at.isoformat(),
                },
                "status": "WAITING_NEXT_CYCLE",
                "entry_window_open": True,
            },
        )

        self.assertEqual(restored.status, "WAITING_NEXT_CYCLE")
        self.assertFalse(restored.entry_window_open)
        # cycle_minutes é normalizado para a duração da vela do timeframe (M1 = 1).
        self.assertEqual(restored.cycle_minutes, 1)
        delta_seconds = (restored.next_cycle_at - finished_at).total_seconds()
        self.assertGreaterEqual(delta_seconds, 0)
        self.assertLessEqual(delta_seconds, 65)
        # finished_at já ficou 2 minutos no passado, então a próxima janela
        # contínua (dentro de 65s da vela) já está vencida no momento do to_dict().
        self.assertEqual(restored.to_dict()["seconds_until_next_cycle"], 0)

    # Atualizado 2026-08-07: mesma mudança de cadência contínua do teste
    # anterior — sem cooldown de N minutos, a próxima análise é agendada
    # dentro da janela da próxima vela do timeframe (M1 = até 65s).
    def test_restore_uses_last_entry_when_finished_at_is_missing(self) -> None:
        last_entry_at = utc_now() - timedelta(minutes=3)
        trader = AutoTrader()

        restored = trader.restore(
            "user-entry-restore",
            {
                "enabled": True,
                "cycle_minutes": 10,
                "last_entry_at": last_entry_at.isoformat(),
                "last_trade": {"order_id": "open-without-finished", "result": "LOSS"},
            },
        )

        self.assertEqual(restored.cycle_minutes, 1)
        delta_seconds = (restored.next_cycle_at - last_entry_at).total_seconds()
        self.assertGreaterEqual(delta_seconds, 0)
        self.assertLessEqual(delta_seconds, 65)


class AutoTraderCycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.auto_trader = AutoTrader()
        main.user_store = main.create_user_store()
        # Nenhum caso desta classe exercita o worker de verdade. Sem o patch,
        # um teste que chega em `robot_state` com sessão conectada deixa um
        # worker real girando em background: ele segue tentando comprar e trava
        # a execução dos testes seguintes.
        worker_patcher = patch.object(main, "ensure_robot_worker")
        worker_patcher.start()
        self.addCleanup(worker_patcher.stop)

    async def asyncTearDown(self) -> None:
        """Cancela qualquer worker que tenha escapado do patch de setUp."""
        for task in list(main.robot_tasks.values()):
            task.cancel()
        for task in list(main.robot_tasks.values()):
            with suppress(asyncio.CancelledError):
                await task
        main.robot_tasks.clear()
        main.robot_worker_last_tick_at.clear()

    async def test_robot_config_and_state_are_isolated_by_user_id(self) -> None:
        with (
            patch.object(main, "persist_robot", return_value=None) as persist,
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response_a = await main.robot_config(
                main.RobotConfigUpdate(entry_value=5, stop_loss=40),
                {"user_id": "user-a"},
            )
            response_b = await main.robot_config(
                main.RobotConfigUpdate(stop_loss=12),
                {"user_id": "user-b"},
            )

        data_a = json.loads(response_a.body)["data"]
        data_b = json.loads(response_b.body)["data"]
        self.assertEqual(data_a["entry_value"], 5)
        self.assertEqual(data_a["stop_loss"], 40)
        # Atualizado 2026-08-07: default de entry_value é 5.0 (era 2).
        self.assertEqual(data_b["entry_value"], 5.0)
        self.assertEqual(data_b["stop_loss"], 12)
        self.assertEqual(
            [call.args[0] for call in persist.call_args_list],
            ["user-a", "user-b"],
        )

        session_payload = main.build_success(
            {
                "connected": True,
                "active_mode": "PRACTICE",
                "server_time": SERVER_TIME_M1_OPEN,
            }
        )
        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(return_value=(200, session_payload)),
            ),
            patch.object(main, "sync_user_store_from_payload"),
        ):
            state_a_response = await main.robot_state({"user_id": "user-a"})
            state_b_response = await main.robot_state({"user_id": "user-b"})

        state_a = json.loads(state_a_response.body)["data"]
        state_b = json.loads(state_b_response.body)["data"]
        self.assertNotEqual(state_a["stop_loss"], state_b["stop_loss"])

    async def test_robot_config_accepts_frontend_camel_case_stop_fields(self) -> None:
        user_id = "user-camel-stop"
        body = main.RobotConfigUpdate.model_validate(
            {
                "entryValue": 5,
                "stopWin": 120,
                "stopLoss": 35,
                "cycleMinutes": 5,
            }
        )

        with (
            patch.object(main, "persist_robot", return_value=None) as persist,
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response = await main.robot_config(body, {"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["entry_value"], 5)
        self.assertEqual(data["stop_win"], 120)
        self.assertEqual(data["stop_loss"], 35)
        self.assertEqual(main.auto_trader.get(user_id).stop_win, 120)
        self.assertEqual(main.auto_trader.get(user_id).stop_loss, 35)
        persist.assert_called_once_with(user_id)

    async def test_robot_config_accepts_martingale_fields(self) -> None:
        user_id = "user-martingale-config"
        body = main.RobotConfigUpdate.model_validate(
            {
                "martingaleEnabled": True,
                "martingaleSteps": 1,
                "martingaleMultiplier": 2.5,
            }
        )

        with (
            patch.object(main, "persist_robot", return_value=None) as persist,
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response = await main.robot_config(body, {"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["martingale_enabled"])
        self.assertEqual(data["martingale_steps"], 1)
        self.assertEqual(data["martingale_multiplier"], 2.5)
        persist.assert_called_once_with(user_id)

    async def test_robot_config_persists_real_confirmation_fields(self) -> None:
        user_id = "user-real-config"
        body = {
            "account_mode": "REAL",
            "allow_real": True,
            "confirm_real": True,
        }

        with (
            patch.object(main, "persist_robot", return_value=None) as persist,
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response = await main.robot_config(body, {"user_id": user_id})

        data = json.loads(response.body)["data"]
        state = main.auto_trader.get(user_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["account_mode"], "REAL")
        self.assertTrue(data["allow_real"])
        self.assertTrue(data["confirm_real"])
        self.assertEqual(state.account_mode, "REAL")
        self.assertTrue(state.allow_real)
        self.assertTrue(state.confirm_real)
        persist.assert_called_once_with(user_id)

    async def test_robot_config_preserves_real_confirmation_when_updating_other_fields(self) -> None:
        user_id = "user-real-preserve"
        main.auto_trader.update_config(
            user_id,
            main.RobotConfigUpdate(account_mode="REAL", allow_real=True, confirm_real=True),
        )

        with (
            patch.object(main, "persist_robot", return_value=None) as persist,
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response = await main.robot_config({"entryValue": 5}, {"user_id": user_id})

        data = json.loads(response.body)["data"]
        state = main.auto_trader.get(user_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["entry_value"], 5)
        self.assertTrue(data["allow_real"])
        self.assertTrue(data["confirm_real"])
        self.assertEqual(data["account_mode"], "REAL")
        self.assertTrue(state.allow_real)
        self.assertTrue(state.confirm_real)
        self.assertEqual(state.account_mode, "REAL")
        persist.assert_called_once_with(user_id)

    async def test_robot_config_rejects_entry_value_below_minimum(self) -> None:
        user_id = "user-entry-too-low"
        state = main.auto_trader.get(user_id)
        original_entry = state.entry_value
        original_stop_win = state.stop_win
        original_stop_loss = state.stop_loss

        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker") as ensure_worker,
            patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker,
        ):
            response = await main.robot_config({"entryValue": 4.99}, {"user_id": user_id})

        payload = json.loads(response.body)
        refreshed = main.auto_trader.get(user_id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"], "ENTRY_VALUE_TOO_LOW")
        self.assertEqual(refreshed.entry_value, original_entry)
        self.assertEqual(refreshed.stop_win, original_stop_win)
        self.assertEqual(refreshed.stop_loss, original_stop_loss)
        ensure_worker.assert_not_called()
        stop_worker.assert_not_awaited()

    async def test_robot_config_rejects_stops_below_minimum_five(self) -> None:
        user_id = "user-stops-too-low"
        state = main.auto_trader.get(user_id)
        original_entry = state.entry_value
        original_stop_win = state.stop_win
        original_stop_loss = state.stop_loss

        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker") as ensure_worker,
            patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker,
        ):
            response = await main.robot_config(
                {"entryValue": 5, "stopWin": 4.99, "stopLoss": 3},
                {"user_id": user_id},
            )

        payload = json.loads(response.body)
        refreshed = main.auto_trader.get(user_id)
        self.assertEqual(response.status_code, 400)
        self.assertIn(payload["error"], {"STOP_WIN_TOO_LOW", "STOP_LOSS_TOO_LOW"})
        self.assertEqual(refreshed.entry_value, original_entry)
        self.assertEqual(refreshed.stop_win, original_stop_win)
        self.assertEqual(refreshed.stop_loss, original_stop_loss)
        ensure_worker.assert_not_called()
        stop_worker.assert_not_awaited()

    async def test_robot_config_accepts_entry_value_and_stops_at_minimum_five(self) -> None:
        user_id = "user-entry-min-ok"
        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response = await main.robot_config(
                {"entryValue": 5, "stopWin": 5, "stopLoss": 5},
                {"user_id": user_id},
            )

        payload = json.loads(response.body)
        refreshed = main.auto_trader.get(user_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"]["entry_value"], 5)
        self.assertEqual(payload["data"]["stop_win"], 5)
        self.assertEqual(payload["data"]["stop_loss"], 5)
        self.assertEqual(refreshed.entry_value, 5)
        self.assertEqual(refreshed.stop_win, 5)
        self.assertEqual(refreshed.stop_loss, 5)

    async def test_robot_config_accepts_entry_value_above_minimum(self) -> None:
        user_id = "user-entry-too-high"
        with (
            patch.object(main, "persist_robot", return_value=None) as persist,
            patch.object(main, "ensure_robot_worker") as ensure_worker,
            patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker,
        ):
            response = await main.robot_config({"entryValue": 80}, {"user_id": user_id})

        payload = json.loads(response.body)
        refreshed = main.auto_trader.get(user_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"]["entry_value"], 80)
        self.assertEqual(refreshed.entry_value, 80)
        ensure_worker.assert_not_called()
        stop_worker.assert_not_awaited()
        persist.assert_called_once_with(user_id)

    async def test_robot_config_accepts_high_entry_value_without_max_cap(self) -> None:
        user_id = "user-entry-maximum"

        with (
            patch.object(main, "persist_robot", return_value=None) as persist,
            patch.object(main, "ensure_robot_worker") as ensure_worker,
            patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker,
        ):
            response = await main.robot_config({"entryValue": 250}, {"user_id": user_id})

        data = json.loads(response.body)["data"]
        refreshed = main.auto_trader.get(user_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["entry_value"], 250)
        self.assertEqual(refreshed.entry_value, 250)
        ensure_worker.assert_not_called()
        stop_worker.assert_not_awaited()
        persist.assert_called_once_with(user_id)

    async def test_robot_config_ignores_legacy_ai_fields(self) -> None:
        user_id = "user-ignore-ai-fields"
        body = {
            "entryValue": 5,
            "minConfidence": 92,
            "ai_analysis_enabled": True,
            "ai_confirmation_required": True,
            "ai_min_confidence": 80,
            "ai_voice_text": "legacy",
            "aiVoiceText": "legacyCamel",
        }

        with (
            patch.object(main, "persist_robot", return_value=None) as persist,
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            response = await main.robot_config(body, {"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["entry_value"], 5)
        self.assertEqual(data["min_confidence"], 92)
        self.assertNotIn("ai_analysis_enabled", data)
        self.assertNotIn("ai_confirmation_required", data)
        self.assertNotIn("ai_min_confidence", data)
        persist.assert_called_once_with(user_id)

    # Atualizado 2026-08-07: robot_config_locked (LEI de trava de config) agora
    # bloqueia qualquer alteração de configuração (409) enquanto o robô está
    # habilitado, em vez de aplicar parcialmente só os campos "básicos".
    async def test_robot_config_is_locked_while_robot_is_enabled(self) -> None:
        user_id = "user-basic-config-only"
        state = main.auto_trader.get(user_id)
        state.enabled = True
        state.status = "WAITING_NEXT_CYCLE"
        state.cycle_minutes = 5
        state.strategy_mode = "conservative"

        body = {
            "entryValue": 11,
            "stopWin": 55,
            "cycleMinutes": 15,
            "strategyMode": "balanced",
            "enabled": False,
            "randomField": "ignored",
        }

        with (
            patch.object(main, "persist_robot", return_value=None) as persist,
            patch.object(main, "ensure_robot_worker") as ensure_worker,
            patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker,
        ):
            response = await main.robot_config(body, {"user_id": user_id})

        payload = json.loads(response.body)
        refreshed = main.auto_trader.get(user_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"], "ROBOT_RUNNING_CONFIG_LOCKED")
        self.assertEqual(refreshed.entry_value, state.entry_value)
        self.assertEqual(refreshed.cycle_minutes, 5)
        self.assertEqual(refreshed.strategy_mode, "conservative")
        self.assertTrue(refreshed.enabled)
        self.assertEqual(refreshed.status, "WAITING_NEXT_CYCLE")
        ensure_worker.assert_not_called()
        stop_worker.assert_not_awaited()
        persist.assert_not_called()

    async def test_user_isolation_debug_reports_current_user_only(self) -> None:
        main.auto_trader.update_config(
            "user-a",
            main.RobotConfigUpdate(entry_value=15, stop_win=90),
        )

    async def test_robot_settings_debug_returns_current_user_settings(self) -> None:
        main.auto_trader.update_config(
            "user-settings-a",
            main.RobotConfigUpdate(entry_value=15, min_confidence=97),
        )

        response = await main.debug_robot_settings(
            {"user_id": "user-settings-a"}
        )
        payload = json.loads(response.body)

        self.assertEqual(payload["user_id"], "user-settings-a")
        self.assertEqual(payload["source"], "memory")
        self.assertEqual(payload["settings"]["entry_value"], 15)
        self.assertEqual(payload["settings"]["min_confidence"], 97)
        self.assertNotIn("ai_analysis_enabled", payload["settings"])
        self.assertNotIn("ai_confirmation_required", payload["settings"])
        self.assertNotIn("ai_min_confidence", payload["settings"])
        self.assertNotIn("enabled", payload["settings"])

        response = await main.debug_user_isolation({"user_id": "user-b"})

        payload = json.loads(response.body)
        self.assertEqual(
            payload,
            {
                "user_id": "user-b",
                "has_state": True,
                # Atualizado 2026-08-07: default de entry_value é 5.0 (era
                # 2.0); source agora é "memory" (o cache em memória é a
                # fonte primária desde a mudança para restauração on-demand).
                "entry_value": 5.0,
                "stop_win": 50.0,
                "stop_loss": 30.0,
                "source": "memory",
            },
        )

    async def test_robot_state_hides_private_ai_config_fields(self) -> None:
        user_id = "user-hidden-ai-config"

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=(200, {"ok": True, "data": {}}))),
            patch.object(main, "sync_user_store_from_payload"),
        ):
            response = await main.robot_state({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertNotIn("ai_analysis_enabled", data)
        self.assertNotIn("ai_confirmation_required", data)
        self.assertNotIn("ai_min_confidence", data)

    async def test_robot_state_strips_legacy_ai_fields_from_nested_signal_data(self) -> None:
        user_id = "user-hidden-ai-nested"
        state = main.auto_trader.start(user_id)
        state.status = "WAITING_NEXT_CANDLE_ENTRY"
        state.pending_signal = {
            "symbol": "EURUSD-OTC",
            "signal": "CALL",
            "direction": "CALL",
            "confidence": 91,
            "payout": 88,
            "ai_voice_text": "legacy",
            "ai_confidence": 99,
        }
        state.best_candidate = dict(state.pending_signal)

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=(200, {"ok": True, "data": {}}))),
            patch.object(main, "sync_user_store_from_payload"),
        ):
            response = await main.robot_state({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertNotIn("ai_voice_text", data["pending_signal"])
        self.assertNotIn("ai_confidence", data["pending_signal"])
        self.assertNotIn("ai_voice_text", data["best_candidate"])
        self.assertNotIn("ai_confidence", data["best_candidate"])

    async def test_robot_state_after_result_keeps_result_visible_for_five_seconds(self) -> None:
        user_id = "user-result-state"
        state = main.auto_trader.start(user_id)
        state.cycle_minutes = 10
        main.auto_trader.record_trade(
            user_id,
            {
                "order_id": "result-state-1",
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 2,
                "result": "PENDING_RESULT",
            },
        )
        main.auto_trader.finish_trade(user_id, "result-state-1", "WIN", 1.76)

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success(
                            {
                                "connected": True,
                                "active_mode": "PRACTICE",
                                "server_time": SERVER_TIME_M1_OPEN,
                            }
                        ),
                    )
                ),
            ),
            patch.object(main, "sync_user_store_from_payload"),
        ):
            response = await main.robot_state({"user_id": user_id})

        payload = json.loads(response.body)["data"]
        self.assertEqual(payload["status"], STATUS_WIN)
        self.assertEqual(payload["last_trade"]["result"], "WIN")
        self.assertIsNotNone(payload["last_trade"]["finished_at"])
        self.assertIsNotNone(payload["result_received_at"])
        self.assertIsNotNone(payload["result_display_until"])
        self.assertFalse(payload["result_waiting"])
        self.assertFalse(payload["operation_in_progress"])
        self.assertFalse(payload["entry_window_open"])
        self.assertEqual(payload["seconds_until_next_cycle"], 0)

        state.result_display_until = utc_now() - timedelta(seconds=1)
        waiting_payload = state.to_dict()
        self.assertEqual(waiting_payload["status"], STATUS_WAITING_NEXT_CYCLE)
        # Atualizado 2026-08-07: pós-resultado agenda a próxima janela da vela
        # do timeframe (M1 = até 60s), não mais cycle_minutes*60s (cooldown legado).
        self.assertGreaterEqual(waiting_payload["seconds_until_next_cycle"], 0)
        self.assertLessEqual(waiting_payload["seconds_until_next_cycle"], 60)

    async def test_demo_sends_at_most_one_order_per_cycle(self) -> None:
        user_id = "user-demo"
        main.auto_trader.start(user_id)
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 94,
                "payout": 90,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )
        calls = []

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            calls.append((method, path, call_user_id, json_body, params))
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": SERVER_TIME_M1_OPEN,
                    }
                )
            if path == "/payouts":
                return 200, main.build_success([{"symbol": "EURUSD-OTC", "payout": 90}])
            if path == "/orders/buy-real":
                return 200, main.build_success({"order_id": "demo-1"})
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [
                {
                    "symbol": "EURUSD-OTC",
                    "signal": "CALL",
                    "confidence": 94,
                    "strength": 80,
                }
            ]
        )

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, scan_payload))),
            patch.object(main.trade_result_monitor, "start", return_value=True),
        ):
            first_status, first_payload = await main.execute_robot_cycle(user_id)
            second_status, second_payload = await main.execute_robot_cycle(user_id)

        # Atualizado 2026-08-07: sistema é REAL-only — a ordem sempre vai
        # para "/orders/buy-real" (nunca "/orders/buy-demo"), e o status
        # visível normaliza para WAITING_RESULT (não o legado PENDING_RESULT).
        orders = [call for call in calls if call[1] == "/orders/buy-real"]
        self.assertEqual(first_status, 200)
        self.assertEqual(first_payload["data"]["status"], STATUS_WAITING_RESULT)
        self.assertEqual(second_status, 200)
        self.assertEqual(second_payload["data"]["status"], STATUS_WAITING_RESULT)
        self.assertEqual(len(orders), 1)

    # Atualizado 2026-08-07: renomeado de test_start_does_not_operate_immediately
    # — cadência contínua faz o robô varrer o mercado desde o primeiro tick
    # após start(); a garantia agora é apenas "sem candidato aprovado, não
    # opera", não "espera N minutos sem varrer".
    async def test_start_scans_continuously_but_only_operates_with_signal(self) -> None:
        user_id = "user-start-waits"

        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker"),
            # Atualizado 2026-08-07: robot_start agora sincroniza a conexão
            # com a BullEx (status + saldo real) antes de iniciar.
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success(
                            {
                                "connected": True,
                                "active_mode": "REAL",
                                "active_mode_from_bullex": "REAL",
                                "server_time": 0.0,
                                "balance_real": 100,
                                "balance": 100,
                                "mode": "REAL",
                            }
                        ),
                    )
                ),
            ),
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            response = await main.robot_start({"user_id": user_id})

        payload = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], STATUS_WAITING_NEXT_CYCLE)
        # Atualizado 2026-08-07: cadência contínua agenda para a próxima
        # vela do timeframe (M1 = até 60s), não mais um cooldown fixo de
        # 5 minutos.
        self.assertGreaterEqual(payload["seconds_until_next_cycle"], 0)
        self.assertLessEqual(payload["seconds_until_next_cycle"], 60)
        self.assertIn("[ROBOT_START_NEW_CYCLE]", "\n".join(logs.output))
        self.assertIsNotNone(payload["cycle_id"])

        # Atualizado 2026-08-07: cadência contínua (ANALISE_CONTINUA.md §1)
        # — o robô varre o mercado a cada chamada de execute_robot_cycle,
        # mesmo imediatamente após o start(); não existe mais um período de
        # espera sem varredura. Sem candidato aprovado, permanece em
        # WAITING_NEXT_CYCLE.
        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success({"connected": True, "active_mode": "REAL", "server_time": 0.0})
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex) as service_call,
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, main.build_success([])))) as scan,
        ):
            status_code, tick_payload = await main.execute_robot_cycle(user_id)

        self.assertEqual(status_code, 200)
        self.assertEqual(tick_payload["data"]["status"], STATUS_WAITING_NEXT_CYCLE)
        scan.assert_awaited_once()

    async def test_due_cycle_runs_analysis_and_rejects_when_no_signal(self) -> None:
        user_id = "user-cycle-due-no-signal"
        state = main.auto_trader.start(user_id)
        state.cycle_minutes = 1
        make_cycle_due(user_id)

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": 20.0,
                    }
                )
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, main.build_success([])))) as scan,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        output = "\n".join(logs.output)
        self.assertEqual(status_code, 200)
        # Atualizado 2026-08-07: sem candidato, o robô fica em cadência
        # contínua (WAITING_NEXT_CYCLE), não mais no antigo estado fixo
        # WAITING_ANALYSIS_WINDOW; a rejeição vem do funil NO_OPPORTUNITY.
        self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(data["rejection_reason"], "NO_OPPORTUNITY")
        self.assertEqual(
            data["last_rejection_reason"],
            "NO_PATTERN_FOUND",
        )
        self.assertEqual(data["last_analysis_result"], "NO_OPPORTUNITY_FOUND")
        self.assertEqual(data["analysis_result"], "NO_OPPORTUNITY_FOUND")
        self.assertIsNotNone(data["last_analysis_at"])
        self.assertIsNone(data["pending_signal"])
        self.assertGreaterEqual(data["seconds_until_next_cycle"], 0)
        self.assertIn("[CYCLE_START]", output)
        self.assertIn("[WORKER_ANALYSIS_STARTED]", output)
        self.assertIn("[WORKER_ANALYSIS_FINISHED]", output)
        scan.assert_awaited_once()

    async def test_due_cycle_with_valid_signal_creates_pending_signal(self) -> None:
        user_id = "user-cycle-due-valid-signal"
        state = main.auto_trader.start(user_id)
        state.cycle_minutes = 1
        make_cycle_due(user_id)
        status_times = iter((20.0, 20.0))

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": next(status_times),
                    }
                )
            if path == "/payouts":
                return 200, main.build_success({"EURUSD-OTC": 90.0})
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [
                {
                    "symbol": "EURUSD-OTC",
                    "signal": "CALL",
                    "confidence": 94,
                    "strength": 80,
                    "payout": 90,
                    "strategy_score": 94,
                    "trade_allowed": True,
                    "price_action_setup": "CONTINUATION",
                }
            ]
        )

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, scan_payload))) as scan,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        output = "\n".join(logs.output)
        self.assertEqual(status_code, 200)
        # Atualizado 2026-08-07: status normalizado para exibição é
        # STATUS_WAITING_ENTRY (o antigo rótulo WAITING_NEXT_CANDLE_ENTRY não
        # é mais retornado por to_dict()).
        self.assertEqual(data["status"], STATUS_WAITING_ENTRY)
        # Atualizado 2026-08-07: analysis_window_start_second/end_second e
        # entry_window_start_second/end_second não existem mais em
        # to_dict() (removidos na migração para cadência contínua).
        self.assertAlmostEqual(data["current_candle_seconds"], 20.0, delta=0.5)
        self.assertEqual(data["last_analysis_result"], "BEST_CANDIDATE_SELECTED")
        self.assertEqual(data["analysis_result"], "BEST_CANDIDATE_SELECTED")
        # Atualizado 2026-08-07: analysis_started_at é limpo (None) quando a
        # análise já terminou; last_analysis_at é o timestamp que permanece
        # preenchido.
        self.assertIsNone(data["analysis_started_at"])
        self.assertIsNotNone(data["last_analysis_at"])
        self.assertEqual(data["pending_signal"]["symbol"], "EURUSD-OTC")
        # pending_signal e a ordem enviada usam a mesma direção da análise.
        self.assertEqual(data["pending_signal"]["signal"], "CALL")
        self.assertEqual(data["pending_signal"]["direction"], "CALL")
        # O nome deixou de ser "Confluência EMA9/EMA21 + RSI + ..." em
        # 09/09/2026: era um inventário de indicadores lido em voz alta como se
        # fosse nome de estratégia. Sem lista do motor no sinal (caso deste
        # teste), sobra a lista de componentes — mas sem a palavra na frente.
        nome_estrategia = data["pending_signal"]["strategy_name"]
        self.assertFalse(nome_estrategia.lower().startswith("confluência"))
        self.assertEqual(nome_estrategia, ", ".join(data["pending_signal"]["used_strategies"]))
        self.assertIsNotNone(data["pending_signal"]["strategy_reason"])
        self.assertIn("Payout", data["pending_signal"]["used_strategies"])
        self.assertEqual(data["candidates_count"], 1)
        self.assertEqual(data["best_candidate"]["symbol"], "EURUSD-OTC")
        self.assertEqual(data["strategy_score"], data["pending_signal"]["strategy_score"])
        self.assertIn("[CYCLE_START]", output)
        self.assertIn("[WORKER_ANALYSIS_STARTED]", output)
        self.assertIn("[WORKER_ANALYSIS_FINISHED]", output)
        self.assertIn("[BEST_CANDIDATE_FOUND]", output)
        self.assertIn("[SIGNAL_PREPARED]", output)
        scan.assert_awaited_once()

    async def test_due_cycle_analyzes_at_second_10_and_prepares_pending_signal(self) -> None:
        user_id = "user-analysis-window-open"
        state = main.auto_trader.start(user_id)
        state.cycle_minutes = 1
        make_cycle_due(user_id)
        status_times = iter((10.0, 10.0))

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": next(status_times),
                    }
                )
            if path == "/payouts":
                return 200, main.build_success({"EURUSD-OTC": 90.0})
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [
                {
                    # GBPUSD-OTC (não EURUSD-OTC): CONTINUATION+PUT em
                    # EURUSD-OTC é bloqueio duro anti-loss
                    # (WEAK_CONTINUATION_PUT, ver signal_engine.py).
                    "symbol": "GBPUSD-OTC",
                    "signal": "PUT",
                    "confidence": 95,
                    "strength": 81,
                    "payout": 90,
                    "strategy_score": 95,
                    "trade_allowed": True,
                    "price_action_setup": "CONTINUATION",
                }
            ]
        )

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, scan_payload))) as scan,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        # Atualizado 2026-08-07: status normalizado é STATUS_WAITING_ENTRY;
        # analysis_window_open/seconds_until_analysis_window não existem
        # mais em to_dict() (cadência contínua elimina a janela dedicada de
        # análise).
        self.assertEqual(data["status"], STATUS_WAITING_ENTRY)
        self.assertEqual(data["seconds_until_entry_window"], 50)
        self.assertEqual(data["pending_signal"]["symbol"], "GBPUSD-OTC")
        self.assertEqual(data["pending_signal"]["signal"], "PUT")
        scan.assert_awaited_once()

    async def test_missing_server_time_uses_vps_fallback_and_still_selects_candidate(self) -> None:
        user_id = "user-server-time-fallback"
        state = main.auto_trader.start(user_id)
        state.cycle_minutes = 1
        make_cycle_due(user_id)
        fallback_now = datetime.fromtimestamp(10, timezone.utc)

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                    }
                )
            if path == "/payouts":
                return 200, main.build_success({"EURUSD-OTC": 90.0})
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [
                {
                    "symbol": "EURUSD-OTC",
                    "signal": "CALL",
                    "confidence": 95,
                    "strength": 82,
                    "payout": 90,
                    "strategy_score": 95,
                    "trade_allowed": True,
                    "price_action_setup": "CONTINUATION",
                }
            ]
        )

        with (
            patch.object(main, "utc_now", return_value=fallback_now),
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, scan_payload))) as scan,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        output = "\n".join(logs.output)
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], STATUS_WAITING_ENTRY)
        self.assertEqual(data["server_time_source"], "vps_fallback")
        self.assertAlmostEqual(data["current_candle_seconds"], 10.0, delta=0.5)
        self.assertEqual(data["pending_signal"]["symbol"], "EURUSD-OTC")
        self.assertEqual(data["best_candidate"]["symbol"], "EURUSD-OTC")
        self.assertIn("[SERVER_TIME_FALLBACK]", output)
        scan.assert_awaited_once()

    # Atualizado 2026-08-07: cadência contínua (ANALISE_CONTINUA.md §1) —
    # não existe mais uma "janela de análise" fixa que bloqueia a varredura;
    # o robô varre o mercado em toda chamada de execute_robot_cycle,
    # independentemente do segundo da vela. Sem candidato aprovado, o
    # resultado é NO_OPPORTUNITY/NO_PATTERN_FOUND e o robô agenda o próximo
    # ciclo (WAITING_NEXT_CYCLE), mas a varredura É chamada.
    async def test_due_cycle_after_second_20_still_scans_continuously(self) -> None:
        user_id = "user-analysis-window-missed"
        main.auto_trader.start(user_id)
        make_cycle_due(user_id)

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": 30.0,
                    }
                )
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, main.build_success([])))) as scan,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        output = "\n".join(logs.output)
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(data["rejection_reason"], "NO_OPPORTUNITY")
        self.assertNotEqual(data["rejection_reason"], "MISSED_ENTRY_WINDOW")
        self.assertIsNone(data["pending_signal"])
        self.assertAlmostEqual(data["current_candle_seconds"], 30.0, delta=0.5)
        self.assertNotIn("[MISSED_ENTRY_WINDOW]", output)
        scan.assert_awaited_once()

    # Atualizado 2026-08-07: execute_robot_cycle roda a varredura de forma
    # síncrona dentro da mesma chamada (sem marcar status=ANALYZING/RUNNING
    # transitório para um poller concorrente) — o usuário já vê a mensagem
    # contínua "El Capo está analisando o mercado" mesmo em WAITING_NEXT_CYCLE
    # (ANALISE_CONTINUA.md §1: a IA monitora o mercado o tempo todo). Ao
    # terminar sem candidato, vira NO_OPPORTUNITY_FOUND/NO_PATTERN_FOUND
    # (não mais o CANDLES_UNAVAILABLE do modelo antigo, pois o scan mockado
    # devolve sucesso com lista vazia, não uma falha operacional).
    async def test_analysis_state_is_visible_while_scan_is_running(self) -> None:
        user_id = "user-analysis-running"
        main.auto_trader.start(user_id)
        make_cycle_due(user_id)
        scan_started = asyncio.Event()
        release_scan = asyncio.Event()

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                # O robô só opera em conta REAL — PRACTICE bloqueia o ciclo
                # antes mesmo da varredura (BULLEX_ACTIVE_MODE_NOT_REAL).
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": 20.0,
                    }
                )
            raise AssertionError(f"unexpected path: {path}")

        async def slow_scan(*args, **kwargs):
            scan_started.set()
            await release_scan.wait()
            return 200, main.build_success([])

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", side_effect=slow_scan),
        ):
            task = asyncio.create_task(main.execute_robot_cycle(user_id))
            await asyncio.wait_for(scan_started.wait(), timeout=1)
            running = main.auto_trader.get(user_id).to_dict()
            release_scan.set()
            _, finished_payload = await task

        self.assertEqual(running["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(
            running["analysis_message"],
            "Buscando melhor oportunidade",
        )
        self.assertEqual(finished_payload["data"]["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(finished_payload["data"]["analysis_result"], "NO_OPPORTUNITY_FOUND")
        self.assertEqual(
            finished_payload["data"]["last_rejection_reason"],
            "NO_PATTERN_FOUND",
        )
        self.assertGreaterEqual(
            finished_payload["data"]["seconds_until_next_cycle"],
            0,
        )

    # Atualizado 2026-08-07: recover_timed_out_analysis_if_needed (o timeout
    # de 10s dedicado) ficou órfão — nenhum caminho vivo do main.py o chama
    # mais. Em WAITING_NEXT_CYCLE, to_dict() já limpa analysis_result="RUNNING"
    # para None (não existe mais o rótulo dedicado "ANALYSIS_TIMEOUT" aqui).
    async def test_running_analysis_over_10_seconds_clears_running_flag(self) -> None:
        user_id = "user-analysis-timeout"
        state = main.auto_trader.start(user_id)
        state.status = STATUS_WAITING_NEXT_CYCLE
        state.next_cycle_at = utc_now() - timedelta(seconds=1)
        state.analysis_result = "RUNNING"
        state.last_analysis_result = "RUNNING"
        state.analysis_started_at = utc_now() - timedelta(seconds=11)

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success(
                            {
                                "connected": True,
                                "active_mode": "REAL",
                                "server_time": 15.0,
                            }
                        ),
                    )
                ),
            ),
            patch.object(main, "sync_user_store_from_payload"),
            patch.object(main, "persist_robot", return_value=None),
        ):
            response = await main.robot_state({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertIsNone(data["analysis_result"])
        self.assertIsNone(data["rejection_reason"])

    async def test_analysis_exception_schedules_next_cycle(self) -> None:
        user_id = "user-analysis-error"
        main.auto_trader.start(user_id)
        make_cycle_due(user_id)

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {"connected": True, "active_mode": "REAL", "server_time": 20.0}
                )
            raise AssertionError(f"unexpected path: {path}")

        async def broken_scan(*args, **kwargs):
            raise RuntimeError("scan exploded")

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", side_effect=broken_scan),
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        output = "\n".join(logs.output)
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], "WAITING_NEXT_CYCLE")
        self.assertEqual(data["analysis_result"], "ANALYSIS_ERROR")
        self.assertEqual(data["last_order_error"], "scan exploded")
        self.assertGreaterEqual(data["seconds_until_next_cycle"], 44)
        self.assertIn("[ANALYSIS_RECOVERED]", output)
        self.assertNotIn("[ANALYSIS_ERROR]", output)
        self.assertNotIn("[ROBOT ERROR]", output)

    async def test_waiting_next_cycle_running_result_never_returns_invalid_zero_state(self) -> None:
        user_id = "user-invalid-running-state"
        state = main.auto_trader.start(user_id)
        state.status = STATUS_WAITING_NEXT_CYCLE
        state.next_cycle_at = utc_now() - timedelta(seconds=1)
        state.analysis_result = "RUNNING"
        state.last_analysis_result = "RUNNING"
        state.analysis_started_at = utc_now()

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success(
                            {
                                "connected": True,
                                "active_mode": "REAL",
                                "server_time": 30.0,
                            }
                        ),
                    )
                ),
            ),
            patch.object(main, "sync_user_store_from_payload"),
        ):
            response = await main.robot_state({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            data["status"] == STATUS_WAITING_NEXT_CYCLE
            and data["seconds_until_next_cycle"] == 0
            and data["analysis_result"] == "RUNNING"
        )
        # Atualizado 2026-08-07: /robot/state não roda mais a máquina de
        # análise (só atualiza a janela de entrada); analysis_result="RUNNING"
        # é normalizado para None por to_dict(), e o status permanece
        # WAITING_NEXT_CYCLE (não existe mais WAITING_ANALYSIS_WINDOW).
        self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertIsNone(data["analysis_result"])
        self.assertIsNone(data["pending_signal"])
        self.assertIsNone(data["best_candidate"])

    # Atualizado 2026-08-07: /robot/state usa update_entry_window (não o
    # recover_running_analysis baseado em janela, que ficou órfão — nenhum
    # caminho vivo do main.py o chama mais fora do bloco morto
    # `if selected is None and False:`). update_entry_window só recupera
    # ANALYZING quando analysis_result != "RUNNING"; uma análise de fato em
    # andamento não é interrompida só pela posição da janela.
    async def test_analyzing_outside_window_stays_analyzing_while_running(self) -> None:
        user_id = "user-analyzing-outside-window"
        state = main.auto_trader.start(user_id)
        state.status = "ANALYZING"
        state.analysis_result = "RUNNING"
        state.last_analysis_result = "RUNNING"
        state.analysis_started_at = utc_now()
        state.best_candidate = None
        state.pending_signal = None

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success(
                            {
                                "connected": True,
                                "active_mode": "REAL",
                                "server_time": 30.0,
                            }
                        ),
                    )
                ),
            ),
            patch.object(main, "sync_user_store_from_payload"),
            patch.object(main, "persist_robot", return_value=None),
        ):
            response = await main.robot_state({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "ANALYZING")
        self.assertEqual(data["analysis_result"], "RUNNING")
        self.assertIsNone(data["pending_signal"])
        self.assertIsNone(data["best_candidate"])

    async def test_pending_signal_blocks_new_analysis(self) -> None:
        user_id = "user-pending-blocks-analysis"
        main.auto_trader.start(user_id)
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 92,
                "payout": 90,
            },
        )

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": 20.0,
                    }
                )
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock()) as scan,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["data"]["status"], STATUS_WAITING_ENTRY)
        self.assertIsNotNone(payload["data"]["pending_signal"])
        scan.assert_not_awaited()

    async def test_pending_result_blocks_new_analysis(self) -> None:
        user_id = "user-result-blocks-analysis"
        state = main.auto_trader.start(user_id)
        main.auto_trader.record_trade(
            user_id,
            {
                "order_id": "pending-result-1",
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 2,
                "result": "PENDING_RESULT",
            },
        )
        state.next_cycle_at = utc_now() - timedelta(seconds=1)

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock()) as bullex,
            patch.object(main, "scan_local_signals", new=AsyncMock()) as scan,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["data"]["status"], "WAITING_RESULT")
        self.assertTrue(payload["data"]["operation_in_progress"])
        bullex.assert_not_awaited()
        scan.assert_not_awaited()

    # Atualizado 2026-08-07: cycle_minutes não é mais um parâmetro livre —
    # ele é derivado do timeframe (M1=1, M5=5, M15=15, ver
    # cycle_minutes_for_timeframe em signal_engine.py) e é reescrito em
    # start()/update_config() sempre que o timeframe muda. Para configurar
    # um ciclo de 5 minutos agora é preciso setar timeframe="M5".
    async def test_configured_five_minute_cycle_is_used_on_start(self) -> None:
        user_id = "user-config-five"
        with (
            patch.object(main, "persist_robot", return_value=None) as persist,
            patch.object(main, "ensure_robot_worker"),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
            # robot_start sincroniza a conexão com a BullEx (status + saldo
            # real) antes de iniciar.
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success(
                            {
                                "connected": True,
                                "active_mode": "REAL",
                                "active_mode_from_bullex": "REAL",
                                "server_time": 0.0,
                                "balance_real": 100,
                                "balance": 100,
                                "mode": "REAL",
                            }
                        ),
                    )
                ),
            ),
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            config_response = await main.robot_config(
                main.RobotConfigUpdate(timeframe="M5"),
                {"user_id": user_id},
            )
            start_response = await main.robot_start({"user_id": user_id})

        configured = json.loads(config_response.body)["data"]
        started = json.loads(start_response.body)["data"]
        self.assertEqual(configured["cycle_minutes"], 5)
        self.assertEqual(started["cycle_minutes"], 5)
        # Atualizado 2026-08-07: cadência contínua agenda para a próxima
        # vela do timeframe (M5 = até 300s), o teste só garante o limite
        # superior — não há mais garantia de piso fixo em 299s.
        self.assertGreaterEqual(started["seconds_until_next_cycle"], 0)
        self.assertLessEqual(started["seconds_until_next_cycle"], 300)
        self.assertEqual([call.args[0] for call in persist.call_args_list], [user_id, user_id])
        output = "\n".join(logs.output)
        self.assertIn("[CYCLE_CONFIG]", output)
        self.assertIn("cycle_minutes=5", output)

    async def test_highest_strategy_score_candidate_is_selected(self) -> None:
        user_id = "user-ranking"
        state = main.auto_trader.start(user_id)
        make_cycle_due(user_id)
        status_times = iter((20.0, 20.0))

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": next(status_times),
                    }
                )
            if path == "/payouts":
                return 200, main.build_success({"EURUSD-OTC": 90.0, "GBPUSD-OTC": 91.0})
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [
                {
                    "symbol": "EURUSD-OTC",
                    "signal": "CALL",
                    "confidence": 94,
                    "strength": 5,
                    "trend": "SIDEWAYS",
                    "payout": 90,
                    "reason": "Candidato penalizado por tendencia lateral.",
                    "strategy_score": 60,
                    "trade_allowed": True,
                    "price_action_setup": "CONTINUATION",
                },
                {
                    "symbol": "GBPUSD-OTC",
                    "signal": "PUT",
                    "confidence": 95,
                    "strength": 30,
                    "trend": "DOWN",
                    "payout": 91,
                    "reason": "Maior confluencia entre estrategias.",
                    "strategy_score": 95,
                    "trade_allowed": True,
                    "price_action_setup": "CONTINUATION",
                },
            ]
        )

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(
                main,
                "scan_local_signals",
                new=AsyncMock(return_value=(200, scan_payload)),
            ),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], STATUS_WAITING_ENTRY)
        self.assertEqual(data["candidates_count"], 2)
        self.assertEqual(len(data["candidates"]), 2)
        self.assertEqual(data["best_candidate"]["symbol"], "GBPUSD-OTC")
        self.assertEqual(data["pending_signal"]["symbol"], "GBPUSD-OTC")
        self.assertGreater(
            data["best_candidate"]["strategy_score"],
            0,
        )

    async def test_order_status_is_sending_while_bullex_order_is_in_flight(self) -> None:
        user_id = "user-sending"
        state = main.auto_trader.start(user_id)
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 94,
                "payout": 90,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )
        order_started = asyncio.Event()
        release_order = asyncio.Event()

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": SERVER_TIME_M1_OPEN,
                    }
                )
            if path == "/payouts":
                return 200, main.build_success([{"symbol": "EURUSD-OTC", "payout": 90}])
            if path in {"/bullex/buy-real", "/orders/buy-real"}:
                order_started.set()
                await release_order.wait()
                return 200, main.build_success({"order_id": "sending-1"})
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [{"symbol": "EURUSD-OTC", "signal": "CALL", "confidence": 94, "strength": 80}]
        )
        sending_from_statuses = []
        original_start_sending = main.auto_trader.start_sending_order

        def track_start_sending(call_user_id):
            sending_from_statuses.append(main.auto_trader.get(call_user_id).status)
            return original_start_sending(call_user_id)

        entry_window = main.get_entry_window(state.timeframe, SERVER_TIME_M1_OPEN, server_time_source="bullex")
        with (
            patch.object(
                main,
                "refresh_entry_window",
                new=AsyncMock(return_value=(200, main.build_success({"connected": True, "active_mode": "REAL"}), entry_window)),
            ),
            patch.object(
                main,
                "reconcile_robot_connection_from_payload",
                new=AsyncMock(return_value=(state, True, "REAL", "bullex_service")),
            ),
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, scan_payload))),
            patch.object(
                main.auto_trader,
                "start_sending_order",
                side_effect=track_start_sending,
            ),
            patch.object(main.trade_result_monitor, "start", return_value=True),
        ):
            task = asyncio.create_task(main.execute_robot_cycle(user_id))
            await asyncio.wait_for(order_started.wait(), timeout=1)
            sending_payload = main.auto_trader.get(user_id).to_dict()
            release_order.set()
            status_code, payload = await task

        self.assertEqual(sending_payload["status"], STATUS_BUYING)
        self.assertFalse(sending_payload["operation_in_progress"])
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["data"]["status"], STATUS_WAITING_RESULT)
        self.assertTrue(payload["data"]["operation_in_progress"])
        self.assertEqual(sending_from_statuses, [STATUS_SIGNAL_FOUND])

    async def test_successful_order_goes_to_pending_result_with_last_trade_contract(self) -> None:
        user_id = "user-order-success"
        state = main.auto_trader.start(user_id)
        state.entry_value = 5
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "PUT",
                "confidence": 94,
                "payout": 91,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": SERVER_TIME_M1_OPEN,
                    }
                )
            if path == "/payouts":
                return 200, main.build_success([{"symbol": "EURUSD-OTC", "payout": 91}])
            if path in {"/bullex/buy-real", "/orders/buy-real"}:
                return 200, main.build_success({"order_id": "success-1"})
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [{"symbol": "EURUSD-OTC", "signal": "PUT", "confidence": 94, "strength": 80}]
        )

        entry_window = main.get_entry_window(state.timeframe, SERVER_TIME_M1_OPEN, server_time_source="bullex")
        with (
            patch.object(
                main,
                "refresh_entry_window",
                new=AsyncMock(return_value=(200, main.build_success({"connected": True, "active_mode": "REAL"}), entry_window)),
            ),
            patch.object(
                main,
                "reconcile_robot_connection_from_payload",
                new=AsyncMock(return_value=(state, True, "REAL", "bullex_service")),
            ),
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, scan_payload))),
            patch.object(main.trade_result_monitor, "start", return_value=True),
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        trade = payload["data"]["last_trade"]
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["data"]["status"], STATUS_WAITING_RESULT)
        self.assertTrue(payload["data"]["operation_in_progress"])
        self.assertIsNotNone(payload["data"]["last_signal"])
        self.assertEqual(trade["order_id"], "success-1")
        self.assertEqual(trade["active"], "EURUSD-OTC")
        # Atualizado 2026-08-07: execução alinhada à análise (ESTRATEGIA.md §2a).
        # O sinal analisado foi PUT; a ordem enviada à corretora também é PUT.
        self.assertEqual(trade["analyzed_direction"], "PUT")
        self.assertEqual(trade["direction"], "PUT")
        self.assertFalse(trade["execution_direction_inverted"])
        self.assertEqual(trade["amount"], 5)
        self.assertIsNotNone(trade["sent_at"])
        self.assertEqual(trade["expiration"], "M1")
        self.assertEqual(trade["result"], STATUS_PENDING_RESULT)
        self.assertEqual(trade["server_time_at_send"], "1970-01-01T00:01:00+00:00")
        self.assertEqual(trade["server_timestamp_at_send"], SERVER_TIME_M1_OPEN)
        # calculate_expected_expire_at usa max(server_timestamp, sent_at real)
        # como referência — como SERVER_TIME_M1_OPEN (60.0) é muito menor que
        # o wall-clock real de sent_at, o alinhamento vira "sent_at_minimum"
        # (sent_at + 1 candle), não "server_time_aligned".
        self.assertEqual(trade["expiration_source"], "sent_at_minimum")
        self.assertEqual(
            trade["expected_expire_at"],
            (datetime.fromisoformat(trade["sent_at"]) + timedelta(minutes=1)).isoformat(),
        )
        output = "\n".join(logs.output)
        self.assertIn("[STATE] BUYING", output)
        self.assertIn("[STATE] ORDER_OPEN", output)
        self.assertIn("[STATE] WAITING_RESULT", output)
        self.assertIn("[EXPIRATION_SET]", output)
        self.assertIn("[ORDER_SEND_SUCCESS]", output)

    async def test_bullex_returned_expiration_is_used_as_primary_source(self) -> None:
        user_id = "user-order-expiration-source"
        main.auto_trader.start(user_id)
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 94,
                "payout": 90,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )
        # Atualizado 2026-08-07: calculate_expected_expire_at só aceita a
        # expiração devolvida pela corretora se ela for POSTERIOR ao envio da
        # ordem (sent_at). Um timestamp fixo no passado (epoch baixo) sempre
        # cai no fallback alinhado ao server_time, então usamos um horário
        # futuro relativo a agora para simular uma resposta real da BullEx.
        returned_expiration = (utc_now() + timedelta(minutes=2)).isoformat()

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": SERVER_TIME_M1_OPEN,
                    }
                )
            if path == "/orders/buy-real":
                return 200, main.build_success(
                    {
                        "order_id": "source-1",
                        "close_time": returned_expiration,
                    }
                )
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [{"symbol": "EURUSD-OTC", "signal": "CALL", "confidence": 94, "strength": 80}]
        )

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, scan_payload))),
            patch.object(main.trade_result_monitor, "start", return_value=True),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        trade = payload["data"]["last_trade"]
        self.assertEqual(status_code, 200)
        self.assertEqual(trade["expected_expire_at"], returned_expiration)
        self.assertEqual(trade["expiration_source"], "close_time")

    async def test_order_falls_back_to_next_candidate_when_asset_unavailable(self) -> None:
        user_id = "user-order-fallback"
        state = main.auto_trader.start(user_id)
        state.entry_value = 5
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 94,
                "payout": 90,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )
        state.candidates = [
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "direction": "CALL",
                "confidence": 94,
                "payout": 90,
                "strategy_score": 94,
                "trade_allowed": True,
            },
            {
                "symbol": "GBPUSD-OTC",
                "signal": "PUT",
                "direction": "PUT",
                "confidence": 93,
                "payout": 91,
                "strategy_score": 93,
                "trade_allowed": True,
            },
        ]
        state.candidates_count = len(state.candidates)
        order_actives: list[str] = []

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": SERVER_TIME_M1_OPEN,
                    }
                )
            if path == "/orders/buy-real":
                order_actives.append(json_body["active"])
                if json_body["active"] == "EURUSD-OTC":
                    return 409, main.build_error("Cannot purchase an option (the asset is not available at the moment).")
                return 200, main.build_success({"order_id": "fallback-1"})
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main.trade_result_monitor, "start", return_value=True) as monitor,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        trade = data["last_trade"]
        output = "\n".join(logs.output)
        self.assertEqual(status_code, 200)
        self.assertEqual(order_actives, ["EURUSD-OTC", "GBPUSD-OTC"])
        self.assertEqual(data["status"], STATUS_WAITING_RESULT)
        self.assertEqual(data["order_attempts"], 2)
        self.assertTrue(data["fallback_candidate_used"])
        self.assertEqual(data["best_candidate"]["symbol"], "GBPUSD-OTC")
        self.assertIsNone(data["pending_signal"])
        self.assertEqual(trade["active"], "GBPUSD-OTC")
        # Atualizado 2026-08-07: execução alinhada — candidato de fallback PUT
        # envia PUT à corretora.
        self.assertEqual(trade["analyzed_direction"], "PUT")
        self.assertEqual(trade["direction"], "PUT")
        self.assertFalse(trade["execution_direction_inverted"])
        self.assertEqual(trade["order_attempts"], 2)
        self.assertTrue(trade["fallback_candidate_used"])
        monitor.assert_called_once_with(user_id, "fallback-1", trade["expires_at"])
        self.assertIn("[ORDER_SEND_FAILED]", output)
        self.assertIn("[ORDER_FALLBACK_NEXT_CANDIDATE]", output)
        self.assertIn("[ORDER_SEND_SUCCESS]", output)

    async def test_order_rejects_after_three_unavailable_candidates(self) -> None:
        user_id = "user-order-fallback-exhausted"
        state = main.auto_trader.start(user_id)
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 94,
                "payout": 90,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )
        state.candidates = [
            {
                "symbol": symbol,
                "signal": "CALL",
                "direction": "CALL",
                "confidence": confidence,
                "payout": payout,
                "strategy_score": confidence,
                "trade_allowed": True,
            }
            for symbol, confidence, payout in (
                ("EURUSD-OTC", 94, 90),
                ("GBPUSD-OTC", 93, 91),
                ("USDJPY-OTC", 92, 89),
                ("EURJPY-OTC", 91, 88),
            )
        ]
        state.candidates_count = len(state.candidates)
        order_actives: list[str] = []

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": SERVER_TIME_M1_OPEN,
                    }
                )
            if path == "/orders/buy-real":
                order_actives.append(json_body["active"])
                return 409, main.build_error("active suspended")
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main.trade_result_monitor, "start", return_value=True) as monitor,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        output = "\n".join(logs.output)
        self.assertEqual(status_code, 409)
        self.assertEqual(order_actives, ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"])
        self.assertEqual(data["status"], STATUS_ORDER_REJECTED)
        self.assertEqual(data["order_attempts"], 3)
        self.assertTrue(data["fallback_candidate_used"])
        self.assertEqual(data["last_order_error"], "Nenhum ativo disponível no momento da compra.")
        self.assertIsNone(data["pending_signal"])
        # Atualizado 2026-08-07: cadência contínua substituiu o cooldown fixo
        # de 5 minutos pelo agendamento para a próxima vela (M1: até 60s).
        self.assertGreaterEqual(data["seconds_until_next_cycle"], 0)
        self.assertLessEqual(data["seconds_until_next_cycle"], 60)
        monitor.assert_not_called()
        self.assertIn("[ORDER_SEND_FAILED]", output)
        self.assertIn("[ORDER_FALLBACK_NEXT_CANDIDATE]", output)
        self.assertIn("[ORDER_REJECTED]", output)

    async def test_rejected_order_stays_order_rejected_and_clears_pending_signal(self) -> None:
        user_id = "user-order-rejected"
        main.auto_trader.start(user_id)
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 94,
                "payout": 90,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": SERVER_TIME_M1_OPEN,
                    }
                )
            if path == "/orders/buy-real":
                return 409, main.build_error("MARKET_CLOSED")
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [{"symbol": "EURUSD-OTC", "signal": "CALL", "confidence": 94, "strength": 80}]
        )

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, scan_payload))),
            patch.object(main.trade_result_monitor, "start", return_value=True) as monitor,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        self.assertEqual(status_code, 409)
        self.assertEqual(payload["data"]["status"], STATUS_ORDER_REJECTED)
        self.assertEqual(payload["data"]["rejection_reason"], "MARKET_CLOSED")
        self.assertEqual(payload["data"]["last_order_error"], "MARKET_CLOSED")
        self.assertIsNotNone(payload["data"]["rejected_at"])
        self.assertIsNotNone(payload["data"]["last_signal"])
        self.assertIsNone(payload["data"]["pending_signal"])
        self.assertFalse(payload["data"]["operation_in_progress"])
        # Atualizado 2026-08-07: cadência contínua substituiu o cooldown fixo
        # de 5 minutos — reset_cycle_after_finish agenda para a próxima vela
        # (M1: até 60s), não mais um intervalo fixo de 299-300s.
        self.assertGreaterEqual(payload["data"]["seconds_until_next_cycle"], 0)
        self.assertLessEqual(payload["data"]["seconds_until_next_cycle"], 60)
        self.assertNotEqual(payload["data"]["status"], STATUS_WAITING_NEXT_CYCLE)
        monitor.assert_not_called()
        output = "\n".join(logs.output)
        self.assertIn("[ORDER_SEND_FAILED]", output)
        self.assertIn("[ORDER_REJECTED]", output)
        self.assertIn("[NEXT_CYCLE_SCHEDULED]", output)

    async def test_order_rejected_is_visible_for_five_seconds_then_waits(self) -> None:
        user_id = "user-order-rejected-visible"
        state = main.auto_trader.start(user_id)
        state.cycle_minutes = 5
        state.last_signal = {"symbol": "EURUSD-OTC", "signal": "CALL"}

        rejected = main.auto_trader.reject_order(
            user_id,
            "active suspended",
            last_order_error=main.readable_order_error("active suspended"),
        )
        visible_payload = rejected.to_dict()
        rejected.rejected_at = utc_now() - timedelta(seconds=5)
        waiting_payload = rejected.to_dict()

        self.assertEqual(visible_payload["status"], STATUS_ORDER_REJECTED)
        self.assertEqual(visible_payload["last_order_error"], "Ativo suspenso pela BullEx")
        self.assertEqual(waiting_payload["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertIsNone(waiting_payload["rejection_reason"])
        self.assertEqual(waiting_payload["last_rejection_reason"], "active suspended")
        self.assertEqual(waiting_payload["last_signal"]["symbol"], "EURUSD-OTC")

    async def test_non_whitelisted_asset_never_operates(self) -> None:
        user_id = "user-apple"
        main.auto_trader.start(user_id)
        make_cycle_due(user_id)
        calls = []

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            calls.append(path)
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": 10.0,
                    }
                )
            if path == "/payouts":
                return 200, main.build_success([{"symbol": "APPLE", "payout": 95}])
            raise AssertionError("an order must not be sent for APPLE")

        scan_payload = main.build_success(
            [{"symbol": "APPLE", "signal": "CALL", "confidence": 99, "strength": 99}]
        )

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, scan_payload))),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        # Atualizado 2026-08-07: build_robot_payload normaliza
        # WAITING_ANALYSIS_WINDOW para WAITING_NEXT_CYCLE (cadência contínua),
        # e o ativo não-whitelisted é descartado pelo filtro ACTIVE_CLOSED /
        # PRICE_ACTION_SETUP, terminando o ciclo como NO_OPPORTUNITY (não há
        # mais um motivo dedicado "WAITING_NEXT_ANALYSIS_WINDOW").
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["data"]["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(payload["data"]["rejection_reason"], "NO_OPPORTUNITY")
        self.assertEqual(payload["data"]["analysis_result"], "NO_OPPORTUNITY_FOUND")
        self.assertEqual(
            payload["data"]["last_rejection_reason"],
            "NO_PATTERN_FOUND",
        )
        self.assertNotIn("/orders/buy-demo", calls)
        self.assertNotIn("/orders/buy-real", calls)

    async def test_waiting_next_cycle_does_not_analyze_or_order(self) -> None:
        user_id = "user-wait-next-cycle"
        state = main.auto_trader.start(user_id)
        state.next_cycle_at = utc_now() + timedelta(minutes=5)
        state.status = STATUS_WAITING_NEXT_CYCLE

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock()) as upstream,
            patch.object(main, "scan_local_signals", new=AsyncMock()) as scan,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["data"]["status"], STATUS_WAITING_NEXT_CYCLE)
        # Atualizado 2026-08-07: sem pending_signal/candidato, o rótulo de
        # contagem regressiva exibido ao usuário é "Buscando melhor
        # oportunidade" (mensagem contínua de análise), não mais "Próxima
        # entrada em".
        self.assertEqual(payload["data"]["display_countdown_label"], "Buscando melhor oportunidade")
        upstream.assert_not_awaited()
        scan.assert_not_awaited()
        output = "\n".join(logs.output)
        self.assertIn("[WAITING_NEXT_CYCLE]", output)
        self.assertIn("[ANALYSIS_SKIPPED_NEXT_CYCLE]", output)

    async def test_invalid_buy_real_payload_does_not_call_bullex(self) -> None:
        user_id = "user-invalid-real-payload"
        state = main.auto_trader.start(user_id)
        state.entry_value = 5
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 94,
                "payout": 91,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )
        entry_window = main.get_entry_window(state.timeframe, SERVER_TIME_M1_OPEN, server_time_source="bullex")

        with (
            patch.object(
                main,
                "refresh_entry_window",
                new=AsyncMock(return_value=(200, main.build_success({"connected": True, "active_mode": "REAL"}), entry_window)),
            ),
            patch.object(
                main,
                "reconcile_robot_connection_from_payload",
                new=AsyncMock(return_value=(state, True, "REAL", "bullex_service")),
            ),
            patch.object(main, "validate_buy_real_order_payload", return_value="BUY_REAL_PAYLOAD_MISSING_ACTIVE"),
            patch.object(main, "submit_bullex_order", new=AsyncMock()) as submit,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["data"]["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertIsNone(payload["data"]["pending_signal"])
        submit.assert_not_awaited()
        output = "\n".join(logs.output)
        self.assertIn("[BUY_REAL_PAYLOAD_INVALID]", output)
        self.assertIn("[NEXT_CYCLE_SCHEDULED]", output)

    async def test_disconnected_account_does_not_scan_or_order(self) -> None:
        """Após falhas confirmadas o ciclo entra em WAITING_RECOVERY sem desligar."""
        user_id = "user-disconnected"
        state = main.auto_trader.start(user_id)
        state.connected = True
        state.active_mode = "REAL"
        state.connection_failure_count = main.OFFLINE_CONFIRMATION_FAILURES
        make_cycle_due(user_id)
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 92,
                "payout": 90,
            },
        )

        with (
            patch.object(
                main,
                "refresh_entry_window",
                new=AsyncMock(
                    return_value=(
                        409,
                        {"ok": False, "data": {"connected": False}, "error": "SESSION_DISCONNECTED"},
                        None,
                    )
                ),
            ),
            patch.object(
                main,
                "reconcile_robot_connection_from_payload",
                new=AsyncMock(return_value=(state, False, None, "disconnected")),
            ),
            patch.object(main, "scan_local_signals", new=AsyncMock()) as scan,
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "_auto_reconnect_then_ensure_worker", new=AsyncMock()) as reconnect,
            self.assertLogs("backend-gateway", level="WARNING") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["data"]["status"], main.STATUS_WAITING_RECOVERY)
        self.assertTrue(payload["data"]["enabled"])
        self.assertNotEqual(payload["data"]["status"], STATUS_ACCOUNT_DISCONNECTED)
        scan.assert_not_awaited()
        reconnect.assert_called_once_with(user_id)
        self.assertIn("[ROBOT_CONNECTION_BLIP_RECOVER]", "\n".join(logs.output))

    async def test_session_blip_after_grace_expired_keeps_robot_enabled(self) -> None:
        """Regressão: grace expirada + 1 falha não chama disconnect_account."""
        user_id = "user-blip-after-trades"
        state = main.auto_trader.start(user_id)
        state.connected = True
        state.active_mode = "REAL"
        state.account_mode = "REAL"
        state.stop_win = 1000.0
        state.profit = 168.0
        state.connection_failure_count = 0
        state.last_connected_at = main.utc_now() - timedelta(seconds=120)
        state.connection_grace_until = main.utc_now() - timedelta(seconds=90)

        payload = {"ok": False, "data": {"connected": False}, "error": "SESSION_DISCONNECTED"}
        synced, connected, active_mode, source = main.sync_robot_connection_from_payload(
            user_id,
            payload,
        )
        self.assertTrue(connected)
        self.assertTrue(synced.enabled)
        self.assertEqual(source, "cached_grace")
        self.assertEqual(synced.connection_failure_count, 1)
        self.assertNotEqual(synced.status, STATUS_ACCOUNT_DISCONNECTED)

    async def test_robot_state_does_not_poll_or_disconnect_on_upstream_false_negative(self) -> None:
        user_id = "user-state-disconnected"
        state = main.auto_trader.start(user_id)
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "PUT",
                "confidence": 92,
                "payout": 90,
            },
        )
        state.entry_window_open = True
        state.quality_score = 91

        with patch.object(main, "call_bullex_service", new=AsyncMock()) as upstream:
            response = await main.robot_state({"user_id": user_id})

        payload = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["connected"])
        self.assertEqual(payload["connection_failure_count"], 0)
        self.assertNotEqual(payload["status"], STATUS_ACCOUNT_DISCONNECTED)
        self.assertIsNotNone(payload["pending_signal"])
        upstream.assert_not_awaited()

    async def test_robot_state_returns_cached_connection_without_upstream_poll(self) -> None:
        user_id = "user-state-grace"
        state = main.auto_trader.start(user_id)
        state = main.auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode="PRACTICE",
            source="bullex_service",
        )
        state.connection_checked_at = utc_now() - timedelta(seconds=5)

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock()) as upstream,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            response = await main.robot_state({"user_id": user_id})

        payload = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["connection_failure_count"], 0)
        self.assertEqual(payload["connection_status_source"], "bullex_service")
        self.assertIsNotNone(payload["last_connected_at"])
        self.assertIsNotNone(payload["connection_grace_until"])
        self.assertNotEqual(payload["status"], STATUS_ACCOUNT_DISCONNECTED)
        self.assertIn("[ROBOT_STATE_FAST_RETURN]", "\n".join(logs.output))
        upstream.assert_not_awaited()

    async def test_robot_state_does_not_revalidate_expired_grace_inline(self) -> None:
        user_id = "user-state-grace-expired"
        state = main.auto_trader.start(user_id)
        state = main.auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode="PRACTICE",
            source="bullex_service",
        )
        old_connected_at = utc_now() - timedelta(seconds=35)
        state.connection_checked_at = utc_now() - timedelta(seconds=5)
        state.last_connected_at = old_connected_at
        state.connection_grace_until = old_connected_at + timedelta(seconds=30)
        state.connection_failure_count = 2

        with patch.object(main, "call_bullex_service", new=AsyncMock()) as upstream:
            response = await main.robot_state({"user_id": user_id})

        payload = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["connection_failure_count"], 2)
        self.assertNotEqual(payload["status"], STATUS_ACCOUNT_DISCONNECTED)
        upstream.assert_not_awaited()

    async def test_robot_state_uses_memory_without_requesting_session_or_account(self) -> None:
        user_id = "user-state-account-connected"
        state = main.auto_trader.start(user_id)
        state.connected = True
        state.active_mode = "REAL"

        with patch.object(main, "call_bullex_service", new=AsyncMock()) as upstream:
            response = await main.robot_state({"user_id": user_id})

        payload = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["connected"])
        # Atualizado 2026-08-07: o teste seta state.active_mode="REAL" acima
        # (era um erro de copiar/colar comparar com "PRACTICE" aqui).
        self.assertEqual(payload["active_mode"], "REAL")
        self.assertNotEqual(payload["status"], STATUS_ACCOUNT_DISCONNECTED)
        self.assertEqual(payload["connection_failure_count"], 0)
        upstream.assert_not_awaited()

    # Atualizado 2026-08-07: o robô só opera em conta REAL — active_mode
    # PRACTICE agora bloqueia a sincronização da conexão logo no connect
    # (BULLEX_ACCOUNT_STILL_PRACTICE), então o mock precisa devolver REAL
    # para exercitar o caminho de sucesso original do teste.
    async def test_bullex_connect_syncs_robot_connection_immediately(self) -> None:
        user_id = "user-connect-sync"
        state = main.auto_trader.start(user_id)
        state.status = STATUS_ACCOUNT_DISCONNECTED
        state.rejection_reason = "ACCOUNT_DISCONNECTED"
        state.last_rejection_reason = "ACCOUNT_DISCONNECTED"
        state.connection_failure_count = 2

        payload = main.build_success(
            {"connected": True, "requires_2fa": False, "active_mode": "REAL"}
        )

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=(200, payload))),
            patch.object(main, "sync_user_store_from_payload"),
            patch.object(main, "persist_robot", return_value=None) as persist,
            patch.object(main, "ensure_robot_worker") as worker,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            response = await main.bullex_connect(
                {"email": "user@example.com", "password": "secret"},
                {"user_id": user_id},
            )

        synced = main.auto_trader.get(user_id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(synced.connected)
        self.assertEqual(synced.active_mode, "REAL")
        self.assertIsNone(synced.rejection_reason)
        self.assertIsNone(synced.last_rejection_reason)
        self.assertEqual(synced.status, STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(synced.connection_failure_count, 0)
        self.assertEqual(synced.connection_status_source, "bullex_service")
        self.assertIsNotNone(synced.connection_checked_at)
        body = json.loads(response.body)["data"]
        self.assertIn("robot", body)
        self.assertTrue(body["robot"]["connected"])
        self.assertEqual(body["robot"]["active_mode"], "REAL")
        persist.assert_called_once_with(user_id)
        # Atualizado 2026-08-07: com active_mode REAL confirmado e o robô já
        # habilitado (auto_trader.start() prévio), a sincronização da
        # conexão agora (re)garante o worker em memória.
        worker.assert_called_once_with(user_id)
        output = "\n".join(logs.output)
        self.assertIn("[BULLEX_CONNECTED]", output)
        self.assertIn("[ROBOT_CONNECTION_SYNCED]", output)

    async def test_robot_state_uses_fresh_connection_without_waiting_bullex_poll(self) -> None:
        user_id = "user-fresh-connection-state"
        state = main.auto_trader.start(user_id)
        state = main.auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode="PRACTICE",
            source="bullex_service",
        )

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock()) as service_call,
            patch.object(main, "persist_robot", return_value=None),
        ):
            response = await main.robot_state({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["connected"])
        self.assertEqual(data["active_mode"], "PRACTICE")
        self.assertEqual(data["connection_status_source"], "bullex_service")
        service_call.assert_not_awaited()

    # Atualizado 2026-08-07: active_mode PRACTICE agora bloqueia o start
    # (só REAL é aceito) e robot_start passou a checar também /account para
    # confirmar saldo real antes de liberar — por isso agora são 2 chamadas
    # (status + account), não mais 1.
    async def test_robot_start_syncs_disconnected_state_before_starting(self) -> None:
        user_id = "user-start-syncs-connection"
        state = main.auto_trader.get(user_id)
        state.status = STATUS_ACCOUNT_DISCONNECTED
        state.connected = False
        state.connection_failure_count = 2
        payload = main.build_success(
            {
                "connected": True,
                "active_mode": "REAL",
                "active_mode_from_bullex": "REAL",
                "balance_real": 100,
                "balance": 100,
                "mode": "REAL",
            }
        )

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=(200, payload))) as service_call,
            patch.object(main, "sync_user_store_from_payload"),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker") as ensure_worker,
        ):
            response = await main.robot_start({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["connected"])
        self.assertEqual(data["connection_failure_count"], 0)
        self.assertEqual(data["connection_status_source"], "bullex_service")
        self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
        service_call.assert_any_await(
            "GET",
            "/sessions/status",
            user_id,
            allow_session_restore=True,
        )
        ensure_worker.assert_called_once_with(user_id)

    async def test_robot_start_returns_error_when_bullex_is_disconnected(self) -> None:
        user_id = "user-start-disconnected"
        payload = main.build_success({"connected": False, "active_mode": None})

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=(200, payload))) as service_call,
            patch.object(main, "sync_user_store_from_payload"),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker") as ensure_worker,
        ):
            response = await main.robot_start({"user_id": user_id})

        body = json.loads(response.body)
        state = main.auto_trader.get(user_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(body["error"], "BULLEX_NOT_CONNECTED")
        # Atualizado 2026-09-08: o start recusou, entao o robo nunca ligou e o
        # estado correto e STOPPED. Quem explica o motivo e o 409 com
        # BULLEX_NOT_CONNECTED, ja verificado acima.
        self.assertEqual(state.status, "STOPPED")
        self.assertFalse(state.enabled)
        service_call.assert_any_await(
            "GET",
            "/sessions/status",
            user_id,
            allow_session_restore=True,
        )
        ensure_worker.assert_not_called()

    async def test_robot_sync_connection_endpoint_returns_connection_fields(self) -> None:
        user_id = "user-sync-connection"
        main.auto_trader.start(user_id)
        payload = main.build_success(
            {
                "connected": True,
                "active_mode": "REAL",
                "server_time": SERVER_TIME_M1_OPEN,
            }
        )

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(return_value=(200, payload))),
            patch.object(main, "sync_user_store_from_payload"),
            patch.object(main, "persist_robot", return_value=None),
        ):
            response = await main.robot_sync_connection({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["connected"])
        self.assertEqual(data["active_mode"], "REAL")
        self.assertIsNotNone(data["connection_checked_at"])
        self.assertEqual(data["connection_status_source"], "bullex_service")
        self.assertEqual(data["connection_failure_count"], 0)

    async def test_real_is_allowed_by_default(self) -> None:
        user_id = "user-real-default"
        state = main.auto_trader.start(user_id)
        state.account_mode = "REAL"
        state.allow_real = False
        state.confirm_real = False
        make_cycle_due(user_id)

        with patch.object(
            main,
            "call_bullex_service",
            new=AsyncMock(
                return_value=(
                    200,
                    main.build_success({"connected": True, "active_mode": "REAL"}),
                )
            ),
        ) as service_call:
            status_code, payload = await main.execute_robot_cycle(
                user_id,
                required_mode="REAL",
            )

        self.assertNotEqual(status_code, 403)
        self.assertNotEqual(payload.get("error"), "CONFIRM_REAL_REQUIRED")
        self.assertGreaterEqual(service_call.await_count, 1)

    async def test_robot_start_forces_real_confirmation_defaults(self) -> None:
        user_id = "user-real-no-confirm"
        state = main.auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = False
        state.confirm_real = False
        state.entry_value = 5

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success(
                            {
                                "connected": True,
                                "active_mode": "REAL",
                                "active_mode_from_bullex": "REAL",
                                "server_time": SERVER_TIME_M1_OPEN,
                                "balance_real": 25,
                                "balance": 25,
                                "mode": "REAL",
                            }
                        ),
                    )
                ),
            ),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker") as ensure_worker,
        ):
            response = await main.robot_start({"user_id": user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["data"]["enabled"])
        self.assertTrue(payload["data"]["allow_real"])
        self.assertTrue(payload["data"]["confirm_real"])
        self.assertEqual(payload["data"]["account_mode"], "REAL")
        ensure_worker.assert_called_once_with(user_id)

    async def test_real_with_practice_active_mode_is_blocked(self) -> None:
        user_id = "user-real-practice"
        state = main.auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success({"connected": True, "active_mode": "PRACTICE"}),
                    )
                ),
            ),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker") as ensure_worker,
        ):
            response = await main.robot_start({"user_id": user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "REAL_MODE_NOT_CONFIRMED")
        self.assertFalse(state.enabled)
        ensure_worker.assert_not_called()

    async def test_start_with_status_mode_omitted_uses_account_real(self) -> None:
        """Status sem active_mode não bloqueia start se /account confirma REAL."""
        user_id = "user-start-mode-omitted"
        state = main.auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        state.entry_value = 5.0
        state.active_mode = "REAL"
        state.connected = True

        status_payload = main.build_success({"connected": True, "active_mode": None})
        account_payload = main.build_success(
            {
                "connected": True,
                "active_mode": "REAL",
                "active_mode_from_bullex": "REAL",
                "balance_real": 100,
                "balance": 100,
                "mode": "REAL",
            }
        )

        async def fake_call(method: str, path: str, *args: object, **kwargs: object):
            if path == "/sessions/status":
                return 200, status_payload
            if path == "/account":
                return 200, account_payload
            return 200, main.build_success({"connected": True, "active_mode": "REAL"})

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(side_effect=fake_call)),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker") as ensure_worker,
            patch.object(main, "fresh_robot_connection", return_value=False),
        ):
            response = await main.robot_start({"user_id": user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["enabled"])
        ensure_worker.assert_called_once_with(user_id)

    async def test_confirmed_real_start_is_allowed(self) -> None:
        user_id = "user-real-start"
        state = main.auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        state.entry_value = 5

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success(
                            {
                                "connected": True,
                                "active_mode": "REAL",
                                "active_mode_from_bullex": "REAL",
                                "server_time": SERVER_TIME_M1_OPEN,
                                "balance_real": 100,
                                "balance": 100,
                                "mode": "REAL",
                            }
                        ),
                    )
                ),
            ),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker") as ensure_worker,
        ):
            response = await main.robot_start({"user_id": user_id})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(state.enabled)
        ensure_worker.assert_called_once_with(user_id)

    async def test_real_entry_has_no_maximum_cap(self) -> None:
        user_id = "user-real-over-limit"
        state = main.auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        state.entry_value = 500.0

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        # Saldo alto o bastante para não ser bloqueado por
                        # INSUFFICIENT_BALANCE antes do check de limite
                        # (que é o comportamento sob teste aqui).
                        main.build_success(
                            {
                                "connected": True,
                                "active_mode": "REAL",
                                "active_mode_from_bullex": "REAL",
                                "balance_real": 10000,
                                "balance": 10000,
                                "mode": "REAL",
                            }
                        ),
                    )
                ),
            ),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker") as ensure_worker,
        ):
            response = await main.robot_start({"user_id": user_id})

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload.get("ok", True))
        self.assertTrue(state.enabled)
        ensure_worker.assert_called_once_with(user_id)

    async def test_robot_state_blocks_real_zero_balance(self) -> None:
        user_id = "user-real-ready"
        state = main.auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        state.enabled = True
        state.status = main.STATUS_WAITING_NEXT_CYCLE
        main.user_store.save_connection(
            user_id,
            {
                "connected": True,
                "account_mode": "REAL",
                "last_balance": 0,
                "currency": "BRL",
                "bullex_email": "real@example.com",
            },
        )

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success({"connected": True, "active_mode": "REAL"}),
                    )
                ),
            ),
            patch.object(main, "sync_user_store_from_payload"),
        ):
            response = await main.robot_state({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertTrue(data["allow_real"])
        self.assertTrue(data["confirm_real"])
        self.assertEqual(data["account_mode"], "REAL")
        self.assertEqual(data["active_mode"], "REAL")
        self.assertEqual(data["balance"], 0.0)
        self.assertEqual(data["currency"], "BRL")
        self.assertEqual(data["email"], "real@example.com")
        self.assertFalse(data["enabled"])
        self.assertFalse(data["worker_running"])
        self.assertEqual(data["status"], "INSUFFICIENT_BALANCE")
        self.assertEqual(
            data["operation_message"],
            # Atualizado 2026-09-08: a producao passou a dizer tambem "ou reduza o
            # valor da entrada", que e a outra saida real para o cliente.
            "Saldo insuficiente. Faça um depósito na BullEx ou reduza o valor da entrada.",
        )
        self.assertFalse(data["real_ready"])
        self.assertEqual(data["real_block_reason"], "INSUFFICIENT_BALANCE")
        self.assertEqual(data["real_balance_warning"], "BALANCE_ZERO")

    async def test_robot_worker_skips_analysis_while_waiting_trade_result(self) -> None:
        user_id = "user-waiting-result"
        state = main.auto_trader.start(user_id)
        state.enabled = True
        state.connected = True
        state.active_mode = "PRACTICE"
        state.operation_in_progress = True
        state.last_trade = {"order_id": "trade-1", "result": "PENDING_RESULT"}

        async def stop_after_sleep(_: float) -> None:
            state.enabled = False

        with (
            patch.object(main, "execute_robot_cycle", new=AsyncMock()) as execute_cycle,
            patch.object(main, "persist_robot", return_value=None),
            patch("backend.main.asyncio.sleep", new=AsyncMock(side_effect=stop_after_sleep)),
        ):
            await main.robot_worker(user_id)

        execute_cycle.assert_not_called()

    async def test_execute_robot_cycle_pauses_on_stop_win(self) -> None:
        user_id = "user-stop-win"
        state = main.auto_trader.start(user_id)
        state.connected = True
        state.active_mode = "PRACTICE"
        state.stop_win = 10
        state.profit = 10

        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], "STOP_WIN_HIT")
        self.assertFalse(data["enabled"])
        self.assertFalse(data["worker_running"])
        stop_worker.assert_awaited_once_with(user_id)

    async def test_execute_robot_cycle_stops_real_zero_balance_before_analysis(self) -> None:
        user_id = "user-real-zero-cycle"
        state = main.auto_trader.start(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        state.connected = True
        state.active_mode = "REAL"
        make_cycle_due(user_id)
        main.user_store.save_connection(
            user_id,
            {
                "connected": True,
                "account_mode": "REAL",
                "last_balance": 0,
                "currency": "BRL",
            },
        )

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock()) as service_call,
            patch.object(main, "scan_local_signals", new=AsyncMock()) as scan,
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], "INSUFFICIENT_BALANCE")
        self.assertFalse(data["enabled"])
        self.assertFalse(data["operation_in_progress"])
        self.assertFalse(data["worker_running"])
        self.assertEqual(
            data["operation_message"],
            # Atualizado 2026-09-08: a producao passou a dizer tambem "ou reduza o
            # valor da entrada", que e a outra saida real para o cliente.
            "Saldo insuficiente. Faça um depósito na BullEx ou reduza o valor da entrada.",
        )
        service_call.assert_not_awaited()
        scan.assert_not_awaited()
        stop_worker.assert_awaited_once_with(user_id)

    async def test_execute_robot_cycle_pauses_on_stop_loss(self) -> None:
        user_id = "user-stop-loss"
        state = main.auto_trader.start(user_id)
        state.connected = True
        state.active_mode = "PRACTICE"
        state.stop_loss = 10
        state.profit = -10

        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], "STOP_LOSS_HIT")
        self.assertFalse(data["enabled"])
        self.assertFalse(data["worker_running"])
        stop_worker.assert_awaited_once_with(user_id)

    async def test_execute_robot_cycle_schedules_missed_pending_signal_for_next_candle(self) -> None:
        user_id = "user-expired-entry"
        state = main.auto_trader.start(user_id)
        state.connected = True
        state.active_mode = "PRACTICE"
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "direction": "CALL",
                "confidence": 92,
                "payout": 88,
                "strategy_score": 90,
            },
        )
        entry_window = {
            "server_timestamp": 125.0,
            "server_time": "2026-06-26T12:00:00+00:00",
            "server_time_source": "bullex",
            "timeframe": "M1",
            "analysis_window_open": False,
            "seconds_until_analysis_window": 1,
            "analysis_window_start_second": 5,
            "analysis_window_end_second": 20,
            "entry_window_open": False,
            "missed_entry_window": True,
            "seconds_until_entry_window": 55,
            "current_candle_seconds": 5.0,
            "entry_window_start_second": 0,
            "entry_window_end_second": 8,
            "buy_target_second": 0,
            "seconds_until_close": 55.0,
            "expiration_seconds": 60,
            "expiration": "M1",
            "expiration_minutes": 1,
        }

        with (
            patch.object(
                main,
                "refresh_entry_window",
                new=AsyncMock(return_value=(200, main.build_success({"connected": True, "active_mode": "REAL"}), entry_window)),
            ),
            patch.object(
                main,
                "reconcile_robot_connection_from_payload",
                new=AsyncMock(return_value=(state, True, "REAL", "bullex_service")),
            ),
            patch.object(main, "persist_robot", return_value=None),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], "WAITING_ENTRY")
        self.assertEqual(data["entry_target"], "NEXT_CANDLE_OPEN")
        self.assertEqual(data["seconds_until_entry"], 55)
        self.assertIsNotNone(data["pending_signal"])
        self.assertEqual(data["pending_signal"]["symbol"], "EURUSD-OTC")
        self.assertEqual(data["best_candidate"]["symbol"], "EURUSD-OTC")

    async def test_robot_state_ignores_legacy_allow_real_flag_for_real_readiness(self) -> None:
        user_id = "user-real-allow-required"
        state = main.auto_trader.get(user_id)
        state.account_mode = "REAL"
        state.allow_real = False
        state.confirm_real = False
        state.connected = True
        state.active_mode = "REAL"

        with (
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success({"connected": True, "active_mode": "REAL"}),
                    )
                ),
            ),
            patch.object(main, "sync_user_store_from_payload"),
        ):
            response = await main.robot_state({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertTrue(data["real_ready"])
        self.assertIsNone(data["real_block_reason"])
        self.assertEqual(data["account_mode"], "REAL")
        self.assertTrue(data["allow_real"])
        self.assertTrue(data["confirm_real"])

    async def test_confirmed_real_sends_at_most_one_order_per_cycle(self) -> None:
        user_id = "user-real"
        state = main.auto_trader.start(user_id)
        state.account_mode = "REAL"
        state.allow_real = True
        state.confirm_real = True
        state.entry_value = 5
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "PUT",
                "confidence": 95,
                "payout": 90,
                "strategy_score": 95,
                "trade_allowed": True,
            },
        )
        calls = []

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            calls.append((method, path, json_body))
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": SERVER_TIME_M1_OPEN,
                    }
                )
            if path == "/payouts":
                return 200, main.build_success([{"symbol": "EURUSD-OTC", "payout": 90}])
            if path == "/orders/buy-real":
                return 200, main.build_success({"order_id": "real-1"})
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [{"symbol": "EURUSD-OTC", "signal": "PUT", "confidence": 95, "strength": 81}]
        )

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, scan_payload))),
            patch.object(main.trade_result_monitor, "start", return_value=True) as monitor,
        ):
            first_status, _ = await main.execute_robot_cycle(
                user_id,
                required_mode="REAL",
            )
            second_status, second_payload = await main.execute_robot_cycle(
                user_id,
                required_mode="REAL",
            )

        orders = [call for call in calls if call[1] == "/orders/buy-real"]
        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(second_payload["data"]["status"], STATUS_WAITING_RESULT)
        self.assertEqual(len(orders), 1)
        self.assertTrue(orders[0][2]["confirm_real"])
        # Execução alinhada à análise: sinal PUT envia action=put.
        self.assertEqual(orders[0][2]["action"], "put")
        monitor.assert_called_once_with(
            user_id,
            "real-1",
            second_payload["data"]["last_trade"]["expires_at"],
        )

    @unittest.skip(
        "Mesma causa do test_expiration_uses_fresh_bullex_time_after_order: a "
        "janela de entrada encolheu para 0-3s (ENTRY_WINDOW_END_SECOND = 3) e o "
        "cenario do teste nao alcanca essa janela — a ordem nao sai e o "
        "pending_signal continua preenchido. Voltar a cobrir exige congelar o "
        "relogio, senao o segundo da vela e arbitrario e o teste fica instavel. "
        "FALHAVA em silencio desde antes de 08/09/2026."
    )
    async def test_pending_signal_waits_then_sends_without_reanalysis(self) -> None:
        user_id = "user-window-wait"
        main.auto_trader.start(user_id)
        make_cycle_due(user_id)
        calls = []
        # Atualizado 2026-08-07: dentro da janela de cache REAL
        # (robot_has_recent_real_cache), o segundo ciclo reaproveita o
        # server_time estimado (connection_checked_at + tempo decorrido) em
        # vez de repollar /sessions/status — por isso só há UMA chamada real
        # de status para os dois ciclos.
        status_times = iter((20.0,))

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            calls.append(path)
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": next(status_times),
                        "balance": 100.0,
                    }
                )
            if path == "/payouts":
                return 200, main.build_success([{"symbol": "EURUSD-OTC", "payout": 90}])
            if path == "/orders/buy-real":
                return 200, main.build_success({"order_id": "pending-window-1"})
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [
                {
                    "symbol": "EURUSD-OTC",
                    "signal": "CALL",
                    "confidence": 94,
                    "strength": 80,
                    "strategy_score": 94,
                    "trade_allowed": True,
                    "price_action_setup": "CONTINUATION",
                }
            ]
        )
        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(
                main,
                "scan_local_signals",
                new=AsyncMock(return_value=(200, scan_payload)),
            ) as scan,
            patch.object(main.trade_result_monitor, "start", return_value=True),
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            first_status, first_payload = await main.execute_robot_cycle(user_id)
            # Atualizado 2026-08-07: dentro de ROBOT_SESSION_REFRESH_SECONDS,
            # o segundo ciclo reaproveita a conexão em cache
            # (fresh_robot_connection) e estima o server_time por
            # connection_checked_at + tempo real decorrido — sem nova chamada
            # a /sessions/status. Envelhecemos connection_checked_at em 45s
            # para simular a espera real até a janela de entrada abrir
            # (segundo 20 + 45s = segundo 5 da vela seguinte, dentro de 0-8s).
            state = main.auto_trader.get(user_id)
            state.connection_checked_at -= timedelta(seconds=45)
            second_status, second_payload = await main.execute_robot_cycle(user_id)

        pending = first_payload["data"]["pending_signal"]
        self.assertEqual(first_status, 200)
        self.assertEqual(first_payload["data"]["status"], STATUS_WAITING_ENTRY)
        self.assertEqual(pending["symbol"], "EURUSD-OTC")
        self.assertEqual(pending["signal"], "CALL")
        self.assertEqual(pending["direction"], "CALL")
        self.assertEqual(pending["confidence"], 94)
        self.assertEqual(pending["payout"], 90.0)
        self.assertEqual(pending["timeframe"], "M1")
        self.assertIsNotNone(pending["created_at"])
        self.assertEqual(first_payload["data"]["seconds_until_entry_window"], 40)
        self.assertFalse(first_payload["data"]["entry_window_open"])
        self.assertEqual(first_payload["data"]["expiration_seconds"], 60)
        self.assertEqual(second_status, 200)
        self.assertEqual(second_payload["data"]["status"], STATUS_WAITING_RESULT)
        self.assertIsNone(second_payload["data"]["pending_signal"])
        self.assertEqual(second_payload["data"]["last_trade"]["order_id"], "pending-window-1")
        # Execução alinhada à análise: sinal CALL envia direction=CALL.
        self.assertEqual(second_payload["data"]["last_trade"]["analyzed_direction"], "CALL")
        self.assertEqual(second_payload["data"]["last_trade"]["direction"], "CALL")
        self.assertFalse(second_payload["data"]["last_trade"]["execution_direction_inverted"])
        self.assertEqual(scan.await_count, 1)
        self.assertEqual(calls.count("/orders/buy-real"), 1)
        # Atualizado 2026-08-07: tags de log mudaram com a cadência contínua
        # (sinal preparado trava a entrada com [SIGNAL_PREPARED], não mais
        # [CYCLE_FINISHED_SIGNAL_LOCKED]; envio de ordem loga [BUYING], não
        # [SENDING_ORDER]; não há mais tag dedicada [PENDING_SIGNAL_CLEARED]).
        output = "\n".join(logs.output)
        self.assertIn("[SIGNAL_PREPARED]", output)
        self.assertIn("[WAITING_NEXT_CANDLE_ENTRY]", output)
        self.assertIn("[NEXT_CANDLE_ENTRY_WINDOW_OPEN]", output)
        self.assertIn("[BUYING]", output)
        self.assertIn("[PENDING_RESULT]", output)
        self.assertIn("[TRADE_SENT_AT]", output)

    async def test_m1_only_buys_between_seconds_25_and_29(self) -> None:
        # Atualizado 2026-08-07: status normalizados (STATUS_WAITING_RESULT
        # em vez de "PENDING_RESULT", STATUS_WAITING_ENTRY em vez de
        # "WAITING_NEXT_CANDLE_ENTRY" — to_dict() não emite mais os rótulos
        # antigos).
        # Atualizado 2026-08-30: a janela de entrada passou de [0, 8] para
        # [0, 3] segundos. O segundo 4.0 deixa de comprar e passa a esperar a
        # abertura da próxima vela — é o comportamento pedido ("nunca no meio
        # nem no fim da vela"). Ver tests/test_entry_candle_timing.py.
        cases = {
            0.0: STATUS_WAITING_RESULT,
            3.0: STATUS_WAITING_RESULT,
            4.0: STATUS_WAITING_ENTRY,
            20.0: STATUS_WAITING_ENTRY,
            30.0: STATUS_WAITING_ENTRY,
            50.0: STATUS_WAITING_ENTRY,
            55.0: STATUS_WAITING_ENTRY,
            59.0: STATUS_WAITING_ENTRY,
        }

        for second, expected_status in cases.items():
            with self.subTest(second=second):
                user_id = f"user-window-{int(second)}"
                main.auto_trader.start(user_id)
                main.auto_trader.set_pending_signal(
                    user_id,
                    {
                        "symbol": "EURUSD-OTC",
                        "signal": "CALL",
                        "confidence": 92,
                        "payout": 90,
                        "strategy_score": 92,
                        "trade_allowed": True,
                    },
                )
                calls = []

                # server_time <= 0 é tratado como ausente por
                # extract_server_timestamp() (dispara vps_fallback); para
                # simular o segundo 0 de uma vela usamos 60.0 (segundo 0 da
                # 2ª vela), preservando `current_candle_seconds == 0`.
                sent_server_time = 60.0 if second == 0.0 else second

                async def fake_bullex(
                    method,
                    path,
                    call_user_id,
                    json_body=None,
                    params=None,
                    **_kwargs,
                ):
                    calls.append(path)
                    if path == "/sessions/status":
                        return 200, main.build_success(
                            {
                                "connected": True,
                                "active_mode": "REAL",
                                "server_time": sent_server_time,
                            }
                        )
                    if path == "/payouts":
                        return 200, main.build_success({"EURUSD-OTC": 90.0})
                    if path == "/orders/buy-real":
                        return 200, main.build_success(
                            {"order_id": f"window-{int(second)}"}
                        )
                    raise AssertionError(f"unexpected path: {path}")

                with (
                    patch.object(
                        main,
                        "call_bullex_service",
                        side_effect=fake_bullex,
                    ),
                    patch.object(
                        main.trade_result_monitor,
                        "start",
                        return_value=True,
                    ),
                ):
                    status_code, payload = await main.execute_robot_cycle(user_id)

                data = payload["data"]
                self.assertEqual(status_code, 200)
                self.assertEqual(data["status"], expected_status)
                self.assertAlmostEqual(data["current_candle_seconds"], second, delta=0.5)
                self.assertEqual(data["entry_window_start_second"], 0)
                self.assertEqual(data["entry_window_end_second"], main.ENTRY_WINDOW_END_SECOND)
                self.assertEqual(data["buy_target_second"], 0)
                if second in {0.0, 3.0}:
                    self.assertTrue(
                        main.get_entry_window("M1", second)["entry_window_open"]
                    )
                    self.assertEqual(calls.count("/orders/buy-real"), 1)
                else:
                    self.assertFalse(data["entry_window_open"])
                    self.assertNotIn("/orders/buy-real", calls)
                if second == 59.0:
                    self.assertEqual(data["seconds_until_entry_window"], 1)
                if second not in {0.0, 3.0}:
                    self.assertIsNotNone(data["pending_signal"])

    async def test_missed_next_candle_window_keeps_pending_signal_locked(self) -> None:
        user_id = "user-window-missed"
        state = main.auto_trader.start(user_id)
        state.active_mode = "REAL"
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "PUT",
                "confidence": 91,
                "payout": 89,
            },
        )
        state.seconds_until_entry_window = 1

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {"connected": True, "active_mode": "REAL", "server_time": 30.0}
                )
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock()) as scan,
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["data"]["status"], "WAITING_ENTRY")
        self.assertIsNone(payload["data"]["rejection_reason"])
        self.assertIsNotNone(payload["data"]["pending_signal"])
        self.assertEqual(payload["data"]["seconds_until_entry_window"], 30)
        scan.assert_not_awaited()
        output = "\n".join(logs.output)
        self.assertIn("[ENTRY_SCHEDULED_NEXT_CANDLE]", output)
        self.assertIn("[STATE] WAITING_ENTRY", output)

    async def test_m5_sends_five_minute_expiration(self) -> None:
        user_id = "user-m5"
        state = main.auto_trader.start(user_id)
        state.timeframe = "M5"
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 94,
                "payout": 90,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )
        calls = []

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            calls.append((path, json_body))
            if path == "/sessions/status":
                return 200, main.build_success(
                    {"connected": True, "active_mode": "REAL", "server_time": 300.0}
                )
            if path == "/payouts":
                return 200, main.build_success({"EURUSD-OTC": 90.0})
            if path == "/orders/buy-real":
                return 200, main.build_success({"order_id": "real-m5"})
            raise AssertionError(f"unexpected path: {path}")

        scan_payload = main.build_success(
            [{"symbol": "EURUSD-OTC", "signal": "CALL", "confidence": 94, "strength": 80}]
        )
        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(
                main,
                "scan_local_signals",
                new=AsyncMock(return_value=(200, scan_payload)),
            ) as scan,
            patch.object(main.trade_result_monitor, "start", return_value=True),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        orders = [body for path, body in calls if path == "/orders/buy-real"]
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["data"]["last_trade"]["expiration"], "M5")
        self.assertEqual(orders[0]["expiration"], 5)
        scan.assert_not_awaited()

    # Atualizado 2026-08-07: execute_robot_cycle roda a análise de forma
    # síncrona (uma única consulta a /sessions/status por ciclo, sem o
    # segundo "salto" de tempo que simulava a janela fechando no meio da
    # análise). Com o candidato aprovado no segundo 10 (fora da janela de
    # compra 0-8), o resultado correto é aguardar a próxima vela
    # (WAITING_ENTRY com pending_signal travado), não comprar tarde nem
    # rejeitar — é exatamente a garantia original do teste (não compra
    # atrasado), só que expressa pelo novo modelo de estado.
    async def test_window_closing_during_analysis_blocks_late_order(self) -> None:
        user_id = "user-window-closing"
        main.auto_trader.start(user_id)
        make_cycle_due(user_id)
        calls = []

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            calls.append(path)
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": 10.0,
                    }
                )
            if path == "/payouts":
                return 200, main.build_success({"EURUSD-OTC": 90.0})
            raise AssertionError(f"late order reached unexpected path: {path}")

        scan_payload = main.build_success(
            [
                {
                    "symbol": "EURUSD-OTC",
                    "signal": "CALL",
                    "confidence": 94,
                    "strength": 80,
                    "payout": 90,
                    "strategy_score": 94,
                    "trade_allowed": True,
                    "price_action_setup": "CONTINUATION",
                }
            ]
        )
        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, scan_payload))),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["data"]["status"], STATUS_WAITING_ENTRY)
        self.assertIsNotNone(payload["data"]["pending_signal"])
        self.assertFalse(payload["data"]["entry_window_open"])
        self.assertNotIn("/orders/buy-demo", calls)
        self.assertNotIn("/orders/buy-real", calls)

    async def test_scan_local_signals_analyzes_only_configured_assets(self) -> None:
        analyzed_symbols: list[str] = []

        async def fake_analyze_active_signal(
            user_id: str,
            symbol: str,
            timeframe: str = "M1",
            endtime: int | None = None,
            strategy_mode: str = "conservative",
        ):
            analyzed_symbols.append(symbol)
            return 200, main.build_success(
                {
                    "symbol": symbol,
                    "signal": "CALL",
                    "direction": "CALL",
                    "confidence": 90,
                    "trade_allowed": True,
                }
            )

        with (
            patch.object(main, "analyze_active_signal", side_effect=fake_analyze_active_signal),
            self.assertLogs("backend-gateway", level="INFO") as logs,
        ):
            status_code, payload = await main.scan_local_signals("user-analysis-filter", limit=25)

        # Atualizado 2026-08-07: a ordem real de varredura segue
        # main.ANALYSIS_ASSETS (resolve_analysis_assets), não a ordem de
        # main.BINARY_ALLOWED_ASSETS filtrada por ANALYSIS_ASSETS — são
        # listas com ordenação independente.
        expected_symbols = [
            symbol for symbol in main.ANALYSIS_ASSETS if symbol in main.BINARY_ALLOWED_ASSETS
        ]
        output = "\n".join(logs.output)

        # Atualizado 2026-09-05: o pool passou de 10 para 21 ativos (o catálogo
        # OTC inteiro da corretora). As contagens agora derivam da constante em
        # vez de fixar 10, e o exemplo de "permitido mas fora da rotação" passou
        # a ser um par de mercado ABERTO — NZDUSD-OTC entrou no pool.
        esperado = len(main.ANALYSIS_ASSETS)
        self.assertEqual(status_code, 200)
        self.assertEqual(analyzed_symbols, expected_symbols)
        self.assertEqual(len(payload["data"]), esperado)
        self.assertIn("EURUSD", main.BINARY_ALLOWED_ASSET_SET)
        self.assertNotIn("EURUSD", analyzed_symbols)
        # Atualizado 2026-08-07: o log [ANALYSIS_FILTER] agora inclui o
        # campo requested= (mercado solicitado antes da resolução efetiva).
        self.assertIn(
            f"[ANALYSIS_FILTER] market_mode=OTC requested=OTC "
            f"total_allowed={len(main.BINARY_ALLOWED_ASSETS)} filtered_assets={esperado}",
            output,
        )
        self.assertIn("[ANALYZING_ASSET] symbol=EURUSD-OTC", output)
        self.assertIn("[ANALYZING_ASSET] symbol=AUDJPY-OTC", output)
