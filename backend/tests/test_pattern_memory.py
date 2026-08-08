"""Memória de padrões: bloqueia contextos com histórico fraco e aprende com resultados.

A camada complementa a estratégia clássica — não a substitui. Com amostra
insuficiente o portão NÃO bloqueia (fail-open). Gale não entra na memória.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend import main
from backend.auto_trader import AutoTrader
from backend.pattern_memory import (
    GLOBAL_PATTERN_OWNER,
    PATTERN_MEMORY_BLOCK,
    InMemoryPatternStore,
    PatternMemoryService,
    PatternStats,
    build_pattern_key,
    extract_setup,
    unify_pattern_rows,
)


def _trade(
    *,
    active: str = "USDCAD-OTC",
    direction: str = "CALL",
    setup: str = "CONTINUATION",
    result: str = "WIN",
    profit: float = 85.0,
    hour: int | None = None,
    timeframe: str = "M1",
    is_gale: bool = False,
    order_id: str = "ord-1",
) -> dict:
    # Default: mesma hora UTC de agora, para bater com evaluate(candidate) sem `now`.
    resolved_hour = datetime.now(timezone.utc).hour if hour is None else hour
    opened = datetime(2026, 7, 30, resolved_hour, 10, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 7, 30, resolved_hour, 11, 0, tzinfo=timezone.utc)
    return {
        "order_id": order_id,
        "active": active,
        "direction": direction,
        "result": result,
        "final_result": result,
        "profit": profit,
        "opened_at": opened.isoformat(),
        "sent_at": opened.isoformat(),
        "finished_at": finished.isoformat(),
        "timeframe": timeframe,
        "is_gale": is_gale,
        "strategy_setup": setup,
        "price_action_setup": setup,
        "confidence": 90,
        "payout": 85,
    }


def build_candidate(**overrides):
    candidate = {
        "symbol": "USDCAD-OTC",
        "active": "USDCAD-OTC",
        "signal": "CALL",
        "direction": "CALL",
        "confidence": 92,
        "payout": 85.0,
        "strategy_score": 90,
        "trade_allowed": True,
        "blocked_filters": [],
        "strategy_setup": "CONTINUATION",
        "price_action_setup": "CONTINUATION",
        "timeframe": "M1",
    }
    candidate.update(overrides)
    return candidate


class PatternKeyTests(unittest.TestCase):
    def test_build_pattern_key_is_stable(self) -> None:
        key = build_pattern_key(
            active="eurusd-otc",
            hour_utc=3,
            setup="continuation",
            direction="call",
            timeframe="m1",
        )
        self.assertEqual(key, "EURUSD-OTC|03|CONTINUATION|CALL|M1")

    def test_extract_setup_prefers_strategy_setup(self) -> None:
        self.assertEqual(
            extract_setup({"strategy_setup": "REVERSAL", "price_action_setup": "CONTINUATION"}),
            "REVERSAL",
        )

    def test_extract_setup_falls_back_to_price_action(self) -> None:
        self.assertEqual(extract_setup({"price_action_setup": "SUPPORT_RESISTANCE"}), "SUPPORT_RESISTANCE")

    def test_extract_setup_from_metrics(self) -> None:
        self.assertEqual(
            extract_setup({"metrics": {"price_action_setup": "REVERSAL"}}),
            "REVERSAL",
        )

    def test_extract_setup_from_nested_analysis_json(self) -> None:
        self.assertEqual(
            extract_setup({"analysis_json": {"metrics": {"price_action_setup": "CONTINUATION"}}}),
            "CONTINUATION",
        )

class PatternMemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.memory = PatternMemoryService(
            enabled=True,
            min_samples_to_block=5,
            max_weak_win_rate=52.0,
        )
        self.user_id = "user-pattern-memory"

    def test_insufficient_sample_does_not_block(self) -> None:
        for i in range(4):
            self.memory.record_outcome(
                self.user_id,
                _trade(result="LOSS", profit=-100, order_id=f"l-{i}"),
            )
        decision = self.memory.evaluate(self.user_id, build_candidate())
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "INSUFFICIENT_SAMPLE")

    def test_weak_pattern_is_blocked(self) -> None:
        # 1 WIN + 5 LOSS = 16.7% < 52% com 6 amostras
        self.memory.record_outcome(
            self.user_id,
            _trade(result="WIN", profit=85, order_id="w-1"),
        )
        for i in range(5):
            self.memory.record_outcome(
                self.user_id,
                _trade(result="LOSS", profit=-100, order_id=f"l-{i}"),
            )
        decision = self.memory.evaluate(self.user_id, build_candidate())
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, PATTERN_MEMORY_BLOCK)
        self.assertGreaterEqual(decision.samples, 5)
        self.assertLess(decision.win_rate, 52.0)

    def test_strong_pattern_is_allowed(self) -> None:
        for i in range(8):
            self.memory.record_outcome(
                self.user_id,
                _trade(result="WIN", profit=85, order_id=f"w-{i}"),
            )
        for i in range(2):
            self.memory.record_outcome(
                self.user_id,
                _trade(result="LOSS", profit=-100, order_id=f"l-{i}"),
            )
        decision = self.memory.evaluate(self.user_id, build_candidate())
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "PATTERN_OK")
        self.assertGreaterEqual(decision.win_rate, 52.0)

    def test_gale_trades_are_ignored(self) -> None:
        for i in range(10):
            self.memory.record_outcome(
                self.user_id,
                _trade(result="LOSS", profit=-200, is_gale=True, order_id=f"g-{i}"),
            )
        decision = self.memory.evaluate(self.user_id, build_candidate())
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.samples, 0)

    def test_disabled_service_never_blocks(self) -> None:
        disabled = PatternMemoryService(enabled=False, min_samples_to_block=1, max_weak_win_rate=99.0)
        for i in range(10):
            disabled.record_outcome(
                self.user_id,
                _trade(result="LOSS", profit=-100, order_id=f"l-{i}"),
            )
        self.assertTrue(disabled.evaluate(self.user_id, build_candidate()).allowed)

    def test_rebuild_from_history(self) -> None:
        history = [
            _trade(result="LOSS", profit=-100, order_id=f"h-{i}")
            for i in range(6)
        ]
        self.memory.rebuild_from_history(self.user_id, history)
        decision = self.memory.evaluate(self.user_id, build_candidate())
        self.assertFalse(decision.allowed)

    def test_tenant_isolation_when_global_disabled(self) -> None:
        """Com caderno por conta, perdas de A não afetam B."""
        personal = PatternMemoryService(
            enabled=True,
            use_global=False,
            min_samples_to_block=5,
            max_weak_win_rate=52.0,
        )
        for i in range(6):
            personal.record_outcome(
                "user-a",
                _trade(result="LOSS", profit=-100, order_id=f"a-{i}"),
            )
        for i in range(6):
            personal.record_outcome(
                "user-b",
                _trade(result="WIN", profit=85, order_id=f"b-{i}"),
            )
        self.assertFalse(personal.evaluate("user-a", build_candidate()).allowed)
        self.assertTrue(personal.evaluate("user-b", build_candidate()).allowed)

    def test_different_hour_is_separate_bucket(self) -> None:
        other_hour = (datetime.now(timezone.utc).hour + 5) % 24
        for i in range(6):
            self.memory.record_outcome(
                self.user_id,
                _trade(result="LOSS", profit=-100, hour=other_hour, order_id=f"h3-{i}"),
            )
        # Candidato na hora atual — sem amostra nesse bucket
        decision = self.memory.evaluate(self.user_id, build_candidate())
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.samples, 0)


class GlobalPatternMemoryTests(unittest.TestCase):
    """Caderno único do sistema: todas as contas alimentam e consultam o mesmo bucket."""

    def setUp(self) -> None:
        self.store = InMemoryPatternStore()
        self.memory = PatternMemoryService(
            enabled=True,
            use_global=True,
            min_samples_to_block=5,
            max_weak_win_rate=52.0,
        )
        self.memory.attach_store(self.store)

    def test_unify_pattern_rows_sums_same_key(self) -> None:
        rows = [
            {
                "user_id": "a",
                "pattern_key": "USDCAD-OTC|10|CONTINUATION|CALL|M1",
                "wins": 2,
                "losses": 1,
                "profit": 70.0,
                "active": "USDCAD-OTC",
                "direction": "CALL",
                "setup": "CONTINUATION",
                "timeframe": "M1",
                "hour_utc": 10,
            },
            {
                "user_id": "b",
                "pattern_key": "USDCAD-OTC|10|CONTINUATION|CALL|M1",
                "wins": 1,
                "losses": 3,
                "profit": -215.0,
                "active": "USDCAD-OTC",
                "direction": "CALL",
                "setup": "CONTINUATION",
                "timeframe": "M1",
                "hour_utc": 10,
            },
            {
                "user_id": "b",
                "pattern_key": "EURUSD-OTC|11|CONTINUATION|PUT|M1",
                "wins": 4,
                "losses": 0,
                "profit": 340.0,
                "active": "EURUSD-OTC",
                "direction": "PUT",
                "setup": "CONTINUATION",
                "timeframe": "M1",
                "hour_utc": 11,
            },
        ]
        merged = unify_pattern_rows(rows)
        key = "USDCAD-OTC|10|CONTINUATION|CALL|M1"
        self.assertEqual(merged[key].wins, 3)
        self.assertEqual(merged[key].losses, 4)
        self.assertEqual(merged[key].profit, -145.0)
        self.assertEqual(merged["EURUSD-OTC|11|CONTINUATION|PUT|M1"].wins, 4)

    def test_global_learning_is_shared_across_users(self) -> None:
        for i in range(3):
            self.memory.record_outcome(
                "user-a",
                _trade(result="LOSS", profit=-100, order_id=f"a-{i}"),
            )
        for i in range(3):
            self.memory.record_outcome(
                "user-b",
                _trade(result="LOSS", profit=-100, order_id=f"b-{i}"),
            )
        # 6 LOSS no mesmo padrão global → bloqueia para qualquer conta
        decision_a = self.memory.evaluate("user-a", build_candidate())
        decision_b = self.memory.evaluate("user-c", build_candidate())
        self.assertFalse(decision_a.allowed)
        self.assertFalse(decision_b.allowed)
        self.assertEqual(decision_a.samples, 6)
        self.assertEqual(decision_b.samples, 6)

    def test_unify_all_into_global_merges_personal_notebooks(self) -> None:
        # Simula cadernos pessoais já persistidos (sem passar por record_outcome global)
        personal = PatternMemoryService(
            enabled=True,
            use_global=False,
            min_samples_to_block=5,
            max_weak_win_rate=52.0,
        )
        personal.attach_store(self.store)
        for i in range(3):
            personal.record_outcome("user-a", _trade(result="LOSS", profit=-100, order_id=f"pa-{i}"))
        for i in range(3):
            personal.record_outcome("user-b", _trade(result="LOSS", profit=-100, order_id=f"pb-{i}"))

        merged_count = self.memory.unify_all_into_global()
        self.assertGreaterEqual(merged_count, 1)
        decision = self.memory.evaluate("user-new", build_candidate())
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.samples, 6)
        snap = self.memory.snapshot_global()
        self.assertGreaterEqual(len(snap), 1)
        self.assertEqual(snap[0]["samples"], 6)
        global_rows = self.store.load_global_patterns()
        self.assertEqual(len(global_rows), 1)
        self.assertEqual(int(global_rows[0]["wins"]) + int(global_rows[0]["losses"]), 6)

    def test_record_outcome_updates_global_and_personal_archive(self) -> None:
        self.memory.record_outcome(
            "user-x",
            _trade(result="WIN", profit=85, order_id="gx-1"),
        )
        global_rows = self.store.load_global_patterns()
        personal_rows = self.store.load_user_patterns("user-x")
        self.assertEqual(len(global_rows), 1)
        self.assertEqual(int(global_rows[0]["wins"]), 1)
        self.assertEqual(len(personal_rows), 1)
        self.assertEqual(int(personal_rows[0]["wins"]), 1)
        self.assertEqual(GLOBAL_PATTERN_OWNER, "__global__")


class PatternMemoryGateIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trader = AutoTrader()
        self.user_id = "user-pattern-gate"
        self.state = self.trader.start(self.user_id)
        self.state.min_confidence = 80
        self.state.min_payout = 80.0
        # Isola a memória global do módulo para o teste
        self._previous = main.pattern_memory
        main.pattern_memory = PatternMemoryService(
            enabled=True,
            use_global=True,
            min_samples_to_block=5,
            max_weak_win_rate=52.0,
        )

    def tearDown(self) -> None:
        main.pattern_memory = self._previous

    def test_threshold_blocks_weak_pattern(self) -> None:
        for i in range(6):
            main.pattern_memory.record_outcome(
                self.user_id,
                _trade(result="LOSS", profit=-100, order_id=f"l-{i}"),
            )
        candidate = build_candidate()
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                candidate,
                self.state,
                minimum_confidence=80,
                user_id=self.user_id,
            )
        )
        self.assertIn(PATTERN_MEMORY_BLOCK, candidate.get("blocked_filters") or [])

    def test_threshold_allows_without_user_id(self) -> None:
        """Sem user_id o portão clássico segue (compatibilidade / testes legados)."""
        for i in range(6):
            main.pattern_memory.record_outcome(
                self.user_id,
                _trade(result="LOSS", profit=-100, order_id=f"l-{i}"),
            )
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                build_candidate(),
                self.state,
                minimum_confidence=80,
            )
        )

    def test_resolve_entry_respects_pattern_memory(self) -> None:
        for i in range(6):
            main.pattern_memory.record_outcome(
                self.user_id,
                _trade(result="LOSS", profit=-100, order_id=f"l-{i}"),
            )
        approved = build_candidate()
        self.state.cycle_best_trade_candidate = approved
        self.state.cycle_best_candidate = approved
        self.assertIsNone(main.resolve_cycle_entry_candidate(self.state, user_id=self.user_id))

    def test_critical_blocks_include_pattern_memory(self) -> None:
        self.assertIn(PATTERN_MEMORY_BLOCK, main.CRITICAL_TRADE_BLOCKS)


if __name__ == "__main__":
    unittest.main()
