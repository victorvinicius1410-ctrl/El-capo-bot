"""Empate (DRAW) é resultado final e tem que entrar no Histórico.

Incidente de 08/09/2026, conta de marketing com o modo LIVE ligado:

    finish_monitored_trade -> robot_persistence.save_trade_history
      -> build_trade_history_item
      -> ValueError: TRADE_RESULT_NOT_FINAL

`build_trade_history_item` aceitava só ``WIN``/``LOSS``, enquanto `main.py` já
tratava ``DRAW`` como terminal em ``{"WIN","LOSS","TIMEOUT","DRAW"}``. A
operação era contabilizada no placar (nem vitória nem derrota, lucro zero) e
**sumia do Histórico** — o cliente via acontecer na tela e depois não achava.

Raro no uso normal, frequente com o modo LIVE, que entra quase toda vela.
"""

from __future__ import annotations

import unittest

from backend.robot_persistence import (
    TRADE_ANALYSIS_FIELDS,
    build_trade_history_item,
    expand_trade_history_analysis,
)


def operacao(resultado: str, **extra) -> dict:
    base = {
        "order_id": "14241326714",
        "result": resultado,
        "final_result": resultado,
        "profit": 0.0,
        "amount": 20.0,
        "active": "GBPAUD-OTC",
        "direction": "CALL",
        "sent_at": "2026-09-08T01:10:10+00:00",
        "finished_at": "2026-09-08T01:11:01+00:00",
        "mode": "REAL",
    }
    base.update(extra)
    return base


class EmpateNoHistoricoTests(unittest.TestCase):
    def test_empate_e_gravado(self) -> None:
        item = build_trade_history_item("u", operacao("DRAW"))
        self.assertEqual(item["result"], "DRAW")
        self.assertEqual(item["order_id"], "14241326714")
        self.assertEqual(float(item["profit"]), 0.0)

    def test_vitoria_e_derrota_seguem_gravando(self) -> None:
        for resultado, lucro in (("WIN", 17.4), ("LOSS", -20.0)):
            with self.subTest(resultado=resultado):
                item = build_trade_history_item("u", operacao(resultado, profit=lucro))
                self.assertEqual(item["result"], resultado)

    def test_resultado_desconhecido_continua_recusado(self) -> None:
        # TIMEOUT e PENDING_RESULT não são desfecho: gravar "não sei" como
        # histórico é pior do que não gravar.
        for resultado in ("TIMEOUT", "PENDING_RESULT", "", "ORDER_REJECTED"):
            with self.subTest(resultado=resultado):
                with self.assertRaises(ValueError):
                    build_trade_history_item("u", operacao(resultado))

    def test_empate_sem_order_id_continua_recusado(self) -> None:
        with self.assertRaises(ValueError):
            build_trade_history_item("u", operacao("DRAW", order_id=""))


class TelemetriaDoModoLiveTests(unittest.TestCase):
    """A operação de demonstração precisa ser identificável na auditoria.

    `strategy_key` já saía como ``LIVE_DEMO``, mas o booleano separado sobrevive
    a qualquer renomeação de estratégia. Sem ele, a auditoria de 03/09 teria que
    confiar num rótulo de texto para excluir o modo das medições.
    """

    def test_live_demo_viaja_na_telemetria(self) -> None:
        self.assertIn("live_demo", TRADE_ANALYSIS_FIELDS)
        self.assertIn("strategy_key", TRADE_ANALYSIS_FIELDS)

    def test_marca_chega_ao_registro_gravado(self) -> None:
        item = build_trade_history_item(
            "u",
            operacao("WIN", profit=16.8, live_demo=True, strategy_key="LIVE_DEMO"),
        )
        analise = item["analysis_json"]
        self.assertIs(analise.get("live_demo"), True)
        self.assertEqual(analise.get("strategy_key"), "LIVE_DEMO")

    def test_operacao_normal_nao_ganha_a_marca(self) -> None:
        item = build_trade_history_item("u", operacao("WIN", profit=16.8))
        self.assertNotIn("live_demo", item["analysis_json"])

    def test_marca_sobrevive_a_leitura_do_historico(self) -> None:
        item = build_trade_history_item(
            "u",
            operacao("DRAW", live_demo=True, strategy_key="LIVE_DEMO"),
        )
        lido = expand_trade_history_analysis(dict(item))
        self.assertIs(lido.get("live_demo"), True)


if __name__ == "__main__":
    unittest.main()
