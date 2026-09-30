"""Ativo com velas velhas não pode virar sinal (EURJPY-OTC, 29–30/09).

A corretora devolvia para o EURJPY-OTC, ativo morto, velas de junho de 2025.
O robô analisava a série congelada e tirava CALL com confiança 100 em 137 de
137 sinais, e ia comprar um par que a corretora recusa sempre.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend import main
from backend.auto_trader import utc_now

# Velas reais que a corretora devolveu para o EURJPY-OTC em 30/09/2026.
VELAS_DE_JUNHO_2025 = [
    {"from": 1749714840, "open": 166.230455, "close": 166.221115, "min": 166.220875, "max": 166.233415},
    {"from": 1749714900, "open": 166.220515, "close": 166.243375, "min": 166.218425, "max": 166.243375},
]


def _velas_de_agora(atraso_s: int = 0) -> list[dict]:
    inicio = int(utc_now().timestamp()) // 60 * 60 - atraso_s
    return [{"from": inicio - 60, "close": 1.0}, {"from": inicio, "close": 1.1}]


class VelasVelhasTests(unittest.TestCase):
    def setUp(self) -> None:
        main._ativos_com_velas_velhas.clear()
        main.reset_shared_market_cache()
        main.session_response_cache.clear()

    tearDown = setUp

    def test_serie_de_2025_e_velha(self) -> None:
        self.assertIsNotNone(main.candles_stale_age(VELAS_DE_JUNHO_2025, 60))

    def test_vela_atual_e_ate_alguns_minutos_passam(self) -> None:
        self.assertIsNone(main.candles_stale_age(_velas_de_agora(), 60))
        self.assertIsNone(main.candles_stale_age(_velas_de_agora(120), 60))
        self.assertIsNotNone(main.candles_stale_age(_velas_de_agora(900), 60))

    def test_m15_tolera_a_propria_vela(self) -> None:
        self.assertIsNone(main.candles_stale_age(_velas_de_agora(1800), 900))

    def test_carregar_velas_velhas_recusa_e_tira_de_todas_as_contas(self) -> None:
        resposta = (200, {"ok": True, "data": {"candles": VELAS_DE_JUNHO_2025}})
        with patch.object(main, "call_bullex_service", AsyncMock(return_value=resposta)):
            velas, _, erro = asyncio.run(main.load_candles_for_active("conta-a", "EURJPY-OTC", "M1"))
        self.assertIsNone(velas)
        self.assertEqual(erro, "CANDLES_STALE")
        self.assertIsNotNone(main.active_cooldown_remaining("conta-b", "EURJPY-OTC"))
        self.assertIsNone(main.active_cooldown_remaining("conta-b", "EURUSD-OTC"))

    def test_velas_atuais_passam(self) -> None:
        resposta = (200, {"ok": True, "data": {"candles": _velas_de_agora()}})
        with patch.object(main, "call_bullex_service", AsyncMock(return_value=resposta)):
            velas, _, erro = asyncio.run(main.load_candles_for_active("conta-a", "EURUSD-OTC", "M1"))
        self.assertIsNone(erro)
        self.assertEqual(len(velas), 2)


if __name__ == "__main__":
    unittest.main()
