"""Contratos de edição e exclusão do histórico simulado de marketing."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.admin_repository import InMemoryAdminRepository
from backend.admin_router import create_admin_router
from backend.admin_service import AdminManagementService
from backend.supabase_admin_repository import (
    SupabaseAdminRepository,
    resolve_synthetic_sequence_from_trade,
)


class MarketingSimulationManagementApiTests(unittest.TestCase):
    """Valida autorização, isolamento e payloads das mutações simuladas."""

    def setUp(self) -> None:
        self.repository = InMemoryAdminRepository()
        service = AdminManagementService(self.repository)
        app = FastAPI()
        self.sync_calls: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = []
        self.payout_calls: list[tuple[str, str]] = []

        async def authenticated_user(request: Request) -> dict[str, str]:
            """Monta uma sessão de teste a partir de headers controlados."""
            return {
                "user_id": request.headers.get("x-test-user-id", "marketing-user"),
                "company_id": request.headers.get("x-test-company-id", "company-a"),
                "account_type": request.headers.get("x-test-account-type", "marketing"),
                "marketing_mode": request.headers.get("x-test-marketing-mode", "simulation"),
                "marketing_win_rate": "50",
                "is_admin": "false",
                "permissions": "",
                "manageable_role_ids": "",
            }

        def marketing_display_sync(
            user_id: str,
            history: list[dict[str, Any]],
            stats: dict[str, Any],
        ) -> None:
            self.sync_calls.append((user_id, list(history), dict(stats)))

        async def asset_payout_resolver(user_id: str, symbol: str) -> int | None:
            self.payout_calls.append((user_id, symbol))
            return 87

        app.include_router(
            create_admin_router(
                service,
                authenticated_user,
                authenticated_user,
                authenticated_user,
                authenticated_user,
                lambda _user_id, _days: [],
                app_env="development",
                marketing_display_sync=marketing_display_sync,
                asset_payout_resolver=asset_payout_resolver,
            )
        )
        self.client = TestClient(app)
        self.trade = {
            "id": "trade-1",
            "result": "WIN",
            "asset": "EURUSD-OTC",
            "direction": "CALL",
            "amount": 10.0,
            "payout": 85,
            "profit": 8.5,
            "created_at": "2026-07-21T12:00:00+00:00",
            "is_simulated": True,
            "source": "marketing_demo",
        }

    def _seed(
        self,
        *,
        company_id: str = "company-a",
        user_id: str = "marketing-user",
        trade: dict[str, Any] | None = None,
    ) -> None:
        """Insere uma operação sintética no repositório de teste."""
        import asyncio

        asyncio.run(
            self.repository.save_simulated_trade(
                company_id,
                user_id,
                trade or self.trade,
            )
        )

    def test_marketing_account_can_edit_and_delete_own_trade(self) -> None:
        self._seed()

        edited = self.client.patch(
            "/marketing-simulation/trades/trade-1",
            json={
                "result": "LOSS",
                "profit": -12.0,
                "amount": 12.0,
                "asset": "GBPUSD-OTC",
                "direction": "PUT",
                "payout": 80,
            },
        )

        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json()["data"]["result"], "LOSS")
        self.assertEqual(edited.json()["data"]["profit"], -12.0)

        deleted = self.client.delete("/marketing-simulation/trades/trade-1")
        self.assertEqual(deleted.status_code, 204)
        history = self.client.get("/marketing-simulation/history")
        self.assertEqual(history.json()["data"], [])
        self.assertGreaterEqual(len(self.sync_calls), 1)

    def test_historico_do_painel_nao_pode_ser_truncado(self) -> None:
        """Regressão: o painel Shift+O perdia as operações NOVAS.

        ``/marketing-simulation/history`` era o único dos cinco chamadores de
        ``list_simulated_trades`` que não passava ``limit``, caindo no default
        100 do repositório. No Supabase a ordem é ``synthetic_sequence.asc``,
        então o corte ficava com as 100 mais ANTIGAS: em 01/09 a conta
        `81c49f33` tinha 525 sintéticas e o painel mostrava só as de 26/07 a
        30/07, sumindo com 425.

        Nenhum teste pegou porque o dublê em memória faz ``[-limit:]`` — corta
        pela outra ponta e devolve as mais novas. Este teste falha nos dois
        repositórios: só passa quando nada é truncado.
        """
        total = 150
        for index in range(total):
            self._seed(
                trade={
                    **self.trade,
                    "id": f"trade-{index}",
                    "created_at": f"2026-07-21T12:{index % 60:02d}:00+00:00",
                }
            )

        history = self.client.get("/marketing-simulation/history")

        self.assertEqual(history.status_code, 200)
        devolvidos = history.json()["data"]
        self.assertEqual(len(devolvidos), total)
        ids = {item["id"] for item in devolvidos}
        self.assertIn("trade-0", ids)
        self.assertIn(f"trade-{total - 1}", ids)

    def test_listar_historico_nao_corrompe_o_placar(self) -> None:
        """O ``replace_history`` do endpoint recalculava o placar na fatia cortada.

        Abrir a aba Histórico reescrevia o estado do simulador com o que a
        listagem devolvesse. Truncada, o placar exibido encolhia junto.
        """
        total = 150
        for index in range(total):
            self._seed(trade={**self.trade, "id": f"trade-{index}"})

        self.client.get("/marketing-simulation/history")
        stats = self.client.get("/marketing-simulation/stats")

        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()["data"]["total_trades"], total)

    def test_non_marketing_account_cannot_list_edit_or_delete(self) -> None:
        self._seed()
        headers = {"x-test-account-type": "client", "x-test-marketing-mode": ""}

        for method, path, kwargs in (
            ("get", "/marketing-simulation/history", {}),
            ("patch", "/marketing-simulation/trades/trade-1", {"json": {"profit": 1}}),
            ("delete", "/marketing-simulation/trades/trade-1", {}),
        ):
            response = getattr(self.client, method)(path, headers=headers, **kwargs)
            self.assertEqual(response.status_code, 403)

    def test_trade_mutations_are_isolated_by_company_and_user(self) -> None:
        self._seed(company_id="company-b", user_id="other-user")

        response = self.client.patch(
            "/marketing-simulation/trades/trade-1",
            json={"profit": 999},
        )

        self.assertEqual(response.status_code, 404)
        other_history = self.repository.simulated_trades[("company-b", "other-user")]
        self.assertEqual(other_history[0]["profit"], 8.5)

    def test_edit_recalculates_cached_score_used_by_next_trade(self) -> None:
        self._seed()
        edited = self.client.patch(
            "/marketing-simulation/trades/trade-1",
            json={"result": "LOSS", "profit": -10},
        )
        self.assertEqual(edited.status_code, 200)

        generated = self.client.post("/marketing-simulation/trades")

        self.assertEqual(generated.status_code, 201)
        self.assertEqual(generated.json()["data"]["result"], "WIN")

    def test_patch_rejects_invalid_or_empty_fields(self) -> None:
        self._seed()

        invalid_payloads = (
            {},
            {"result": "DRAW"},
            {"amount": 0},
            {"asset": ""},
            {"direction": "BUY"},
            {"payout": 101},
            {"profit": None},
            {"company_id": "company-b"},
            {"user_id": "other-user"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.patch(
                    "/marketing-simulation/trades/trade-1",
                    json=payload,
                )
                self.assertEqual(response.status_code, 422)

    def test_stats_reflect_edited_and_deleted_history(self) -> None:
        self._seed()
        self._seed(
            trade={
                **self.trade,
                "id": "trade-2",
                "result": "LOSS",
                "profit": -10.0,
                "amount": 10.0,
            }
        )

        before = self.client.get("/marketing-simulation/stats")
        self.assertEqual(before.status_code, 200)
        self.assertEqual(
            before.json()["data"],
            {
                "wins": 1,
                "losses": 1,
                "total_trades": 2,
                "win_rate": 50.0,
                "profit": -1.5,
            },
        )

        edited = self.client.patch(
            "/marketing-simulation/trades/trade-2",
            json={"result": "WIN", "profit": 8.0},
        )
        self.assertEqual(edited.status_code, 200)

        after_edit = self.client.get("/marketing-simulation/stats")
        self.assertEqual(after_edit.json()["data"]["wins"], 2)
        self.assertEqual(after_edit.json()["data"]["losses"], 0)
        self.assertEqual(after_edit.json()["data"]["profit"], 16.5)

        deleted = self.client.delete("/marketing-simulation/trades/trade-1")
        self.assertEqual(deleted.status_code, 204)

        after_delete = self.client.get("/marketing-simulation/stats")
        self.assertEqual(
            after_delete.json()["data"],
            {
                "wins": 1,
                "losses": 0,
                "total_trades": 1,
                "win_rate": 100.0,
                "profit": 8.0,
            },
        )

    def test_non_marketing_cannot_read_stats(self) -> None:
        response = self.client.get(
            "/marketing-simulation/stats",
            headers={"x-test-account-type": "client", "x-test-marketing-mode": ""},
        )
        self.assertEqual(response.status_code, 403)

    def test_create_trade_accepts_amount_and_payout_overrides(self) -> None:
        """Valor e payout definidos no Shift+O entram na operação gerada."""
        created = self.client.post(
            "/marketing-simulation/trades",
            json={
                "amount": 25.5,
                "payout": 87,
                "asset": "eurusd-otc",
                "direction": "PUT",
            },
        )

        self.assertEqual(created.status_code, 201)
        data = created.json()["data"]
        self.assertEqual(data["amount"], 25.5)
        self.assertEqual(data["payout"], 87)
        self.assertEqual(data["asset"], "EURUSD-OTC")
        self.assertEqual(data["direction"], "PUT")
        self.assertIn(data["result"], ("WIN", "LOSS"))
        if data["result"] == "WIN":
            self.assertEqual(data["profit"], round(25.5 * 87 / 100, 2))
        else:
            self.assertEqual(data["profit"], -25.5)

    def test_create_trade_rejects_invalid_overrides(self) -> None:
        for payload in (
            {"amount": 0},
            {"payout": 101},
            {"direction": "BUY"},
            {"asset": "   "},
        ):
            with self.subTest(payload=payload):
                response = self.client.post("/marketing-simulation/trades", json=payload)
                self.assertEqual(response.status_code, 422)

    def test_decide_result_follows_target_win_rate(self) -> None:
        from backend.marketing_simulation_service import MarketingSimulationService

        self.assertEqual(
            MarketingSimulationService.decide_result(wins=0, losses=0, target_win_rate=100),
            "WIN",
        )
        self.assertEqual(
            MarketingSimulationService.decide_result(wins=0, losses=0, target_win_rate=0),
            "LOSS",
        )
        self.assertEqual(
            MarketingSimulationService.decide_result(wins=1, losses=1, target_win_rate=50),
            "WIN",
        )

    def test_create_trade_accepts_forced_win_or_loss(self) -> None:
        """Shift+O pode forçar WIN/LOSS em vez da taxa automática."""
        win = self.client.post(
            "/marketing-simulation/trades",
            json={
                "amount": 20,
                "payout": 80,
                "asset": "EURUSD-OTC",
                "direction": "CALL",
                "result": "WIN",
            },
        )
        loss = self.client.post(
            "/marketing-simulation/trades",
            json={
                "amount": 15,
                "payout": 80,
                "asset": "GBPUSD-OTC",
                "direction": "PUT",
                "result": "LOSS",
            },
        )

        self.assertEqual(win.status_code, 201)
        self.assertEqual(win.json()["data"]["result"], "WIN")
        self.assertEqual(win.json()["data"]["profit"], 16.0)
        self.assertEqual(loss.status_code, 201)
        self.assertEqual(loss.json()["data"]["result"], "LOSS")
        self.assertEqual(loss.json()["data"]["profit"], -15.0)

    def test_create_trade_rejects_invalid_forced_result(self) -> None:
        response = self.client.post(
            "/marketing-simulation/trades",
            json={"result": "DRAW"},
        )
        self.assertEqual(response.status_code, 422)

    def test_create_trade_accepts_custom_created_at(self) -> None:
        """Shift+O pode gravar a operação com data/hora escolhida."""
        response = self.client.post(
            "/marketing-simulation/trades",
            json={
                "amount": 10,
                "payout": 85,
                "asset": "EURUSD-OTC",
                "direction": "CALL",
                "result": "WIN",
                "created_at": "2026-07-20T14:30:00-03:00",
            },
        )

        self.assertEqual(response.status_code, 201)
        created_at = response.json()["data"]["created_at"]
        self.assertTrue(str(created_at).startswith("2026-07-20T17:30:00"))

    def test_create_trade_without_created_at_uses_now(self) -> None:
        """Sem created_at, mantém o horário atual da criação."""
        before = datetime.now(timezone.utc)
        response = self.client.post(
            "/marketing-simulation/trades",
            json={
                "amount": 10,
                "payout": 85,
                "asset": "EURUSD-OTC",
                "direction": "CALL",
                "result": "WIN",
            },
        )
        after = datetime.now(timezone.utc)

        self.assertEqual(response.status_code, 201)
        created_at = datetime.fromisoformat(
            str(response.json()["data"]["created_at"]).replace("Z", "+00:00")
        )
        self.assertGreaterEqual(created_at, before - timedelta(seconds=2))
        self.assertLessEqual(created_at, after + timedelta(seconds=2))

    def test_create_trade_rejects_invalid_created_at(self) -> None:
        response = self.client.post(
            "/marketing-simulation/trades",
            json={"created_at": "nao-e-data"},
        )
        self.assertEqual(response.status_code, 422)

    def test_generate_history_from_score_appends_and_syncs_new_scoreboard(self) -> None:
        """Gera o placar novo e preserva operações sintéticas já existentes."""
        self._seed()

        generated = self.client.post(
            "/marketing-simulation/generate-history",
            json={
                "wins": 3,
                "losses": 1,
                "amount": 10,
                "asset": "EURUSD-OTC",
                "period": "M1",
            },
        )

        self.assertEqual(generated.status_code, 201)
        data = generated.json()["data"]
        self.assertEqual(len(data), 4)
        self.assertEqual(sum(1 for item in data if item["result"] == "LOSS"), 1)
        created_ats = [item["created_at"] for item in data]
        self.assertEqual(created_ats, sorted(created_ats))
        for item in data:
            self.assertEqual(item["asset"], "EURUSD-OTC")
            self.assertEqual(item["payout"], 87)
            self.assertEqual(item["amount"], 10)
            if item["result"] == "WIN":
                self.assertEqual(item["profit"], round(10 * 87 / 100, 2))
            else:
                self.assertEqual(item["profit"], -10)

        history = self.client.get("/marketing-simulation/history")
        self.assertEqual(len(history.json()["data"]), 5)
        stats = self.client.get("/marketing-simulation/stats")
        self.assertEqual(stats.json()["data"]["wins"], 4)
        self.assertEqual(stats.json()["data"]["losses"], 1)
        self.assertGreaterEqual(len(self.sync_calls), 1)
        synced_history = self.sync_calls[-1][1]
        synced_stats = self.sync_calls[-1][2]
        self.assertEqual(len(synced_history), 4)
        self.assertEqual(synced_stats["wins"], 3)
        self.assertEqual(synced_stats["losses"], 1)
        self.assertNotEqual(synced_stats.get("accumulate"), True)

    def test_generate_history_rejects_empty_or_oversized_score(self) -> None:
        for payload in (
            {"wins": 0, "losses": 0, "amount": 10},
            {"wins": 101, "losses": 0, "amount": 10},
            {"wins": -1, "losses": 1, "amount": 10},
            {"wins": 1, "losses": 0, "amount": 0},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/marketing-simulation/generate-history",
                    json=payload,
                )
                self.assertEqual(response.status_code, 422)

    def test_non_marketing_cannot_generate_history(self) -> None:
        response = self.client.post(
            "/marketing-simulation/generate-history",
            headers={"x-test-account-type": "client", "x-test-marketing-mode": ""},
            json={
                "wins": 2,
                "losses": 1,
                "amount": 10,
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_generate_history_uses_fallback_payout_when_bullex_unavailable(self) -> None:
        """Sem payout da corretora, ainda gera o histórico com payout típico."""
        from backend.admin_router import create_admin_router
        from backend.admin_service import AdminManagementService
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient

        repository = InMemoryAdminRepository()
        service = AdminManagementService(repository)
        app = FastAPI()

        async def authenticated_user(request: Request) -> dict[str, str]:
            return {
                "user_id": "marketing-user",
                "company_id": "company-a",
                "account_type": "marketing",
                "marketing_mode": "simulation",
                "marketing_win_rate": "80",
                "is_admin": "false",
                "permissions": "",
                "manageable_role_ids": "",
            }

        async def failing_payout(_user_id: str, _symbol: str) -> int | None:
            return None

        app.include_router(
            create_admin_router(
                service,
                authenticated_user,
                authenticated_user,
                authenticated_user,
                authenticated_user,
                lambda _user_id, _days: [],
                app_env="development",
                asset_payout_resolver=failing_payout,
            )
        )
        client = TestClient(app)
        response = client.post(
            "/marketing-simulation/generate-history",
            json={"wins": 2, "losses": 0, "amount": 10, "asset": "EURUSD-OTC"},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        self.assertEqual(len(data), 2)
        for item in data:
            self.assertEqual(item["payout"], 85)
            self.assertEqual(item["asset"], "EURUSD-OTC")

    def test_asset_pool_excludes_crypto_not_allowed_for_binary(self) -> None:
        from backend.marketing_simulation_service import MarketingSimulationService

        pool = set(MarketingSimulationService.ASSET_POOL)
        self.assertNotIn("ETHUSD-OTC", pool)
        self.assertNotIn("BTCUSD-OTC", pool)
        self.assertIn("EURUSD-OTC", pool)

    def test_operation_timestamps_stay_inside_period_window(self) -> None:
        from datetime import datetime, timezone

        from backend.marketing_simulation_service import MarketingSimulationService

        service = MarketingSimulationService(seed="timestamps", target_win_rate=80)
        now = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)
        stamps = service.build_operation_timestamps(4, period="M5", now=now)
        self.assertEqual(len(stamps), 4)
        parsed = [datetime.fromisoformat(item) for item in stamps]
        self.assertEqual(parsed, sorted(parsed))
        self.assertLessEqual(parsed[-1], now)
        cadence = MarketingSimulationService.PERIOD_SECONDS["M5"]
        min_gap, max_gap = MarketingSimulationService.GAP_MULTIPLIER_RANGE["M5"]
        gaps = [
            (parsed[index + 1] - parsed[index]).total_seconds()
            for index in range(len(parsed) - 1)
        ]
        for gap in gaps:
            self.assertGreater(gap, cadence * min_gap * 0.85)
            self.assertLess(gap, cadence * max_gap * 1.25 + 200)
        span_seconds = (parsed[-1] - parsed[0]).total_seconds()
        self.assertGreater(span_seconds, cadence * min_gap)

    def test_next_trade_includes_simulated_strategy(self) -> None:
        from backend.marketing_simulation_service import MarketingSimulationService

        service = MarketingSimulationService(seed="strategy", target_win_rate=80)
        trade = service.next_trade(
            amount=10,
            payout=85,
            asset="EURUSD-OTC",
            direction="CALL",
            result="WIN",
            period="M5",
        )
        self.assertTrue(str(trade.get("strategy_name") or "").strip())
        self.assertTrue(str(trade.get("strategy_summary") or "").strip())
        self.assertEqual(trade.get("timeframe"), "M5")
        self.assertIn(trade.get("strategy_key"), {
            "RETRACEMENT_SR",
            "EXHAUSTION_REVERSAL",
            "CANDLE_FLOW",
            "CONTINUATION",
        })

    def test_enrich_trade_with_strategy_is_deterministic(self) -> None:
        from backend.marketing_simulation_service import MarketingSimulationService

        trade = {
            "id": "11111111-2222-4333-8444-555555555555",
            "result": "WIN",
            "asset": "EURUSD-OTC",
            "direction": "PUT",
            "amount": 10.0,
            "payout": 85,
            "profit": 8.5,
            "created_at": "2026-07-21T12:00:00+00:00",
        }
        first = MarketingSimulationService.enrich_trade_with_strategy(trade, period="M5")
        second = MarketingSimulationService.enrich_trade_with_strategy(trade, period="M5")
        self.assertEqual(first["strategy_name"], second["strategy_name"])
        self.assertEqual(first["strategy_key"], second["strategy_key"])
        self.assertEqual(first["timeframe"], "M5")

    def test_create_trade_response_includes_strategy(self) -> None:
        created = self.client.post(
            "/marketing-simulation/trades",
            json={
                "amount": 10,
                "payout": 85,
                "asset": "EURUSD-OTC",
                "direction": "CALL",
                "result": "WIN",
            },
        )
        self.assertEqual(created.status_code, 201)
        data = created.json()["data"]
        self.assertTrue(str(data.get("strategy_name") or "").strip())
        self.assertTrue(str(data.get("strategy_summary") or "").strip())
        self.assertEqual(data.get("timeframe"), "M5")


class SupabaseMarketingSimulationRepositoryTests(unittest.IsolatedAsyncioTestCase):
    """Garante que mutações PostgREST sempre carreguem os filtros da sessão."""

    async def test_update_and_delete_filter_company_user_and_trade(self) -> None:
        calls: list[tuple[str, str, dict[str, Any]]] = []
        repository = SupabaseAdminRepository("https://supabase.example", "service-role")
        trade_uuid = "11111111-2222-4333-8444-555555555555"

        async def capture(
            method: str,
            path: str,
            *,
            json: Any = None,
            headers: dict[str, str] | None = None,
        ) -> list[dict[str, Any]]:
            """Captura a requisição sem acessar a rede."""
            calls.append((method, path, {"json": json, "headers": headers}))
            return []

        repository._request = capture  # type: ignore[method-assign]

        updated = await repository.update_simulated_trade(
            "company-a",
            "user-a",
            trade_uuid,
            {"profit": 5, "company_id": "company-b"},
        )
        deleted = await repository.delete_simulated_trade(
            "company-a",
            "user-a",
            trade_uuid,
        )
        cleared = await repository.clear_simulated_trades("company-a", "user-a")

        self.assertIsNone(updated)
        self.assertFalse(deleted)
        self.assertEqual(cleared, 0)
        for _, path, _ in calls[:2]:
            self.assertIn("company_id=eq.company-a", path)
            self.assertIn("user_id=eq.user-a", path)
            self.assertIn(f"id=eq.{trade_uuid}", path)
        self.assertIn("company_id=eq.company-a", calls[2][1])
        self.assertIn("user_id=eq.user-a", calls[2][1])
        self.assertNotIn("&id=eq.", calls[2][1])
        self.assertNotIn("?id=eq.", calls[2][1])
        self.assertEqual(calls[0][2]["json"], {"profit": 5})
        self.assertEqual(calls[2][0], "DELETE")

    async def test_non_uuid_trade_id_never_filters_the_uuid_column(self) -> None:
        """IDs da Bullex (numéricos) não podem filtrar a coluna UUID."""
        calls: list[tuple[str, str, dict[str, Any]]] = []
        repository = SupabaseAdminRepository("https://supabase.example", "service-role")

        async def capture(
            method: str,
            path: str,
            *,
            json: Any = None,
            headers: dict[str, str] | None = None,
        ) -> list[dict[str, Any]]:
            calls.append((method, path, {"json": json, "headers": headers}))
            return []

        repository._request = capture  # type: ignore[method-assign]

        updated = await repository.update_simulated_trade(
            "company-a",
            "user-a",
            "14100935220",
            {"profit": 1},
        )
        deleted = await repository.delete_simulated_trade(
            "company-a",
            "user-a",
            "14100935220",
        )

        self.assertIsNone(updated)
        self.assertFalse(deleted)
        for _, path, _ in calls:
            self.assertNotIn("&id=eq.", path)

    async def test_delete_by_bullex_order_id_removes_live_mirror(self) -> None:
        """O espelho da operação ao vivo é achado por ``broker_order_id``."""
        calls: list[tuple[str, str, dict[str, Any]]] = []
        repository = SupabaseAdminRepository("https://supabase.example", "service-role")

        async def capture(
            method: str,
            path: str,
            *,
            json: Any = None,
            headers: dict[str, str] | None = None,
        ) -> list[dict[str, Any]]:
            calls.append((method, path, {"json": json, "headers": headers}))
            return [{"id": "11111111-2222-4333-8444-555555555555"}]

        repository._request = capture  # type: ignore[method-assign]

        deleted = await repository.delete_simulated_trade(
            "company-a",
            "user-a",
            "14100935220",
        )

        self.assertTrue(deleted)
        self.assertEqual(len(calls), 1)
        method, path, _ = calls[0]
        self.assertEqual(method, "DELETE")
        self.assertIn("company_id=eq.company-a", path)
        self.assertIn("user_id=eq.user-a", path)
        self.assertIn("broker_order_id=eq.14100935220", path)

    async def test_delete_by_bullex_order_id_tolerates_missing_column(self) -> None:
        """Sem a migration aplicada, o fluxo cai no fallback do robô."""
        repository = SupabaseAdminRepository("https://supabase.example", "service-role")

        async def fail(
            method: str,
            path: str,
            *,
            json: Any = None,
            headers: dict[str, str] | None = None,
        ) -> list[dict[str, Any]]:
            # Resposta real do PostgREST antes da migration ser aplicada.
            raise RuntimeError(
                'Supabase DELETE /rest/v1/marketing_simulated_trades -> 400: '
                '{"code":"42703","message":"column '
                'marketing_simulated_trades.broker_order_id does not exist"}'
            )

        repository._request = fail  # type: ignore[method-assign]

        deleted = await repository.delete_simulated_trade(
            "company-a",
            "user-a",
            "14100935220",
        )

        self.assertFalse(deleted)

    async def test_live_mirror_persists_broker_order_id(self) -> None:
        """Sem o vínculo, a operação ao vivo duplicava no histórico do robô."""
        calls: list[tuple[str, str, dict[str, Any]]] = []
        repository = SupabaseAdminRepository("https://supabase.example", "service-role")
        company_id = "11111111-2222-4333-8444-555555555555"
        user_id = "22222222-3333-4444-8555-666666666666"

        async def capture(
            method: str,
            path: str,
            *,
            json: Any = None,
            headers: dict[str, str] | None = None,
        ) -> list[dict[str, Any]]:
            calls.append((method, path, {"json": json, "headers": headers}))
            if method == "GET":
                return []
            return [
                {
                    **(json or {}),
                    "id": "33333333-4444-4555-8666-777777777777",
                }
            ]

        repository._request = capture  # type: ignore[method-assign]

        stored = await repository.save_simulated_trade(
            company_id,
            user_id,
            {
                "id": "synthetic-live-ab12cd34",
                "result": "WIN",
                "asset": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 10.0,
                "payout": 87,
                "profit": 8.7,
                "created_at": "2026-07-29T12:00:00+00:00",
                "broker_order_id": "14100935220",
            },
        )

        insert = next(call for call in calls if call[0] == "POST")
        self.assertEqual(insert[2]["json"]["broker_order_id"], "14100935220")
        self.assertEqual(stored["broker_order_id"], "14100935220")

    async def test_live_mirror_retries_without_broker_order_id_column(self) -> None:
        """Insert continua funcionando antes da migration ser aplicada."""
        attempts: list[dict[str, Any]] = []
        repository = SupabaseAdminRepository("https://supabase.example", "service-role")
        company_id = "11111111-2222-4333-8444-555555555555"
        user_id = "22222222-3333-4444-8555-666666666666"

        async def capture(
            method: str,
            path: str,
            *,
            json: Any = None,
            headers: dict[str, str] | None = None,
        ) -> list[dict[str, Any]]:
            if method == "GET":
                return []
            attempts.append(dict(json or {}))
            if "broker_order_id" in (json or {}):
                raise RuntimeError(
                    "PGRST204: Could not find the 'broker_order_id' column"
                )
            return [{**(json or {}), "id": "33333333-4444-4555-8666-777777777777"}]

        repository._request = capture  # type: ignore[method-assign]

        stored = await repository.save_simulated_trade(
            company_id,
            user_id,
            {
                "id": "synthetic-live-ab12cd34",
                "result": "WIN",
                "asset": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 10.0,
                "payout": 87,
                "profit": 8.7,
                "created_at": "2026-07-29T12:00:00+00:00",
                "broker_order_id": "14100935220",
            },
        )

        self.assertEqual(len(attempts), 2)
        self.assertNotIn("broker_order_id", attempts[1])
        self.assertIsNone(stored["broker_order_id"])


class InMemoryMarketingMirrorDeleteTests(unittest.IsolatedAsyncioTestCase):
    """O repositório em memória segue o mesmo contrato de exclusão."""

    async def test_delete_matches_broker_order_id(self) -> None:
        repository = InMemoryAdminRepository()
        await repository.save_simulated_trade(
            "company-a",
            "user-a",
            {
                "id": "11111111-2222-4333-8444-555555555555",
                "result": "WIN",
                "asset": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 10.0,
                "payout": 87,
                "profit": 8.7,
                "created_at": "2026-07-29T12:00:00+00:00",
                "broker_order_id": "14100935220",
            },
        )

        deleted = await repository.delete_simulated_trade(
            "company-a",
            "user-a",
            "14100935220",
        )

        self.assertTrue(deleted)
        self.assertEqual(
            await repository.list_simulated_trades("company-a", "user-a"),
            [],
        )


class MarketingRobotHistoryDeleteApiTests(unittest.TestCase):
    """Exclusão na /history com order_id da Bullex (não-UUID)."""

    def setUp(self) -> None:
        self.repository = InMemoryAdminRepository()
        service = AdminManagementService(self.repository)
        app = FastAPI()
        self.robot_deletes: list[tuple[str, str]] = []

        async def authenticated_user(request: Request) -> dict[str, str]:
            return {
                "user_id": "marketing-user",
                "company_id": "company-a",
                "account_type": "marketing",
                "marketing_mode": "simulation",
                "marketing_win_rate": "50",
                "is_admin": "false",
                "permissions": "",
                "manageable_role_ids": "",
            }

        def robot_history_deleter(user_id: str, order_id: str) -> bool:
            self.robot_deletes.append((user_id, order_id))
            return order_id == "14100935220"

        app.include_router(
            create_admin_router(
                service,
                authenticated_user,
                authenticated_user,
                authenticated_user,
                authenticated_user,
                lambda _user_id, _days: [],
                app_env="development",
                robot_history_deleter=robot_history_deleter,
            )
        )
        self.client = TestClient(app)

    def test_delete_bullex_order_id_uses_robot_history_fallback(self) -> None:
        response = self.client.delete("/marketing-simulation/trades/14100935220")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.robot_deletes, [("marketing-user", "14100935220")])

    def test_delete_unknown_order_id_returns_204_idempotent(self) -> None:
        response = self.client.delete("/marketing-simulation/trades/99999999999")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.robot_deletes, [("marketing-user", "99999999999")])


class SyntheticSequenceResolutionTests(unittest.TestCase):
    """order_id Bullex não pode virar synthetic_sequence (overflow INTEGER)."""

    def test_synthetic_id_parses_sequence(self) -> None:
        self.assertEqual(
            resolve_synthetic_sequence_from_trade({"id": "synthetic-000042"}),
            42,
        )

    def test_bullex_order_id_returns_none(self) -> None:
        self.assertIsNone(
            resolve_synthetic_sequence_from_trade({"id": "14105550918"})
        )

    def test_live_mirror_id_returns_none(self) -> None:
        self.assertIsNone(
            resolve_synthetic_sequence_from_trade({"id": "synthetic-live-ab12cd34"})
        )

    def test_explicit_sequence_wins(self) -> None:
        self.assertEqual(
            resolve_synthetic_sequence_from_trade(
                {"id": "14105550918", "_synthetic_sequence": 7}
            ),
            7,
        )


if __name__ == "__main__":
    unittest.main()
