"""``server_time`` de ``/sessions/status`` nunca pode sair do cache.

Medição em produção 2026-09-01 (`aca984b4`, sessão real conectada):

- Requisições a cada 3s receberam o MESMO ``server_time`` por 58s seguidos —
  defasagem crescendo de 25,08s para 58,11s, e 73,77s no pior caso observado.
  Causa: ``SESSION_STATUS_TTL_SECONDS=20`` mais o re-serve de
  ``SESSION_STATUS_THROTTLE_SECONDS=10``. Com ~15 robôs consultando o endpoint
  continuamente, o throttle mantinha a mesma amostra congelada indefinidamente.
- Espaçando as chamadas em 22s, a leitura fresca ficou 0,15–0,86s atrás do
  relógio local. Ou seja: o relógio da corretora está saudável; o atraso era
  inteiramente do nosso cache.

Impacto medido em 209 ordens reais das 12h anteriores: o robô calculava o
segundo da vela a partir dessa amostra e achava que entrava no segundo 1,8
(mediana) quando entrava, de fato, no segundo 10,2 (p90 16,9). Como a
telemetria de ``[TRADE_SENT_AT]`` era derivada do mesmo relógio, ela confirmava
a si mesma e o defeito ficava invisível.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bullex_service import main as bullex_main


def _payload(*, connected: bool = True, server_time: float = 1_000.0) -> dict:
    return {
        "ok": True,
        "data": {"user_id": "u1", "connected": connected, "server_time": server_time},
        "error": None,
    }


class FreshServerTimeTests(unittest.TestCase):
    def _session(self, timestamp: float):
        return SimpleNamespace(
            client=SimpleNamespace(get_server_timestamp=lambda: timestamp)
        )

    def test_substitui_o_timestamp_congelado_do_cache(self):
        cached = _payload(server_time=1_000.0)
        with patch.object(bullex_main.session_manager, "get", return_value=self._session(1_073.0)):
            fresh = bullex_main.with_fresh_server_time(cached, "u1")
        self.assertEqual(fresh["data"]["server_time"], 1_073.0)
        self.assertIn("server_time_sampled_at", fresh["data"])

    def test_nao_muta_o_payload_guardado_no_cache(self):
        # Regressão: mutar o dict cacheado gravaria a estampa junto e ela
        # envelheceria com o cache, recriando exatamente o defeito.
        cached = _payload(server_time=1_000.0)
        with patch.object(bullex_main.session_manager, "get", return_value=self._session(1_073.0)):
            bullex_main.with_fresh_server_time(cached, "u1")
        self.assertEqual(cached["data"]["server_time"], 1_000.0)
        self.assertNotIn("server_time_sampled_at", cached["data"])

    def test_sessao_desconectada_fica_como_esta(self):
        cached = _payload(connected=False, server_time=1_000.0)
        with patch.object(bullex_main.session_manager, "get", return_value=self._session(1_073.0)):
            fresh = bullex_main.with_fresh_server_time(cached, "u1")
        self.assertEqual(fresh["data"]["server_time"], 1_000.0)

    def test_sem_sessao_viva_devolve_o_payload_original(self):
        cached = _payload(server_time=1_000.0)
        with patch.object(bullex_main.session_manager, "get", return_value=None):
            fresh = bullex_main.with_fresh_server_time(cached, "u1")
        self.assertEqual(fresh["data"]["server_time"], 1_000.0)

    def test_falha_na_leitura_viva_nao_derruba_a_resposta(self):
        cached = _payload(server_time=1_000.0)
        boom = SimpleNamespace(
            client=SimpleNamespace(
                get_server_timestamp=lambda: (_ for _ in ()).throw(RuntimeError("ws down"))
            )
        )
        with patch.object(bullex_main.session_manager, "get", return_value=boom):
            fresh = bullex_main.with_fresh_server_time(cached, "u1")
        self.assertEqual(fresh["data"]["server_time"], 1_000.0)

    def test_timestamp_invalido_nao_vira_ancora(self):
        cached = _payload(server_time=1_000.0)
        with patch.object(bullex_main.session_manager, "get", return_value=self._session(0.0)):
            fresh = bullex_main.with_fresh_server_time(cached, "u1")
        self.assertEqual(fresh["data"]["server_time"], 1_000.0)


if __name__ == "__main__":
    unittest.main()
