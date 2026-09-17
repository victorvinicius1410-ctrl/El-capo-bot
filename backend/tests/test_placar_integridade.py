"""O placar do cliente não pode perder — nem inventar — operação real.

Cada teste aqui é um dos defeitos medidos na revisão de 15/09/2026
(backend/docs/PLACAR_DIAGNOSTICO_2026-09-15.md). Todos eram invisíveis: o log
dizia que a operação terminou e o placar não mexia. Um teste por caminho, para
a próxima regressão apontar o caminho exato em vez de "o placar sumiu".
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from backend import main
from backend.auto_trader import AutoTrader, utc_now


def operacao(
    order_id: str,
    *,
    result: str = "PENDING_RESULT",
    amount: float = 10.0,
    is_gale: bool = False,
    gale_step: int = 0,
    parent: str | None = None,
) -> dict:
    """Ordem como ``main.py`` a grava ao enviar para a corretora."""
    agora = utc_now()
    return {
        "order_id": order_id,
        "active": "EURUSD-OTC",
        "direction": "CALL",
        "amount": amount,
        "confidence": 90,
        "payout": 87.0,
        "timeframe": "M1",
        "expiration": "M1",
        "result": result,
        "sent_at": agora.isoformat(),
        "is_gale": is_gale,
        "gale_step": gale_step,
        "parent_order_id": parent,
        "original_amount": amount,
        "mode": "REAL",
    }


class GaleAbandonadoTests(unittest.TestCase):
    """§F3 — gale disparado cuja etapa nunca entra."""

    def setUp(self) -> None:
        self.trader = AutoTrader()
        self.user = "u-gale"
        estado = self.trader.get(self.user)
        estado.enabled = True
        estado.martingale_enabled = True
        estado.martingale_steps = 1

    def test_perna_perdida_conta_quando_a_etapa_nao_entra(self) -> None:
        self.trader.record_trade(self.user, operacao("1001"))
        finalizado, estado = self.trader.finish_trade(self.user, "1001", "LOSS", -10.0)
        self.assertFalse(finalizado, "a perna dispara o gale, não fecha o ciclo")
        self.assertEqual((estado.wins, estado.losses), (0, 0))

        fechado = self.trader.close_abandoned_gale(self.user)

        self.assertIsNotNone(fechado)
        self.assertEqual(fechado["final_result"], "LOSS")
        self.assertEqual((estado.wins, estado.losses), (0, 1))
        self.assertEqual(estado.profit, -10.0)

    def test_sem_gale_pendente_nao_inventa_derrota(self) -> None:
        self.trader.record_trade(self.user, operacao("1002"))
        self.trader.finish_trade(self.user, "1002", "WIN", 8.7)
        estado = self.trader.get(self.user)
        self.assertIsNone(self.trader.close_abandoned_gale(self.user))
        self.assertEqual((estado.wins, estado.losses), (1, 0))

    def test_gale_que_entra_e_vence_continua_contando_um_ciclo(self) -> None:
        self.trader.record_trade(self.user, operacao("1003"))
        self.trader.finish_trade(self.user, "1003", "LOSS", -10.0)
        self.trader.record_trade(
            self.user, operacao("1004", amount=20.0, is_gale=True, gale_step=1, parent="1003")
        )
        self.trader.finish_trade(self.user, "1004", "WIN", 17.4)
        estado = self.trader.get(self.user)
        self.assertEqual((estado.wins, estado.losses), (1, 0))
        # Reciclar o ciclo depois NÃO pode inventar um LOSS.
        self.assertIsNone(self.trader.close_abandoned_gale(self.user))
        self.assertEqual((estado.wins, estado.losses), (1, 0))


class PernaSuperadaNoRecalculoTests(unittest.TestCase):
    """§F3/§F2 — o recálculo contava a perna E o fechamento do mesmo ciclo."""

    def test_perna_referenciada_como_pai_nao_vale_ponto(self) -> None:
        agora = utc_now()
        perna = {
            **operacao("2001", result="LOSS"),
            "profit": -10.0,
            "finished_at": (agora - timedelta(minutes=2)).isoformat(),
            "cycle_result": None,
            "final_result": None,
        }
        fechamento = {
            **operacao("2002", result="WIN", amount=20.0, is_gale=True, gale_step=1, parent="2001"),
            "profit": 17.4,
            "finished_at": agora.isoformat(),
            "cycle_result": "WIN",
            "final_result": "WIN",
        }
        estado = AutoTrader().restore("u-recalculo", {}, [perna, fechamento])
        self.assertEqual((estado.wins, estado.losses), (1, 0))

    def test_linha_antiga_sem_cycle_result_continua_contando(self) -> None:
        """Linha do Shift+O e linha legada não têm `cycle_result` e são reais."""
        agora = utc_now()
        antiga = {
            **operacao("2003", result="WIN"),
            "profit": 8.7,
            "finished_at": agora.isoformat(),
        }
        antiga.pop("cycle_result", None)
        estado = AutoTrader().restore("u-antiga", {}, [antiga])
        self.assertEqual((estado.wins, estado.losses), (1, 0))


class ResultadoAtrasadoTests(unittest.TestCase):
    """§F5 — resultado que chega depois de o ciclo virar sumia em silêncio."""

    def test_resultado_de_ordem_antiga_conta_fora_do_ciclo(self) -> None:
        trader = AutoTrader()
        user = "u-atrasado"
        trader.get(user).enabled = True
        antiga = operacao("3001")
        trader.record_trade(user, antiga)
        # Ciclo reciclado por `waiting_result_stale` e ordem nova aberta.
        trader.reset_cycle_after_result(user)
        trader.record_trade(user, operacao("3002"))

        finalizado, _ = trader.finish_trade(user, "3001", "WIN", 8.7)
        self.assertFalse(finalizado, "não é mais a ordem do ciclo corrente")

        fechado = trader.count_late_result(user, antiga, "WIN", 8.7)
        estado = trader.get(user)
        self.assertIsNotNone(fechado)
        self.assertEqual((estado.wins, estado.losses), (1, 0))
        # Não pode encostar no ciclo corrente, que é de outra ordem.
        self.assertEqual(str(estado.last_trade.get("order_id")), "3002")

    def test_nao_conta_duas_vezes(self) -> None:
        trader = AutoTrader()
        user = "u-atrasado-2"
        trader.get(user).enabled = True
        antiga = operacao("3003")
        trader.record_trade(user, antiga)
        trader.finish_trade(user, "3003", "WIN", 8.7)
        self.assertIsNone(trader.count_late_result(user, antiga, "WIN", 8.7))
        estado = trader.get(user)
        self.assertEqual((estado.wins, estado.losses), (1, 0))


class ViradaDoDiaTests(unittest.TestCase):
    """§F2 — nada zerava o placar à meia-noite de Brasília."""

    def test_operacao_de_ontem_sai_do_placar_do_dia(self) -> None:
        trader = AutoTrader()
        user = "u-virada"
        ontem = utc_now() - timedelta(days=1)
        trader._histories[user] = [
            {
                **operacao("4001", result="WIN"),
                "profit": 8.7,
                "finished_at": ontem.isoformat(),
                "cycle_result": "WIN",
                "final_result": "WIN",
            }
        ]
        estado = trader.get(user)
        estado.wins, estado.losses, estado.profit = 1, 0, 8.7

        self.assertEqual(trader.recompute_session_score_for_today(user), (0, 0, 0.0))
        self.assertEqual((estado.wins, estado.losses), (0, 0))

    def test_resultado_de_hoje_permanece(self) -> None:
        trader = AutoTrader()
        user = "u-virada-2"
        trader._histories[user] = [
            {
                **operacao("4002", result="WIN"),
                "profit": 8.7,
                "finished_at": utc_now().isoformat(),
                "cycle_result": "WIN",
                "final_result": "WIN",
            }
        ]
        self.assertEqual(trader.recompute_session_score_for_today(user), (1, 0, 8.7))


class PersistNaoRebaixaTests(unittest.TestCase):
    """§F1 — o gateway gravava o placar dele por cima do placar vivo."""

    def setUp(self) -> None:
        self.user = "u-persist"
        main.auto_trader._states.pop(self.user, None)
        main._session_score_authority.pop(self.user, None)

    def _snapshot(self, wins: int, losses: int, profit: float) -> dict:
        return {"ok": True, "data": {"wins": wins, "losses": losses, "profit": profit}}

    def test_gateway_nao_grava_placar_em_branco_por_cima_do_vivo(self) -> None:
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main.robot_bus, "get_snapshot", return_value=self._snapshot(5, 3, 25.0)),
            patch.object(main, "get_session_score_authority", return_value=None),
        ):
            payload, _ = main._protect_session_score_on_persist(
                self.user, {"wins": 0, "losses": 0, "profit": 0.0}, None
            )
        self.assertEqual((payload["wins"], payload["losses"], payload["profit"]), (5, 3, 25.0))

    def test_baixa_intencional_passa_direto(self) -> None:
        """Reiniciar placar / excluir operação PRECISAM poder gravar zero."""
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main.robot_bus, "get_snapshot", return_value=self._snapshot(5, 3, 25.0)),
            patch.object(main, "get_session_score_authority", return_value=(0, 0, 0.0)),
        ):
            payload, _ = main._protect_session_score_on_persist(
                self.user, {"wins": 0, "losses": 0, "profit": 0.0}, None
            )
        self.assertEqual((payload["wins"], payload["losses"], payload["profit"]), (0, 0, 0.0))

    def test_ultima_operacao_nao_e_apagada(self) -> None:
        vivo = {"order_id": "5001", "result": "WIN"}
        snapshot = self._snapshot(1, 0, 8.7)
        snapshot["data"]["last_trade"] = vivo
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main.robot_bus, "get_snapshot", return_value=snapshot),
            patch.object(main, "get_session_score_authority", return_value=None),
        ):
            payload, last_trade = main._protect_session_score_on_persist(
                self.user, {"wins": 0, "losses": 0, "profit": 0.0}, None
            )
        self.assertEqual(last_trade, vivo)
        self.assertEqual(payload["last_trade"], vivo)

    def test_no_runtime_o_placar_dele_manda(self) -> None:
        """Em `worker` o dono é o próprio processo: nada é substituído."""
        with (
            patch.object(main, "robot_runtime_mode", return_value="worker"),
            patch.object(main.robot_bus, "get_snapshot", return_value=self._snapshot(9, 9, 99.0)),
        ):
            payload, _ = main._protect_session_score_on_persist(
                self.user, {"wins": 2, "losses": 1, "profit": 7.4}, None
            )
        self.assertEqual((payload["wins"], payload["losses"], payload["profit"]), (2, 1, 7.4))


class OrdemOrfaTests(unittest.TestCase):
    """§F6 — o monitor morre com o processo e a ordem fica sem resultado."""

    def test_persistencia_lista_ordem_pendente(self) -> None:
        import tempfile
        from pathlib import Path

        from backend.robot_persistence import SQLiteRobotPersistence

        with tempfile.TemporaryDirectory() as pasta:
            persistencia = SQLiteRobotPersistence(str(Path(pasta) / "t.db"))
            user = "11111111-1111-4111-8111-111111111111"
            persistencia.save_trade(user, operacao("6001"))
            persistencia.save_trade(
                user,
                {**operacao("6002", result="WIN"), "profit": 8.7, "finished_at": utc_now().isoformat()},
            )
            pendentes = persistencia.load_pending_trades(6)
        self.assertEqual([str(t.get("order_id")) for _, t in pendentes], ["6001"])


if __name__ == "__main__":
    unittest.main()
