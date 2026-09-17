"""Gale por etapas: o robô tem que dobrar exatamente o que foi configurado.

`martingale_steps` é a "Quantidade de Gales" do painel: 1 = dobra uma única
vez, 2 = dobra duas vezes, e assim por diante (até 10). Cada etapa dobra o
valor da etapa anterior pelo `martingale_multiplier`.

Os testes enviam a ordem do gale do mesmo jeito que o runtime
(`backend/main.py`): valor = `state.gale_amount`, e `is_gale`/`gale_step`/
`gale_amount` copiados do `pending_signal`. Assim o contrato entre o
`AutoTrader` e o laço de ordens fica coberto de verdade.
"""

from __future__ import annotations

import unittest

from backend.auto_trader import AutoTrader

PAYOUT = 88.0


def _win_profit(amount: float) -> float:
    return round(amount * PAYOUT / 100, 2)


class GaleMultiStepTests(unittest.TestCase):
    def _start(
        self,
        user_id: str,
        *,
        steps: int,
        multiplier: float = 2.0,
        entry: float = 10.0,
        enabled: bool = True,
    ) -> AutoTrader:
        trader = AutoTrader()
        state = trader.start(user_id)
        state.martingale_enabled = enabled
        state.martingale_steps = steps
        state.martingale_multiplier = multiplier
        state.entry_value = entry
        # Stops fora do caminho: quem corta o gale aqui é só a contagem de etapas.
        state.stop_win = 1_000_000
        state.stop_loss = 1_000_000
        return trader

    def _send_entry(self, trader: AutoTrader, user_id: str, order_id: str) -> float:
        """Envia a entrada original com `entry_value`."""
        state = trader.get(user_id)
        amount = float(state.entry_value)
        trader.record_trade(
            user_id,
            {
                "order_id": order_id,
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": amount,
                "confidence": 90,
                "payout": PAYOUT,
                "result": "PENDING_RESULT",
            },
        )
        return amount

    def _send_pending_gale(self, trader: AutoTrader, user_id: str, order_id: str) -> float:
        """Replica `backend/main.py`: monta a ordem do gale do sinal pendente."""
        state = trader.get(user_id)
        signal = dict(state.pending_signal or {})
        self.assertTrue(signal.get("is_gale"), "sinal pendente não é de gale")
        amount = float(state.gale_amount)
        trader.record_trade(
            user_id,
            {
                "order_id": order_id,
                "active": str(signal.get("symbol") or "EURUSD-OTC"),
                "direction": str(signal.get("direction") or "CALL"),
                "amount": amount,
                "confidence": 90,
                "payout": PAYOUT,
                "result": "PENDING_RESULT",
                "is_gale": True,
                "gale_step": int(signal.get("gale_step") or 1),
                "gale_amount": float(signal.get("gale_amount") or amount),
                "parent_order_id": signal.get("parent_order_id"),
            },
        )
        return amount

    def test_um_gale_dobra_uma_unica_vez(self) -> None:
        trader = self._start("u-g1", steps=1)
        self._send_entry(trader, "u-g1", "1001")

        finalized, state = trader.finish_trade("u-g1", "1001", "LOSS", -10.0)
        self.assertFalse(finalized, "LOSS com gale pendente não fecha o ciclo")
        self.assertTrue(state.gale_pending)
        self.assertEqual(state.gale_step, 1)
        self.assertEqual(state.gale_amount, 20.0)

        amount = self._send_pending_gale(trader, "u-g1", "1002")
        self.assertEqual(amount, 20.0)

        finalized, state = trader.finish_trade("u-g1", "1002", "LOSS", -20.0)
        self.assertTrue(finalized, "1 gale configurado: o ciclo fecha na etapa 1")
        self.assertFalse(state.gale_pending, "não pode existir gale 2")
        self.assertEqual(state.losses, 1, "o ciclo inteiro conta 1 LOSS")
        self.assertEqual(state.wins, 0)
        self.assertEqual(state.profit, -30.0, "10 + 20 perdidos")

    def test_dois_gales_dobram_duas_vezes(self) -> None:
        trader = self._start("u-g2", steps=2)
        self._send_entry(trader, "u-g2", "1001")

        finalized, state = trader.finish_trade("u-g2", "1001", "LOSS", -10.0)
        self.assertFalse(finalized)
        self.assertEqual(state.gale_step, 1)
        self.assertEqual(self._send_pending_gale(trader, "u-g2", "1002"), 20.0)

        finalized, state = trader.finish_trade("u-g2", "1002", "LOSS", -20.0)
        self.assertFalse(finalized, "ainda falta a etapa 2")
        self.assertTrue(state.gale_pending)
        self.assertEqual(state.gale_step, 2)
        self.assertEqual(state.gale_amount, 40.0, "dobra o valor da etapa anterior")
        self.assertEqual(self._send_pending_gale(trader, "u-g2", "1003"), 40.0)

        finalized, state = trader.finish_trade("u-g2", "1003", "LOSS", -40.0)
        self.assertTrue(finalized)
        self.assertFalse(state.gale_pending, "2 gales configurados: para na etapa 2")
        self.assertEqual(state.losses, 1)
        self.assertEqual(state.profit, -70.0, "10 + 20 + 40 perdidos")

    def test_tres_gales_dobram_tres_vezes(self) -> None:
        trader = self._start("u-g3", steps=3)
        self._send_entry(trader, "u-g3", "1001")
        trader.finish_trade("u-g3", "1001", "LOSS", -10.0)

        self.assertEqual(self._send_pending_gale(trader, "u-g3", "1002"), 20.0)
        trader.finish_trade("u-g3", "1002", "LOSS", -20.0)

        self.assertEqual(self._send_pending_gale(trader, "u-g3", "1003"), 40.0)
        trader.finish_trade("u-g3", "1003", "LOSS", -40.0)

        state = trader.get("u-g3")
        self.assertEqual(state.gale_step, 3)
        self.assertEqual(self._send_pending_gale(trader, "u-g3", "1004"), 80.0)

        finalized, state = trader.finish_trade("u-g3", "1004", "LOSS", -80.0)
        self.assertTrue(finalized)
        self.assertFalse(state.gale_pending, "3 gales configurados: para na etapa 3")
        self.assertEqual(state.losses, 1)
        self.assertEqual(state.profit, -150.0, "10 + 20 + 40 + 80 perdidos")

    def test_win_no_meio_encerra_a_sequencia_e_soma_o_ciclo(self) -> None:
        trader = self._start("u-win", steps=3)
        self._send_entry(trader, "u-win", "1001")
        trader.finish_trade("u-win", "1001", "LOSS", -10.0)
        self._send_pending_gale(trader, "u-win", "1002")
        trader.finish_trade("u-win", "1002", "LOSS", -20.0)
        amount = self._send_pending_gale(trader, "u-win", "1003")
        self.assertEqual(amount, 40.0)

        finalized, state = trader.finish_trade("u-win", "1003", "WIN", _win_profit(amount))
        self.assertTrue(finalized)
        self.assertFalse(state.gale_pending, "WIN encerra a sequência")
        self.assertEqual(state.wins, 1)
        self.assertEqual(state.losses, 0, "o ciclo terminou em WIN")
        # -10 -20 +35.20
        self.assertEqual(state.profit, 5.2)

    def test_empate_no_gale_nao_abre_nova_etapa(self) -> None:
        trader = self._start("u-draw", steps=3)
        self._send_entry(trader, "u-draw", "1001")
        trader.finish_trade("u-draw", "1001", "LOSS", -10.0)
        self._send_pending_gale(trader, "u-draw", "1002")

        finalized, state = trader.finish_trade("u-draw", "1002", "DRAW", 0.0)
        self.assertTrue(finalized)
        self.assertFalse(state.gale_pending, "empate devolve a stake e encerra")
        self.assertEqual(state.wins, 0)
        self.assertEqual(state.losses, 0)
        self.assertEqual(state.profit, -10.0, "só a perda da entrada original")

    def test_multiplicador_diferente_de_dois(self) -> None:
        trader = self._start("u-mult", steps=2, multiplier=1.5)
        self._send_entry(trader, "u-mult", "1001")
        trader.finish_trade("u-mult", "1001", "LOSS", -10.0)
        self.assertEqual(self._send_pending_gale(trader, "u-mult", "1002"), 15.0)
        trader.finish_trade("u-mult", "1002", "LOSS", -15.0)
        self.assertEqual(self._send_pending_gale(trader, "u-mult", "1003"), 22.5)

    def test_sinal_do_gale_carrega_a_etapa_para_o_painel(self) -> None:
        trader = self._start("u-label", steps=2)
        self._send_entry(trader, "u-label", "1001")
        trader.finish_trade("u-label", "1001", "LOSS", -10.0)
        signal = dict(trader.get("u-label").pending_signal or {})
        self.assertEqual(signal.get("gale_step"), 1)
        self.assertEqual(signal.get("parent_order_id"), "1001")

        self._send_pending_gale(trader, "u-label", "1002")
        trader.finish_trade("u-label", "1002", "LOSS", -20.0)
        signal = dict(trader.get("u-label").pending_signal or {})
        self.assertEqual(signal.get("gale_step"), 2)
        self.assertEqual(signal.get("parent_order_id"), "1002", "o pai da etapa 2 é a etapa 1")
        self.assertEqual(signal.get("gale_amount"), 40.0)

    def test_narracao_diz_a_etapa_do_gale(self) -> None:
        trader = self._start("u-voz", steps=2)
        self._send_entry(trader, "u-voz", "1001")
        trader.finish_trade("u-voz", "1001", "LOSS", -10.0)
        self._send_pending_gale(trader, "u-voz", "1002")
        trader.finish_trade("u-voz", "1002", "LOSS", -20.0)
        amount = self._send_pending_gale(trader, "u-voz", "1003")

        trader.finish_trade("u-voz", "1003", "WIN", _win_profit(amount))
        payload = trader.get("u-voz").to_dict()
        self.assertEqual(payload["voice_message"], "WIN no Gale 2")

    def test_gale_desligado_nao_dispara(self) -> None:
        trader = self._start("u-off", steps=3, enabled=False)
        self._send_entry(trader, "u-off", "1001")

        finalized, state = trader.finish_trade("u-off", "1001", "LOSS", -10.0)
        self.assertTrue(finalized, "sem gale o LOSS fecha o ciclo na hora")
        self.assertFalse(state.gale_pending)
        self.assertEqual(state.losses, 1)
        self.assertEqual(state.profit, -10.0)

    def test_etapas_acima_do_limite_nao_estouram(self) -> None:
        """`martingale_steps` fora de 1..10 não pode virar sequência infinita."""
        trader = self._start("u-clamp", steps=99)
        self._send_entry(trader, "u-clamp", "1001")
        trader.finish_trade("u-clamp", "1001", "LOSS", -10.0)

        order = 1
        amount = float(trader.get("u-clamp").entry_value)
        while trader.get("u-clamp").gale_pending:
            order += 1
            amount = self._send_pending_gale(trader, "u-clamp", f"10{order:02d}")
            trader.finish_trade("u-clamp", f"10{order:02d}", "LOSS", -amount)
            self.assertLessEqual(order, 12, "sequência de gale não pode ser infinita")

        self.assertEqual(trader.get("u-clamp").gale_step, 10, "teto de 10 etapas")

    def test_cada_etapa_recebe_o_orcamento_de_tentativas_inteiro(self) -> None:
        """Tentativas da entrada anterior não podem barrar o envio do gale.

        O laço de ordens corta em `MAX_ORDER_ATTEMPTS_PER_CYCLE` (3) e
        `prepare_cycle` sai antes de zerar o contador quando há sinal pendente.
        """
        trader = self._start("u-attempts", steps=3)
        self._send_entry(trader, "u-attempts", "1001")
        state = trader.get("u-attempts")
        # Entrada original só saiu na 3ª tentativa (ativo recusado antes).
        state.order_attempts = 3

        trader.finish_trade("u-attempts", "1001", "LOSS", -10.0)
        self.assertEqual(trader.get("u-attempts").order_attempts, 0, "G1 começa do zero")

        self._send_pending_gale(trader, "u-attempts", "1002")
        trader.get("u-attempts").order_attempts = 3
        trader.finish_trade("u-attempts", "1002", "LOSS", -20.0)
        self.assertEqual(trader.get("u-attempts").order_attempts, 0, "G2 começa do zero")
        self.assertTrue(trader.get("u-attempts").gale_pending)

    def test_stop_loss_corta_a_sequencia_do_gale(self) -> None:
        trader = self._start("u-stop", steps=5)
        state = trader.get("u-stop")
        state.stop_loss = 25.0
        state.stop_loss_mode = "money"
        self._send_entry(trader, "u-stop", "1001")
        trader.finish_trade("u-stop", "1001", "LOSS", -10.0)
        self._send_pending_gale(trader, "u-stop", "1002")

        # Perder a etapa 1 já estoura o stop loss (10 + 20 = 30 > 25):
        # a etapa 2 não pode ser aberta.
        finalized, state = trader.finish_trade("u-stop", "1002", "LOSS", -20.0)
        self.assertTrue(finalized)
        self.assertFalse(state.gale_pending, "stop loss tem prioridade sobre o gale")


if __name__ == "__main__":
    unittest.main()
