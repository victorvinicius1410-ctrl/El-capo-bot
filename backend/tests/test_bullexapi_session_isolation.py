"""
Testa o isolamento por instância de BullexAPI e ContextVar SessionGlobals.

Bug real corrigido em 2026-08-07: `socket_option_closed` e `order_binary`
eram atributos de CLASSE (definidos no corpo de `BullexAPI`, fora do
`__init__`), então TODAS as instâncias (uma por sessão/usuário do
bullex-service) compartilhavam o MESMO dict em memória. Uma ordem fechada
de um usuário podia, em tese, ser lida por outra sessão que consultasse
`GET /orders/{id}/result` — risco de WIN/LOSS trocado no histórico e
violação de isolamento multi-tenant (ver docs/ROBO_E_SUPORTE.md).

Fase 3: demais mutáveis de classe (candles, profile, etc.) também viram
atributos de instância; `global_value` usa ContextVar `SessionGlobals` para
SSID/balance_id — permitindo `BULLEX_MAX_CONCURRENT_API_CALLS=3`.

Ver também: tests/test_bullex_order_result.py (cinto de segurança por id).
"""

from __future__ import annotations

import asyncio
import unittest

import bullexapi.global_value as global_value
from bullexapi.api import BullexAPI
from bullexapi.global_value import (
    SessionGlobals,
    get_session_globals,
    reset_session_globals,
    set_session_globals,
)


class BullexApiSessionIsolationTests(unittest.TestCase):
    def test_socket_option_closed_is_not_shared_between_instances(self) -> None:
        session_a = BullexAPI("host", "user-a", "pass-a")
        session_b = BullexAPI("host", "user-b", "pass-b")

        session_a.socket_option_closed[111] = {"msg": {"id": 111, "win": "win"}}

        self.assertIn(111, session_a.socket_option_closed)
        self.assertNotIn(111, session_b.socket_option_closed)
        self.assertIsNot(session_a.socket_option_closed, session_b.socket_option_closed)

    def test_order_binary_is_not_shared_between_instances(self) -> None:
        session_a = BullexAPI("host", "user-a", "pass-a")
        session_b = BullexAPI("host", "user-b", "pass-b")

        session_a.order_binary[222] = {"option_id": 222, "win": "loose"}

        self.assertIn(222, session_a.order_binary)
        self.assertNotIn(222, session_b.order_binary)
        self.assertIsNot(session_a.order_binary, session_b.order_binary)

    def test_candles_and_profile_are_not_shared_between_instances(self) -> None:
        session_a = BullexAPI("host", "user-a", "pass-a")
        session_b = BullexAPI("host", "user-b", "pass-b")

        session_a.candles.candles_data = [{"from": 1}]
        session_a.profile.balance = 999.0
        session_a.socket_option_opened[1] = {"opened": True}
        session_a.real_time_candles["EURUSD"][60][1] = {"close": 1.1}
        session_a.traders_mood["EURUSD"] = 55

        self.assertIsNot(session_a.candles, session_b.candles)
        self.assertIsNot(session_a.profile, session_b.profile)
        self.assertIsNot(session_a.socket_option_opened, session_b.socket_option_opened)
        self.assertIsNot(session_a.real_time_candles, session_b.real_time_candles)
        self.assertIsNot(session_a.traders_mood, session_b.traders_mood)
        self.assertNotEqual(getattr(session_b.profile, "balance", None), 999.0)
        self.assertNotIn(1, session_b.socket_option_opened)
        self.assertNotIn("EURUSD", session_b.traders_mood)

    def test_session_globals_contextvar_isolates_ssid_and_balance(self) -> None:
        async def _run() -> None:
            barrier = asyncio.Barrier(2)
            results: dict[str, tuple[object, object]] = {}

            async def worker(name: str, ssid: str, balance_id: int) -> None:
                sg = SessionGlobals(SSID=ssid, balance_id=balance_id)
                token = set_session_globals(sg)
                try:
                    await barrier.wait()
                    # Outra task já setou o próprio ContextVar; o nosso
                    # não pode vazar SSID/balance_id.
                    await asyncio.sleep(0.02)
                    results[name] = (global_value.SSID, global_value.balance_id)
                    self.assertIs(get_session_globals(), sg)
                    self.assertEqual(global_value.SSID, ssid)
                    self.assertEqual(global_value.balance_id, balance_id)
                finally:
                    reset_session_globals(token)

            await asyncio.gather(
                worker("a", "ssid-a", 1001),
                worker("b", "ssid-b", 2002),
            )
            self.assertEqual(results["a"], ("ssid-a", 1001))
            self.assertEqual(results["b"], ("ssid-b", 2002))

        asyncio.run(_run())

    def test_module_proxy_reads_active_session_globals(self) -> None:
        previous = get_session_globals()
        sg = SessionGlobals(SSID="proxy-ssid", balance_id=4242)
        token = set_session_globals(sg)
        try:
            self.assertEqual(global_value.SSID, "proxy-ssid")
            self.assertEqual(global_value.balance_id, 4242)
            global_value.balance_id = 5252
            self.assertEqual(sg.balance_id, 5252)
        finally:
            reset_session_globals(token)
        # Fora do context, volta ao fallback (não ao sg da sessão).
        self.assertIsNot(get_session_globals(), sg)
        self.assertIs(get_session_globals(), previous)


if __name__ == "__main__":
    unittest.main()
