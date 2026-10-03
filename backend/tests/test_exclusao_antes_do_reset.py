"""Apagar operação de ANTES do "Reiniciar placar" não mexe no placar.

Caso de 02/10/2026 (conta marketing 11e0b3d5): placar recém-reiniciado em 0x0,
e o usuário apagou no Histórico 4 LOSS de antes do reset. Cada exclusão
descontou a operação do placar zerado — terminou 0x0 com lucro de +81,89 que
nunca existiu, e ele teve de reiniciar de novo.

A regra é a mesma do recálculo do placar: a operação conta se
``finished_at >= stop_reset_at`` (``conta_no_placar``). Fora da janela ela já
não estava no placar nem no stop; apagá-la só a tira do resto (Histórico,
espelho, memória de padrões). O contador no banco já fazia certo:
``placar_apagar`` só desfaz lançamentos do período atual.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import Mock, patch

from backend import main
from backend import placar_janela
from backend import robot_runtime_main
from backend.placar_janela import apagada_fora_do_placar, momento_da_operacao
from tests.test_marketing_score_authority import ScoreAuthorityTestCase


def loss(order_id: str, profit: float, finished_at: datetime) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "result": "LOSS",
        "profit": profit,
        "finished_at": finished_at.isoformat(),
    }


class ExclusaoAntesDoResetTests(ScoreAuthorityTestCase):
    def reiniciar(self) -> Any:
        state = main.auto_trader.reset_score(self.user_id)
        self.reset_at = state.stop_reset_at
        return state

    def apagar(self, trade: dict[str, Any], modo: str = "external") -> None:
        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "robot_runtime_mode", return_value=modo),
            patch.object(main, "publish_robot_control_snapshot", return_value=None),
            patch.object(main.logger, "exception", side_effect=AssertionError),
        ):
            main.apply_marketing_score_removal(self.user_id, trade)

    async def test_caso_sergio_quatro_loss_de_antes_do_reset(self) -> None:
        self.reiniciar()
        antes = self.reset_at - timedelta(minutes=30)
        for order_id, lucro in (
            ("14317005558", -25.52),
            ("14316842670", -21.68),
            ("14316806557", -19.27),
            ("14316567483", -15.42),
        ):
            self.apagar(loss(order_id, lucro, antes))
        self.assertEqual(self.score(), (0, 0, 0.0))
        # Nenhuma "baixa autoritativa": não houve baixa.
        self.assertIsNone(main.get_session_score_authority(self.user_id))

    async def test_runtime_recebe_a_ordem_para_esquecer_sem_trocar_o_placar(self) -> None:
        self.reiniciar()
        self.apagar(loss("14317005558", -25.52, self.reset_at - timedelta(minutes=5)))
        comandos = [c for c in self.bus.commands if c[1] == "apply_score"]
        self.assertEqual(len(comandos), 1)
        argumentos = comandos[0][2]
        self.assertIs(argumentos.get("keep_score"), True)
        self.assertEqual(argumentos["removed_trade"]["order_id"], "14317005558")

    async def test_sem_runtime_separado_esquece_a_memoria_de_padroes(self) -> None:
        self.reiniciar()
        padroes = Mock()
        with patch.object(main, "pattern_memory", padroes):
            self.apagar(loss("14317005558", -25.52, self.reset_at - timedelta(minutes=5)), modo="embedded")
        padroes.forget_outcome.assert_called_once()
        self.assertEqual(self.score(), (0, 0, 0.0))

    async def test_operacao_depois_do_reset_continua_descontando(self) -> None:
        self.reiniciar()
        self.set_score(0, 1, -10.0)
        self.apagar(loss("14320000001", -10.0, self.reset_at + timedelta(minutes=1)))
        self.assertEqual(self.score(), (0, 0, 0.0))
        comandos = [c for c in self.bus.commands if c[1] == "apply_score"]
        self.assertNotIn("keep_score", comandos[-1][2])

    async def test_linha_do_shift_o_so_com_created_at(self) -> None:
        # `marketing_simulated_trades` devolve só `created_at`.
        self.reiniciar()
        self.apagar(
            {
                "id": "6c1f2f7e-6f1b-4d1e-9b52-1c2a3b4c5d6e",
                "order_id": "6c1f2f7e-6f1b-4d1e-9b52-1c2a3b4c5d6e",
                "result": "LOSS",
                "profit": -20.0,
                "created_at": (self.reset_at - timedelta(hours=2)).isoformat(),
            }
        )
        self.assertEqual(self.score(), (0, 0, 0.0))
        # Sintética não é dinheiro nem aprendizado: o runtime não é chamado.
        self.assertEqual([c for c in self.bus.commands if c[1] == "apply_score"], [])

    async def test_sem_data_mantem_o_comportamento_de_antes(self) -> None:
        self.reiniciar()
        self.set_score(1, 0, 8.7)
        self.delete_win(order_id="sem-data")
        self.assertEqual(self.score(), (0, 0, 0.0))


class RuntimeKeepScoreTests(ScoreAuthorityTestCase):
    async def test_apply_score_com_keep_score_nao_troca_o_placar_do_runtime(self) -> None:
        # Placar vivo do runtime 2x1; o gateway (atrasado) mandaria 0x0.
        self.set_score(2, 1, 5.0)
        apagar = Mock()
        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "publish_robot_control_snapshot", return_value=None),
            patch.object(robot_runtime_main, "apagar_operacao_do_runtime", apagar),
        ):
            await robot_runtime_main._handle_command(
                main,
                {
                    "user_id": self.user_id,
                    "action": "apply_score",
                    "wins": 0,
                    "losses": 0,
                    "profit": 0,
                    "keep_score": True,
                    "removed_trade": {"order_id": "14317005558", "result": "LOSS", "profit": -25.52},
                },
            )
        self.assertEqual(self.score(), (2, 1, 5.0))
        apagar.assert_called_once()
        self.assertIsNone(main.get_session_score_authority(self.user_id))


class JanelaTests(unittest.TestCase):
    def test_momento_prefere_finished_at(self) -> None:
        trade = {"finished_at": "2026-10-02T18:00:00Z", "created_at": "2026-10-02T17:59:00Z"}
        self.assertEqual(momento_da_operacao(trade), datetime(2026, 10, 2, 18, tzinfo=timezone.utc))

    def test_momento_cai_para_created_at(self) -> None:
        self.assertEqual(
            momento_da_operacao({"created_at": "2026-10-02T17:59:00+00:00"}),
            datetime(2026, 10, 2, 17, 59, tzinfo=timezone.utc),
        )

    def test_sem_data_nao_e_fora_do_placar(self) -> None:
        self.assertIsNone(momento_da_operacao({"finished_at": "lixo"}))
        self.assertFalse(apagada_fora_do_placar({}, "2026-10-02T18:18:39Z"))

    def test_fronteira_do_reset(self) -> None:
        reset = "2026-10-02T18:18:39+00:00"
        self.assertTrue(apagada_fora_do_placar({"finished_at": "2026-10-02T18:18:38+00:00"}, reset))
        self.assertFalse(apagada_fora_do_placar({"finished_at": reset}, reset))

    def test_antes_da_regra_continua_tambem_esta_fora(self) -> None:
        # Sem reset nenhum, a janela começa em PLACAR_CONTINUO_DESDE (01/10 em
        # produção; o tests/__init__.py a recua para 2020, daí o patch).
        with patch.object(
            placar_janela,
            "PLACAR_CONTINUO_DESDE",
            datetime(2026, 10, 1, 3, tzinfo=timezone.utc),
        ):
            self.assertTrue(apagada_fora_do_placar({"finished_at": "2026-09-29T12:00:00Z"}, None))
            self.assertFalse(apagada_fora_do_placar({"finished_at": "2026-10-02T12:00:00Z"}, None))


if __name__ == "__main__":
    unittest.main()
