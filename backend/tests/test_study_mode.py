"""Modo Estudo (17/09/2026): chave de apresentação da conta marketing.

Só esconde análise e loss no painel enquanto o LIVE está ligado. Estes testes
travam o que NÃO pode mudar: só marketing liga, o runtime recebe o comando, o
placar real continua contando loss e a operação é marcada para o relatório.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from backend import main
from backend import robot_persistence
from backend import robot_runtime_main
from backend.auto_trader import AutoTrader, RobotState


USER_ID = "11111111-2222-3333-4444-555555555555"


class FakeBus:
    def __init__(self) -> None:
        self.commands: list[tuple[str, str, dict[str, Any]]] = []

    def publish_command(self, user_id: str, action: str, **kwargs: Any) -> None:
        self.commands.append((user_id, action, dict(kwargs)))


class GatewayStudyModeTests(unittest.IsolatedAsyncioTestCase):
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
        return await main.robot_study_mode(
            {"enabled": enabled},
            auth={"user_id": USER_ID, "account_type": account_type},
        )

    async def test_marketing_liga_e_manda_para_o_runtime(self) -> None:
        with patch.object(main, "robot_runtime_mode", return_value="external"):
            response = await self.call(True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(main.auto_trader.get(USER_ID).study_mode)
        self.assertEqual(self.bus.commands, [(USER_ID, "study_mode", {"enabled": True})])

    async def test_cliente_e_recusado_e_nada_e_enviado(self) -> None:
        with patch.object(main, "robot_runtime_mode", return_value="external"):
            response = await self.call(True, account_type="client")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.bus.commands, [])
        self.assertFalse(main.auto_trader.get(USER_ID).study_mode)

    async def test_modo_worker_nao_publica(self) -> None:
        with patch.object(main, "robot_runtime_mode", return_value="worker"):
            await self.call(True)
        self.assertEqual(self.bus.commands, [])
        self.assertTrue(main.auto_trader.get(USER_ID).study_mode)


class RuntimeStudyModeCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_gateway(self) -> SimpleNamespace:
        return SimpleNamespace(
            auto_trader=AutoTrader(),
            persist_robot=MagicMock(),
            publish_robot_control_snapshot=MagicMock(),
        )

    async def test_comando_liga_e_desliga_sem_mexer_no_placar(self) -> None:
        gateway = self.make_gateway()
        state = gateway.auto_trader.get(USER_ID)
        state.wins, state.losses, state.profit = 4, 3, -20.0
        await robot_runtime_main._handle_command(
            gateway, {"user_id": USER_ID, "action": "study_mode", "enabled": True}
        )
        state = gateway.auto_trader.get(USER_ID)
        self.assertTrue(state.study_mode)
        self.assertEqual((state.wins, state.losses, state.profit), (4, 3, -20.0))
        gateway.persist_robot.assert_called_once_with(USER_ID)
        gateway.publish_robot_control_snapshot.assert_called_once_with(USER_ID)

        await robot_runtime_main._handle_command(
            gateway, {"user_id": USER_ID, "action": "study_mode", "enabled": False}
        )
        self.assertFalse(gateway.auto_trader.get(USER_ID).study_mode)


class StudyModeStateTests(unittest.TestCase):
    def test_campo_vai_no_payload_e_volta_no_restore(self) -> None:
        state = RobotState()
        state.study_mode = True
        data = state.to_dict()
        self.assertIs(data["study_mode"], True)

        trader = AutoTrader()
        trader.restore(USER_ID, data)
        self.assertTrue(trader.get(USER_ID).study_mode)

    def test_fica_so_na_persistencia_local(self) -> None:
        self.assertIn("study_mode", robot_persistence.ROBOT_SETTING_FIELDS)
        self.assertNotIn("study_mode", robot_persistence.SUPABASE_ROBOT_SETTING_FIELDS)

    def test_operacao_leva_a_marca_para_o_relatorio(self) -> None:
        self.assertIn("study_mode", robot_persistence.TRADE_ANALYSIS_FIELDS)


if __name__ == "__main__":
    unittest.main()
