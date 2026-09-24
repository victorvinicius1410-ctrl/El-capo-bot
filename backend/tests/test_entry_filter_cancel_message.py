"""Cancelamento silencioso quando o filtro de S/R/pavio barra o disparo.

Em 11/09/2026, depois do deploy do filtro de pavio e do S/R visível, 66 entradas
foram canceladas em 5h na reconferência do disparo. Todas chegavam ao painel
como "Entrada rejeitada. Motivo: Nenhum ativo disponível no momento da compra."
— parecia falha do sistema, e 4-5 contas recebiam no mesmo segundo.

Desde então o painel **não anuncia** a entrada antes dessa conferência, então
não há entrada para rejeitar: o ciclo segue como um ciclo sem oportunidade e o
robô espera a próxima vela. Quando a CORRETORA recusa a compra, aí sim continua
sendo rejeição de verdade, com a mensagem de ativo indisponível.
"""

import unittest
from unittest.mock import AsyncMock, patch

from backend import main
from backend.auto_trader import STATUS_ORDER_REJECTED, STATUS_WAITING_NEXT_CYCLE, AutoTrader

SERVER_TIME_M1_OPEN = 60.0


def _preparar(user_id: str, simbolos: tuple[str, ...]) -> None:
    state = main.auto_trader.start(user_id)
    main.auto_trader.set_pending_signal(
        user_id,
        {
            "symbol": simbolos[0],
            "signal": "CALL",
            "confidence": 94,
            "payout": 90,
            "strategy_score": 94,
            "trade_allowed": True,
        },
    )
    state.candidates = [
        {
            "symbol": s,
            "signal": "CALL",
            "direction": "CALL",
            "confidence": 94 - i,
            "payout": 90,
            "strategy_score": 94 - i,
            "trade_allowed": True,
        }
        for i, s in enumerate(simbolos)
    ]
    state.candidates_count = len(state.candidates)


class MensagemDeCancelamentoTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.auto_trader = AutoTrader()
        main.user_store = main.create_user_store()
        patcher = patch.object(main, "ensure_robot_worker")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.compras: list[str] = []

    async def _bullex(self, method, path, call_user_id, json_body=None, params=None, **_kwargs):
        if path == "/sessions/status":
            return 200, main.build_success(
                {"connected": True, "active_mode": "REAL", "server_time": SERVER_TIME_M1_OPEN}
            )
        if path == "/orders/buy-real":
            self.compras.append(json_body["active"])
            return 409, main.build_error("active suspended")
        raise AssertionError(f"unexpected path: {path}")

    async def _rodar(self, user_id, vereditos):
        with (
            patch.object(main, "call_bullex_service", side_effect=self._bullex),
            patch.object(main, "revalidate_level_before_entry", new=AsyncMock(side_effect=vereditos)),
            patch.object(main.trade_result_monitor, "start", return_value=True),
        ):
            return await main.execute_robot_cycle(user_id)

    async def test_pavio_nao_vira_entrada_rejeitada(self) -> None:
        _preparar("user-cancel-pavio", ("EURUSD-OTC",))
        _, payload = await self._rodar("user-cancel-pavio", ["PAVIO_NA_ENTRADA"])
        data = payload["data"]
        self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertNotEqual(data["status"], STATUS_ORDER_REJECTED)
        self.assertEqual(data["last_rejection_reason"], "PAVIO_NA_ENTRADA")
        self.assertIsNone(data["pending_signal"])
        self.assertEqual(self.compras, [])

    async def test_sr_nao_vira_entrada_rejeitada(self) -> None:
        _preparar("user-cancel-sr", ("EURUSD-OTC",))
        _, payload = await self._rodar("user-cancel-sr", ["SR_ZONE_NA_ENTRADA"])
        data = payload["data"]
        self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(data["last_rejection_reason"], "SR_ZONE_NA_ENTRADA")

    async def test_confirmacao_do_aberto_nao_vira_entrada_rejeitada(self) -> None:
        """24/09: RSI/REV-Z indicou e a vela fechada não confirmou — sem ordem."""
        for codigo in (
            "RSI_SEM_EXTREMO_NO_FECHAMENTO",
            "RSI_DIRECAO_VIROU",
            "RSI_CONFIRMACAO_SEM_DADOS",
            "REVZ_SEM_EXTREMO_NO_FECHAMENTO",
        ):
            with self.subTest(codigo=codigo):
                usuario = f"user-cancel-{codigo.lower()}"
                # O caminho da recusa não depende do ativo; o -OTC só evita a
                # revalidação de canal (`/payouts`) que o mock não atende.
                _preparar(usuario, ("EURUSD-OTC",))
                state = main.auto_trader.get(usuario)
                veredito = {"strategy": "RSI", "direction": "CALL", "timeframe": "M1"}
                for candidato in [state.pending_signal, *state.candidates]:
                    candidato["revz"] = dict(veredito)
                with patch.object(main, "confirm_revz_before_entry", new=AsyncMock(return_value=codigo)):
                    _, payload = await self._rodar(usuario, [None])
                data = payload["data"]
                self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
                self.assertEqual(data["last_rejection_reason"], codigo)
                self.assertEqual(self.compras, [])

    async def test_corretora_recusando_os_outros_ainda_e_rejeicao(self) -> None:
        """Filtro no primeiro, corretora recusando os seguintes: houve compra."""
        _preparar("user-cancel-misto", ("EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"))
        _, payload = await self._rodar("user-cancel-misto", ["PAVIO_NA_ENTRADA", None, None])
        self.assertEqual(payload["data"]["status"], STATUS_ORDER_REJECTED)
        self.assertEqual(payload["data"]["last_order_error"], main.NO_AVAILABLE_ASSET_ERROR)
        self.assertEqual(self.compras, ["GBPUSD-OTC", "USDJPY-OTC"])


if __name__ == "__main__":
    unittest.main()
