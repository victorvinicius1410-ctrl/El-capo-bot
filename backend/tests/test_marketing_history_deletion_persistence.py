"""Garante que operação excluída do histórico não volte no próximo GET/F5.

O endpoint ``GET /robot/history`` mescla ``robot_trade_history`` (banco) com o
histórico em memória do ``auto_trader``. Sem alinhar as duas fontes (mais o
espelho ``robot_trades`` usado na restauração), a linha apagada reaparecia
assim que a tela era recarregada.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import PropertyMock, patch

from backend import main
from backend.auto_trader import AutoTrader
from backend.robot_persistence import SQLiteRobotPersistence


def finished_trade(
    order_id: str,
    result: str,
    profit: float,
    *,
    finished_at: datetime,
) -> dict[str, Any]:
    """Monta uma operação finalizada mínima aceita pelo histórico do robô."""
    return {
        "order_id": order_id,
        "mode": "REAL",
        "account_mode": "REAL",
        "active": "EURUSD-OTC",
        "direction": "CALL",
        "amount": 10.0,
        "payout": 87,
        "result": result,
        "profit": profit,
        "sent_at": (finished_at - timedelta(minutes=1)).isoformat(),
        "finished_at": finished_at.isoformat(),
    }


def simulated_trade(
    trade_id: str,
    result: str,
    profit: float,
    *,
    created_at: datetime,
    broker_order_id: str | None = None,
) -> dict[str, Any]:
    """Monta um item do histórico editável Shift+O (marketing)."""
    trade: dict[str, Any] = {
        "id": trade_id,
        "result": result,
        "asset": "EURUSD-OTC",
        "direction": "CALL",
        "amount": 10.0,
        "payout": 87,
        "profit": profit,
        "created_at": created_at.isoformat(),
    }
    if broker_order_id:
        trade["broker_order_id"] = broker_order_id
    return trade


class MarketingHistoryDeletionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """Exclusão precisa valer para banco, memória e espelho de restauração."""

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_persistence = main.robot_persistence
        self.old_trader = main.auto_trader
        main.robot_persistence = SQLiteRobotPersistence(
            str(Path(self.directory.name) / "robot.db")
        )
        main.auto_trader = AutoTrader()
        self.user_id = "marketing-user"
        self.now = datetime.now(timezone.utc)

    async def asyncTearDown(self) -> None:
        main.robot_persistence = self.old_persistence
        main.auto_trader = self.old_trader
        self.directory.cleanup()

    async def history_order_ids(self, days: int = 30) -> list[str]:
        """Executa o mesmo caminho do F5 da tela `/history`."""
        response = await main.robot_history(days, {"user_id": self.user_id})
        payload = json.loads(response.body)
        return [item["order_id"] for item in payload["data"]["items"]]

    async def test_sync_preserves_existing_history_when_adding_simulated(self) -> None:
        """Simular operação não pode apagar linhas antigas do `/history`."""
        older = finished_trade("live-old", "WIN", 8.7, finished_at=self.now - timedelta(hours=2))
        main.robot_persistence.save_trade_history(self.user_id, older)
        main.auto_trader.replace_history(self.user_id, [older])
        kept = simulated_trade("uuid-kept", "WIN", 8.7, created_at=self.now)
        deleted = simulated_trade(
            "uuid-deleted",
            "LOSS",
            -10.0,
            created_at=self.now - timedelta(minutes=5),
        )
        main.sync_marketing_display_to_robot(
            self.user_id,
            [deleted, kept],
            {"wins": 1, "losses": 1, "profit": -1.3},
        )
        self.assertEqual(
            sorted(await self.history_order_ids()),
            ["live-old", "uuid-deleted", "uuid-kept"],
        )

        main.sync_marketing_display_to_robot(
            self.user_id,
            [kept],
            {"wins": 1, "losses": 0, "profit": 8.7},
        )

        self.assertEqual(
            sorted(await self.history_order_ids()),
            ["live-old", "uuid-deleted", "uuid-kept"],
        )

    async def test_sync_does_not_wipe_restore_mirror(self) -> None:
        """O espelho `robot_trades` de operações reais permanece após o sync."""
        live = finished_trade("14100935220", "WIN", 8.7, finished_at=self.now)
        main.robot_persistence.save_trade(self.user_id, live)
        kept = simulated_trade("uuid-kept", "WIN", 8.7, created_at=self.now)

        main.sync_marketing_display_to_robot(
            self.user_id,
            [kept],
            {"wins": 1, "losses": 0, "profit": 8.7},
        )

        stored = main.robot_persistence.load_trades(self.user_id)
        self.assertEqual([item.get("order_id") for item in stored], ["14100935220"])
        self.assertIn("uuid-kept", await self.history_order_ids())

    async def test_delete_live_order_removes_database_memory_and_mirror(self) -> None:
        """Exclusão por order_id Bullex limpa as três fontes do histórico."""
        trade = finished_trade("14100935220", "WIN", 8.7, finished_at=self.now)
        main.robot_persistence.save_trade_history(self.user_id, trade)
        main.robot_persistence.save_trade(self.user_id, trade)
        main.auto_trader.replace_history(self.user_id, [trade])

        deleted = main.delete_marketing_robot_history_item(self.user_id, "14100935220")

        self.assertTrue(deleted)
        self.assertEqual(main.robot_persistence.load_trade_history(self.user_id, 30), [])
        self.assertEqual(main.robot_persistence.load_trades(self.user_id), [])
        self.assertEqual(await self.history_order_ids(), [])

    async def test_deleted_live_order_stays_deleted_after_state_restore(self) -> None:
        """Depois de reiniciar o backend o item apagado não pode voltar."""
        trade = finished_trade("14100935220", "LOSS", -10.0, finished_at=self.now)
        main.robot_persistence.save_trade_history(self.user_id, trade)
        main.robot_persistence.save_trade(self.user_id, trade)
        main.auto_trader.replace_history(self.user_id, [trade])

        main.delete_marketing_robot_history_item(self.user_id, "14100935220")
        main.auto_trader.restore(
            self.user_id,
            {"enabled": False},
            main.robot_persistence.load_trades(self.user_id),
        )

        self.assertEqual(await self.history_order_ids(), [])

    async def test_sync_uses_broker_order_id_to_avoid_duplicate_live_trade(self) -> None:
        """Espelho e operação ao vivo compartilham o mesmo identificador."""
        live = finished_trade("14100935220", "WIN", 8.7, finished_at=self.now)
        main.robot_persistence.save_trade_history(self.user_id, live)
        main.auto_trader.replace_history(self.user_id, [live])
        mirror = simulated_trade(
            "uuid-mirror",
            "WIN",
            8.7,
            created_at=self.now,
            broker_order_id="14100935220",
        )

        main.sync_marketing_display_to_robot(
            self.user_id,
            [mirror],
            {"wins": 1, "losses": 0, "profit": 8.7},
        )

        self.assertEqual(await self.history_order_ids(), ["14100935220"])

    async def test_sync_preserves_live_strategy_when_mirror_has_broker_order_id(self) -> None:
        """Espelho Shift+O não apaga estratégia da operação ao vivo."""
        live = finished_trade("14100935220", "WIN", 8.7, finished_at=self.now)
        live["strategy_name"] = "Retração em Zonas de Suporte e Resistência"
        live["strategy_key"] = "RETRACEMENT_SR"
        live["strategy_summary"] = "Pullback em suporte com rejeição."
        live["timeframe"] = "M5"
        main.robot_persistence.save_trade_history(self.user_id, live)
        main.auto_trader.replace_history(self.user_id, [live])
        mirror = simulated_trade(
            "uuid-mirror",
            "WIN",
            8.7,
            created_at=self.now,
            broker_order_id="14100935220",
        )

        main.sync_marketing_display_to_robot(
            self.user_id,
            [mirror],
            {"wins": 1, "losses": 0, "profit": 8.7},
        )

        rows = main.robot_persistence.load_trade_history(self.user_id, 90)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("strategy_name"), live["strategy_name"])
        self.assertEqual(rows[0].get("strategy_key"), live["strategy_key"])
        self.assertEqual(rows[0].get("timeframe"), "M5")


class MarketingScoreRemovalOnDeleteTests(unittest.IsolatedAsyncioTestCase):
    """Excluir operação marketing precisa baixar o placar do overlay."""

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_persistence = main.robot_persistence
        self.old_trader = main.auto_trader
        main.robot_persistence = SQLiteRobotPersistence(
            str(Path(self.directory.name) / "robot.db")
        )
        main.auto_trader = AutoTrader()
        self.user_id = "marketing-score-user"
        self.now = datetime.now(timezone.utc)
        # Marca de baixa intencional é estado de módulo (TTL 120s): sem limpar,
        # o placar de um teste vaza para o seguinte.
        main.clear_session_score_authority(self.user_id)

    async def asyncTearDown(self) -> None:
        main.clear_session_score_authority(self.user_id)
        main.robot_persistence = self.old_persistence
        main.auto_trader = self.old_trader
        self.directory.cleanup()

    def test_delete_adjusts_score_from_history_row(self) -> None:
        trade = finished_trade("uuid-win", "WIN", 8.7, finished_at=self.now)
        main.robot_persistence.save_trade_history(self.user_id, trade)
        main.auto_trader.replace_history(self.user_id, [trade])
        state = main.auto_trader.get(self.user_id)
        state.wins = 3
        state.losses = 1
        state.profit = 20.0

        with (
            patch.object(main, "publish_marketing_score_to_overlay", return_value=None),
            patch.object(main, "persist_robot", return_value=None),
        ):
            removed = main.delete_marketing_robot_history_item(self.user_id, "uuid-win")

        self.assertIsNotNone(removed)
        self.assertEqual(state.wins, 2)
        self.assertEqual(state.losses, 1)
        self.assertEqual(state.profit, 11.3)

    def test_delete_adjusts_score_when_only_memory_has_trade(self) -> None:
        """Antes só ajustava se delete_trade_history_item achasse a linha."""
        trade = finished_trade("mem-only", "LOSS", -10.0, finished_at=self.now)
        main.auto_trader.replace_history(self.user_id, [trade])
        state = main.auto_trader.get(self.user_id)
        state.wins = 2
        state.losses = 2
        state.profit = -5.0

        with (
            patch.object(main, "publish_marketing_score_to_overlay", return_value=None),
            patch.object(main, "persist_robot", return_value=None),
        ):
            removed = main.delete_marketing_robot_history_item(self.user_id, "mem-only")

        self.assertIsNotNone(removed)
        self.assertEqual(state.wins, 2)
        self.assertEqual(state.losses, 1)
        self.assertEqual(state.profit, 5.0)

    def test_delete_adjusts_score_when_only_mirror_has_trade(self) -> None:
        """Regressão 03/09: ordem ao vivo presente só em ``robot_trades``.

        ``robot_persistence.delete_trade`` devolve apenas ``bool``. Sem ler a
        linha do espelho ANTES de apagar, ``trade_meta`` virava
        ``{"order_id": ...}`` sem ``result`` e ``apply_marketing_score_removal``
        saía em silêncio: a operação sumia do Histórico e o placar continuava
        igual — sem nenhuma linha de log para diagnosticar.
        """
        trade = finished_trade("14227655713", "WIN", 8.7, finished_at=self.now)
        main.robot_persistence.save_trade(self.user_id, trade)
        state = main.auto_trader.get(self.user_id)
        state.wins = 3
        state.losses = 1
        state.profit = 20.0

        with (
            patch.object(main, "publish_marketing_score_to_overlay", return_value=None),
            patch.object(main, "persist_robot", return_value=None),
        ):
            removed = main.delete_marketing_robot_history_item(
                self.user_id,
                "14227655713",
            )

        self.assertIsNotNone(removed)
        self.assertEqual(removed.get("result"), "WIN")
        self.assertEqual(state.wins, 2)
        self.assertEqual(state.losses, 1)
        self.assertAlmostEqual(state.profit, 11.3)

    def test_apply_removal_adopts_redis_score_before_decrement(self) -> None:
        state = main.auto_trader.get(self.user_id)
        state.wins = 0
        state.losses = 0
        state.profit = 0.0

        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(
                type(main.robot_bus),
                "enabled",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                main.robot_bus,
                "get_snapshot",
                return_value={
                    "ok": True,
                    "data": {"wins": 5, "losses": 2, "profit": 30.0},
                },
            ),
            patch.object(main, "publish_marketing_score_to_overlay", return_value=None),
            patch.object(main, "persist_robot", return_value=None),
        ):
            main.apply_marketing_score_removal(
                self.user_id,
                {"result": "WIN", "profit": 8.7, "order_id": "x"},
            )

        self.assertEqual(state.wins, 4)
        self.assertEqual(state.losses, 2)
        self.assertAlmostEqual(state.profit, 21.3)

    def test_apply_removal_publish_does_not_restore_redis_score(self) -> None:
        """Regressão: reconcile no publish desfazia a exclusão (5x3 → 5x3).

        Após decrementar, ``publish_robot_control_snapshot`` lia o Redis ainda
        com o placar antigo e o reconcile “nunca rebaixa” restaurava WIN/LOSS.
        """
        state = main.auto_trader.get(self.user_id)
        state.wins = 0
        state.losses = 0
        state.profit = 0.0
        published: list[dict] = []

        def _capture(_user_id: str, payload: dict) -> None:
            published.append(payload)

        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(
                type(main.robot_bus),
                "enabled",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                main.robot_bus,
                "get_snapshot",
                return_value={
                    "ok": True,
                    "data": {"wins": 5, "losses": 3, "profit": 25.0},
                },
            ),
            patch.object(main.robot_bus, "publish_snapshot", side_effect=_capture),
            patch.object(main.robot_bus, "publish_command", return_value=None),
            patch.object(main.robot_state_ws_hub, "has_connections", return_value=False),
            patch.object(main, "is_manual_disconnect", return_value=False),
            patch.object(main, "get_cached_account_snapshot", return_value={}),
            patch.object(main, "persist_robot", return_value=None),
        ):
            main.apply_marketing_score_removal(
                self.user_id,
                {"result": "WIN", "profit": 8.7, "order_id": "uuid-win"},
            )

        self.assertEqual(state.wins, 4)
        self.assertEqual(state.losses, 3)
        self.assertAlmostEqual(state.profit, 16.3)
        self.assertTrue(published)
        data = published[-1]["data"]
        self.assertEqual(data["wins"], 4)
        self.assertEqual(data["losses"], 3)


    def test_poll_after_removal_keeps_score_down_with_stale_sources(self) -> None:
        """Regressão: a operação excluída voltava ao placar no poll seguinte.

        ``persist_robot`` grava ``robot_states`` numa thread de background e o
        snapshot Redis publicado antes da exclusão continua no lugar. O
        ``GET /robot/state`` (``enrich_robot_snapshot_session_score``) lia
        essas duas fontes atrasadas, o reconcile "nunca rebaixa" promovia a
        memória do gateway de 4x3 de volta para 5x3 e a operação reaparecia —
        de forma permanente, porque o próximo ``persist_robot`` regravava 5x3.
        """
        state = main.auto_trader.get(self.user_id)
        state.wins = 5
        state.losses = 3
        state.profit = 25.0
        main.robot_persistence.save_state(
            self.user_id,
            {"wins": 5, "losses": 3, "profit": 25.0},
        )
        stale_snapshot = {"ok": True, "data": {"wins": 5, "losses": 3, "profit": 25.0}}

        with (
            patch.object(main, "publish_marketing_score_to_overlay", return_value=None),
            patch.object(main, "persist_robot", return_value=None),
        ):
            main.apply_marketing_score_removal(
                self.user_id,
                {"result": "WIN", "profit": 8.7, "order_id": "uuid-win"},
            )
            enriched = main.enrich_robot_snapshot_session_score(
                self.user_id,
                stale_snapshot,
            )

        self.assertEqual(state.wins, 4)
        self.assertEqual(state.losses, 3)
        self.assertEqual(enriched["data"]["wins"], 4)
        self.assertEqual(enriched["data"]["losses"], 3)

    def test_removing_last_operation_does_not_rehydrate_from_database(self) -> None:
        """Excluir a única operação: 1x0 na DB não pode reidratar o 0-0."""
        state = main.auto_trader.get(self.user_id)
        state.wins = 1
        state.losses = 0
        state.profit = 8.7
        main.robot_persistence.save_state(
            self.user_id,
            {"wins": 1, "losses": 0, "profit": 8.7},
        )

        with (
            patch.object(main, "publish_marketing_score_to_overlay", return_value=None),
            patch.object(main, "persist_robot", return_value=None),
        ):
            main.apply_marketing_score_removal(
                self.user_id,
                {"result": "WIN", "profit": 8.7, "order_id": "uuid-win"},
            )
            rehydrated = main.rehydrate_score_from_persistence_if_blank(self.user_id)

        self.assertFalse(rehydrated)
        self.assertEqual(state.wins, 0)
        self.assertEqual(state.losses, 0)

    def test_real_result_releases_authority_and_score_can_rise(self) -> None:
        """A marca da baixa não pode travar o placar de um WIN novo."""
        state = main.auto_trader.get(self.user_id)
        state.wins = 5
        state.losses = 3
        state.profit = 25.0

        with (
            patch.object(main, "publish_marketing_score_to_overlay", return_value=None),
            patch.object(main, "persist_robot", return_value=None),
        ):
            main.apply_marketing_score_removal(
                self.user_id,
                {"result": "WIN", "profit": 8.7, "order_id": "uuid-win"},
            )
            # finish_monitored_trade limpa a marca ao contabilizar o resultado.
            main.clear_session_score_authority(self.user_id)
            enriched = main.enrich_robot_snapshot_session_score(
                self.user_id,
                {"ok": True, "data": {"wins": 5, "losses": 3, "profit": 25.0}},
            )

        self.assertEqual(enriched["data"]["wins"], 5)
        self.assertEqual(enriched["data"]["losses"], 3)


class AdoptLiveSessionScoreTests(unittest.TestCase):
    """Start/stop não podem republicar placar 0-0 por cima do Redis."""

    def setUp(self) -> None:
        self.user_id = "adopt-score-user"
        main.auto_trader.stop(self.user_id)

    def test_publish_control_snapshot_keeps_redis_score_when_gateway_blank(self) -> None:
        state = main.auto_trader.get(self.user_id)
        state.wins = 0
        state.losses = 0
        state.profit = 0.0
        published: list[dict] = []

        def _capture(_user_id: str, payload: dict) -> None:
            published.append(payload)

        with (
            patch.object(
                type(main.robot_bus),
                "enabled",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                main.robot_bus,
                "get_snapshot",
                return_value={
                    "ok": True,
                    "data": {"wins": 7, "losses": 3, "profit": 41.2},
                },
            ),
            patch.object(main.robot_bus, "publish_snapshot", side_effect=_capture),
            patch.object(main.robot_state_ws_hub, "has_connections", return_value=False),
            patch.object(main, "is_manual_disconnect", return_value=False),
            patch.object(main, "get_cached_account_snapshot", return_value={}),
        ):
            payload = main.publish_robot_control_snapshot(self.user_id, worker_running=True)

        data = payload["data"]
        self.assertEqual(data["wins"], 7)
        self.assertEqual(data["losses"], 3)
        self.assertEqual(data["profit"], 41.2)
        self.assertEqual(published[0]["data"]["wins"], 7)


class AutoTraderHistoryReplacementTests(unittest.TestCase):
    """`replace_history` é a fonte única do histórico em memória."""

    def setUp(self) -> None:
        self.trader = AutoTrader()
        self.now = datetime.now(timezone.utc)

    def test_replace_history_keeps_only_finished_trades(self) -> None:
        self.trader.replace_history(
            "user-a",
            [
                finished_trade("done", "WIN", 8.7, finished_at=self.now),
                {"order_id": "pending", "result": "PENDING_RESULT"},
            ],
        )

        trades = self.trader.history("user-a")["trades"]

        self.assertEqual([trade["order_id"] for trade in trades], ["done"])

    def test_replace_history_preserves_completed_order_ids(self) -> None:
        """Esquecer ordens concluídas reabriria resultados já processados."""
        self.trader.replace_history(
            "user-a",
            [finished_trade("first", "WIN", 8.7, finished_at=self.now)],
        )
        self.trader.replace_history(
            "user-a",
            [finished_trade("second", "LOSS", -10.0, finished_at=self.now)],
        )

        completed = self.trader._completed_order_ids["user-a"]

        self.assertEqual(completed, {"first", "second"})

    def test_replace_history_ignores_blank_user(self) -> None:
        self.trader.replace_history("", [finished_trade("x", "WIN", 1, finished_at=self.now)])

        self.assertEqual(self.trader.history("")["trades"], [])


if __name__ == "__main__":
    unittest.main()
