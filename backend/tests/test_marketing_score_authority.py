"""Operação excluída no Shift+O não pode voltar ao placar do El Capo.

Relato recorrente do dono: numa conta ``marketing`` que opera de verdade, o
Shift+O exclui uma operação do histórico, ela some da tela ``/history`` — e o
placar (WIN×LOSS/lucro) continua contando. Cada correção anterior arrumava um
caminho e o defeito voltava por outro.

A causa é estrutural: o placar da sessão vive em QUATRO réplicas — memória do
``backend-gateway``, memória do ``robot-runtime``, ``robot:snapshot`` no Redis e
``robot_states`` no Supabase — e todo reconcile entre elas escolhe o MAIOR
total (``_pick_preferred_session_score``, "nunca rebaixa"). Isso está certo para
WIN/LOSS novo (a réplica atrasada é sempre a menor) e é exatamente errado para a
exclusão, a única operação que baixa o placar de propósito. Bastava UMA réplica
atrasada — e sempre há, porque ``persist_robot`` grava em background e o runtime
republica snapshot a cada 1s — para o poll seguinte ressuscitar a operação. E de
forma permanente: o ``persist_robot`` seguinte regravava o valor ressuscitado.

A trava é a marca de placar autoritativo (``mark/get/clear_session_score_authority``
+ ``robot:score_authority:{user_id}``): enquanto ela existe, todo caminho de
leitura obedece ao placar da baixa em vez de promover a réplica atrasada. Ela é
liberada quando uma operação real é contabilizada ou no "Reiniciar placar".

Esta suíte cobre um caminho de ressurreição por teste, para que a próxima
regressão aponte o caminho exato em vez de "o placar voltou".
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import PropertyMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend import main
from backend import robot_runtime_main
from backend.admin_repository import InMemoryAdminRepository
from backend.admin_router import create_admin_router
from backend.admin_service import AdminManagementService
from backend.auto_trader import AutoTrader
from backend.robot_persistence import SQLiteRobotPersistence


def finished_trade(
    order_id: str,
    result: str,
    profit: float,
    *,
    finished_at: datetime,
) -> dict[str, Any]:
    """Operação finalizada mínima aceita pelo histórico do robô."""
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


class FakeRobotBus:
    """Barramento Redis em memória: os dois processos veem o mesmo estado.

    O defeito só aparece quando gateway e ``robot-runtime`` discordam, então os
    testes precisam de um Redis de verdade em espírito — chave de snapshot e
    chave de autoridade compartilhadas — e não de mocks independentes.
    """

    def __init__(self) -> None:
        self.enabled = True
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.authority: dict[str, dict[str, Any]] = {}
        self.commands: list[tuple[str, str, dict[str, Any]]] = []
        self.manual_disconnect: set[str] = set()

    def get_snapshot(self, user_id: str) -> dict[str, Any] | None:
        return self.snapshots.get(user_id)

    def publish_snapshot(self, user_id: str, payload: dict[str, Any]) -> None:
        self.snapshots[user_id] = payload

    def publish_command(self, user_id: str, action: str, **kwargs: Any) -> None:
        self.commands.append((user_id, action, dict(kwargs)))

    def set_score_authority(
        self,
        user_id: str,
        wins: int,
        losses: int,
        profit: float,
    ) -> None:
        self.authority[user_id] = {
            "wins": int(wins),
            "losses": int(losses),
            "profit": round(float(profit), 2),
        }

    def get_score_authority(self, user_id: str) -> dict[str, Any] | None:
        return self.authority.get(user_id)

    def clear_score_authority(self, user_id: str) -> None:
        self.authority.pop(user_id, None)

    def set_manual_disconnect(self, user_id: str, active: bool) -> None:
        if active:
            self.manual_disconnect.add(user_id)
        else:
            self.manual_disconnect.discard(user_id)

    def is_manual_disconnect(self, user_id: str) -> bool | None:
        return user_id in self.manual_disconnect

    def close(self) -> None:
        return None


class ScoreAuthorityTestCase(unittest.IsolatedAsyncioTestCase):
    """Base com persistência isolada, auto_trader limpo e barramento falso."""

    user_id = "marketing-authority-user"

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_persistence = main.robot_persistence
        self.old_trader = main.auto_trader
        self.old_bus = main.robot_bus
        main.robot_persistence = SQLiteRobotPersistence(
            str(Path(self.directory.name) / "robot.db")
        )
        main.auto_trader = AutoTrader()
        self.bus = FakeRobotBus()
        main.robot_bus = self.bus
        # Marca de baixa é estado de módulo: sem limpar, vaza entre testes.
        main._session_score_authority.clear()
        self.now = datetime.now(timezone.utc)

    async def asyncTearDown(self) -> None:
        main._session_score_authority.clear()
        main.robot_persistence = self.old_persistence
        main.auto_trader = self.old_trader
        main.robot_bus = self.old_bus
        self.directory.cleanup()

    def set_score(self, wins: int, losses: int, profit: float) -> Any:
        state = main.auto_trader.get(self.user_id)
        state.wins = wins
        state.losses = losses
        state.profit = profit
        return state

    def score(self) -> tuple[int, int, float]:
        state = main.auto_trader.get(self.user_id)
        return state.wins, state.losses, round(float(state.profit), 2)

    def delete_win(self, order_id: str = "uuid-win", profit: float = 8.7) -> None:
        """Aplica a baixa de um WIN pelo mesmo caminho do Shift+O.

        ``apply_marketing_score_removal`` engole exceções por design (nunca
        derrubar a exclusão por causa do placar). Aqui isso mascararia o teste,
        então o log de falha vira erro.
        """
        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main.logger, "exception", side_effect=AssertionError),
        ):
            main.apply_marketing_score_removal(
                self.user_id,
                {"result": "WIN", "profit": profit, "order_id": order_id},
            )


class SessionScoreAuthorityPrimitiveTests(ScoreAuthorityTestCase):
    """A marca em si: gravar, ler, expirar, liberar e atravessar processos."""

    async def test_mark_then_get_returns_score(self) -> None:
        main.mark_session_score_authority(self.user_id, 4, 3, 16.3)
        self.assertEqual(
            main.get_session_score_authority(self.user_id),
            (4, 3, 16.3),
        )

    async def test_mark_mirrors_to_redis_for_the_other_process(self) -> None:
        main.mark_session_score_authority(self.user_id, 4, 3, 16.3)
        self.assertEqual(
            self.bus.authority[self.user_id],
            {"wins": 4, "losses": 3, "profit": 16.3},
        )

    async def test_authority_written_by_other_process_is_obeyed(self) -> None:
        """Runtime aplica a baixa; o gateway nunca marcou nada localmente."""
        self.bus.set_score_authority(self.user_id, 2, 1, 5.0)
        self.assertEqual(
            main.get_session_score_authority(self.user_id),
            (2, 1, 5.0),
        )

    async def test_clear_by_other_process_unfreezes_local_copy(self) -> None:
        """Regressão: cópia local sobrevivendo ao clear congelava o placar.

        O gateway marca a baixa e guarda uma cópia em memória. Se o runtime
        contabiliza o WIN seguinte e apaga a chave, a cópia local do gateway
        não pode continuar valendo — senão o placar fica preso por 120s.
        """
        main.mark_session_score_authority(self.user_id, 4, 3, 16.3)
        self.bus.clear_score_authority(self.user_id)
        self.assertIsNone(main.get_session_score_authority(self.user_id))

    async def test_authority_expires(self) -> None:
        main.mark_session_score_authority(self.user_id, 4, 3, 16.3)
        self.bus.enabled = False  # força o caminho da marca em memória
        expired = main.monotonic() - 1.0
        main._session_score_authority[self.user_id] = (expired, (4, 3, 16.3))
        self.assertIsNone(main.get_session_score_authority(self.user_id))

    async def test_clear_removes_from_both_sides(self) -> None:
        main.mark_session_score_authority(self.user_id, 4, 3, 16.3)
        main.clear_session_score_authority(self.user_id)
        self.assertIsNone(main.get_session_score_authority(self.user_id))
        self.assertNotIn(self.user_id, self.bus.authority)

    async def test_authority_works_without_redis(self) -> None:
        """Dev/teste sem Redis: a marca em memória ainda tem que valer."""
        self.bus.enabled = False
        main.mark_session_score_authority(self.user_id, 1, 0, 8.7)
        self.assertEqual(
            main.get_session_score_authority(self.user_id),
            (1, 0, 8.7),
        )

    async def test_zero_score_authority_is_not_confused_with_absence(self) -> None:
        """Excluir a última operação deixa 0x0 — que não é 'sem marca'."""
        main.mark_session_score_authority(self.user_id, 0, 0, 0.0)
        self.assertEqual(
            main.get_session_score_authority(self.user_id),
            (0, 0, 0.0),
        )

    async def test_blank_user_id_is_ignored(self) -> None:
        main.mark_session_score_authority("", 1, 1, 1.0)
        self.assertIsNone(main.get_session_score_authority(""))
        main.clear_session_score_authority("")

    async def test_redis_failure_falls_back_to_local_mark(self) -> None:
        main.mark_session_score_authority(self.user_id, 4, 3, 16.3)
        with patch.object(
            self.bus,
            "get_score_authority",
            side_effect=RuntimeError("redis down"),
        ):
            self.assertEqual(
                main.get_session_score_authority(self.user_id),
                (4, 3, 16.3),
            )

    async def test_enforce_lowers_memory_to_authority(self) -> None:
        self.set_score(5, 3, 25.0)
        main.mark_session_score_authority(self.user_id, 4, 3, 16.3)
        main.apply_session_score_authority_to_state(self.user_id)
        self.assertEqual(self.score(), (4, 3, 16.3))


class ScoreResurrectionPathTests(ScoreAuthorityTestCase):
    """Um teste por caminho que já ressuscitou (ou ressuscitaria) a operação."""

    def stale_everywhere(self) -> None:
        """Deixa as três réplicas com o placar ANTERIOR à exclusão (5x3)."""
        self.set_score(5, 3, 25.0)
        main.robot_persistence.save_state(
            self.user_id,
            {"wins": 5, "losses": 3, "profit": 25.0},
        )
        self.bus.publish_snapshot(
            self.user_id,
            {"ok": True, "data": {"wins": 5, "losses": 3, "profit": 25.0}},
        )

    async def test_reconcile_on_gateway_does_not_promote_stale_replicas(self) -> None:
        self.stale_everywhere()
        self.delete_win()
        main.reconcile_session_score_on_gateway(self.user_id)
        self.assertEqual(self.score(), (4, 3, 16.3))

    async def test_get_robot_state_enrich_clamps_stale_snapshot(self) -> None:
        """``GET /robot/state``: o caminho do poll do painel."""
        self.stale_everywhere()
        self.delete_win()
        enriched = main.enrich_robot_snapshot_session_score(
            self.user_id,
            {"ok": True, "data": {"wins": 5, "losses": 3, "profit": 25.0}},
        )
        self.assertEqual(enriched["data"]["wins"], 4)
        self.assertEqual(enriched["data"]["losses"], 3)
        self.assertEqual(enriched["data"]["profit"], 16.3)

    async def test_repeated_polls_never_restore_the_operation(self) -> None:
        """"Sempre volta a ocorrer": 20 polls seguidos com réplicas atrasadas."""
        self.stale_everywhere()
        self.delete_win()
        for _ in range(20):
            enriched = main.enrich_robot_snapshot_session_score(
                self.user_id,
                {"ok": True, "data": {"wins": 5, "losses": 3, "profit": 25.0}},
            )
            self.assertEqual(enriched["data"]["wins"], 4)
            self.assertEqual(self.score(), (4, 3, 16.3))

    async def test_ws_snapshot_payload_keeps_score_down_in_external_mode(self) -> None:
        """Push WS do painel (``build_robot_state_snapshot_payload``)."""
        self.stale_everywhere()
        self.delete_win()
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main, "is_manual_disconnect", return_value=False),
        ):
            payload = main.build_robot_state_snapshot_payload(self.user_id)
        self.assertEqual(payload["data"]["wins"], 4)
        self.assertEqual(payload["data"]["losses"], 3)

    async def test_ws_snapshot_payload_keeps_score_down_in_worker_mode(self) -> None:
        """Mesmo caminho dentro do ``robot-runtime`` (sem snapshot Redis)."""
        self.stale_everywhere()
        self.delete_win()
        with (
            patch.object(main, "robot_runtime_mode", return_value="worker"),
            patch.object(main, "is_manual_disconnect", return_value=False),
            patch.object(main, "get_cached_account_snapshot", return_value={}),
        ):
            payload = main.build_robot_state_snapshot_payload(self.user_id)
        self.assertEqual(payload["data"]["wins"], 4)
        self.assertEqual(payload["data"]["losses"], 3)

    async def test_control_snapshot_on_start_does_not_restore(self) -> None:
        """Cliente clica Iniciar depois de excluir: start publica snapshot."""
        self.stale_everywhere()
        self.delete_win()
        with (
            patch.object(main, "is_manual_disconnect", return_value=False),
            patch.object(main, "get_cached_account_snapshot", return_value={}),
            patch.object(main.robot_state_ws_hub, "has_connections", return_value=False),
        ):
            payload = main.publish_robot_control_snapshot(
                self.user_id,
                worker_running=True,
            )
        self.assertEqual(payload["data"]["wins"], 4)
        self.assertEqual(self.bus.snapshots[self.user_id]["data"]["wins"], 4)

    async def test_persistence_rehydrate_does_not_restore_last_operation(self) -> None:
        """Excluir a ÚNICA operação: 1x0 na DB não pode reidratar o 0x0."""
        self.set_score(1, 0, 8.7)
        main.robot_persistence.save_state(
            self.user_id,
            {"wins": 1, "losses": 0, "profit": 8.7},
        )
        self.delete_win()
        self.assertFalse(main.rehydrate_score_from_persistence_if_blank(self.user_id))
        self.assertEqual(self.score(), (0, 0, 0.0))

    async def test_gateway_enabled_reconcile_does_not_restore(self) -> None:
        """Runtime pausou (Stop Win/Loss) e o gateway lê o snapshot dele."""
        self.stale_everywhere()
        self.delete_win()
        state = main.auto_trader.get(self.user_id)
        state.enabled = True
        self.bus.publish_snapshot(
            self.user_id,
            {
                "ok": True,
                "data": {
                    "wins": 5,
                    "losses": 3,
                    "profit": 25.0,
                    "enabled": False,
                    "status": "STOPPED",
                },
            },
        )
        with patch.object(main, "robot_runtime_mode", return_value="external"):
            reconciled = main.reconcile_gateway_enabled_from_runtime_snapshot(
                self.user_id,
                state,
            )
        self.assertEqual(reconciled.wins, 4)
        self.assertEqual(reconciled.losses, 3)

    async def test_two_deletions_in_a_row_subtract_twice(self) -> None:
        """A 2ª exclusão parte da 1ª baixa, não do placar ressuscitado."""
        self.stale_everywhere()
        self.delete_win(order_id="uuid-win-1")
        self.delete_win(order_id="uuid-win-2")
        self.assertEqual(self.score(), (3, 3, 7.6))

    async def test_score_never_goes_negative(self) -> None:
        self.set_score(0, 0, 0.0)
        self.delete_win()
        wins, losses, _ = self.score()
        self.assertEqual((wins, losses), (0, 0))

    async def test_deleting_operation_without_result_leaves_score_alone(self) -> None:
        """Ordem pendente/DRAW não entra no placar: excluir não pode baixar."""
        self.set_score(5, 3, 25.0)
        with patch.object(main, "persist_robot", return_value=None):
            main.apply_marketing_score_removal(
                self.user_id,
                {"result": "PENDING_RESULT", "profit": 0, "order_id": "pending"},
            )
        self.assertEqual(self.score(), (5, 3, 25.0))
        self.assertIsNone(main.get_session_score_authority(self.user_id))


class ScoreCanStillRiseTests(ScoreAuthorityTestCase):
    """A trava não pode virar o defeito oposto: placar congelado."""

    async def test_real_result_after_deletion_raises_score(self) -> None:
        self.set_score(5, 3, 25.0)
        self.delete_win()
        self.assertEqual(self.score(), (4, 3, 16.3))
        main.auto_trader.start(self.user_id)
        self.set_score(4, 3, 16.3)
        main.auto_trader.record_trade(
            self.user_id,
            {
                "order_id": "ord-novo",
                "mode": "REAL",
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 10,
                "payout": 85,
                "result": "PENDING_RESULT",
            },
        )
        with patch.object(main, "persist_robot", return_value=None):
            await main.finish_monitored_trade(self.user_id, "ord-novo", "WIN", 8.5)
        self.assertEqual(main.auto_trader.get(self.user_id).wins, 5)
        self.assertIsNone(main.get_session_score_authority(self.user_id))

    async def test_reset_score_endpoint_releases_authority(self) -> None:
        self.set_score(5, 3, 25.0)
        self.delete_win()
        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "is_manual_disconnect", return_value=False),
            patch.object(main, "get_cached_account_snapshot", return_value={}),
            patch.object(main.robot_state_ws_hub, "has_connections", return_value=False),
            patch.object(main, "robot_runtime_mode", return_value="embedded"),
        ):
            await main.robot_reset_score({"user_id": self.user_id})
        self.assertIsNone(main.get_session_score_authority(self.user_id))
        self.assertEqual(self.score(), (0, 0, 0.0))

    async def test_reconcile_still_promotes_when_no_deletion_happened(self) -> None:
        """Sem baixa vigente o comportamento antigo continua: nunca rebaixa."""
        self.set_score(0, 0, 0.0)
        self.bus.publish_snapshot(
            self.user_id,
            {"ok": True, "data": {"wins": 7, "losses": 3, "profit": 41.2}},
        )
        main.reconcile_session_score_on_gateway(self.user_id)
        self.assertEqual(self.score(), (7, 3, 41.2))


class RuntimeCrossProcessTests(ScoreAuthorityTestCase):
    """Gateway e ``robot-runtime`` precisam baixar o placar juntos."""

    async def test_apply_score_command_lowers_runtime_memory(self) -> None:
        self.set_score(5, 3, 25.0)
        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "publish_robot_control_snapshot", return_value=None),
        ):
            await robot_runtime_main._handle_command(
                main,
                {
                    "user_id": self.user_id,
                    "action": "apply_score",
                    "wins": 4,
                    "losses": 3,
                    "profit": 16.3,
                },
            )
        self.assertEqual(self.score(), (4, 3, 16.3))
        self.assertEqual(
            main.get_session_score_authority(self.user_id),
            (4, 3, 16.3),
        )

    async def test_start_hydration_does_not_resurrect_after_deletion(self) -> None:
        """``start`` força re-hidratação pela DB — que ainda tem a linha velha.

        ``restore`` recalcula o placar pelo histórico persistido e
        ``_prefer_live_session_score`` mantém o maior total: sem a marca, ligar
        o robô depois de excluir trazia a operação de volta.
        """
        trade = finished_trade("live-1", "WIN", 8.7, finished_at=self.now)
        main.robot_persistence.save_trade_history(self.user_id, trade)
        main.robot_persistence.save_trade(self.user_id, trade)
        main.robot_persistence.save_state(
            self.user_id,
            {"wins": 1, "losses": 0, "profit": 8.7, "enabled": True},
        )
        self.set_score(1, 0, 8.7)
        self.delete_win(order_id="live-1")

        robot_runtime_main._hydrate_user_from_persistence(
            main,
            self.user_id,
            force=True,
        )
        self.assertEqual(self.score(), (0, 0, 0.0))

    async def test_reset_score_command_clears_authority_in_runtime(self) -> None:
        self.set_score(5, 3, 25.0)
        self.delete_win()
        with patch.object(main, "publish_robot_control_snapshot", return_value=None):
            await robot_runtime_main._handle_command(
                main,
                {"user_id": self.user_id, "action": "reset_score"},
            )
        self.assertIsNone(main.get_session_score_authority(self.user_id))


class GatewayRuntimeSimulationTests(ScoreAuthorityTestCase):
    """Simulação do minuto seguinte à exclusão, com os dois processos ativos."""

    async def test_delegates_apply_score_to_runtime_in_external_mode(self) -> None:
        """A baixa precisa sair do gateway para o runtime pelo barramento."""
        self.set_score(5, 3, 25.0)
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "is_manual_disconnect", return_value=False),
            patch.object(main, "get_cached_account_snapshot", return_value={}),
            patch.object(main.robot_state_ws_hub, "has_connections", return_value=False),
        ):
            main.apply_marketing_score_removal(
                self.user_id,
                {"result": "WIN", "profit": 8.7, "order_id": "uuid-win"},
            )
        apply_score = [cmd for cmd in self.bus.commands if cmd[1] == "apply_score"]
        self.assertEqual(len(apply_score), 1)
        self.assertEqual(
            apply_score[0][2],
            {"wins": 4, "losses": 3, "profit": 16.3},
        )

    async def test_score_stays_down_through_a_minute_of_polling(self) -> None:
        """O relato do dono: "sempre volta a ocorrer".

        Reproduz o minuto seguinte à exclusão com as duas fontes de
        ressurreição rodando juntas — o publisher do runtime (1x/s, a partir da
        memória dele) e o poll do painel (que enriquece o snapshot com a DB e a
        memória do gateway) — mais o ``persist_robot`` real gravando o que
        estiver em memória. Antes, bastava uma volta para o placar subir de
        volta e a gravação seguinte tornava a subida permanente.
        """
        trade = finished_trade("live-1", "WIN", 8.7, finished_at=self.now)
        main.robot_persistence.save_trade_history(self.user_id, trade)
        self.set_score(5, 3, 25.0)
        main.robot_persistence.save_state(
            self.user_id,
            {"wins": 5, "losses": 3, "profit": 25.0},
        )
        self.bus.publish_snapshot(
            self.user_id,
            {"ok": True, "data": {"wins": 5, "losses": 3, "profit": 25.0}},
        )

        with patch.object(main, "persist_robot", return_value=None):
            main.delete_marketing_robot_history_item(self.user_id, "live-1")

        stale = {"ok": True, "data": {"wins": 5, "losses": 3, "profit": 25.0}}
        for tick in range(60):
            if tick % 5 == 0:
                # Snapshot atrasado republicado (runtime que ainda não aplicou).
                self.bus.publish_snapshot(self.user_id, dict(stale))
            enriched = main.enrich_robot_snapshot_session_score(
                self.user_id,
                self.bus.get_snapshot(self.user_id),
            )
            self.assertEqual(
                (enriched["data"]["wins"], enriched["data"]["losses"]),
                (4, 3),
                f"placar voltou no tick {tick}",
            )
            # Gravação periódica do estado, como o persist_robot em produção.
            state = main.auto_trader.get(self.user_id)
            main.robot_persistence.save_state(
                self.user_id,
                {
                    "wins": state.wins,
                    "losses": state.losses,
                    "profit": state.profit,
                },
            )
        self.assertEqual(self.score(), (4, 3, 16.3))
        self.assertEqual(
            main.robot_persistence.load_state(self.user_id)["wins"],
            4,
        )


class MarketingDeleteEndpointTests(ScoreAuthorityTestCase):
    """``DELETE /marketing-simulation/trades/{id}`` com o wiring real do app.

    Os testes acima cobrem funções; este cobre o clique da lixeira no Shift+O
    passando pelo router com ``robot_history_deleter`` e
    ``marketing_score_remover`` reais — foi entre essas duas peças que o placar
    já foi decrementado duas vezes e zero vezes.
    """

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.repository = InMemoryAdminRepository()
        service = AdminManagementService(self.repository)
        user_id = self.user_id

        async def authenticated_user(request: Request) -> dict[str, str]:
            return {
                "user_id": user_id,
                "company_id": "company-mkt",
                "account_type": "marketing",
                "marketing_mode": "simulation",
                "marketing_win_rate": "50",
                "is_admin": "false",
                "permissions": "",
                "manageable_role_ids": "",
            }

        app = FastAPI()
        app.include_router(
            create_admin_router(
                service,
                authenticated_user,
                authenticated_user,
                authenticated_user,
                authenticated_user,
                lambda _user_id, _days: [],
                app_env="development",
                marketing_display_sync=main.sync_marketing_display_to_robot,
                robot_history_deleter=main.delete_marketing_robot_history_item,
                marketing_score_remover=main.apply_marketing_score_removal,
            )
        )
        self.client = TestClient(app)
        self.persist_patch = patch.object(main, "persist_robot", return_value=None)
        self.persist_patch.start()
        self.addCleanup(self.persist_patch.stop)

    async def seed_simulated(self, trade: dict[str, Any]) -> None:
        await self.repository.save_simulated_trade("company-mkt", self.user_id, trade)

    def simulated(
        self,
        trade_id: str,
        *,
        broker_order_id: str | None = None,
        result: str = "WIN",
        profit: float = 8.7,
    ) -> dict[str, Any]:
        trade: dict[str, Any] = {
            "id": trade_id,
            "result": result,
            "asset": "EURUSD-OTC",
            "direction": "CALL",
            "amount": 10.0,
            "payout": 87,
            "profit": profit,
            "created_at": self.now.isoformat(),
        }
        if broker_order_id:
            trade["broker_order_id"] = broker_order_id
        return trade

    async def test_delete_live_order_present_only_in_history_table(self) -> None:
        main.robot_persistence.save_trade_history(
            self.user_id,
            finished_trade("14227655713", "WIN", 8.7, finished_at=self.now),
        )
        self.set_score(5, 3, 25.0)
        response = self.client.delete("/marketing-simulation/trades/14227655713")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.score(), (4, 3, 16.3))

    async def test_delete_live_order_present_only_in_trades_mirror(self) -> None:
        """Regressão: ``delete_trade`` só devolve bool.

        Sem ler a linha do espelho ANTES de apagar, o ajuste saía em silêncio —
        a operação sumia do Histórico e continuava no placar. É este o caso que
        o dono via com mais frequência (ordem ao vivo recente).
        """
        main.robot_persistence.save_trade(
            self.user_id,
            finished_trade("14227655714", "WIN", 8.7, finished_at=self.now),
        )
        self.set_score(5, 3, 25.0)
        response = self.client.delete("/marketing-simulation/trades/14227655714")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.score(), (4, 3, 16.3))

    async def test_delete_operation_present_only_in_memory(self) -> None:
        trade = finished_trade("mem-only", "LOSS", -10.0, finished_at=self.now)
        main.auto_trader.replace_history(self.user_id, [trade])
        self.set_score(2, 2, -5.0)
        response = self.client.delete("/marketing-simulation/trades/mem-only")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.score(), (2, 1, 5.0))

    async def test_delete_by_uuid_subtracts_exactly_once(self) -> None:
        """UUID do Shift+O + ``broker_order_id`` limpam duas fontes, um decremento."""
        await self.seed_simulated(
            self.simulated("uuid-mirror", broker_order_id="14227655715")
        )
        main.robot_persistence.save_trade_history(
            self.user_id,
            finished_trade("14227655715", "WIN", 8.7, finished_at=self.now),
        )
        self.set_score(5, 3, 25.0)
        response = self.client.delete("/marketing-simulation/trades/uuid-mirror")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.score(), (4, 3, 16.3))

    async def test_second_delete_is_idempotent(self) -> None:
        """Duplo clique / cache stale não pode baixar o placar duas vezes."""
        main.robot_persistence.save_trade_history(
            self.user_id,
            finished_trade("14227655716", "WIN", 8.7, finished_at=self.now),
        )
        self.set_score(5, 3, 25.0)
        first = self.client.delete("/marketing-simulation/trades/14227655716")
        second = self.client.delete("/marketing-simulation/trades/14227655716")
        self.assertEqual(first.status_code, 204)
        self.assertEqual(second.status_code, 204)
        self.assertEqual(self.score(), (4, 3, 16.3))

    async def test_deleted_operation_stays_out_of_history_and_score(self) -> None:
        """O F5 da tela e o poll do placar, juntos, depois da exclusão."""
        kept = finished_trade("keep-1", "WIN", 8.7, finished_at=self.now)
        removed = finished_trade(
            "drop-1",
            "WIN",
            8.7,
            finished_at=self.now - timedelta(minutes=5),
        )
        for trade in (kept, removed):
            main.robot_persistence.save_trade_history(self.user_id, trade)
        main.auto_trader.replace_history(self.user_id, [removed, kept])
        self.set_score(2, 0, 17.4)
        # Réplicas atrasadas, como em produção.
        main.robot_persistence.save_state(
            self.user_id,
            {"wins": 2, "losses": 0, "profit": 17.4},
        )
        self.bus.publish_snapshot(
            self.user_id,
            {"ok": True, "data": {"wins": 2, "losses": 0, "profit": 17.4}},
        )

        response = self.client.delete("/marketing-simulation/trades/drop-1")
        self.assertEqual(response.status_code, 204)

        items = main.load_robot_history_items(self.user_id, days=1)
        self.assertEqual([item["order_id"] for item in items], ["keep-1"])
        enriched = main.enrich_robot_snapshot_session_score(
            self.user_id,
            self.bus.get_snapshot(self.user_id),
        )
        self.assertEqual(enriched["data"]["wins"], 1)
        self.assertEqual(self.score(), (1, 0, 8.7))


if __name__ == "__main__":
    unittest.main()
