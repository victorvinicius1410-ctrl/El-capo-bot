"""Placar como contador único no banco (fase sombra).

Pedido do dono em 01/10/2026: um contador que soma cada resultado e zera no
"Reiniciar placar", no lugar de 5 cópias do placar que discordavam entre si.
A regra que estes testes guardam: depois de QUALQUER sequência de resultados,
o contador no banco é exatamente o placar e o stop que o robô calcula hoje.
Ver backend/docs/PLACAR_CONTADOR.md.
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

from backend.auto_trader import AutoTrader, utc_now
from backend.placar_contador import ContadorDoPlacar
from backend.robot_persistence import SQLiteRobotPersistence

USER = "c0ffee00-0000-4000-8000-000000000001"


def _ordem(order_id: str, *, amount: float = 10.0, gale_step: int = 0, parent: str | None = None,
           live: bool = False) -> dict:
    return {
        "order_id": order_id,
        "active": "EURUSD-OTC",
        "direction": "CALL",
        "amount": amount,
        "payout": 87.0,
        "timeframe": "M1",
        "result": "PENDING_RESULT",
        "sent_at": utc_now().isoformat(),
        "is_gale": gale_step > 0,
        "gale_step": gale_step,
        "parent_order_id": parent,
        "original_amount": amount,
        "mode": "REAL",
        "live_mode_active": live,
    }


class _ComBanco(unittest.TestCase):
    def setUp(self) -> None:
        self._pasta = tempfile.TemporaryDirectory()
        self.banco = SQLiteRobotPersistence(str(Path(self._pasta.name) / "t.db"))

    def tearDown(self) -> None:
        self._pasta.cleanup()

    def linha(self, user: str = USER) -> dict:
        return (self.banco.placar_ler([user]) or {}).get(user) or {}


class FuncoesDoBancoTests(_ComBanco):
    """Mesma semântica das funções do Postgres (testadas também num Postgres 16)."""

    def test_mesma_ordem_conta_uma_vez(self) -> None:
        self.banco.placar_lancar(USER, "100", wins=1, losses=0, profit=8.7, dinheiro=8.7)
        self.banco.placar_lancar(USER, "100", wins=1, losses=0, profit=8.7, dinheiro=8.7)
        linha = self.linha()
        self.assertEqual((linha["wins"], linha["losses"], linha["profit"]), (1, 0, 8.7))
        self.assertEqual(linha["stop_ganho"], 8.7)

    def test_gale_perna_ciclo_e_apagar(self) -> None:
        self.banco.placar_lancar(USER, "900", wins=0, losses=0, profit=0, dinheiro=-10)
        self.banco.placar_lancar(USER, "900#ciclo", wins=0, losses=1, profit=-10, dinheiro=0)
        self.assertEqual((self.linha()["losses"], self.linha()["stop_perda"]), (1, 10.0))
        self.banco.placar_apagar(USER, "900")
        linha = self.linha()
        self.assertEqual((linha["losses"], linha["profit"], linha["stop_perda"]), (0, 0.0, 0.0))
        self.banco.placar_apagar(USER, "900")  # repetir não muda nada
        self.assertEqual(self.linha()["versao"], linha["versao"])

    def test_vitrine_nao_entra_no_stop(self) -> None:
        self.banco.placar_lancar(USER, "100", wins=1, losses=0, profit=8.7, dinheiro=8.7)
        self.banco.placar_vitrine(USER, 5, 1, 40.0)
        linha = self.linha()
        self.assertEqual((linha["wins"], linha["losses"], linha["profit"]), (5, 1, 40.0))
        self.assertEqual((linha["stop_wins"], linha["stop_losses"], linha["stop_ganho"]), (1, 0, 8.7))

    def test_reiniciar_abre_periodo_novo(self) -> None:
        self.banco.placar_lancar(USER, "100", wins=1, losses=0, profit=8.7, dinheiro=8.7)
        self.banco.placar_reiniciar(USER)
        self.assertEqual((self.linha()["wins"], self.linha()["stop_ganho"]), (0, 0.0))
        # A mesma ordem num período novo conta de novo (o período é outro).
        self.banco.placar_lancar(USER, "100", wins=1, losses=0, profit=8.7, dinheiro=8.7)
        self.assertEqual(self.linha()["wins"], 1)

    def test_semear_nao_sobrescreve(self) -> None:
        inicio = utc_now() - timedelta(hours=2)
        self.banco.placar_semear(USER, inicio, wins=3, losses=2, profit=5.5, stop_wins=3,
                                 stop_losses=2, stop_ganho=26.1, stop_perda=20.6)
        self.banco.placar_semear(USER, inicio, wins=9, losses=9, profit=9, stop_wins=9,
                                 stop_losses=9, stop_ganho=9, stop_perda=9)
        self.assertEqual((self.linha()["wins"], self.linha()["losses"]), (3, 2))


class EscritorTests(_ComBanco):
    def test_desligado_nao_grava(self) -> None:
        contador = ContadorDoPlacar(self.banco, "off")
        contador.lancar(USER, "1", wins=1, profit=8.7, dinheiro=8.7)
        contador.esperar_fila()
        self.assertEqual(self.linha(), {})

    def test_sem_tabela_avisa_uma_vez_e_desliga(self) -> None:
        erro = Exception("404")
        erro.response = MagicMock(status_code=404, text='{"code":"PGRST202"}')
        banco = MagicMock()
        banco.placar_lancar.side_effect = erro
        contador = ContadorDoPlacar(banco, "sombra", dormir=lambda _s: None)
        with self.assertLogs("backend-gateway", level=logging.WARNING) as logs:
            contador.lancar(USER, "1", wins=1, profit=8.7, dinheiro=8.7)
            contador.esperar_fila()
            contador.lancar(USER, "2", wins=1, profit=8.7, dinheiro=8.7)
            contador.esperar_fila()
        self.assertEqual(sum("PLACAR_CONTADOR_SEM_TABELA" in m for m in logs.output), 1)
        self.assertFalse(contador.ligado)
        self.assertEqual(banco.placar_lancar.call_count, 1)

    def test_falha_passageira_tenta_de_novo(self) -> None:
        banco = MagicMock()
        banco.placar_lancar.side_effect = [ConnectionError("x"), ConnectionError("y"), {"wins": 1}]
        contador = ContadorDoPlacar(banco, "sombra", dormir=lambda _s: None)
        contador.lancar(USER, "1", wins=1, profit=8.7, dinheiro=8.7)
        contador.esperar_fila()
        self.assertEqual((banco.placar_lancar.call_count, contador.gravados, contador.perdidos), (3, 1, 0))

    def test_falha_que_nao_passa_vira_erro_no_log(self) -> None:
        banco = MagicMock()
        banco.placar_lancar.side_effect = ConnectionError("fora")
        contador = ContadorDoPlacar(banco, "sombra", dormir=lambda _s: None)
        with self.assertLogs("backend-gateway", level=logging.ERROR) as logs:
            contador.lancar(USER, "1", wins=1, profit=8.7, dinheiro=8.7)
            contador.esperar_fila()
        self.assertIn("PLACAR_CONTADOR_PERDEU", logs.output[0])
        self.assertEqual(contador.perdidos, 1)

    def test_ordem_de_chegada_e_respeitada(self) -> None:
        contador = ContadorDoPlacar(self.banco, "sombra")
        contador.lancar(USER, "1", wins=1, profit=8.7, dinheiro=8.7)
        contador.reiniciar(USER)
        contador.lancar(USER, "2", losses=1, profit=-10, dinheiro=-10)
        contador.esperar_fila()
        self.assertEqual((self.linha()["wins"], self.linha()["losses"]), (0, 1))


class ContadorIgualAoPlacarTests(_ComBanco):
    """A invariante da fase sombra: contador == placar e stop do robô."""

    def setUp(self) -> None:
        super().setUp()
        self.contador = ContadorDoPlacar(self.banco, "sombra")
        self.trader = AutoTrader()
        self.trader.contador_do_placar = self.contador
        estado = self.trader.get(USER)
        estado.enabled = True
        estado.martingale_enabled = True
        estado.martingale_steps = 2
        # Stop alto: com o padrão (R$30) a 2ª perda do gale bate o Stop Loss e o
        # robô fecha o ciclo em vez de abrir o G2 — certo, mas não é o caso aqui.
        estado.stop_loss = 10_000.0
        estado.stop_win = 10_000.0
        self.banco.placar_reiniciar(USER)

    def conferir(self) -> None:
        self.contador.esperar_fila()
        estado = self.trader.get(USER)
        totais = self.trader.management_totals(USER)
        linha = self.linha()
        self.assertEqual(
            (linha["wins"], linha["losses"], round(linha["profit"], 2)),
            (estado.wins, estado.losses, round(estado.profit, 2)),
            "placar exibido",
        )
        self.assertEqual(
            (round(linha["stop_ganho"], 2), round(linha["stop_perda"], 2)),
            (totais["gross_profit"], totais["gross_loss"]),
            "stop em dinheiro",
        )

    def operar(self, order_id: str, resultado: str, lucro: float, **kw) -> None:
        self.trader.record_trade(USER, _ordem(order_id, **kw))
        self.trader.finish_trade(USER, order_id, resultado, lucro)

    def test_win_loss_e_empate(self) -> None:
        self.operar("1", "WIN", 8.7)
        self.operar("2", "LOSS", -10.0)
        self.operar("3", "DRAW", 0.0)
        self.conferir()

    def test_gale_que_vence(self) -> None:
        self.operar("10", "LOSS", -10.0)
        self.operar("11", "LOSS", -20.0, amount=20.0, gale_step=1, parent="10")
        self.operar("12", "WIN", 34.8, amount=40.0, gale_step=2, parent="11")
        self.conferir()
        self.assertEqual((self.linha()["wins"], self.linha()["losses"]), (1, 0))

    def test_gale_abandonado(self) -> None:
        self.operar("20", "LOSS", -10.0)
        self.trader.close_abandoned_gale(USER)
        self.conferir()
        self.assertEqual(self.linha()["losses"], 1)

    def test_resultado_atrasado(self) -> None:
        antiga = _ordem("30")
        self.trader.record_trade(USER, antiga)
        self.trader.reset_cycle_after_result(USER)
        self.operar("31", "WIN", 8.7)
        self.trader.count_late_result(USER, antiga, "LOSS", -10.0)
        self.trader.count_late_result(USER, antiga, "LOSS", -10.0)  # repetido
        self.conferir()

    def test_loss_oculto_do_live(self) -> None:
        self.operar("40", "WIN", 8.7, live=True)
        self.operar("41", "LOSS", -10.0, live=True)
        self.conferir()
        self.assertEqual((self.linha()["wins"], self.linha()["losses"]), (1, 0))

    def test_sequencia_longa_misturada(self) -> None:
        for i in range(40):
            base = 1000 + i * 10
            if i % 5 == 0:
                self.operar(str(base), "LOSS", -10.0)
                self.operar(str(base + 1), "WIN", 17.4, amount=20.0, gale_step=1, parent=str(base))
            elif i % 7 == 0:
                self.operar(str(base), "LOSS", -10.0)
                self.trader.close_abandoned_gale(USER)
            else:
                self.operar(str(base), "WIN" if i % 2 else "LOSS", 8.7 if i % 2 else -10.0)
        self.conferir()
        # 150 > 100 operações em memória: o stop do contador não esquece as antigas.
        self.assertGreater(self.linha()["versao"], 40)


if __name__ == "__main__":
    unittest.main()
