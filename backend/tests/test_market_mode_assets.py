"""Testes do modo de mercado (OTC / aberto / ambos) na lista de análise."""

from __future__ import annotations

import unittest
import unittest.mock
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

    def test_both_scans_otc_and_open_when_forex_is_open(self) -> None:
        """Ambos com sessão forex aberta inclui pares OTC e sem -OTC."""
        open_moment = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(main.effective_market_mode("BOTH", now=open_moment), "BOTH")
        assets = main.resolve_analysis_assets("BOTH", now=open_moment)
        # Atualizado 08/09/2026: o modo OPEN varre apenas os pares que a
        # corretora vende como OPCAO no mercado aberto (11 dos 21). Os
        # outros 10 existem la so como OTC sintetico.
        self.assertEqual(
            len(assets),
            len(main.ANALYSIS_ASSETS_OTC) + len(main.ANALYSIS_ASSETS_OPEN),
        )
        self.assertIn("EURUSD-OTC", assets)
        self.assertIn("EURUSD", assets)

    def test_both_falls_back_to_otc_when_forex_closed(self) -> None:
        closed = datetime(2026, 7, 25, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(main.effective_market_mode("BOTH", now=closed), "OTC")
        assets = main.resolve_analysis_assets("BOTH", now=closed)
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
        self.assertEqual(len(assets), len(main.ANALYSIS_ASSETS_OTC))
        self.assertTrue(all(symbol.endswith("-OTC") for symbol in assets))
        self.assertIn("EURUSD-OTC", assets)
        self.assertNotIn("EURUSD", assets)

    def test_resolve_analysis_assets_open_when_session_open(self) -> None:
        open_moment = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(main.effective_market_mode("OPEN", now=open_moment), "OPEN")
        assets = main.resolve_analysis_assets("OPEN", now=open_moment)
        # Atualizado 08/09/2026: o modo OPEN varre apenas os pares que a
        # corretora vende como OPCAO no mercado aberto (11 dos 21). Os
        # outros 10 existem la so como OTC sintetico.
        self.assertEqual(len(assets), len(main.ANALYSIS_ASSETS_OPEN))
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
        # Atualizado 08/09/2026: o modo OPEN varre apenas os pares que a
        # corretora vende como OPCAO no mercado aberto (11 dos 21). Os
        # outros 10 existem la so como OTC sintetico.
        self.assertEqual(len(open_assets), len(main.ANALYSIS_ASSETS_OPEN))
        self.assertTrue(all(not s.endswith("-OTC") for s in open_assets))

        both_assets = main.select_analysis_assets_for_cycle(
            "user-both",
            max_assets=None,
            market_mode="BOTH",
        )
        # Sem `now` injetado: se o teste rodar no fim de semana cai em OTC;
        # o contrato de BOTH+aberto está em test_both_scans_otc_and_open.
        otc = len(main.ANALYSIS_ASSETS_OTC)
        abertos = len(main.ANALYSIS_ASSETS_OPEN)
        self.assertTrue(len(both_assets) in {otc, otc + abertos})

    def test_open_nao_cai_para_otc_com_canal_fechado(self) -> None:
        """M1 com turbo fechado no aberto: fica sem operar, não cai para OTC."""
        main.analysis_asset_queue_offsets.clear()
        with (
            unittest.mock.patch("backend.main.is_forex_open_market_open", return_value=True),
            unittest.mock.patch("backend.main.cached_asset_open_for_active", return_value=False),
        ):
            assets = main.select_analysis_assets_for_cycle(
                "user-open-fallback",
                max_assets=None,
                market_mode="OPEN",
                timeframe="M1",
            )
        # DECISAO REVISTA EM 08/09/2026 — o dono foi explicito: "se esta
        # configurado para mercado aberto tem que ser operacoes no mercado
        # aberto". O fallback existia para "nao deixar o robo parado", mas o
        # custo era pior: conta em OPEN comprava OTC com dinheiro real, num
        # mercado que a pessoa nao escolheu, e o painel seguia dizendo
        # "mercado aberto". Aconteceu de verdade nesse dia (USDJPY-OTC, R$20).
        # Ciclo sem oportunidade no aberto agora e ciclo sem operacao.
        self.assertFalse(
            [a for a in assets if a.endswith("-OTC")],
            "conta em OPEN recebeu ativo OTC",
        )

    def test_open_keeps_open_assets_when_payout_cache_unknown_on_first_cycles(self) -> None:
        """Cache vazio no primeiro ciclo ainda tenta o mercado aberto."""
        user_id = "user-open-payout-first"
        main.analysis_asset_queue_offsets.clear()
        state = main.auto_trader.get(user_id)
        state.consecutive_no_opportunity_cycles = 1
        state.blocked_filters = ["PAYOUT_UNAVAILABLE"]
        with (
            unittest.mock.patch("backend.main.is_forex_open_market_open", return_value=True),
            unittest.mock.patch("backend.main.cached_asset_open_for_active", return_value=None),
        ):
            assets = main.select_analysis_assets_for_cycle(
                user_id,
                max_assets=None,
                market_mode="OPEN",
                timeframe="M1",
            )
        self.assertTrue(assets)
        self.assertTrue(all(not symbol.endswith("-OTC") for symbol in assets))

    def test_open_nao_cai_para_otc_com_payout_indisponivel(self) -> None:
        """Timeout de payout no aberto: fica sem operar, não troca de mercado."""
        user_id = "user-open-payout-streak"
        main.analysis_asset_queue_offsets.clear()
        state = main.auto_trader.get(user_id)
        state.consecutive_no_opportunity_cycles = 3
        state.blocked_filters = ["PAYOUT_UNAVAILABLE", "TREND_CLEAR"]
        with (
            unittest.mock.patch("backend.main.is_forex_open_market_open", return_value=True),
            unittest.mock.patch("backend.main.cached_asset_open_for_active", return_value=None),
        ):
            assets = main.select_analysis_assets_for_cycle(
                user_id,
                max_assets=None,
                market_mode="OPEN",
                timeframe="M1",
            )
        # Mesma decisão revista de 08/09/2026: OPEN não cai para OTC. Ver a
        # explicação em test_open_falls_back_to_otc_when_execution_channel_closed.
        self.assertFalse(
            [a for a in assets if a.endswith("-OTC")],
            "conta em OPEN recebeu ativo OTC",
        )

    def test_open_nao_cai_para_otc_mesmo_com_canal_marcado_aberto(self) -> None:
        """Canal aberto sem payout é inoperável — e ainda assim não vira OTC."""
        user_id = "user-open-payout-open-flag"
        main.analysis_asset_queue_offsets.clear()
        state = main.auto_trader.get(user_id)
        state.consecutive_no_opportunity_cycles = 5
        state.blocked_filters = ["PAYOUT_UNAVAILABLE"]
        with (
            unittest.mock.patch("backend.main.is_forex_open_market_open", return_value=True),
            unittest.mock.patch("backend.main.cached_asset_open_for_active", return_value=True),
        ):
            assets = main.select_analysis_assets_for_cycle(
                user_id,
                max_assets=None,
                market_mode="OPEN",
                timeframe="M1",
            )
        # Mesma decisão revista de 08/09/2026: OPEN não cai para OTC. Ver a
        # explicação em test_open_falls_back_to_otc_when_execution_channel_closed.
        self.assertFalse(
            [a for a in assets if a.endswith("-OTC")],
            "conta em OPEN recebeu ativo OTC",
        )

    def test_open_does_not_fallback_when_quality_filters_block_with_payout(self) -> None:
        """Sem payout ausente, rejeição de estratégia não troca o mercado."""
        user_id = "user-open-quality-only"
        main.analysis_asset_queue_offsets.clear()
        state = main.auto_trader.get(user_id)
        state.consecutive_no_opportunity_cycles = 20
        state.blocked_filters = ["TREND_CLEAR", "SR_ZONE"]
        with (
            unittest.mock.patch("backend.main.is_forex_open_market_open", return_value=True),
            unittest.mock.patch("backend.main.cached_asset_open_for_active", return_value=True),
        ):
            assets = main.select_analysis_assets_for_cycle(
                user_id,
                max_assets=None,
                market_mode="OPEN",
                timeframe="M1",
            )
        self.assertTrue(assets)
        self.assertTrue(all(not symbol.endswith("-OTC") for symbol in assets))


if __name__ == "__main__":
    unittest.main()
