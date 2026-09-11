"""A narrativa da análise descreve o que foi medido, sem veredito.

Pedido do dono em 10/09/2026: o El Capo não pode soar incerto na explicação
da entrada. A regra do módulo é tirar o veredito inteiro — nem ressalva, nem
convicção — e manter o fato contrário em tom neutro.
"""

from __future__ import annotations

import itertools
import unittest

from backend.narrativa_analise import monta_narrativa, observacoes

# Nada disso pode aparecer: ressalva, convicção ou adjetivo de dúvida.
PROIBIDAS = (
    "certeza", "ressalva", "fraco", "magro", "admito", "não está bonita",
    "não me deu", "mais limpos", "perfeito", "exagerar", "razoável",
    "confortável", "claro", "a favor", "incomoda", "indecis", "nada firme",
    "pesa contra", "ponto fraco",
)


def _metricas(rsi, ema_a_favor, corpo, pavio, cores, setup, vol):
    return {
        "rsi14": rsi,
        "ema9": 1.2 if ema_a_favor else 1.0,
        "ema21": 1.1,
        "candle_body": corpo,
        "candle_range": 1.0,
        "upper_wick_ratio": pavio,
        "lower_wick_ratio": pavio,
        "last_3_colors": cores,
        "price_action_setup": setup,
        "volatility": vol,
    }


class NarrativaSemVereditoTest(unittest.TestCase):
    def test_nenhuma_combinacao_gera_palavra_de_duvida_ou_conviccao(self) -> None:
        combinacoes = itertools.product(
            (25, 50, 60, 75),
            (True, False),
            (0.2, 0.5, 0.8),
            (0.1, 0.5),
            (["GREEN"] * 3, ["GREEN", "RED", "GREEN"], ["RED"] * 3),
            ("CONTINUATION", "REVERSAL", "WEAK", ""),
            ("HIGH", "LOW"),
        )
        vistos = 0
        for rsi, ema, corpo, pavio, cores, setup, vol in combinacoes:
            for direcao in ("CALL", "PUT"):
                metricas = _metricas(rsi, ema, corpo, pavio, cores, setup, vol)
                for marcador in range(3):
                    texto = monta_narrativa("EURUSD-OTC", direcao, metricas, marcador=marcador)
                    vistos += 1
                    minusculo = texto.lower()
                    for palavra in PROIBIDAS:
                        self.assertNotIn(palavra, minusculo, texto)
                    self.assertTrue(texto.endswith((f"Entrada de {direcao}.", f"Vou de {direcao}.")), texto)
        self.assertGreater(vistos, 1000)

    def test_fato_contrario_continua_sendo_dito(self) -> None:
        metricas = _metricas(50, False, 0.2, 0.5, ["GREEN", "RED", "GREEN"], "WEAK", "LOW")
        frases = observacoes(metricas, "CALL")
        self.assertIn("as médias curtas apontam para o lado contrário", frases)
        self.assertIn("a última vela fechou com corpo de 20% do range", frases)
        self.assertIn("a última vela deixou um pavio de 50% contra a direção", frases)
        self.assertIn("as últimas velas vêm alternando de cor", frases)

    def test_sem_metricas_diz_a_direcao_da_leitura(self) -> None:
        texto = monta_narrativa("GBPUSD-OTC", "PUT", {}, marcador="x")
        self.assertIn("a leitura das últimas velas aponta para venda", texto)
        for palavra in PROIBIDAS:
            self.assertNotIn(palavra, texto.lower())

    def test_mesma_vela_gera_o_mesmo_texto(self) -> None:
        metricas = _metricas(75, True, 0.8, 0.1, ["GREEN"] * 3, "CONTINUATION", "HIGH")
        a = monta_narrativa("EURJPY-OTC", "CALL", metricas, marcador=123)
        b = monta_narrativa("EURJPY-OTC", "CALL", metricas, marcador=123)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
