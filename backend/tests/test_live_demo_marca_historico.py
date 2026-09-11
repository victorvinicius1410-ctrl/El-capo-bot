"""A operação do modo LIVE precisa chegar ao histórico com a marca de auditoria.

`robot_persistence.TRADE_ANALYSIS_FIELDS` lista `live_demo` e documenta que é
esse booleano que a auditoria consulta para excluir o modo das medições de
estratégia. Medido em 08/09/2026: 184 operações gravadas com
`strategy_key=LIVE_DEMO` e **zero** com o booleano — o registro da operação
nunca carregava o campo e o dict de persistência descarta `None`.

Desde 09/09/2026 o booleano é a ÚNICA marca: `strategy_key` passou a levar a
chave real do setup, porque a chave é campo de tela (badge do Histórico e
primeiro item da lista de estratégias que o robô fala). Ver `live_demo_mode`.
"""

import unittest

from backend.robot_persistence import TRADE_ANALYSIS_FIELDS, build_trade_history_item


class LiveDemoMarcaHistoricoTest(unittest.TestCase):
    def _analysis(self, trade: dict) -> dict:
        base = {
            "order_id": "1",
            "active": "EURUSD-OTC",
            "direction": "CALL",
            "amount": 5.0,
            "result": "WIN",
            "profit": 4.3,
            "sent_at": "2026-09-08T20:00:00+00:00",
            "finished_at": "2026-09-08T20:01:00+00:00",
        }
        return build_trade_history_item("u1", {**base, **trade})["analysis_json"]

    def test_campo_esta_na_lista_persistida(self) -> None:
        self.assertIn("live_demo", TRADE_ANALYSIS_FIELDS)
        self.assertIn("strategy_key", TRADE_ANALYSIS_FIELDS)

    def test_entrada_do_modo_live_grava_o_booleano(self) -> None:
        analysis = self._analysis({"strategy_key": "CANDLE_FLOW", "live_demo": True})

        self.assertIs(analysis.get("live_demo"), True)
        # A chave gravada é a do setup real — nada de LIVE_DEMO na tela.
        self.assertEqual(analysis.get("strategy_key"), "CANDLE_FLOW")

    def test_operacao_normal_nao_ganha_marca(self) -> None:
        analysis = self._analysis({"strategy_key": "CANDLE_FLOW", "live_demo": None})

        self.assertEqual(analysis.get("strategy_key"), "CANDLE_FLOW")
        self.assertNotIn("live_demo", analysis)


if __name__ == "__main__":
    unittest.main()
