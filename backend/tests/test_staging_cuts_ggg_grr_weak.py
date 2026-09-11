"""Cortes staging 30/08: SEQ_GGG, SEQ_GRR e ban WEAK (sistema 02).

Autópsia EC02 (22–24/08): GGG WR 31,6%, GRR WR 33,3%, WEAK 47,2%.
Horas tóxicas S02 (02/03/17/19) permanecem iguais. Produção (01) não muda.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from unittest import mock

from backend import main, signal_engine
from backend.signal_engine import (
    _apply_quality_filters,
    is_seq_green_green_green,
    is_seq_green_red_red,
    is_weak_setup,
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


def _run_filters(signal: dict, *, frequency_recovery: bool = False) -> dict:
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
        )


class HelperUnitTests(unittest.TestCase):
    """Helpers isolados dos três cortes 30/08."""

    def test_seq_ggg_detects_three_greens(self) -> None:
        self.assertTrue(is_seq_green_green_green(["GREEN", "GREEN", "GREEN"]))
        self.assertTrue(is_seq_green_green_green(("green", "green", "green")))

    def test_seq_ggg_ignores_other_patterns(self) -> None:
        self.assertFalse(is_seq_green_green_green(["GREEN", "RED", "GREEN"]))
        self.assertFalse(is_seq_green_green_green(["RED", "GREEN", "GREEN"]))
        self.assertFalse(is_seq_green_green_green([]))
        self.assertFalse(is_seq_green_green_green(None))

    def test_seq_grr_detects_green_red_red(self) -> None:
        self.assertTrue(is_seq_green_red_red(["GREEN", "RED", "RED"]))
        self.assertTrue(is_seq_green_red_red(("green", "red", "red")))

    def test_seq_grr_ignores_other_patterns(self) -> None:
        self.assertFalse(is_seq_green_red_red(["GREEN", "GREEN", "GREEN"]))
        self.assertFalse(is_seq_green_red_red(["GREEN", "RED", "GREEN"]))
        self.assertFalse(is_seq_green_red_red(["RED", "RED", "RED"]))

    def test_weak_setup_helper(self) -> None:
        self.assertTrue(is_weak_setup("WEAK"))
        self.assertTrue(is_weak_setup("weak"))
        self.assertFalse(is_weak_setup("CONTINUATION"))
        self.assertFalse(is_weak_setup("REVERSAL"))
        self.assertFalse(is_weak_setup(None))


class FilterHardBlockTests(unittest.TestCase):
    """Filtros hard no pipeline de qualidade."""

    def setUp(self) -> None:
        # As flags sao decisao de IMPLANTACAO, nao de codigo: foram desligadas
        # de proposito em 03/09/2026 para recuperar volume. Assertar o valor
        # implantado deixava esta suite vermelha sem nada quebrado, e mascarava
        # falhas de verdade. O comportamento do corte continua coberto — a flag
        # e ligada aqui dentro. Sao DUAS flags homonimas: o motor filtra o sinal
        # e o portao do ciclo refaz a checagem, cada um lendo a sua.
        for modulo in (signal_engine, main):
            for nome in ("SEQ_GGG_HARD_BLOCK", "SEQ_GRR_HARD_BLOCK", "WEAK_SETUP_HARD_BLOCK"):
                if hasattr(modulo, nome):
                    patcher = patch.object(modulo, nome, True)
                    patcher.start()
                    self.addCleanup(patcher.stop)

    def test_ggg_blocks_even_on_weak_call(self) -> None:
        signal = _base_continuation_signal()
        signal["price_action_setup"] = "WEAK"
        signal["direction"] = "CALL"
        signal["last_3_colors"] = ["GREEN", "GREEN", "GREEN"]
        result = _run_filters(signal)
        self.assertIn("SEQ_GGG", result["blocked_filters"])
        self.assertFalse(result["trade_allowed"])

    def test_grr_blocks_continuation_put(self) -> None:
        signal = _base_continuation_signal()
        signal["price_action_setup"] = "CONTINUATION"
        signal["direction"] = "PUT"
        signal["last_3_colors"] = ["GREEN", "RED", "RED"]
        signal["last_3_direction"] = "DOWN"
        result = _run_filters(signal)
        self.assertIn("SEQ_GRR", result["blocked_filters"])
        self.assertFalse(result["trade_allowed"])

    def test_weak_setup_hard_block(self) -> None:
        signal = _base_continuation_signal()
        signal["price_action_setup"] = "WEAK"
        signal["direction"] = "CALL"
        signal["last_3_colors"] = ["RED", "GREEN", "GREEN"]
        result = _run_filters(signal)
        self.assertIn("WEAK_SETUP", result["blocked_filters"])
        self.assertFalse(result["trade_allowed"])

    def test_weak_not_relaxed_by_frequency_recovery(self) -> None:
        signal = _base_continuation_signal()
        signal["price_action_setup"] = "WEAK"
        signal["direction"] = "CALL"
        signal["last_3_colors"] = ["RED", "GREEN", "GREEN"]
        result = _run_filters(signal, frequency_recovery=True)
        self.assertIn("WEAK_SETUP", result["blocked_filters"])
        self.assertFalse(result["trade_allowed"])

    def test_good_continuation_rgg_still_allowed(self) -> None:
        signal = _base_continuation_signal()
        signal["price_action_setup"] = "CONTINUATION"
        signal["direction"] = "CALL"
        signal["last_3_colors"] = ["RED", "GREEN", "GREEN"]
        signal["last_3_direction"] = "UP"
        signal["trend"] = "UP"
        signal["strength"] = 20
        result = _run_filters(signal)
        self.assertNotIn("SEQ_GGG", result["blocked_filters"])
        self.assertNotIn("SEQ_GRR", result["blocked_filters"])
        self.assertNotIn("WEAK_SETUP", result["blocked_filters"])


class CriticalBlocksWiringTests(unittest.TestCase):
    """Garante wiring em CRITICAL / RECOVERY_NON_RELAXABLE e flags on."""

    def setUp(self) -> None:
        # As flags sao decisao de IMPLANTACAO, nao de codigo: foram desligadas
        # de proposito em 03/09/2026 para recuperar volume. Assertar o valor
        # implantado deixava esta suite vermelha sem nada quebrado, e mascarava
        # falhas de verdade. O comportamento do corte continua coberto — a flag
        # e ligada aqui dentro. Sao DUAS flags homonimas: o motor filtra o sinal
        # e o portao do ciclo refaz a checagem, cada um lendo a sua.
        for modulo in (signal_engine, main):
            for nome in ("SEQ_GGG_HARD_BLOCK", "SEQ_GRR_HARD_BLOCK", "WEAK_SETUP_HARD_BLOCK"):
                if hasattr(modulo, nome):
                    patcher = patch.object(modulo, nome, True)
                    patcher.start()
                    self.addCleanup(patcher.stop)

    def test_flags_existem_e_sao_booleanas(self) -> None:
        # O VALOR e decisao de implantacao (hoje desligadas). O que o teste
        # garante e que as flags existem e continuam sendo o interruptor do
        # corte — se alguma sumir, os testes de comportamento acima param de
        # significar o que dizem.
        for nome in ("SEQ_GGG_HARD_BLOCK", "SEQ_GRR_HARD_BLOCK", "WEAK_SETUP_HARD_BLOCK"):
            self.assertIsInstance(getattr(signal_engine, nome), bool)

    def test_critical_and_non_relaxable_include_new_blocks(self) -> None:
        for name in ("SEQ_GGG", "SEQ_GRR", "WEAK_SETUP"):
            self.assertIn(name, main.CRITICAL_TRADE_BLOCKS)
            self.assertIn(name, main.RECOVERY_NON_RELAXABLE_TRADE_BLOCKS)

    def test_candidate_threshold_rejects_ggg(self) -> None:
        cand = _candidate(
            direction="CALL",
            price_action_setup="CONTINUATION",
            trade_allowed=True,
        )
        cand["last_3_colors"] = ["GREEN", "GREEN", "GREEN"]
        cand["metrics"] = {"last_3_colors": ["GREEN", "GREEN", "GREEN"]}
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                cand,
                state=None,
                minimum_confidence=70,
            )
        )

    def test_candidate_threshold_rejects_weak(self) -> None:
        cand = _candidate(
            direction="CALL",
            price_action_setup="WEAK",
            trade_allowed=True,
        )
        cand["last_3_colors"] = ["RED", "GREEN", "GREEN"]
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                cand,
                state=None,
                minimum_confidence=70,
            )
        )


if __name__ == "__main__":
    unittest.main()
