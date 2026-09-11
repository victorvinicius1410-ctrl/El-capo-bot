"""Escada de cooldown do par que a corretora recusa repetidamente.

Contexto (08/09/2026): de 133 ordens reais, 94 voltaram com "asset is not
available" e 89 eram o mesmo par. O cooldown fixo de 60s era exatamente uma
vela M1 — expirava, o catálogo dizia "aberto" de novo e o robô reelegia o par
para sempre.
"""

import unittest
from datetime import timedelta

from backend import main as gateway_main


class UnavailableAssetCooldownTest(unittest.TestCase):
    def setUp(self) -> None:
        gateway_main._unavailable_asset_strikes.clear()
        self.addCleanup(gateway_main._unavailable_asset_strikes.clear)

    def test_escada_cresce_a_cada_recusa_seguida(self) -> None:
        degraus = [
            gateway_main.unavailable_asset_cooldown_seconds(
                gateway_main.register_unavailable_asset_strike("EURJPY-OTC")
            )
            for _ in range(5)
        ]

        # Primeiro degrau segue sendo uma vela M1 — recusa isolada não é punida.
        self.assertEqual(degraus[0], 60)
        self.assertEqual(degraus, sorted(degraus))
        # Em 3 tentativas o par já sai da roda por 15 minutos, não por 1 vela.
        self.assertGreaterEqual(degraus[2], 900)
        # O último degrau se repete em vez de crescer sem limite.
        self.assertEqual(degraus[-1], degraus[-2])

    def test_contagem_soma_entre_contas(self) -> None:
        # "asset is not available" e condicao da corretora: o que a conta A
        # descobre poupa a conta B de repetir a mesma tentativa perdida.
        primeira = gateway_main.register_unavailable_asset_strike("EURJPY-OTC")
        segunda = gateway_main.register_unavailable_asset_strike("EURJPY-OTC")

        self.assertEqual(primeira, 1)
        self.assertEqual(segunda, 2)
        self.assertEqual(gateway_main.unavailable_asset_cooldown_seconds(segunda), 300)

    def test_pares_diferentes_nao_se_contaminam(self) -> None:
        for _ in range(3):
            gateway_main.register_unavailable_asset_strike("EURJPY-OTC")

        strikes = gateway_main.register_unavailable_asset_strike("GBPUSD-OTC")

        self.assertEqual(strikes, 1)
        self.assertEqual(gateway_main.unavailable_asset_cooldown_seconds(strikes), 60)

    def test_ordem_aceita_zera_a_contagem(self) -> None:
        for _ in range(3):
            gateway_main.register_unavailable_asset_strike("EURJPY-OTC")

        gateway_main.clear_unavailable_asset_strikes("EURJPY-OTC")

        self.assertEqual(
            gateway_main.register_unavailable_asset_strike("EURJPY-OTC"), 1
        )

    def test_recusa_antiga_nao_soma_com_a_de_agora(self) -> None:
        gateway_main.register_unavailable_asset_strike("EURJPY-OTC")
        strikes, quando = gateway_main._unavailable_asset_strikes["EURJPY-OTC"]
        gateway_main._unavailable_asset_strikes["EURJPY-OTC"] = (
            strikes,
            quando
            - timedelta(seconds=gateway_main.UNAVAILABLE_ASSET_STRIKE_DECAY_SECONDS + 60),
        )

        self.assertEqual(
            gateway_main.register_unavailable_asset_strike("EURJPY-OTC"), 1
        )

    def test_normaliza_o_nome_do_ativo(self) -> None:
        gateway_main.register_unavailable_asset_strike("eurjpy-otc")

        self.assertEqual(
            gateway_main.register_unavailable_asset_strike("EURJPY-OTC"), 2
        )


if __name__ == "__main__":
    unittest.main()
