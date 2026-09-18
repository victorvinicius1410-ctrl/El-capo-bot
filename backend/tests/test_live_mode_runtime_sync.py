"""Modo LIVE precisa chegar ao robot-runtime.

Em produção o gateway roda com ``ROBOT_RUNTIME_MODE=external`` e quem opera é
o ``robot-runtime``, com o estado do robô na memória dele. Até 10/09/2026 o
``POST /robot/live-mode`` só mudava o gateway: o runtime seguia com
``live_demo=False``, regravava False na persistência e publicava False no
snapshot — o botão voltava apagado e o modo nunca ligava com o robô rodando.
Medido em 10/09 19:37–19:47 numa conta marketing: 4 cliques ``enabled=True``,
zero ``[LIVE_DEMO_RELEASE]``.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from backend import main
from backend import robot_runtime_main
from backend.auto_trader import AutoTrader


USER_ID = "11111111-2222-3333-4444-555555555555"


class FakeBus:
    def __init__(self) -> None:
        self.commands: list[tuple[str, str, dict[str, Any]]] = []

    def publish_command(self, user_id: str, action: str, **kwargs: Any) -> None:
        self.commands.append((user_id, action, dict(kwargs)))


class GatewayLiveModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.old_trader = main.auto_trader
        self.old_bus = main.robot_bus
        main.auto_trader = AutoTrader()
        self.bus = FakeBus()
        main.robot_bus = self.bus
        self.persist = patch.object(main, "persist_robot", return_value=None)
        self.persist.start()

    async def asyncTearDown(self) -> None:
        self.persist.stop()
        main.auto_trader = self.old_trader
        main.robot_bus = self.old_bus

    async def call(self, enabled: bool, account_type: str = "marketing") -> Any:
        return await main.robot_live_mode(
            {"enabled": enabled},
            auth={"user_id": USER_ID, "account_type": account_type},
        )

    async def test_external_mode_forwards_to_runtime(self) -> None:
        with patch.object(main, "robot_runtime_mode", return_value="external"):
            response = await self.call(True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.bus.commands, [(USER_ID, "live_mode", {"enabled": True})])

    async def test_external_mode_forwards_turning_off(self) -> None:
        with patch.object(main, "robot_runtime_mode", return_value="external"):
            await self.call(False)
        self.assertEqual(self.bus.commands, [(USER_ID, "live_mode", {"enabled": False})])

    async def test_worker_mode_does_not_publish(self) -> None:
        with patch.object(main, "robot_runtime_mode", return_value="worker"):
            await self.call(True)
        self.assertEqual(self.bus.commands, [])
        self.assertTrue(main.auto_trader.get(USER_ID).live_demo)

    async def test_gateway_preserves_existing_score_when_turning_live_on(self) -> None:
        state = main.auto_trader.get(USER_ID)
        state.wins, state.losses, state.profit = 6, 1, 42.0

        with patch.object(main, "robot_runtime_mode", return_value="worker"):
            response = await self.call(True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(state.live_demo)
        self.assertEqual((state.wins, state.losses, state.profit), (6, 1, 42.0))

    async def test_non_marketing_is_denied_and_nothing_is_sent(self) -> None:
        with patch.object(main, "robot_runtime_mode", return_value="external"):
            response = await self.call(True, account_type="client")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.bus.commands, [])
        self.assertFalse(main.auto_trader.get(USER_ID).live_demo)


class RuntimeLiveModeCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_gateway(self) -> SimpleNamespace:
        return SimpleNamespace(
            auto_trader=AutoTrader(),
            persist_robot=MagicMock(),
            publish_robot_control_snapshot=MagicMock(),
        )

    async def test_command_turns_live_on_in_runtime_memory(self) -> None:
        gateway = self.make_gateway()
        await robot_runtime_main._handle_command(
            gateway, {"user_id": USER_ID, "action": "live_mode", "enabled": True}
        )
        self.assertTrue(gateway.auto_trader.get(USER_ID).live_demo)
        gateway.persist_robot.assert_called_once_with(USER_ID)
        gateway.publish_robot_control_snapshot.assert_called_once_with(USER_ID)

    async def test_command_turns_live_off(self) -> None:
        gateway = self.make_gateway()
        gateway.auto_trader.get(USER_ID).live_demo = True
        await robot_runtime_main._handle_command(
            gateway, {"user_id": USER_ID, "action": "live_mode", "enabled": False}
        )
        self.assertFalse(gateway.auto_trader.get(USER_ID).live_demo)

    async def test_running_robot_keeps_score_when_live_changes(self) -> None:
        # O caso real: robô rodando, placar vivo na memória do runtime. O
        # comando mexe só no modo — não pode re-hidratar nem mexer no placar.
        gateway = self.make_gateway()
        state = gateway.auto_trader.get(USER_ID)
        state.wins, state.losses, state.profit = 4, 1, 250.0
        await robot_runtime_main._handle_command(
            gateway, {"user_id": USER_ID, "action": "live_mode", "enabled": True}
        )
        state = gateway.auto_trader.get(USER_ID)
        self.assertTrue(state.live_demo)
        self.assertEqual((state.wins, state.losses, state.profit), (4, 1, 250.0))

    async def test_snapshot_failure_does_not_undo_the_change(self) -> None:
        gateway = self.make_gateway()
        gateway.publish_robot_control_snapshot.side_effect = RuntimeError("redis fora")
        await robot_runtime_main._handle_command(
            gateway, {"user_id": USER_ID, "action": "live_mode", "enabled": True}
        )
        self.assertTrue(gateway.auto_trader.get(USER_ID).live_demo)


if __name__ == "__main__":
    unittest.main()
