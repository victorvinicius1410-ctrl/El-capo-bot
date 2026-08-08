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


if __name__ == "__main__":
    unittest.main()
