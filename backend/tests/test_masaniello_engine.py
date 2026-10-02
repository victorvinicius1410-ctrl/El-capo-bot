"""Motor do Gerenciamento Consistente (Masaniello): a conta tem que fechar.

Os números da planilha do dono estão escritos aqui à mão (capital 100, 38
operações, 14 acertos, payout 1,82): se o motor mudar, este teste acusa mesmo
que o arquivo de vetores seja regerado junto.

`fixtures/masaniello_vectors.json` é lido também pelo teste do painel
(`frontend/src/lib/masaniello.test.ts`): os dois motores têm que devolver
exatamente a mesma coisa.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend import masaniello

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "masaniello_vectors.json").read_text(encoding="utf-8")
)
ROW_KEYS = (
    "seq",
    "result",
    "stake",
    "stake_planned",
    "adjusted_to_min",
    "profit",
    "capital_after",
    "hit_rate",
    "errors_left",
)


class MasanielloPlanilhaTests(unittest.TestCase):
    def test_entradas_da_planilha_do_dono(self) -> None:
        cycle = masaniello.simulate(100, 38, 14, 82, ["L", "W", "L", "W"])
        self.assertEqual([row["stake"] for row in cycle["rows"]], [0.40, 0.58, 0.38, 0.55])
        self.assertEqual(cycle["target"], 100.81)
        self.assertEqual(cycle["status"], masaniello.STATUS_ACTIVE)

    def test_perfis_no_payout_de_referencia(self) -> None:
        esperado = {
            "conservador": (110.58, 6, 6.82, 0.47, 61.43),
            "moderado": (133.31, 5, 15.42, 1.28, 74.06),
            "agressivo": (192.73, 4, 27.86, 4.18, 107.07),
        }
        for perfil, (meta, erros, primeira, menor, maior) in esperado.items():
            n, w = masaniello.PROFILES[perfil]
            resumo = masaniello.plan_summary(100, n, w, 80, 5)
            self.assertEqual(
                (
                    resumo["target"],
                    resumo["max_errors"],
                    resumo["first_stake"],
                    resumo["min_stake"],
                    resumo["max_stake"],
                ),
                (meta, erros, primeira, menor, maior),
                perfil,
            )


class MasanielloVetoresTests(unittest.TestCase):
    def test_simulacoes_batem_com_os_vetores(self) -> None:
        for caso in VECTORS["simulations"]:
            entrada = caso["input"]
            cycle = masaniello.simulate(
                entrada["capital"],
                entrada["operations"],
                entrada["wins"],
                entrada["payout_ref"],
                entrada["results"],
                min_entry=entrada["min_entry"],
                payout_real=entrada["payout_real"],
            )
            esperado = caso["expect"]
            with self.subTest(caso["name"]):
                for chave in ("status", "end_reason", "capital_atual", "target", "wins", "losses", "next_stake"):
                    self.assertEqual(cycle[chave], esperado[chave], chave)
                linhas = [{k: row[k] for k in ROW_KEYS} for row in cycle["rows"]]
                self.assertEqual(linhas, esperado["rows"])

    def test_resumos_batem_com_os_vetores(self) -> None:
        for caso in VECTORS["plans"]:
            entrada = caso["input"]
            with self.subTest(str(entrada)):
                self.assertEqual(
                    masaniello.plan_summary(
                        entrada["capital"],
                        entrada["operations"],
                        entrada["wins"],
                        entrada["payout_ref"],
                        entrada["min_entry"],
                    ),
                    caso["expect"],
                )


class MasanielloRegrasTests(unittest.TestCase):
    def test_erros_esgotados_perdem_o_capital_inteiro(self) -> None:
        cycle = masaniello.simulate(100, 10, 4, 80, ["L"] * 7, min_entry=5)
        self.assertEqual(cycle["status"], masaniello.STATUS_BUST)
        self.assertEqual(cycle["end_reason"], masaniello.REASON_ERRORS)
        self.assertEqual(cycle["capital_atual"], 0.0)
        # A última entrada possível é tudo o que sobrou.
        self.assertEqual(cycle["rows"][-1]["stake"], cycle["rows"][-2]["capital_after"])

    def test_sem_ajuste_ao_minimo_a_meta_e_garantida_em_qualquer_ordem(self) -> None:
        for ordem in ("WWWW", "LWWLLWW", "LLLLLLWWWW"[:10], "WLWLWLW"):
            cycle = masaniello.simulate(2000, 10, 4, 80, list(ordem), min_entry=5)
            with self.subTest(ordem):
                self.assertEqual(cycle["status"], masaniello.STATUS_TARGET_HIT)
                self.assertFalse(any(row["adjusted_to_min"] for row in cycle["rows"]))
                self.assertAlmostEqual(cycle["capital_atual"], cycle["target"], delta=0.05)

    def test_payout_real_maior_so_adianta_o_capital(self) -> None:
        cycle = masaniello.simulate(2000, 10, 5, 80, list("LWLWWLWW"), min_entry=5, payout_real=87)
        self.assertEqual(cycle["status"], masaniello.STATUS_TARGET_HIT)
        self.assertGreater(cycle["capital_atual"], cycle["target"])

    def test_entrada_abaixo_do_minimo_sobe_e_fica_marcada(self) -> None:
        cycle = masaniello.simulate(100, 10, 4, 80, ["W", "W"], min_entry=5)
        segunda = cycle["rows"][1]
        self.assertLess(segunda["stake_planned"], 5)
        self.assertEqual(segunda["stake"], 5)
        self.assertTrue(segunda["adjusted_to_min"])

    def test_empate_nao_conta_e_nao_mexe_no_capital(self) -> None:
        cycle = masaniello.new_cycle(100, 10, 4, 80, min_entry=5)
        antes = masaniello.next_stake(cycle)
        cycle = masaniello.mark_pending(cycle, order_id="1", stake=antes["stake"])
        cycle = masaniello.apply_result(cycle, order_id="1", result="DRAW", profit=0.0)
        self.assertEqual((cycle["wins"], cycle["losses"], cycle["capital_atual"]), (0, 0, 100.0))
        self.assertIsNone(cycle["rows"][0]["seq"])
        self.assertIsNone(cycle["pending"])
        self.assertEqual(masaniello.next_stake(cycle), antes)

    def test_resultado_repetido_nao_lanca_duas_vezes(self) -> None:
        cycle = masaniello.new_cycle(100, 10, 4, 80, min_entry=5)
        cycle = masaniello.mark_pending(cycle, order_id="77", stake=6.82)
        uma = masaniello.apply_result(cycle, order_id="77", result="LOSS", profit=-6.82)
        duas = masaniello.apply_result(uma, order_id="77", result="LOSS", profit=-6.82)
        self.assertIs(duas, uma)
        self.assertEqual((uma["losses"], uma["capital_atual"], len(uma["rows"])), (1, 93.18, 1))

    def test_capital_usa_o_lucro_real_da_corretora(self) -> None:
        cycle = masaniello.new_cycle(100, 10, 4, 80, min_entry=5)
        cycle = masaniello.mark_pending(cycle, order_id="1", stake=6.82, payout=88.0)
        cycle = masaniello.apply_result(cycle, order_id="1", result="WIN", profit=6.0)
        self.assertEqual(cycle["capital_atual"], 106.0)

    def test_capital_abaixo_do_minimo_nao_tem_proxima_entrada(self) -> None:
        cycle = masaniello.new_cycle(4, 10, 6, 80, min_entry=5)
        self.assertIsNone(masaniello.next_stake(cycle))
        self.assertIsNone(cycle["next_stake"])

    def test_entrada_nunca_passa_do_capital(self) -> None:
        cycle = masaniello.new_cycle(6, 10, 6, 80, min_entry=5)
        cycle["capital_atual"] = 5.4
        self.assertEqual(masaniello.next_stake(cycle)["stake"], 5.0)

    def test_plano_valido(self) -> None:
        self.assertTrue(masaniello.valid_plan(10, 4))
        self.assertFalse(masaniello.valid_plan(10, 10))
        self.assertFalse(masaniello.valid_plan(10, 0))
        self.assertFalse(masaniello.valid_plan(masaniello.MAX_OPERATIONS + 1, 4))
        self.assertFalse(masaniello.valid_plan("x", 4))

    def test_capital_minimo_paga_a_primeira_entrada(self) -> None:
        minimo = masaniello.min_capital(10, 4, 80, 5)
        self.assertGreaterEqual(masaniello.plan_summary(minimo, 10, 4, 80, 5)["first_stake"], 5)
        self.assertLess(masaniello.plan_summary(minimo - 1, 10, 4, 80, 5)["first_stake"], 5)

    def test_nenhuma_chave_do_ciclo_comeca_com_ai(self) -> None:
        # `strip_ai_fields` apaga em silêncio qualquer chave que comece com "ai".
        cycle = masaniello.simulate(100, 10, 4, 80, ["W", "L"], min_entry=5)
        cycle = masaniello.mark_pending(cycle, order_id="9", stake=5)
        chaves = set(cycle) | set(cycle["pending"]) | {k for row in cycle["rows"] for k in row}
        self.assertEqual([k for k in chaves if k.lower().startswith("ai")], [])


class MasanielloCopiaMaisNovaTests(unittest.TestCase):
    def test_mesmo_ciclo_ganha_o_rev_maior(self) -> None:
        velho = masaniello.new_cycle(100, 10, 4, 80, cycle_id="c1")
        novo = masaniello.mark_pending(velho, order_id="1", stake=6.82)
        self.assertIs(masaniello.pick_freshest_cycle(velho, novo), novo)
        self.assertIs(masaniello.pick_freshest_cycle(novo, velho), novo)

    def test_ciclos_diferentes_ganha_o_que_comecou_depois(self) -> None:
        antigo = masaniello.new_cycle(100, 10, 4, 80, cycle_id="c1", at="2026-10-02T10:00:00+00:00")
        antigo = masaniello.mark_pending(antigo, order_id="1", stake=6.82)
        recente = masaniello.new_cycle(100, 10, 4, 80, cycle_id="c2", at="2026-10-02T11:00:00+00:00")
        self.assertIs(masaniello.pick_freshest_cycle(antigo, recente), recente)
        self.assertIs(masaniello.pick_freshest_cycle(recente, antigo), recente)

    def test_sem_ciclo(self) -> None:
        ciclo = masaniello.new_cycle(100, 10, 4, 80)
        self.assertIs(masaniello.pick_freshest_cycle(None, ciclo), ciclo)
        self.assertIs(masaniello.pick_freshest_cycle(ciclo, None), ciclo)
        self.assertIsNone(masaniello.pick_freshest_cycle(None, None))


if __name__ == "__main__":
    unittest.main()
