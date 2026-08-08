"""Cache compartilhado de candles/payouts entre usuários no bullex-service.

Candles e payouts de um mesmo ativo/intervalo são iguais para qualquer
usuário — antes desta mudança, cada usuário tinha seu próprio cache
(`_probe_cache` por `user_id`), então N usuários observando o mesmo par
geravam N chamadas upstream disputando o `_call_gate` (semáforo global,
`BULLEX_MAX_CONCURRENT_API_CALLS=1`). Ver PERFORMANCE_SISTEMA.md
("CALL_GATE_TIMEOUT sob carga").
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from bullex_service import main


class SharedMarketDataCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_session_manager = main.session_manager
        main.session_manager = main.SessionManager(None)

    def tearDown(self) -> None:
        main.session_manager = self.old_session_manager

    def test_candles_second_user_reuses_shared_cache_without_upstream_call(self) -> None:
        fake_candles = [{"from": 1, "to": 61, "open": 1.1, "close": 1.2, "min": 1.05, "max": 1.25}]

        with patch.object(main.session_manager, "run", return_value=fake_candles) as mocked_run:
            first = main.get_candles(active="EURUSD-OTC", interval=60, count=1, endtime=1_700_000_060, x_user_id="user-a")
            second = main.get_candles(active="EURUSD-OTC", interval=60, count=1, endtime=1_700_000_060, x_user_id="user-b")

        mocked_run.assert_called_once()
        self.assertTrue(first["ok"])
        self.assertEqual(first["data"], second["data"])

    def test_candles_different_assets_do_not_share_cache(self) -> None:
        with patch.object(main.session_manager, "run", return_value=[]) as mocked_run:
            main.get_candles(active="EURUSD-OTC", interval=60, count=1, endtime=1_700_000_060, x_user_id="user-a")
            main.get_candles(active="GBPUSD-OTC", interval=60, count=1, endtime=1_700_000_060, x_user_id="user-a")

        self.assertEqual(mocked_run.call_count, 2)

    def test_payouts_second_user_reuses_shared_cache_without_upstream_call(self) -> None:
        fake_payouts = [
            {
                "symbol": "EURUSD-OTC",
                "payout": 88,
                "type": "digital",
                "open_turbo": True,
                "open_binary": True,
                "is_open": True,
            }
        ]

        with patch.object(main.session_manager, "run", return_value=fake_payouts) as mocked_run:
            first = main.get_payouts(active="EURUSD-OTC", x_user_id="user-a")
            second = main.get_payouts(active="EURUSD-OTC", x_user_id="user-b")

        mocked_run.assert_called_once()
        self.assertTrue(first["ok"])
        self.assertEqual(first["data"], second["data"])

    def test_shared_cache_expires_after_ttl(self) -> None:
        payload = {"ok": True, "data": [{"symbol": "EURUSD-OTC", "payout": 88}]}
        main.session_manager.set_shared_market_cache("/payouts?active=EURUSD-OTC", 200, payload, ttl_seconds=60)

        hit = main.session_manager.get_shared_market_cache("/payouts?active=EURUSD-OTC")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], payload)

        # TTL expirado -> nao reaproveita mais (evita servir dado antigo demais).
        main.session_manager._market_data_cache["/payouts?active=EURUSD-OTC"].expires_at -= 120
        expired = main.session_manager.get_shared_market_cache("/payouts?active=EURUSD-OTC")
        self.assertIsNone(expired)

    def test_payouts_shared_cache_used_as_fallback_when_upstream_fails_after_race(self) -> None:
        # Simula corrida real: user-a preenche o cache compartilhado DEPOIS
        # que o gate de user-b já tinha começado e falhou — o fallback no
        # bloco `except` ainda resgata o dado fresco em vez de devolver erro.
        fake_payouts = [{"symbol": "EURUSD-OTC", "payout": 88, "type": "digital", "open_turbo": True, "open_binary": True, "is_open": True}]

        def run_side_effect(_user_id, _operation, **_kwargs):
            main.session_manager.set_shared_market_cache(
                "/payouts?active=EURUSD-OTC", 200, main.build_success(fake_payouts), ttl_seconds=60
            )
            raise main.ServiceError("SESSION_DISCONNECTED", 409)

        with patch.object(main.session_manager, "run", side_effect=run_side_effect):
            second = main.get_payouts(active="EURUSD-OTC", x_user_id="user-b")

        self.assertTrue(second["ok"])
        self.assertEqual(second["data"], fake_payouts)


if __name__ == "__main__":
    unittest.main()
