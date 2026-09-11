"""Testes do barramento Redis do robô (URL DB1 + modo)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend.robot_bus import RobotBus, resolve_robot_redis_url, robot_runtime_mode


class RobotBusTests(unittest.TestCase):
    def test_runtime_mode_default_embedded(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            # Remove se existir
            import os

            os.environ.pop("ROBOT_RUNTIME_MODE", None)
            self.assertEqual(robot_runtime_mode(), "embedded")

    def test_resolve_redis_url_forces_db_1(self) -> None:
        with patch.dict(
            "os.environ",
            {"PROD_REDIS_URL": "redis://redis:6379/0"},
            clear=False,
        ):
            self.assertEqual(resolve_robot_redis_url(), "redis://redis:6379/1")

    def test_publish_command_noop_without_url(self) -> None:
        bus = RobotBus(redis_url=None)
        bus.publish_command("u1", "ensure")  # não levanta

    def test_publish_and_get_snapshot_roundtrip(self) -> None:
        fake = MagicMock()
        store: dict[str, str] = {}

        def setex(key: str, _ttl: int, value: str) -> None:
            store[key] = value

        def get(key: str) -> str | None:
            return store.get(key)

        fake.setex.side_effect = setex
        fake.get.side_effect = get
        bus = RobotBus(redis_url="redis://localhost:6379/1")
        bus._client = fake
        bus.publish_snapshot("u1", {"ok": True, "data": {"status": "ANALYZING"}})
        got = bus.get_snapshot("u1")
        self.assertEqual(got["data"]["status"], "ANALYZING")
        fake.publish.assert_called()

    def test_iter_state_messages_yields_user_payload(self) -> None:
        import json

        fake = MagicMock()
        pubsub = MagicMock()
        fake.pubsub.return_value = pubsub
        pubsub.listen.return_value = [
            {"type": "subscribe", "data": 1},
            {
                "type": "message",
                "data": json.dumps(
                    {
                        "user_id": "u9",
                        "payload": {"ok": True, "data": {"status": "RUNNING"}},
                    }
                ),
            },
        ]
        # Após a 1ª mensagem útil, should_stop encerra.
        calls = {"n": 0}

        def should_stop() -> bool:
            calls["n"] += 1
            return calls["n"] > 2

        bus = RobotBus(redis_url="redis://localhost:6379/1")
        bus._client = fake
        items = list(bus.iter_state_messages(should_stop=should_stop))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][0], "u9")
        self.assertEqual(items[0][1]["data"]["status"], "RUNNING")


class RobotStartDelegationTests(unittest.IsolatedAsyncioTestCase):
    """O gateway em modo external precisa publicar `start`, não `ensure`.

    Regressão real (08/08): o `ensure` deixou de re-hidratar o estado para não
    sobrescrever o placar vivo, e só `start`/`stop` passaram a forçar a recarga.
    O `stop` foi ligado, o `start` não — o runtime seguia com o `enabled=False`
    antigo em memória e respondia `SESSION_RESTORE_SKIPPED reason=robot_disabled`.
    Nenhum robô subia pelo botão do painel.
    """

    async def test_start_publishes_start_command_in_external_mode(self) -> None:
        from backend import main as gateway

        with (
            patch.dict("os.environ", {"ROBOT_RUNTIME_MODE": "external"}, clear=False),
            patch.object(gateway, "persist_robot", return_value=None) as persist,
            patch.object(gateway.robot_bus, "publish_command") as publish,
        ):
            await gateway.start_robot_worker("u1")

        persist.assert_called_once_with("u1")
        publish.assert_called_once_with("u1", "start")

    async def test_start_waits_for_persistence_before_publishing(self) -> None:
        """Publicar antes da gravação landar faz o runtime reler `enabled=False`."""
        from concurrent.futures import Future

        from backend import main as gateway

        order: list[str] = []
        future: Future = Future()
        future.set_result(None)

        def fake_persist(_user_id: str) -> Future:
            order.append("persist")
            return future

        with (
            patch.dict("os.environ", {"ROBOT_RUNTIME_MODE": "external"}, clear=False),
            patch.object(gateway, "persist_robot", side_effect=fake_persist),
            patch.object(
                gateway.robot_bus,
                "publish_command",
                side_effect=lambda *_a, **_k: order.append("publish"),
            ),
        ):
            await gateway.start_robot_worker("u1")

        self.assertEqual(order, ["persist", "publish"])


class ManualDisconnectPropagationTests(unittest.TestCase):
    """A desconexão manual precisa chegar ao robot-runtime.

    Regressão real (09/08): o painel recebe o estado do robô por WS, e em modo
    external esse snapshot vem do runtime (`robot_bus.get_snapshot`). O runtime
    só recebia `stop` (que mexe em `enabled`), nunca soube da desconexão, e
    seguia publicando `connected: true` — o painel voltava para "Conectado" e o
    cliente clicava em Desconectar duas ou três vezes.
    """

    def setUp(self) -> None:
        from backend import main as gateway

        self.gateway = gateway
        self.user_id = "u-manual-propagation"
        self.addCleanup(gateway.bullex_manual_disconnect.discard, self.user_id)

    def test_disconnect_marks_and_publishes(self) -> None:
        with (
            patch.dict("os.environ", {"ROBOT_RUNTIME_MODE": "external"}, clear=False),
            patch.object(self.gateway.robot_bus, "publish_command") as publish,
        ):
            self.gateway.set_manual_disconnect(self.user_id, True)

        self.assertIn(self.user_id, self.gateway.bullex_manual_disconnect)
        publish.assert_called_once_with(self.user_id, "disconnect")

    def test_reconnect_clears_and_publishes(self) -> None:
        self.gateway.bullex_manual_disconnect.add(self.user_id)
        with (
            patch.dict("os.environ", {"ROBOT_RUNTIME_MODE": "external"}, clear=False),
            patch.object(self.gateway.robot_bus, "publish_command") as publish,
        ):
            self.gateway.set_manual_disconnect(self.user_id, False)

        self.assertNotIn(self.user_id, self.gateway.bullex_manual_disconnect)
        publish.assert_called_once_with(self.user_id, "reconnected")

    def test_redis_is_authority_after_gateway_restart(self) -> None:
        """Processo novo (deploy) não tem o set em memória — precisa ler do Redis.

        Regressão real (09/08): o cliente desconectava, o gateway era recriado,
        o `set` sumia e o poll seguinte reconectava com a senha salva. Ele
        deslogava, entrava de novo e a conta aparecia conectada sozinha.
        """
        self.gateway.bullex_manual_disconnect.discard(self.user_id)
        with patch.object(self.gateway.robot_bus, "is_manual_disconnect", return_value=True):
            self.assertTrue(self.gateway.is_manual_disconnect(self.user_id))
        # E o espelho local é reidratado a partir do Redis.
        self.assertIn(self.user_id, self.gateway.bullex_manual_disconnect)

    def test_redis_clear_wins_over_stale_local_mirror(self) -> None:
        self.gateway.bullex_manual_disconnect.add(self.user_id)
        with patch.object(self.gateway.robot_bus, "is_manual_disconnect", return_value=False):
            self.assertFalse(self.gateway.is_manual_disconnect(self.user_id))
        self.assertNotIn(self.user_id, self.gateway.bullex_manual_disconnect)

    def test_falls_back_to_local_mirror_when_redis_is_down(self) -> None:
        self.gateway.bullex_manual_disconnect.add(self.user_id)
        with patch.object(self.gateway.robot_bus, "is_manual_disconnect", return_value=None):
            self.assertTrue(self.gateway.is_manual_disconnect(self.user_id))

    def test_embedded_mode_does_not_publish(self) -> None:
        with (
            patch.dict("os.environ", {"ROBOT_RUNTIME_MODE": "embedded"}, clear=False),
            patch.object(self.gateway.robot_bus, "publish_command") as publish,
        ):
            self.gateway.set_manual_disconnect(self.user_id, True)

        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
