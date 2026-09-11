"""Conta configurada para MERCADO ABERTO não opera OTC.

Incidente de 08/09/2026: a conta de teste foi ligada em `market_mode=OPEN` e,
poucos ciclos depois, emitiu ordem de **USDJPY-OTC** com R$20 de dinheiro real.
A corretora recusou por acaso (turbo fechado nesse par), mas a intenção era
comprar — num mercado que a pessoa não escolheu, enquanto o painel continuava
mostrando "mercado aberto".

Dois caminhos faziam a troca por baixo:

1. `open_market_fallback_otc_reason` → `assets = ANALYSIS_ASSETS_OTC`, quando o
   payout dos pares abertos ainda não tinha chegado (cache frio nos primeiros
   ciclos após ligar).
2. `effective_market_mode` rebaixa OPEN para OTC com a sessão forex fechada, e
   `resolve_analysis_assets` já devolve a lista OTC — o caminho do fim de semana.

Ciclo sem oportunidade em mercado aberto passa a ser ciclo sem operação. BOTH
continua caindo para OTC: lá a pessoa escolheu os dois mercados.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend import main


SEGUNDA = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
SABADO = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def eh_otc(simbolo: str) -> bool:
    return simbolo.upper().endswith("-OTC")


class OpenNuncaVeOtcTests(unittest.TestCase):
    def setUp(self) -> None:
        main.analysis_asset_queue_offsets.clear()
        # Um usuário por teste: `auto_trader` guarda estado em memória e o
        # contador de ciclos sem oportunidade vazava de um teste para o outro.
        self.conta = f"conta-{self._testMethodName}"

    def selecionar(self, modo: str, *, sem_payout: bool = False) -> list[str]:
        alvo = None if not sem_payout else False
        with patch.object(main, "cached_asset_open_for_active", return_value=alvo):
            return main.select_analysis_assets_for_cycle(
                self.conta, max_assets=10, market_mode=modo, timeframe="M1"
            )

    def test_open_so_devolve_ativo_de_mercado_aberto(self) -> None:
        ativos = self.selecionar("OPEN")
        self.assertTrue(ativos, "modo OPEN não devolveu nenhum ativo")
        self.assertFalse(
            [a for a in ativos if eh_otc(a)],
            "conta em OPEN recebeu ativo OTC na varredura",
        )

    def test_open_sem_payout_nao_cai_para_otc(self) -> None:
        # O caso exato do incidente: cache frio, payout ainda não chegou.
        estado = main.auto_trader.get(self.conta)
        estado.consecutive_no_opportunity_cycles = 10
        estado.blocked_filters = ["PAYOUT_UNAVAILABLE"]
        ativos = self.selecionar("OPEN", sem_payout=True)
        self.assertFalse(
            [a for a in ativos if eh_otc(a)],
            "o fallback devolveu ativo OTC para conta em OPEN",
        )

    def test_otc_continua_so_com_otc(self) -> None:
        ativos = self.selecionar("OTC")
        self.assertTrue(ativos)
        self.assertTrue(all(eh_otc(a) for a in ativos))


class FimDeSemanaTests(unittest.TestCase):
    """Com a sessão forex fechada, OPEN não vira OTC — fica sem operar."""

    def setUp(self) -> None:
        main.analysis_asset_queue_offsets.clear()

    def test_effective_rebaixa_mas_a_varredura_nao_segue(self) -> None:
        # O rebaixamento continua existindo (outros pontos dependem dele)...
        self.assertEqual(main.effective_market_mode("OPEN", now=SABADO), "OTC")
        # ...mas a fila de análise da conta em OPEN não pode virar OTC.
        with (
            patch.object(main, "is_forex_open_market_open", return_value=False),
            patch.object(main, "cached_asset_open_for_active", return_value=None),
        ):
            ativos = main.select_analysis_assets_for_cycle(
                "conta-fds", max_assets=10, market_mode="OPEN", timeframe="M1"
            )
        self.assertFalse(
            [a for a in ativos if eh_otc(a)],
            "fim de semana transformou conta OPEN em conta OTC",
        )


class BothContinuaCaindoTests(unittest.TestCase):
    """BOTH escolheu os dois mercados: cair para OTC continua correto."""

    def setUp(self) -> None:
        main.analysis_asset_queue_offsets.clear()

    def test_both_com_forex_fechado_usa_otc(self) -> None:
        with (
            patch.object(main, "is_forex_open_market_open", return_value=False),
            patch.object(main, "cached_asset_open_for_active", return_value=None),
        ):
            ativos = main.select_analysis_assets_for_cycle(
                "conta-both", max_assets=10, market_mode="BOTH", timeframe="M1"
            )
        self.assertTrue(ativos)
        self.assertTrue(
            all(eh_otc(a) for a in ativos),
            "BOTH com forex fechado deveria varrer OTC",
        )

    def test_both_com_forex_aberto_ve_os_dois(self) -> None:
        with (
            patch.object(main, "is_forex_open_market_open", return_value=True),
            patch.object(main, "cached_asset_open_for_active", return_value=True),
        ):
            ativos = main.select_analysis_assets_for_cycle(
                "conta-both-aberto", max_assets=40, market_mode="BOTH", timeframe="M1"
            )
        self.assertTrue([a for a in ativos if eh_otc(a)])
        self.assertTrue([a for a in ativos if not eh_otc(a)])


if __name__ == "__main__":
    unittest.main()
