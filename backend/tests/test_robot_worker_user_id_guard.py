"""Guarda contra worker real para user_id que não é UUID (LEI 10).

Incidente 2026-08-18 ~22h18: 35 usuários de teste (`user-demo`, etc.) com
`enabled=true` sobrando na tabela `robot_states` de produção fizeram o
robot-runtime religar workers reais para eles, dobrando a carga sobre o
`_call_gate` do bullex-service e zerando `SIGNAL_FOUND`/`BEST_CANDIDATE`
para TODOS os usuários por 40+ minutos (cache de mercado compartilhado
saturado). Ver docs/ROBO_E_SUPORTE.md e docs/PERFORMANCE_SISTEMA.md.

Este teste trava que `_handle_command` nunca chame `ensure_robot_worker`
(nem hidrate a persistência) para um `user_id` que não seja UUID, mesmo que
a mensagem Redis diga `action=start`/`ensure`. Ações `stop`/`reset_score`/
`disconnect` continuam liberadas para qualquer formato de id (não criam
worker novo, só desligam/ajustam estado local).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend.robot_runtime_main import _handle_command, _is_valid_account_user_id


class IsValidAccountUserIdTests(unittest.TestCase):
    def test_accepts_real_uuid(self) -> None:
        self.assertTrue(
            _is_valid_account_user_id("3fa85f64-5717-4562-b3fc-2c963f66afa6")
        )

    def test_rejects_test_fixture_ids(self) -> None:
        for fake_id in ("user-demo", "user-runtime-stop", "", "   ", "12345"):
            with self.subTest(fake_id=fake_id):
                self.assertFalse(_is_valid_account_user_id(fake_id))

    def test_rejects_non_string_input(self) -> None:
        self.assertFalse(_is_valid_account_user_id(None))  # type: ignore[arg-type]


class HandleCommandUserIdGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_rejects_non_uuid_user_id_without_creating_worker(self) -> None:
        gateway = MagicMock()
        gateway.robot_persistence = None

        with patch(
            "backend.robot_runtime_main._hydrate_user_from_persistence"
        ) as hydrate:
            await _handle_command(
                gateway, {"action": "start", "user_id": "user-demo"}
            )

        hydrate.assert_not_called()
        gateway.ensure_robot_worker.assert_not_called()
        gateway.mark_user_active.assert_not_called()

    async def test_ensure_rejects_non_uuid_user_id_without_creating_worker(self) -> None:
        gateway = MagicMock()
        gateway.robot_persistence = None

        with patch(
            "backend.robot_runtime_main._hydrate_user_from_persistence"
        ) as hydrate:
            await _handle_command(
                gateway, {"action": "ensure", "user_id": "user-demo-42"}
            )

        hydrate.assert_not_called()
        gateway.ensure_robot_worker.assert_not_called()

    async def test_start_still_works_for_real_uuid(self) -> None:
        gateway = MagicMock()
        gateway.robot_persistence = None
        real_uuid = "3fa85f64-5717-4562-b3fc-2c963f66afa6"

        with patch(
            "backend.robot_runtime_main._hydrate_user_from_persistence"
        ) as hydrate:
            await _handle_command(gateway, {"action": "start", "user_id": real_uuid})

        hydrate.assert_called_once_with(gateway, real_uuid, force=True)
        gateway.ensure_robot_worker.assert_called_once_with(real_uuid)

    async def test_stop_is_not_blocked_for_non_uuid_user_id(self) -> None:
        """`stop` só desliga/limpa estado local — não cria worker, então
        continua liberado para qualquer formato de id (compatível com
        `test_robot_control_snapshot.py`, que usa ids como
        `user-runtime-stop`)."""
        gateway = MagicMock()
        gateway.robot_persistence = None
        gateway.robot_tasks = {}
        gateway.auto_trader = MagicMock()

        with patch(
            "backend.robot_runtime_main._hydrate_user_from_persistence"
        ) as hydrate:
            await _handle_command(
                gateway, {"action": "stop", "user_id": "user-runtime-stop"}
            )

        hydrate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
