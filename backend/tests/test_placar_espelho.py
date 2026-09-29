"""O "Iniciar Operação" não pode mudar a composição do placar.

Caso real de 28/09/2026 (conta 11e0b3d5): o cliente apagou 3 losses no Shift+O
(2x2), um LOSS fechou (2x3), ele parou, trocou M5→M1, iniciou — e o placar virou
1x4/−315. O robô tinha contado certo: o restore recalculava pelo espelho
``robot_trades``, que mentia de dois jeitos —
- o WIN da primeira ordem estava regravado como ``PENDING_RESULT`` (uma cópia
  velha de ``last_trade`` no gateway sobrescrevia o resultado do runtime);
- um loss apagado tinha voltado (o ``persist_robot`` recriava o ``last_trade``).

Ver backend/docs/PLACAR_OVERLAY.md §2026-09-29.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend import main
from backend import robot_runtime_main
from backend.auto_trader import AutoTrader, utc_now
from backend.robot_persistence import RestoreTrades, SQLiteRobotPersistence

USER = "11e0b3d5-0000-4000-8000-000000000001"


def _ordem(order_id: str, result: str, profit: float, fim) -> dict:
    """Operação final como o runtime grava no Histórico e no espelho."""
    return {
        "order_id": order_id,
        "active": "EURUSD-OTC",
        "direction": "CALL",
        "amount": 100.0,
        "payout": 85.0,
        "timeframe": "M5",
        "result": result,
        "cycle_result": result if result in {"WIN", "LOSS"} else None,
        "final_result": result,
        "profit": profit,
        "sent_at": (fim - timedelta(seconds=5)).isoformat(),
        "finished_at": fim.isoformat(),
        "is_gale": False,
        "gale_step": 0,
        "parent_order_id": None,
    }


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._pasta = tempfile.TemporaryDirectory()
        self.persist = SQLiteRobotPersistence(str(Path(self._pasta.name) / "t.db"))

    def tearDown(self) -> None:
        self._pasta.cleanup()

    def _dia_do_sergio(self) -> dict[str, dict]:
        """Grava o dia de 28/09 como ficou no banco e devolve as ordens."""
        base = utc_now() - timedelta(seconds=120)
        passo = timedelta(seconds=10)
        ops = {
            "win_preso": _ordem("14303959340", "WIN", 85.0, base),
            "loss1": _ordem("14304013639", "LOSS", -100.0, base + passo),
            "apagado": _ordem("14304063332", "LOSS", -100.0, base + 2 * passo),
            "loss2": _ordem("14304114339", "LOSS", -100.0, base + 3 * passo),
            "win2": _ordem("14304295312", "WIN", 85.0, base + 4 * passo),
            "loss3": _ordem("14304459352", "LOSS", -100.0, base + 5 * passo),
        }
        # Histórico: a verdade — sem o loss apagado.
        for chave, op in ops.items():
            if chave != "apagado":
                self.persist.save_trade_history(USER, op)
        # Espelho: WIN regravado como pendente + loss apagado ressuscitado.
        for chave, op in ops.items():
            if chave == "win_preso":
                pendente = {**op, "result": "PENDING_RESULT", "cycle_result": None, "profit": None}
                pendente.pop("finished_at")
                self.persist.save_trade(USER, pendente)
            else:
                self.persist.save_trade(USER, op)
        return ops

    def _payload_com_reset(self) -> dict:
        estado = AutoTrader().get(USER)
        estado.stop_reset_at = utc_now() - timedelta(hours=1)
        return estado.to_dict()


class RestorePeloHistoricoTests(_Base):
    def test_espelho_sujo_reproduz_o_1x4(self) -> None:
        """Prova de que o cenário é o de produção: pelo espelho dá 1x4/−315."""
        self._dia_do_sergio()
        trader = AutoTrader()
        estado = trader.restore(USER, self._payload_com_reset(), self.persist.load_trades(USER))
        self.assertEqual((estado.wins, estado.losses, estado.profit), (1, 4, -315.0))

    def test_iniciar_mantem_2x3(self) -> None:
        self._dia_do_sergio()
        trader = AutoTrader()
        trades = self.persist.load_trades_for_restore(USER)
        estado = trader.restore(USER, self._payload_com_reset(), trades)
        self.assertEqual((estado.wins, estado.losses, estado.profit), (2, 3, -130.0))

    def test_ordem_em_voo_do_espelho_continua_entrando(self) -> None:
        agora = utc_now()
        self.persist.save_trade_history(USER, _ordem("7001", "WIN", 85.0, agora - timedelta(seconds=30)))
        em_voo = _ordem("7002", "PENDING_RESULT", 0.0, agora)
        em_voo.pop("finished_at")
        self.persist.save_trade(USER, em_voo)
        trades = self.persist.load_trades_for_restore(USER)
        self.assertEqual([t["order_id"] for t in trades], ["7001", "7002"])
        self.assertTrue(trades.authoritative)

    def test_historico_vazio_hoje_zera_placar_de_ontem(self) -> None:
        payload = AutoTrader().get(USER).to_dict()
        payload.update({"wins": 5, "losses": 3, "profit": 125.0})
        trader = AutoTrader()
        estado = trader.restore(USER, payload, RestoreTrades([], authoritative=True))
        self.assertEqual((estado.wins, estado.losses, estado.profit), (0, 0, 0.0))

    def test_falha_de_leitura_preserva_o_placar(self) -> None:
        """Lista vazia sem Histórico lido é ambígua: não zera."""
        payload = AutoTrader().get(USER).to_dict()
        payload.update({"wins": 5, "losses": 3, "profit": 125.0})
        estado = AutoTrader().restore(USER, payload, RestoreTrades([], authoritative=False))
        self.assertEqual((estado.wins, estado.losses), (5, 3))

    def test_falha_no_historico_cai_no_espelho(self) -> None:
        self._dia_do_sergio()
        with patch.object(self.persist, "load_trade_history", side_effect=RuntimeError("fora")):
            trades = self.persist.load_trades_for_restore(USER)
        self.assertFalse(trades.authoritative)
        self.assertEqual(len(trades), 6)

    def test_last_trade_pendente_de_ordem_fechada_volta_final(self) -> None:
        ops = self._dia_do_sergio()
        payload = self._payload_com_reset()
        payload["last_trade"] = {"order_id": "14303959340", "result": "PENDING_RESULT"}
        estado = AutoTrader().restore(USER, payload, self.persist.load_trades_for_restore(USER))
        self.assertEqual(estado.last_trade["result"], "WIN")
        self.assertEqual(estado.last_trade["finished_at"], ops["win_preso"]["finished_at"])

    def test_quem_operou_hoje(self) -> None:
        self._dia_do_sergio()
        self.assertEqual(self.persist.load_user_ids_with_history_today(), {USER})


class EspelhoProtegidoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = "u-espelho"
        main.auto_trader._states.pop(self.user, None)
        main.auto_trader._completed_order_ids.pop(self.user, None)
        main._deleted_orders_memory.pop(self.user, None)

    def test_gateway_external_nao_escreve_no_espelho(self) -> None:
        with patch.object(main, "robot_runtime_mode", return_value="external"):
            self.assertIsNone(main._trade_for_mirror(self.user, {"order_id": "1", "result": "WIN"}))

    def test_runtime_nao_rebaixa_ordem_fechada_para_pendente(self) -> None:
        main.auto_trader._completed_order_ids[self.user] = {"8001"}
        with patch.object(main, "robot_runtime_mode", return_value="worker"):
            self.assertIsNone(
                main._trade_for_mirror(self.user, {"order_id": "8001", "result": "PENDING_RESULT"})
            )
            final = {"order_id": "8001", "result": "WIN"}
            self.assertIs(main._trade_for_mirror(self.user, final), final)
            em_voo = {"order_id": "8002", "result": "PENDING_RESULT"}
            self.assertIs(main._trade_for_mirror(self.user, em_voo), em_voo)

    def test_ordem_apagada_nao_volta_ao_espelho(self) -> None:
        trader = main.auto_trader
        estado = trader.get(self.user)
        estado.last_trade = {"order_id": "9001", "result": "LOSS", "profit": -100.0}
        trader._histories[self.user] = [dict(estado.last_trade)]
        with patch.object(main.robot_bus, "mark_deleted_orders") as publicar:
            main.mark_deleted_orders(self.user, ["9001"])
        publicar.assert_called_once_with(self.user, ["9001"])
        self.assertTrue(main.is_deleted_order(self.user, "9001"))
        self.assertTrue(estado.last_trade["score_removed"])
        # O stop continua vendo o dinheiro real da ordem apagada.
        self.assertEqual([t["order_id"] for t in trader._histories[self.user]], ["9001"])
        with patch.object(main, "robot_runtime_mode", return_value="worker"):
            self.assertIsNone(main._trade_for_mirror(self.user, estado.last_trade))
            # Mesmo sem a marca local (outro processo), a lápide segura.
            self.assertIsNone(
                main._trade_for_mirror(self.user, {"order_id": "9001", "result": "LOSS"})
            )

    def test_persist_nao_recria_ordem_apagada(self) -> None:
        estado = main.auto_trader.get(self.user)
        estado.last_trade = {"order_id": "9101", "result": "LOSS", "profit": -100.0}
        with patch.object(main.robot_bus, "mark_deleted_orders"):
            main.mark_deleted_orders(self.user, ["9101"])
        with (
            patch.object(main, "robot_runtime_mode", return_value="worker"),
            patch.object(main.robot_persistence, "save_state"),
            patch.object(main.robot_persistence, "save_trade") as gravar,
        ):
            futuro = main.persist_robot(self.user)
            if futuro is not None:
                futuro.result(timeout=5)
        gravar.assert_not_called()

    def test_apply_score_do_runtime_marca_last_trade_apagado(self) -> None:
        estado = main.auto_trader.get(self.user)
        estado.last_trade = {"order_id": "9201", "result": "LOSS", "profit": -100.0}
        main._deleted_orders_memory[self.user] = {"9201": float("inf")}
        with (
            patch.object(main, "persist_robot"),
            patch.object(main, "publish_robot_control_snapshot"),
            patch.object(main.robot_bus, "set_score_authority"),
        ):
            asyncio.run(
                robot_runtime_main._handle_command(
                    main,
                    {"user_id": self.user, "action": "apply_score", "wins": 0, "losses": 0, "profit": 0},
                )
            )
        self.assertTrue(main.auto_trader.get(self.user).last_trade.get("score_removed"))


class OrfaJaFinalTests(unittest.TestCase):
    def test_pendente_com_historico_final_nao_conta_de_novo(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            persist = SQLiteRobotPersistence(str(Path(pasta) / "t.db"))
            fim = utc_now() - timedelta(minutes=5)
            final = _ordem("14303959340", "WIN", 85.0, fim)
            persist.save_trade_history(USER, final)
            pendente = {**final, "result": "PENDING_RESULT", "cycle_result": None}
            pendente.pop("finished_at")
            persist.save_trade(USER, pendente)
            finalizar = AsyncMock()
            buscar = AsyncMock()
            gateway = type(
                "G",
                (),
                {
                    "robot_persistence": persist,
                    "fetch_trade_result": buscar,
                    "normalize_trade_result": staticmethod(lambda p: ("WIN", 85.0)),
                    "finish_monitored_trade": finalizar,
                },
            )()
            asyncio.run(robot_runtime_main._recuperar_ordens_orfas(gateway, horas=6))
            finalizar.assert_not_awaited()
            buscar.assert_not_awaited()
            espelho = {t["order_id"]: t for t in persist.load_trades(USER)}
            self.assertEqual(espelho["14303959340"]["result"], "WIN")


if __name__ == "__main__":
    unittest.main()
