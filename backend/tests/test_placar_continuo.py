"""Placar e stop só zeram no "Reiniciar placar" (regra do dono, 30/09/2026).

Antes a meia-noite de Brasília zerava o placar e o stop. Agora a janela vai do
último Reiniciar até agora e pode ter vários dias; estes testes cobrem o que
isso muda: a transição no deploy, o stop em dinheiro acima das 100 operações
que o robô guarda em memória, a exclusão no Shift+O de uma operação antiga, o
stop que continua batido depois da meia-noite e a leitura paginada do banco.
Ver backend/docs/PLACAR_OVERLAY.md §2026-09-30 (noite).
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend import main, placar_janela, robot_runtime_main
from backend.auto_trader import (
    HISTORY_MEMORY_LIMIT,
    STATUS_STOP_WIN_HIT,
    AutoTrader,
    resolve_robot_stop_reason,
    utc_now,
)
from backend.brasilia_time import BRASILIA_TZ
from backend.robot_persistence import (
    RestoreTrades,
    SQLiteRobotPersistence,
    SupabaseRobotPersistence,
)

USER = "81c49f33-0000-4000-8000-000000000001"
DEPLOY = datetime(2026, 10, 1, tzinfo=BRASILIA_TZ).astimezone(timezone.utc)


def _op(order_id: str, result: str, profit: float, fim: datetime) -> dict:
    return {
        "order_id": order_id,
        "active": "EURUSD-OTC",
        "direction": "CALL",
        "amount": 10.0,
        "result": result,
        "cycle_result": result,
        "final_result": result,
        "profit": profit,
        "sent_at": (fim - timedelta(minutes=1)).isoformat(),
        "finished_at": fim.isoformat(),
        "is_gale": False,
        "gale_step": 0,
        "parent_order_id": None,
    }


def _serie(n: int, inicio: datetime, *, prefixo: int = 100000) -> list[dict]:
    """``n`` operações alternando WIN (+8) e LOSS (-10), 5 min entre elas."""
    return [
        _op(
            str(prefixo + i),
            "WIN" if i % 2 == 0 else "LOSS",
            8.0 if i % 2 == 0 else -10.0,
            inicio + timedelta(minutes=5 * i),
        )
        for i in range(n)
    ]


def _payload(stop_reset_at: datetime | None) -> dict:
    payload = AutoTrader().get(USER).to_dict()
    payload["stop_reset_at"] = stop_reset_at.isoformat() if stop_reset_at else None
    return payload


class JanelaTests(unittest.TestCase):
    def test_sem_reiniciar_conta_desde_o_deploy(self) -> None:
        with patch.object(placar_janela, "PLACAR_CONTINUO_DESDE", DEPLOY):
            self.assertEqual(placar_janela.inicio_do_placar(None), DEPLOY)
            antigo = DEPLOY - timedelta(days=6)
            self.assertEqual(placar_janela.inicio_do_placar(antigo.isoformat()), DEPLOY)
            novo = DEPLOY + timedelta(days=3, hours=2)
            self.assertEqual(placar_janela.inicio_do_placar(novo), novo)

    def test_conta_no_placar(self) -> None:
        with patch.object(placar_janela, "PLACAR_CONTINUO_DESDE", DEPLOY):
            reset = DEPLOY + timedelta(days=1)
            self.assertTrue(placar_janela.conta_no_placar(reset + timedelta(days=5), reset))
            self.assertFalse(placar_janela.conta_no_placar(reset - timedelta(seconds=1), reset))
            self.assertFalse(placar_janela.conta_no_placar(None, reset))
            self.assertFalse(placar_janela.conta_no_placar("lixo", reset))

    def test_carimbo_da_regra(self) -> None:
        with patch.object(placar_janela, "PLACAR_CONTINUO_DESDE", DEPLOY):
            self.assertTrue(placar_janela.placar_da_regra_atual("continuo"))
            # Gravado no dia do deploy, antes da troca: já é o placar da regra.
            self.assertTrue(placar_janela.placar_da_regra_atual("2026-10-01"))
            self.assertFalse(placar_janela.placar_da_regra_atual("2026-09-30"))
            self.assertFalse(placar_janela.placar_da_regra_atual(None))


class TransicaoTests(unittest.TestCase):
    def test_quem_reiniciou_ha_6_dias_nao_herda_os_dias_antigos(self) -> None:
        """81c49f33 reiniciou 6,4 dias antes do deploy: o placar é só o do dia."""
        deploy = utc_now() - timedelta(hours=10)
        antes = _serie(20, deploy - timedelta(days=5), prefixo=1000)
        depois = _serie(4, deploy + timedelta(hours=1), prefixo=2000)
        with patch.object(placar_janela, "PLACAR_CONTINUO_DESDE", deploy):
            estado = AutoTrader().restore(
                USER,
                _payload(deploy - timedelta(days=6)),
                RestoreTrades([*antes, *depois], authoritative=True),
            )
        self.assertEqual((estado.wins, estado.losses, round(estado.profit, 2)), (2, 2, -4.0))


class AcumuladoDoStopTests(unittest.TestCase):
    """O robô guarda 100 operações em memória; a janela passa disso."""

    def setUp(self) -> None:
        self.reset = utc_now() - timedelta(days=3)
        self.ops = _serie(150, self.reset + timedelta(minutes=1))
        self.trader = AutoTrader()
        self.trader.restore(
            USER, _payload(self.reset), RestoreTrades(list(self.ops), authoritative=True)
        )

    def test_restore_conta_as_150(self) -> None:
        estado = self.trader.get(USER)
        self.assertEqual((estado.wins, estado.losses), (75, 75))
        self.assertEqual(round(estado.profit, 2), -150.0)
        self.assertEqual(len(self.trader._histories[USER]), HISTORY_MEMORY_LIMIT)
        totais = self.trader.management_totals(USER)
        self.assertEqual(totais, {"gross_profit": 600.0, "gross_loss": 750.0, "net_profit": -150.0})

    def test_operacoes_novas_empurram_as_velhas_para_o_acumulado(self) -> None:
        novas = _serie(30, utc_now() - timedelta(minutes=1), prefixo=900000)
        self.trader.replace_history(USER, [*self.trader._histories[USER], *novas])
        self.assertEqual(len(self.trader._histories[USER]), HISTORY_MEMORY_LIMIT)
        self.assertEqual(self.trader.management_totals(USER)["net_profit"], -180.0)

    def test_shift_o_numa_operacao_que_saiu_da_memoria(self) -> None:
        antiga = self.ops[0]  # WIN +8, fora das 100 últimas
        info = {k: antiga[k] for k in ("result", "profit", "finished_at")}
        self.trader.mark_trade_removed(USER, antiga["order_id"], info)
        self.assertEqual(self.trader.management_totals(USER)["net_profit"], -158.0)
        # Repetir a exclusão não tira o dinheiro duas vezes.
        self.trader.mark_trade_removed(USER, antiga["order_id"], info)
        self.assertEqual(self.trader.management_totals(USER)["net_profit"], -158.0)

    def test_shift_o_numa_operacao_da_memoria(self) -> None:
        recente = self.ops[-1]  # LOSS -10, está nas 100 últimas
        self.trader.mark_trade_removed(USER, recente["order_id"], {"result": "LOSS", "profit": -10.0})
        self.assertEqual(self.trader.management_totals(USER)["net_profit"], -140.0)

    def test_reiniciar_placar_zera_o_acumulado(self) -> None:
        self.trader.reset_score(USER)
        self.assertEqual(self.trader.management_totals(USER)["net_profit"], 0.0)

    def test_runtime_repassa_o_que_o_gateway_mandou(self) -> None:
        antiga = self.ops[2]  # WIN +8, fora da memória
        gateway = type("G", (), {"auto_trader": self.trader, "pattern_memory": None})()
        robot_runtime_main.apagar_operacao_do_runtime(
            gateway,
            USER,
            {k: antiga[k] for k in ("order_id", "result", "profit", "finished_at")},
        )
        self.assertEqual(self.trader.management_totals(USER)["net_profit"], -158.0)


class StopDepoisDaMeiaNoiteTests(unittest.TestCase):
    def setUp(self) -> None:
        main.auto_trader._states.pop(USER, None)

    def test_gestao_soma_os_dias_desde_o_reiniciar(self) -> None:
        estado = main.auto_trader.get(USER)
        estado.stop_reset_at = utc_now() - timedelta(days=2)
        estado.stop_win_mode = "money"
        estado.stop_win = 10.0
        historico = [
            _op("7001", "WIN", 50.0, utc_now() - timedelta(days=3)),  # antes do Reiniciar
            _op("7002", "WIN", 8.0, utc_now() - timedelta(days=1)),  # ontem
            _op("7003", "WIN", 4.0, utc_now() - timedelta(minutes=3)),  # hoje
        ]
        with patch.object(main, "load_daily_history_cached", return_value=historico):
            resumo = main.build_management_summary(USER, estado)
            self.assertEqual(resumo["net_profit"], 12.0)
            self.assertEqual(resumo["stop_reason"], STATUS_STOP_WIN_HIT)
            self.assertEqual(resumo["window_start"], estado.stop_reset_at.isoformat())
            # Iniciar continua travado até o Reiniciar, mesmo no dia seguinte.
            self.assertEqual(main.daily_stop_reason(USER, estado), "STOP_WIN_HIT")

    def test_stop_em_operacoes_conta_o_placar_de_varios_dias(self) -> None:
        trader = AutoTrader()
        reset = utc_now() - timedelta(days=2)
        ops = [
            _op("7101", "LOSS", -10.0, reset + timedelta(hours=1)),
            _op("7102", "LOSS", -10.0, reset + timedelta(days=1)),
        ]
        payload = _payload(reset)
        payload.update({"stop_loss_mode": "operations", "stop_loss_operations": 2})
        estado = trader.restore(USER, payload, RestoreTrades(ops, authoritative=True))
        self.assertEqual(resolve_robot_stop_reason(estado), "STOP_LOSS_HIT")


class GatewayMandaFimDaOperacaoTests(unittest.TestCase):
    def test_removed_trade_leva_finished_at(self) -> None:
        main.auto_trader._states.pop(USER, None)
        apagada = _op("7201", "LOSS", -10.0, utc_now() - timedelta(days=1))
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main, "persist_robot"),
            patch.object(main, "publish_robot_control_snapshot"),
            patch.object(main, "adopt_live_session_score_if_blank"),
            patch.object(main.robot_bus, "set_score_authority"),
            patch.object(main.robot_bus, "publish_command") as comando,
        ):
            main.apply_marketing_score_removal(USER, apagada)
        self.assertEqual(
            comando.call_args.kwargs["removed_trade"]["finished_at"], apagada["finished_at"]
        )


class LeituraDoBancoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._pasta = tempfile.TemporaryDirectory()
        self.persist = SQLiteRobotPersistence(str(Path(self._pasta.name) / "t.db"))

    def tearDown(self) -> None:
        self._pasta.cleanup()

    def test_restore_le_desde_o_reiniciar_de_dias_atras(self) -> None:
        reset = utc_now() - timedelta(days=4)
        for op in (
            _op("7301", "WIN", 8.0, reset - timedelta(hours=1)),
            _op("7302", "WIN", 8.0, reset + timedelta(days=1)),
            _op("7303", "LOSS", -10.0, utc_now() - timedelta(minutes=2)),
        ):
            self.persist.save_trade_history(USER, op)
        trades = self.persist.load_trades_for_restore(USER, stop_reset_at=reset.isoformat())
        self.assertEqual([t["order_id"] for t in trades], ["7302", "7303"])
        self.assertTrue(trades.authoritative)

    def test_restore_sempre_le_o_dia_de_hoje(self) -> None:
        """Reiniciar há 1 min: a memória segue com as operações do dia."""
        self.persist.save_trade_history(USER, _op("7401", "WIN", 8.0, utc_now() - timedelta(minutes=30)))
        reset = utc_now() - timedelta(minutes=1)
        trades = self.persist.load_trades_for_restore(USER, stop_reset_at=reset)
        self.assertEqual([t["order_id"] for t in trades], ["7401"])
        estado = AutoTrader().restore(USER, _payload(reset), trades)
        self.assertEqual((estado.wins, estado.losses), (0, 0))


class _SupabaseFalso(SupabaseRobotPersistence):
    def __init__(self, paginas: list[list[dict]]) -> None:  # noqa: D107 - sem rede
        self.paginas = list(paginas)
        self.chamadas: list[tuple[str, dict]] = []

    def _request(self, method, path, json=None, extra_headers=None, return_response=False):
        self.chamadas.append((path, dict(extra_headers or {})))
        return self.paginas.pop(0) if self.paginas else []


class SupabasePaginadoTests(unittest.TestCase):
    def test_clientes_ativos_pula_para_o_proximo_cliente(self) -> None:
        pagina_cheia = [{"user_id": "a"}] * 400 + [{"user_id": "b"}] * 600
        banco = _SupabaseFalso([pagina_cheia, [{"user_id": "c"}, {"user_id": "d"}]])
        ids = banco.load_user_ids_with_history_since(DEPLOY)
        self.assertEqual(ids, {"a", "b", "c", "d"})
        self.assertEqual(len(banco.chamadas), 2)
        self.assertIn("user_id=gt.b", banco.chamadas[1][0])
        self.assertIn("order=user_id.asc", banco.chamadas[1][0])

    def test_historico_desde_pagina_ate_o_fim(self) -> None:
        linha = {"order_id": "1", "result": "WIN", "profit": 8.0, "finished_at": utc_now().isoformat()}
        banco = _SupabaseFalso([[linha] * 1000, [linha] * 1000, [linha] * 3])
        itens = banco.load_trade_history_since(USER, DEPLOY)
        self.assertEqual(len(itens), 2003)
        self.assertEqual(
            [h["Range"] for _, h in banco.chamadas], ["0-999", "1000-1999", "2000-2999"]
        )
        self.assertIn("order=finished_at.desc,id.desc", banco.chamadas[0][0])


if __name__ == "__main__":
    unittest.main()
