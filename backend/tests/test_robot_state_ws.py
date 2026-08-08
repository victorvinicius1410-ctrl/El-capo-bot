"""Testes do hub WebSocket de estado do robô (tickets, digest, debounce)."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from backend.robot_state_ws import (
    WS_TICKET_TTL_SECONDS,
    RobotStateWsHub,
    _payload_digest,
)


class RobotStateWsHubTests(unittest.IsolatedAsyncioTestCase):
    def test_ticket_is_single_use_and_expires(self) -> None:
        hub = RobotStateWsHub()
        token = hub.issue_ticket("u1", "c1", now=100.0)
        self.assertEqual(hub.consume_ticket(token, now=100.5), ("u1", "c1"))
        self.assertIsNone(hub.consume_ticket(token, now=101.0))

        token2 = hub.issue_ticket("u2", "c1", now=200.0)
        self.assertIsNone(
            hub.consume_ticket(token2, now=200.0 + WS_TICKET_TTL_SECONDS + 0.1)
        )

    def test_payload_digest_stable(self) -> None:
        a = {"ok": True, "data": {"status": "ANALYZING", "n": 1}}
        b = {"data": {"n": 1, "status": "ANALYZING"}, "ok": True}
        self.assertEqual(_payload_digest(a), _payload_digest(b))

    async def test_push_skips_unchanged_digest(self) -> None:
        hub = RobotStateWsHub()
        calls = {"n": 0}

        def builder(_uid: str) -> dict:
            calls["n"] += 1
            return {"ok": True, "data": {"status": "STOPPED"}}

        hub.snapshot_builder = builder
        ws = MagicMock()
        ws.send_json = AsyncMock()
        await hub.register("u1", ws)
        self.assertTrue(await hub.push_snapshot_if_changed("u1"))
        self.assertFalse(await hub.push_snapshot_if_changed("u1"))
        self.assertEqual(ws.send_json.await_count, 1)

    async def test_schedule_publish_marks_pending(self) -> None:
        hub = RobotStateWsHub()
        hub.schedule_publish("u1", now=10.0)
        self.assertIn("u1", hub._pending_publish)


if __name__ == "__main__":
    unittest.main()
