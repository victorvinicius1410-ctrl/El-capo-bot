"""Quando o mercado aberto está realmente disponível.

`is_forex_open_market_open` conhece apenas a janela semanal (dom 22:00 → sex
22:00 UTC). Ele **não conhece feriado**: em 07/09/2026 (Labor Day nos EUA + 7 de
Setembro) disse "aberto" enquanto o volume era 18% de uma segunda normal — e a
medição daquele dia levou a uma conclusão errada sobre a corretora, que custou
uma trava indevida no painel.

`open_market_really_available` cruza o relógio com o que a corretora responde
por ativo. Se ela diz que todos os pares abertos conhecidos estão fechados,
está fechado — feriado incluído.

O caso do cache vazio é deliberado: sem nenhuma amostra, o relógio vale. Travar
o painel por falta de dado seria pior do que o defeito que isto conserta.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend import main


SEGUNDA_MEIO_DIA = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
SABADO = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
DOMINGO_ANTES_DA_ABERTURA = datetime(2026, 9, 6, 20, 0, tzinfo=timezone.utc)
DOMINGO_DEPOIS_DA_ABERTURA = datetime(2026, 9, 6, 23, 0, tzinfo=timezone.utc)


class ForaDaJanelaSemanalTests(unittest.TestCase):
    def test_sabado_fecha_sem_perguntar_a_corretora(self) -> None:
        with patch.object(main, "cached_asset_open_for_active") as consulta:
            disponivel, motivo = main.open_market_really_available("u", "M1", now=SABADO)
        self.assertFalse(disponivel)
        self.assertEqual(motivo, "forex_closed")
        consulta.assert_not_called()

    def test_domingo_antes_das_22h_fecha(self) -> None:
        disponivel, motivo = main.open_market_really_available(
            "u", "M1", now=DOMINGO_ANTES_DA_ABERTURA
        )
        self.assertFalse(disponivel)
        self.assertEqual(motivo, "forex_closed")

    def test_domingo_depois_das_22h_abre(self) -> None:
        with patch.object(main, "cached_asset_open_for_active", return_value=True):
            disponivel, motivo = main.open_market_really_available(
                "u", "M1", now=DOMINGO_DEPOIS_DA_ABERTURA
            )
        self.assertTrue(disponivel)
        self.assertIsNone(motivo)


class DentroDaJanelaTests(unittest.TestCase):
    def test_um_par_aberto_basta(self) -> None:
        def consulta(_user, symbol, _tf):
            return symbol == "EURUSD"

        with patch.object(main, "cached_asset_open_for_active", side_effect=consulta):
            disponivel, motivo = main.open_market_really_available(
                "u", "M1", now=SEGUNDA_MEIO_DIA
            )
        self.assertTrue(disponivel)
        self.assertIsNone(motivo)

    def test_feriado_a_corretora_fecha_tudo_e_o_relogio_mente(self) -> None:
        # O caso de 07/09/2026: a janela semanal diz "aberto", a corretora não.
        self.assertTrue(main.is_forex_open_market_open(SEGUNDA_MEIO_DIA))
        with patch.object(main, "cached_asset_open_for_active", return_value=False):
            disponivel, motivo = main.open_market_really_available(
                "u", "M1", now=SEGUNDA_MEIO_DIA
            )
        self.assertFalse(disponivel)
        self.assertEqual(motivo, "broker_closed")

    def test_cache_vazio_confia_no_relogio(self) -> None:
        # Sessão fria: travar o painel por falta de dado seria pior.
        with patch.object(main, "cached_asset_open_for_active", return_value=None):
            disponivel, motivo = main.open_market_really_available(
                "u", "M1", now=SEGUNDA_MEIO_DIA
            )
        self.assertTrue(disponivel)
        self.assertIsNone(motivo)

    def test_sem_usuario_so_o_relogio_responde(self) -> None:
        with patch.object(main, "cached_asset_open_for_active") as consulta:
            disponivel, _ = main.open_market_really_available(None, "M1", now=SEGUNDA_MEIO_DIA)
        self.assertTrue(disponivel)
        consulta.assert_not_called()

    def test_so_consulta_os_pares_que_a_corretora_oferece(self) -> None:
        vistos: list[str] = []

        def consulta(_user, symbol, _tf):
            vistos.append(symbol)
            return False

        with patch.object(main, "cached_asset_open_for_active", side_effect=consulta):
            main.open_market_really_available("u", "M1", now=SEGUNDA_MEIO_DIA)
        self.assertEqual(set(vistos), set(main.ANALYSIS_ASSETS_OPEN))
        # Os pares que só existem como OTC não têm o que responder.
        self.assertNotIn("EURAUD", vistos)

    def test_timeframe_chega_na_consulta(self) -> None:
        # turbo (M1) e binary (M5+) abrem em horários diferentes: USDCAD, por
        # exemplo, so existe na binaria.
        recebidos: list[str] = []

        def consulta(_user, _symbol, tf):
            recebidos.append(tf)
            return True

        with patch.object(main, "cached_asset_open_for_active", side_effect=consulta):
            main.open_market_really_available("u", "M15", now=SEGUNDA_MEIO_DIA)
        self.assertEqual(recebidos[0], "M15")


if __name__ == "__main__":
    unittest.main()
