"""O par de mercado aberto tem outro nome no catálogo da corretora.

A BullEx chama o par aberto de ``EURUSD-op`` e o sintético de ``EURUSD-OTC``.
O El Capo usa o nome nu (``EURUSD``) para o mercado aberto em todo o resto do
sistema — inclusive nas velas, que sempre funcionaram assim.

Até 08/09/2026 essa diferença tornava o catálogo de opção invisível: o mapa de
canais era indexado por ``EURUSD-OP`` e a consulta pedia ``EURUSD``, então todo
par aberto voltava ``payout=None``, batia em ``PAYOUT_UNAVAILABLE`` e nunca
virava candidato. Medido no mesmo dia: 41 ordens OTC e ZERO abertas, com
``EURUSD-op`` ABERTO e payout 84 (turbo) / 85 (binária).

A conclusão anterior — "a corretora não oferece par aberto" — estava errada.
"""

from __future__ import annotations

import unittest

from bullex_service.main import (
    _payout_turbo_binary,
    parse_binary_open_map,
    parse_binary_profit_map,
    to_broker_active,
    to_internal_active,
)


def init_v2(*ativos: tuple[str, bool]) -> dict:
    """Monta um `get_all_init_v2` com os ativos e o estado de abertura dados."""
    secao = {
        str(i): {"name": f"front.{nome}", "enabled": aberto, "is_suspended": False}
        for i, (nome, aberto) in enumerate(ativos)
    }
    return {"turbo": {"actives": dict(secao)}, "binary": {"actives": dict(secao)}}


class TraducaoDeNomeTests(unittest.TestCase):
    def test_nome_da_corretora_vira_o_nosso(self) -> None:
        self.assertEqual(to_internal_active("EURUSD-op"), "EURUSD")
        self.assertEqual(to_internal_active("front.GBPJPY-op"), "FRONT.GBPJPY")

    def test_otc_passa_intacto_nos_dois_sentidos(self) -> None:
        self.assertEqual(to_internal_active("EURUSD-OTC"), "EURUSD-OTC")
        self.assertEqual(to_broker_active("EURUSD-OTC"), "EURUSD-OTC")

    def test_nosso_nome_ganha_sufixo_para_a_corretora(self) -> None:
        self.assertEqual(to_broker_active("EURUSD"), "EURUSD-op")
        self.assertEqual(to_broker_active("gbpusd"), "GBPUSD-op")

    def test_nao_duplica_sufixo(self) -> None:
        self.assertEqual(to_broker_active("EURUSD-op"), "EURUSD-op")

    def test_vazio_nao_vira_sufixo_solto(self) -> None:
        self.assertEqual(to_broker_active(""), "")


class MapaDeAberturaTests(unittest.TestCase):
    def test_par_aberto_e_indexado_pelo_nosso_nome(self) -> None:
        mapa = parse_binary_open_map(init_v2(("EURUSD-op", True)))
        # Era aqui que quebrava: a chave saia EURUSD-OP e ninguem procurava por ela.
        self.assertIn("EURUSD", mapa)
        self.assertNotIn("EURUSD-OP", mapa)
        self.assertTrue(mapa["EURUSD"]["turbo"])

    def test_otc_continua_com_a_chave_de_sempre(self) -> None:
        mapa = parse_binary_open_map(init_v2(("EURUSD-OTC", True)))
        self.assertIn("EURUSD-OTC", mapa)
        self.assertTrue(mapa["EURUSD-OTC"]["binary"])

    def test_aberto_e_fechado_convivem(self) -> None:
        mapa = parse_binary_open_map(init_v2(("EURUSD-op", True), ("AUDCAD-op", False)))
        self.assertTrue(mapa["EURUSD"]["turbo"])
        self.assertFalse(mapa["AUDCAD"]["turbo"])


class PayoutTurboBinaryTests(unittest.TestCase):
    def test_fracao_da_corretora_vira_percentual(self) -> None:
        mapa = parse_binary_profit_map({"EURUSD-op": {"turbo": 0.84, "binary": 0.85}})
        self.assertEqual(mapa["EURUSD"], {"turbo": 84.0, "binary": 85.0})

    def test_zero_e_negativo_nao_entram(self) -> None:
        mapa = parse_binary_profit_map({"EURUSD-op": {"turbo": 0, "binary": -1}})
        self.assertNotIn("EURUSD", mapa)

    def test_escolhe_o_canal_aberto(self) -> None:
        mapa = {"EURUSD": {"turbo": 84.0, "binary": 85.0}}
        # so binary aberto -> paga o da binary, nao o maior
        self.assertEqual(
            _payout_turbo_binary(mapa, "EURUSD", open_turbo=False, open_binary=True), 85.0
        )
        self.assertEqual(
            _payout_turbo_binary(mapa, "EURUSD", open_turbo=True, open_binary=False), 84.0
        )

    def test_com_os_dois_abertos_fica_com_o_maior(self) -> None:
        mapa = {"EURUSD": {"turbo": 84.0, "binary": 85.0}}
        self.assertEqual(
            _payout_turbo_binary(mapa, "EURUSD", open_turbo=True, open_binary=True), 85.0
        )

    def test_canal_fechado_nunca_anuncia_payout(self) -> None:
        # Anunciar payout de canal fechado leva direto ao
        # "asset is not available at the moment" na hora da compra.
        mapa = {"EURUSD": {"turbo": 84.0, "binary": 85.0}}
        self.assertIsNone(
            _payout_turbo_binary(mapa, "EURUSD", open_turbo=False, open_binary=False)
        )
        self.assertIsNone(
            _payout_turbo_binary(mapa, "EURUSD", open_turbo=None, open_binary=None)
        )

    def test_ativo_ausente_do_mapa(self) -> None:
        self.assertIsNone(_payout_turbo_binary({}, "EURUSD", True, True))


class CenarioMedidoEm0809Tests(unittest.TestCase):
    """Reproduz o catálogo real medido em 08/09/2026, terça 15h30 UTC."""

    def setUp(self) -> None:
        self.abertura = parse_binary_open_map(
            init_v2(
                ("EURUSD-op", True),
                ("GBPUSD-op", True),
                ("USDJPY-op", True),
                ("AUDJPY-op", False),
                ("EURUSD-OTC", True),
            )
        )
        self.payouts = parse_binary_profit_map(
            {
                "EURUSD-op": {"turbo": 0.84, "binary": 0.85},
                "GBPUSD-op": {"turbo": 0.84, "binary": 0.85},
                "USDJPY-op": {"turbo": 0.84, "binary": 0.85},
                "NZDUSD-op": {"turbo": 0.30, "binary": 0.30},
                "EURUSD-OTC": {"turbo": 0.88, "binary": 0.88},
            }
        )

    def payout_de(self, simbolo: str) -> float | None:
        entrada = self.abertura.get(simbolo) or {}
        return _payout_turbo_binary(
            self.payouts, simbolo, entrada.get("turbo"), entrada.get("binary")
        )

    def test_os_majors_abertos_passam_no_minimo_do_usuario(self) -> None:
        for par in ("EURUSD", "GBPUSD", "USDJPY"):
            with self.subTest(par=par):
                payout = self.payout_de(par)
                self.assertIsNotNone(payout, f"{par} continua sem payout")
                self.assertGreaterEqual(payout, 80, "reprovaria no min_payout padrão")

    def test_par_fechado_nao_recebe_payout(self) -> None:
        self.assertIsNone(self.payout_de("AUDJPY"))

    def test_nzdusd_aberto_tem_payout_ruim_e_seria_reprovado(self) -> None:
        # 30% exige 77% de acerto so para empatar. O min_payout de 80 barra
        # sozinho — mas o numero precisa CHEGAR ao portao para ele decidir.
        mapa = parse_binary_open_map(init_v2(("NZDUSD-op", True)))
        payout = _payout_turbo_binary(
            self.payouts, "NZDUSD", mapa["NZDUSD"]["turbo"], mapa["NZDUSD"]["binary"]
        )
        self.assertEqual(payout, 30.0)
        self.assertLess(payout, 80)

    def test_otc_nao_foi_afetado(self) -> None:
        self.assertEqual(self.payout_de("EURUSD-OTC"), 88.0)


if __name__ == "__main__":
    unittest.main()
