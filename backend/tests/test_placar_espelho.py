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
from unittest.mock import AsyncMock, PropertyMock, patch

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


class ViradaDoDiaTests(unittest.TestCase):
    """29/09: placar de ONTEM ganhava do de hoje por ser maior."""

    def setUp(self) -> None:
        self.user = "d353ab80-0000-4000-8000-000000000001"
        for mapa in (
            main.auto_trader._states,
            main._ultimo_dia_do_placar,
            main._gateway_score_day,
            main._session_score_authority,
        ):
            mapa.pop(self.user, None)

    def _persistencia(self, payload: dict, trades: list) -> object:
        return type(
            "P",
            (),
            {
                "load_states": staticmethod(lambda: [(self.user, payload)]),
                "load_trades_for_restore": staticmethod(
                    lambda _u: RestoreTrades(list(trades), authoritative=True)
                ),
            },
        )()

    def test_iniciar_descarta_placar_da_memoria_de_ontem(self) -> None:
        """d353ab80: restaurado às 23h com 1x3, iniciou às 09:40 do dia seguinte."""
        estado = main.auto_trader.get(self.user)
        estado.wins, estado.losses, estado.profit = 1, 3, -10.8
        # Restaurado no boot de ontem e nunca ligado desde então.
        main._ultimo_dia_do_placar[self.user] = main.brasilia_today() - timedelta(days=1)
        payload = estado.to_dict()
        hoje = [_ordem("9501", "WIN", 4.2, utc_now() - timedelta(seconds=30))]
        gateway = type(
            "G",
            (),
            {
                "robot_persistence": self._persistencia(payload, hoje),
                "auto_trader": main.auto_trader,
                "_ultimo_dia_do_placar": main._ultimo_dia_do_placar,
                "mark_session_score_authority": staticmethod(main.mark_session_score_authority),
            },
        )()
        with patch.object(main.robot_bus, "set_score_authority"):
            robot_runtime_main._hydrate_user_from_persistence(gateway, self.user, force=True)
        self.assertEqual(self._placar(), (1, 0, 4.2))
        self.assertEqual(main._ultimo_dia_do_placar[self.user], main.brasilia_today())
        self.assertEqual(main.get_session_score_authority(self.user), (1, 0, 4.2))

    def test_iniciar_mantem_placar_vivo_de_hoje(self) -> None:
        """Memória conferida hoje e à frente do banco continua valendo."""
        estado = main.auto_trader.get(self.user)
        estado.wins, estado.losses, estado.profit = 3, 1, 7.6
        main._ultimo_dia_do_placar[self.user] = main.brasilia_today()
        gateway = type(
            "G",
            (),
            {
                "robot_persistence": self._persistencia(estado.to_dict(), []),
                "auto_trader": main.auto_trader,
                "_ultimo_dia_do_placar": main._ultimo_dia_do_placar,
            },
        )()
        robot_runtime_main._hydrate_user_from_persistence(gateway, self.user, force=True)
        self.assertEqual(self._placar(), (3, 1, 7.6))

    def _placar(self) -> tuple:
        s = main.auto_trader.get(self.user)
        return (int(s.wins), int(s.losses), round(float(s.profit), 2))

    def _snapshot(self, wins: int, losses: int, profit: float, dia) -> dict:
        return {
            "ok": True,
            "data": {"wins": wins, "losses": losses, "profit": profit, "score_day": dia},
        }

    def test_gateway_adota_placar_de_hoje_mesmo_menor(self) -> None:
        """e3b52de7: gateway com 1x1 de ontem, runtime com 0x0 de hoje."""
        estado = main.auto_trader.get(self.user)
        estado.wins, estado.losses, estado.profit = 1, 1, -1.3
        hoje = main.brasilia_today().isoformat()
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(type(main.robot_bus), "enabled", new_callable=PropertyMock, return_value=True),
            patch.object(main.robot_bus, "get_snapshot", return_value=self._snapshot(0, 0, 0.0, hoje)),
            patch.object(main, "_load_persisted_session_score", return_value=(1, 1, -1.3)),
            patch.object(main, "get_session_score_authority", return_value=None),
        ):
            main.reconcile_session_score_on_gateway(self.user)
            self.assertEqual(self._placar(), (0, 0, 0.0))
            # Na leitura seguinte o banco de ontem não promove de volta.
            main.reconcile_session_score_on_gateway(self.user)
        self.assertEqual(self._placar(), (0, 0, 0.0))

    def test_snapshot_sem_dia_mantem_nunca_rebaixa(self) -> None:
        estado = main.auto_trader.get(self.user)
        estado.wins, estado.losses, estado.profit = 5, 3, 25.0
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(type(main.robot_bus), "enabled", new_callable=PropertyMock, return_value=True),
            patch.object(main.robot_bus, "get_snapshot", return_value=self._snapshot(2, 0, 17.4, None)),
            patch.object(main, "_load_persisted_session_score", return_value=None),
            patch.object(main, "get_session_score_authority", return_value=None),
        ):
            main.reconcile_session_score_on_gateway(self.user)
        self.assertEqual(self._placar(), (5, 3, 25.0))

    def test_persist_do_gateway_grava_o_placar_de_hoje(self) -> None:
        hoje = main.brasilia_today().isoformat()
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main.robot_bus, "get_snapshot", return_value=self._snapshot(1, 0, 4.2, hoje)),
            patch.object(main, "get_session_score_authority", return_value=None),
        ):
            payload, _ = main._protect_session_score_on_persist(
                self.user, {"wins": 1, "losses": 3, "profit": -10.8}, None
            )
        self.assertEqual((payload["wins"], payload["losses"], payload["profit"]), (1, 0, 4.2))

    def test_runtime_carimba_o_dia_no_snapshot(self) -> None:
        main._ultimo_dia_do_placar[self.user] = main.brasilia_today()
        with patch.object(main, "robot_runtime_mode", return_value="worker"):
            data = main.build_robot_payload(main.auto_trader.get(self.user), user_id=self.user)["data"]
        self.assertEqual(data["score_day"], main.brasilia_today().isoformat())


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
