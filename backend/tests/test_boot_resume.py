"""O robot-runtime religa sozinho, no boot, quem estava ligado.

Até 28/09/2026 o boot não subia worker nenhum e o painel seguia dizendo
"ligado". A conta do dono (mercado aberto) ficou de 27/09 08:54 UTC — restart
do runtime — até a manhã de 28/09 sem operar, com o mercado pagando. Em 18/08
a religação automática tinha sido desligada porque 35 usuários de teste com
enabled=true ganharam worker real: por isso só UUID e só conta ativa.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend import robot_runtime_main
from backend.auto_trader import AutoTrader

AGORA = datetime(2026, 9, 28, 9, 40, tzinfo=timezone.utc)
ATIVA = "11111111-2222-3333-4444-555555555555"
NUNCA_OPEROU = "22222222-3333-4444-5555-666666666666"
ABANDONADA = "33333333-4444-5555-6666-777777777777"
DESLIGADA = "44444444-5555-6666-7777-888888888888"
TESTE = "user-demo"


def _gateway() -> SimpleNamespace:
    trader = AutoTrader()
    restauradas = {}
    for user_id, enabled, ultima in (
        (ATIVA, True, AGORA - timedelta(days=3)),
        (NUNCA_OPEROU, True, None),
        (ABANDONADA, True, AGORA - timedelta(days=40)),
        (DESLIGADA, False, AGORA - timedelta(hours=1)),
        (TESTE, True, None),
    ):
        state = trader.get(user_id)
        state.enabled = enabled
        state.last_entry_at = ultima
        restauradas[user_id] = {}
    return SimpleNamespace(auto_trader=trader, restorable_robot_states=restauradas)


class ContasParaReligarTest(unittest.TestCase):
    def test_so_contas_reais_ligadas_e_ativas(self) -> None:
        contas = robot_runtime_main._contas_para_religar(_gateway(), AGORA)
        self.assertEqual(contas, [ATIVA, NUNCA_OPEROU])

    def test_ultima_entrada_sem_fuso_nao_quebra(self) -> None:
        gateway = _gateway()
        gateway.auto_trader.get(ATIVA).last_entry_at = datetime(2026, 9, 27, 8, 0)
        self.assertIn(ATIVA, robot_runtime_main._contas_para_religar(gateway, AGORA))


class ReligarNoBootTest(unittest.IsolatedAsyncioTestCase):
    async def test_manda_ensure_um_por_vez(self) -> None:
        handle = AsyncMock()
        with patch.object(robot_runtime_main, "_handle_command", handle), patch.object(
            robot_runtime_main, "BOOT_RESUME_SPACING_SECONDS", 0
        ), patch.object(robot_runtime_main, "BOOT_RESUME_ENABLED", True):
            await robot_runtime_main._religar_quem_estava_ligado(_gateway(), _nunca_para())
        acoes = [chamada.args[1] for chamada in handle.await_args_list]
        self.assertEqual(
            acoes,
            [{"action": "ensure", "user_id": ATIVA}, {"action": "ensure", "user_id": NUNCA_OPEROU}],
        )

    async def test_interruptor_no_env_desliga(self) -> None:
        handle = AsyncMock()
        with patch.object(robot_runtime_main, "_handle_command", handle), patch.object(
            robot_runtime_main, "BOOT_RESUME_ENABLED", False
        ):
            await robot_runtime_main._religar_quem_estava_ligado(_gateway(), _nunca_para())
        handle.assert_not_awaited()

    async def test_falha_numa_conta_nao_para_as_outras(self) -> None:
        handle = AsyncMock(side_effect=[RuntimeError("corretora"), None])
        with patch.object(robot_runtime_main, "_handle_command", handle), patch.object(
            robot_runtime_main, "BOOT_RESUME_SPACING_SECONDS", 0
        ), patch.object(robot_runtime_main, "BOOT_RESUME_ENABLED", True):
            await robot_runtime_main._religar_quem_estava_ligado(_gateway(), _nunca_para())
        self.assertEqual(handle.await_count, 2)


def _nunca_para():
    import asyncio

    return asyncio.Event()


if __name__ == "__main__":
    unittest.main()
