"""Testes do modo de mercado (OTC / aberto / ambos) na lista de análise."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend import main


class MarketModeAssetsTests(unittest.TestCase):
    def test_normalize_market_mode(self) -> None:
        self.assertEqual(main.normalize_market_mode("otc"), "OTC")
        self.assertEqual(main.normalize_market_mode("OPEN"), "OPEN")
        self.assertEqual(main.normalize_market_mode("ambos"), "BOTH")
        self.assertEqual(main.normalize_market_mode("BOTH"), "BOTH")
        self.assertEqual(main.normalize_market_mode(None), "OTC")
        self.assertEqual(main.normalize_market_mode("invalid"), "OTC")

    def test_forex_open_market_hours(self) -> None:
        # Domingo 21:59 UTC — ainda fechado
        self.assertFalse(
            main.is_forex_open_market_open(datetime(2026, 7, 26, 21, 59, tzinfo=timezone.utc))
        )
        # Domingo 22:00 UTC — abre
        self.assertTrue(
            main.is_forex_open_market_open(datetime(2026, 7, 26, 22, 0, tzinfo=timezone.utc))
        )
        # Quarta — aberto
        self.assertTrue(
            main.is_forex_open_market_open(datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc))
        )
        # Sexta 22:00 UTC — fechado
        self.assertFalse(
            main.is_forex_open_market_open(datetime(2026, 7, 24, 22, 0, tzinfo=timezone.utc))
        )
        # Sábado — fechado
        saturday = datetime(2026, 7, 25, 15, 0, tzinfo=timezone.utc)
        self.assertFalse(main.is_forex_open_market_open(saturday))
        hours = main.hours_until_forex_open_market(saturday)
        self.assertIsNotNone(hours)
        self.assertGreater(hours or 0, 0)
        next_open = main.next_forex_open_market_at(saturday)
        self.assertIsNotNone(next_open)
        assert next_open is not None
        self.assertEqual(next_open.weekday(), 6)  # domingo
        self.assertEqual(next_open.hour, 22)

    def test_both_always_operates_otc_only(self) -> None:
        """Ambos permanece selecionável, mas a varredura/ordens usam só OTC."""
        open_moment = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(main.effective_market_mode("BOTH", now=open_moment), "OTC")
        assets = main.resolve_analysis_assets("BOTH")
        self.assertEqual(len(assets), 10)
        self.assertTrue(all(symbol.endswith("-OTC") for symbol in assets))
        self.assertNotIn("EURUSD", assets)

    def test_open_unavailable_when_forex_closed(self) -> None:
        closed = datetime(2026, 7, 25, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(main.coerce_selectable_market_mode("OPEN", now=closed), "OTC")
        self.assertEqual(main.effective_market_mode("OPEN", now=closed), "OTC")
        assets = main.resolve_analysis_assets("OPEN")
        # resolve usa now() real; forçamos via effective + lista OTC
        self.assertEqual(
            main.resolve_analysis_assets(main.effective_market_mode("OPEN", now=closed)),
            main.resolve_analysis_assets("OTC"),
        )

    def test_resolve_analysis_assets_otc(self) -> None:
        assets = main.resolve_analysis_assets("OTC")
        self.assertEqual(len(assets), 10)
        self.assertTrue(all(symbol.endswith("-OTC") for symbol in assets))
        self.assertIn("EURUSD-OTC", assets)
        self.assertNotIn("EURUSD", assets)

    def test_resolve_analysis_assets_open_when_session_open(self) -> None:
        open_moment = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(main.effective_market_mode("OPEN", now=open_moment), "OPEN")
        assets = main.resolve_analysis_assets("OPEN", now=open_moment)
        self.assertEqual(len(assets), 10)
        self.assertTrue(all(not symbol.endswith("-OTC") for symbol in assets))
        self.assertIn("EURUSD", assets)
        self.assertNotIn("EURUSD-OTC", assets)

    def test_open_and_otc_symbols_are_binary_allowed(self) -> None:
        for symbol in [
            *main.resolve_analysis_assets("OTC"),
            *main.ANALYSIS_ASSETS_OPEN,
        ]:
            self.assertTrue(
                main.is_binary_asset_allowed(symbol),
                msg=f"{symbol} deveria estar em BINARY_ALLOWED_ASSETS",
            )

    def test_select_analysis_assets_respects_market_mode(self) -> None:
        main.analysis_asset_queue_offsets.clear()
        open_moment = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        # Injeta now via resolve direto; select usa effective no momento do teste.
        open_assets = main.resolve_analysis_assets("OPEN", now=open_moment)
        self.assertEqual(len(open_assets), 10)
        self.assertTrue(all(not s.endswith("-OTC") for s in open_assets))

        both_assets = main.select_analysis_assets_for_cycle(
            "user-both",
            max_assets=None,
            market_mode="BOTH",
        )
        self.assertEqual(both_assets, main.resolve_analysis_assets("OTC"))


if __name__ == "__main__":
    unittest.main()
