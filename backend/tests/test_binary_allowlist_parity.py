"""As duas allowlists de ativos precisam ser a MESMA lista.

`backend.main.BINARY_ALLOWED_ASSETS` decide o que o robô varre;
`bullex_service.main.BINARY_ALLOWED_ASSETS` decide o que a nossa porta para a
corretora aceita responder. Quando divergem, o gateway pede um ativo que ele
mesmo considera válido e leva **HTTP 400 `ASSET_NOT_ALLOWED` da nossa própria
infra** — indistinguível, no log do robô, de "a corretora não tem esse ativo".

Foi exatamente isso em 2026-09-07: os 11 pares de mercado aberto adicionados em
04/09 (`AUDCAD`, `AUDCHF`, `CADCHF`, `CHFJPY`, `EURAUD`, `EURCAD`, `EURNZD`,
`GBPAUD`, `GBPCAD`, `GBPCHF`, `NZDUSD`) entraram só no gateway. A conta em
`market_mode=OPEN` levava 400 em mais da metade dos pares, não achava payout em
nenhum e caía para OTC em todo ciclo — operando 100% OTC com o painel exibindo
"mercado aberto".
"""

from __future__ import annotations

import unittest

from backend.main import BINARY_ALLOWED_ASSETS as GATEWAY_ASSETS
from bullex_service.main import BINARY_ALLOWED_ASSETS as SERVICE_ASSETS


class BinaryAllowlistParityTests(unittest.TestCase):
    """Divergência entre gateway e bullex-service é sempre defeito."""

    def test_service_accepts_everything_the_gateway_scans(self) -> None:
        faltando = sorted(set(GATEWAY_ASSETS) - set(SERVICE_ASSETS))
        self.assertEqual(
            faltando,
            [],
            "ativos que o robô varre e o bullex-service recusaria com 400: "
            f"{faltando}",
        )

    def test_gateway_scans_everything_the_service_accepts(self) -> None:
        sobrando = sorted(set(SERVICE_ASSETS) - set(GATEWAY_ASSETS))
        self.assertEqual(
            sobrando,
            [],
            f"ativos liberados no bullex-service que o robô nunca pede: {sobrando}",
        )

    def test_lists_are_identical_including_order(self) -> None:
        self.assertEqual(list(SERVICE_ASSETS), list(GATEWAY_ASSETS))

    def test_no_duplicates(self) -> None:
        for nome, lista in (("gateway", GATEWAY_ASSETS), ("bullex_service", SERVICE_ASSETS)):
            with self.subTest(lista=nome):
                self.assertEqual(len(lista), len(set(lista)))

    def test_every_open_market_asset_scanned_is_allowed_end_to_end(self) -> None:
        """Cada par de mercado aberto da varredura passa nas duas portas."""
        from backend.main import ANALYSIS_ASSETS_OPEN, is_binary_asset_allowed
        from bullex_service.main import ensure_binary_asset_allowed

        for symbol in ANALYSIS_ASSETS_OPEN:
            with self.subTest(symbol=symbol):
                self.assertTrue(is_binary_asset_allowed(symbol))
                self.assertEqual(ensure_binary_asset_allowed(symbol), symbol)

    def test_every_otc_asset_scanned_is_allowed_end_to_end(self) -> None:
        from backend.main import ANALYSIS_ASSETS_OTC, is_binary_asset_allowed
        from bullex_service.main import ensure_binary_asset_allowed

        for symbol in ANALYSIS_ASSETS_OTC:
            with self.subTest(symbol=symbol):
                self.assertTrue(is_binary_asset_allowed(symbol))
                self.assertEqual(ensure_binary_asset_allowed(symbol), symbol)


if __name__ == "__main__":
    unittest.main()
