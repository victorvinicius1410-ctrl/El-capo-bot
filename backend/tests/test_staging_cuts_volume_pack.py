"""Cortes staging volume: horas ruins + USDCHF + pares WEAK tóxicos.

Pacote: S02 (GRG + PUT <45%) já ativo + horas 02/03/17/19 BRT + ban USDCHF
+ EURGBP WEAK CALL + AUDUSD WEAK PUT.

Meta histórica: WR ~57%, conta ativa ~8–11 ops/dia. Só elcapo2 (sistema 02).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock
import unittest
from unittest.mock import patch

from backend import main, signal_engine
from backend.signal_engine import (
    _apply_quality_filters,
    is_banned_asset,
    is_toxic_hour_brt,
    is_toxic_weak_pair,
)
from tests.test_accuracy_ranking_substitution import _base_continuation_signal, _candidate
from tests.test_candle_analysis import make_candles


def _empty_levels() -> dict:
    return {
        "near_support": False,
        "near_resistance": False,
        "support": None,
        "resistance": None,
    }


def _run_filters(
    signal: dict,
    *,
    frequency_recovery: bool = False,
    now: datetime | None = None,
) -> dict:
    with (
        mock.patch(
            "backend.signal_engine._support_resistance_context",
            return_value=_empty_levels(),
        ),
        mock.patch(
            "backend.signal_engine._has_level_conflict",
            return_value=False,
        ),
        mock.patch(
            "backend.signal_engine._level_rejection_confirmed",
            return_value=False,
        ),
    ):
        return _apply_quality_filters(
            dict(signal),
            make_candles(40),
            "conservative",
            88.0,
            frequency_recovery=frequency_recovery,
            now=now,
        )


class HelperUnitTests(unittest.TestCase):
    """Helpers isolados do pacote volume."""

    def test_toxic_hours_brt(self) -> None:
        # 17:30 BRT = 20:30 UTC
        self.assertTrue(
            is_toxic_hour_brt(datetime(2026, 8, 20, 20, 30, tzinfo=timezone.utc))
        )
        # 02:00 BRT = 05:00 UTC
        self.assertTrue(
            is_toxic_hour_brt(datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc))
        )
        # 10:00 BRT = 13:00 UTC — hora boa
        self.assertFalse(
            is_toxic_hour_brt(datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc))
        )

    def test_banned_usdchf(self) -> None:
        self.assertTrue(is_banned_asset("USDCHF-OTC"))
        self.assertTrue(is_banned_asset("USDCHF"))
        self.assertTrue(is_banned_asset("usdchf-otc"))
        self.assertFalse(is_banned_asset("GBPUSD-OTC"))
        self.assertFalse(is_banned_asset("EURUSD"))

    def test_toxic_weak_pairs(self) -> None:
        self.assertTrue(is_toxic_weak_pair("WEAK", "CALL", "EURGBP-OTC"))
        self.assertTrue(is_toxic_weak_pair("WEAK", "CALL", "EURGBP"))
        self.assertTrue(is_toxic_weak_pair("WEAK", "PUT", "AUDUSD-OTC"))
        self.assertFalse(is_toxic_weak_pair("CONTINUATION", "CALL", "EURGBP-OTC"))
        self.assertFalse(is_toxic_weak_pair("WEAK", "PUT", "EURGBP-OTC"))
        self.assertFalse(is_toxic_weak_pair("WEAK", "CALL", "AUDUSD-OTC"))
        self.assertFalse(is_toxic_weak_pair("WEAK", "CALL", "GBPUSD-OTC"))


class ToxicHourBlockTests(unittest.TestCase):
    """Horas 02/03/17/19 BRT são hard block QUANDO a flag está ligada.

    A flag é decisão de implantação, não de código: foi desligada de propósito
    em 03/09/2026 para recuperar volume. Assertar o valor implantado deixava
    esta suíte vermelha sem que nada estivesse quebrado, e mascarava falhas de
    verdade. O que precisa continuar coberto é o COMPORTAMENTO do corte, então
    a flag é ligada aqui dentro.
    """

    def setUp(self) -> None:
        patcher = patch.object(signal_engine, "TOXIC_HOUR_HARD_BLOCK", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_flags_and_lists(self) -> None:
        self.assertEqual(signal_engine.TOXIC_HOURS_BRT, frozenset({2, 3, 17, 19}))
        self.assertIn("TOXIC_HOUR", main.CRITICAL_TRADE_BLOCKS)
        self.assertIn("TOXIC_HOUR", main.RECOVERY_NON_RELAXABLE_TRADE_BLOCKS)
        self.assertNotIn("TOXIC_HOUR", signal_engine.FREQUENCY_RECOVERY_SOFT_BLOCKS)

    def test_blocks_at_17h_brt(self) -> None:
        signal = _base_continuation_signal(
            body_ratio=0.85,
            near_support_resistance=True,
            last_3_colors=["RED", "GREEN", "GREEN"],
        )
        now = datetime(2026, 8, 20, 20, 15, tzinfo=timezone.utc)  # 17:15 BRT
        filtered = _run_filters(signal, now=now)
        self.assertIn("TOXIC_HOUR", filtered["blocked_filters"])
        self.assertFalse(filtered["trade_allowed"])

    def test_allows_at_10h_brt(self) -> None:
        signal = _base_continuation_signal(
            body_ratio=0.85,
            near_support_resistance=True,
            last_3_colors=["RED", "GREEN", "GREEN"],
        )
        now = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)  # 10:00 BRT
        filtered = _run_filters(signal, now=now)
        self.assertNotIn("TOXIC_HOUR", filtered["blocked_filters"])

    def test_recovery_does_not_relax(self) -> None:
        signal = _base_continuation_signal(
            price_action_setup="WEAK",
            body_ratio=0.85,
            near_support_resistance=True,
            last_3_colors=["RED", "GREEN", "GREEN"],
        )
        now = datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc)  # 02:00 BRT
        recovered = _run_filters(signal, frequency_recovery=True, now=now)
        hard = [
            name
            for name in recovered["blocked_filters"]
            if name in main.effective_critical_trade_blocks(frequency_recovery=True)
        ]
        self.assertIn("TOXIC_HOUR", hard)


class AssetBanBlockTests(unittest.TestCase):
    """USDCHF banido em qualquer setup/direção."""

    def setUp(self) -> None:
        # Mesma razão de ToxicHourBlockTests: a flag é decisão de implantação.
        # São DUAS flags homônimas — o motor filtra o sinal e o portão do ciclo
        # refaz a checagem por conta própria, cada um lendo a sua.
        for modulo in (signal_engine, main):
            patcher = patch.object(modulo, "ASSET_BAN_HARD_BLOCK", True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_flags_and_lists(self) -> None:
        self.assertIn("USDCHF-OTC", signal_engine.BANNED_ASSETS)
        self.assertIn("ASSET_BAN", main.CRITICAL_TRADE_BLOCKS)
        self.assertIn("ASSET_BAN", main.RECOVERY_NON_RELAXABLE_TRADE_BLOCKS)

    def test_usdchf_call_blocked(self) -> None:
        signal = _base_continuation_signal(
            symbol="USDCHF-OTC",
            body_ratio=0.85,
            near_support_resistance=True,
            last_3_colors=["RED", "GREEN", "GREEN"],
        )
        now = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
        filtered = _run_filters(signal, now=now)
        self.assertIn("ASSET_BAN", filtered["blocked_filters"])
        self.assertFalse(filtered["trade_allowed"])

    def test_gbpusd_still_allowed(self) -> None:
        signal = _base_continuation_signal(
            symbol="GBPUSD-OTC",
            body_ratio=0.85,
            near_support_resistance=True,
            last_3_colors=["RED", "GREEN", "GREEN"],
        )
        now = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
        filtered = _run_filters(signal, now=now)
        self.assertNotIn("ASSET_BAN", filtered["blocked_filters"])

    def test_cycle_gate_rejects_usdchf(self) -> None:
        state = mock.Mock(min_payout=80, timeframe="M1")
        candidate = _candidate(
            symbol="USDCHF-OTC",
            direction="CALL",
            body_ratio=0.85,
            trade_allowed=True,
            strategy_score=90,
            payout=88.0,
            blocked_filters=[],
        )
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                candidate, state, minimum_confidence=80, user_id=None
            )
        )


class ToxicWeakPairBlockTests(unittest.TestCase):
    """EURGBP WEAK CALL e AUDUSD WEAK PUT bloqueados."""

    def test_flags_and_lists(self) -> None:
        self.assertTrue(signal_engine.TOXIC_WEAK_PAIR_HARD_BLOCK)
        self.assertIn("TOXIC_WEAK_PAIR", main.CRITICAL_TRADE_BLOCKS)
        self.assertIn("TOXIC_WEAK_PAIR", main.RECOVERY_NON_RELAXABLE_TRADE_BLOCKS)

    def test_eurgbp_weak_call_blocked(self) -> None:
        signal = _base_continuation_signal(
            symbol="EURGBP-OTC",
            price_action_setup="WEAK",
            body_ratio=0.85,
            near_support_resistance=True,
            last_3_colors=["RED", "GREEN", "GREEN"],
        )
        now = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
        filtered = _run_filters(signal, frequency_recovery=True, now=now)
        self.assertIn("TOXIC_WEAK_PAIR", filtered["blocked_filters"])
        self.assertFalse(filtered["trade_allowed"])

    def test_audusd_weak_put_blocked(self) -> None:
        signal = _base_continuation_signal(
            symbol="AUDUSD-OTC",
            signal="PUT",
            direction="PUT",
            price_action_setup="WEAK",
            ema9=1.100,
            ema21=1.102,
            rsi=38.0,
            body_ratio=0.70,
            lower_wick_ratio=0.05,
            last_3_direction="DOWN",
            last_3_colors=["GREEN", "RED", "RED"],
            trend="DOWN",
            near_support_resistance=True,
        )
        now = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
        filtered = _run_filters(signal, frequency_recovery=True, now=now)
        self.assertIn("TOXIC_WEAK_PAIR", filtered["blocked_filters"])
        self.assertFalse(filtered["trade_allowed"])

    def test_eurgbp_continuation_call_still_allowed(self) -> None:
        signal = _base_continuation_signal(
            symbol="EURGBP-OTC",
            price_action_setup="CONTINUATION",
            body_ratio=0.85,
            near_support_resistance=True,
            last_3_colors=["RED", "GREEN", "GREEN"],
            last_3_direction="UP",
        )
        now = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
        filtered = _run_filters(signal, now=now)
        self.assertNotIn("TOXIC_WEAK_PAIR", filtered["blocked_filters"])

    def test_previous_s02_cuts_still_on(self) -> None:
        self.assertTrue(signal_engine.CALL_GRG_HARD_BLOCK)
        self.assertTrue(signal_engine.PUT_BODY_HARD_BLOCK)
        self.assertEqual(signal_engine.PUT_MIN_BODY_RATIO, 0.45)


if __name__ == "__main__":
    unittest.main()
