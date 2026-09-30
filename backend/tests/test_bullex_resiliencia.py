"""Correções da camada da corretora a partir dos erros de 29–30/09.

Cada teste é um caso real de produção: EURGBP aberto derrubando a sessão,
resultado de ordem perdido na queda do websocket, trava de envio presa,
payout de par aberto levando 4 s e "offline" fantasma depois do Iniciar.
"""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bullex_service import main
from bullexapi import constants as OP_code


class TabelaDeAtivosTests(unittest.TestCase):
    def tearDown(self) -> None:
        OP_code.ACTIVES.pop("TESTEPAR-op", None)

    def test_init_da_corretora_registra_ativo_que_faltava(self) -> None:
        init = {"turbo": {"actives": {"99901": {"name": "front.TESTEPAR-op", "enabled": True, "is_suspended": False}}}}
        self.assertNotIn("TESTEPAR-op", OP_code.ACTIVES)
        main.parse_binary_open_map(init)
        self.assertEqual(OP_code.ACTIVES["TESTEPAR-op"], 99901)

    def test_nao_sobrescreve_id_conhecido(self) -> None:
        original = OP_code.ACTIVES["EURUSD-op"]
        main.register_broker_active("EURUSD-op", 1)
        self.assertEqual(OP_code.ACTIVES["EURUSD-op"], original)

    def test_ativo_fora_da_tabela_e_recusado_como_indisponivel(self) -> None:
        with self.assertRaises(main.ServiceError) as ctx:
            main.ensure_asset_in_broker_table("TESTEPAR", user_id="u")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("asset is not available", ctx.exception.message)
        self.assertEqual(main.ensure_asset_in_broker_table("EURUSD", user_id="u"), "EURUSD-op")


class SessaoNaoCaiPorErroDeDadoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = "u-erro-dado"
        self.sessao = SimpleNamespace(
            user_id=self.user,
            client=SimpleNamespace(api=SimpleNamespace(close=lambda: None)),
            requires_2fa=False,
        )
        main.session_manager.upsert(self.sessao)

    def tearDown(self) -> None:
        main.session_manager.remove(self.user)

    def _rodar(self, erro: Exception) -> None:
        def operacao(_sessao):
            raise erro

        with (
            patch.object(main.session_manager, "ensure_session_alive", return_value=self.sessao),
            patch.object(main.session_manager, "_session_context", MagicMock()),
        ):
            with self.assertRaises(main.ServiceError):
                main.session_manager.run(self.user, operacao)

    def test_keyerror_nao_derruba_a_sessao(self) -> None:
        self._rodar(KeyError("EURGBP-op"))
        self.assertIsNotNone(main.session_manager.get(self.user))

    def test_erro_de_conexao_continua_derrubando(self) -> None:
        self._rodar(ConnectionError("caiu"))
        self.assertIsNone(main.session_manager.get(self.user))


class ResultadoPerdidoTests(unittest.TestCase):
    def setUp(self) -> None:
        main._ordens_fecham_em.clear()
        main._ultima_consulta_ordem.clear()

    def test_reconexao_herda_resultados(self) -> None:
        antigo = SimpleNamespace(api=SimpleNamespace(socket_option_closed={7: {"msg": {"win": "win"}}}, order_binary={}))
        novo = SimpleNamespace(api=SimpleNamespace(socket_option_closed={8: {"x": 1}}, order_binary={}))
        self.assertEqual(main.herdar_resultados(antigo, novo, user_id="u"), 1)
        self.assertIn(7, novo.api.socket_option_closed)
        self.assertEqual(novo.api.socket_option_closed[8], {"x": 1})

    def test_resposta_da_corretora_vira_resultado(self) -> None:
        dados = {"msg": {"closed_options": [
            {"id": [111], "win": "loose", "amount": 15, "win_amount": 0},
            {"id": [14307723702], "win": "win", "amount": 15, "win_amount": 28.05},
        ]}}
        self.assertEqual(
            main.parse_closed_option_result(dados, 14307723702),
            {"order_id": 14307723702, "result": "win", "profit": 13.05},
        )
        self.assertEqual(main.parse_closed_option_result(dados, 111)["profit"], -15.0)
        self.assertIsNone(main.parse_closed_option_result(dados, 222))

    def test_so_consulta_depois_da_expiracao_e_sem_martelar(self) -> None:
        main.registrar_ordem_enviada(555, 1)
        self.assertFalse(main._pode_consultar_corretora(555))  # ainda não expirou
        main._ordens_fecham_em[555] = time.monotonic() - 60
        self.assertTrue(main._pode_consultar_corretora(555))
        self.assertFalse(main._pode_consultar_corretora(555))  # 15 s de intervalo

    def test_consulta_com_teto_de_espera(self) -> None:
        api = SimpleNamespace(get_options_v2_data=None, get_options_v2=lambda *_: None)
        with patch.object(main, "ORDER_LOOKUP_TIMEOUT_SECONDS", 0.2):
            inicio = time.monotonic()
            self.assertIsNone(main.lookup_order_result_at_broker(SimpleNamespace(api=api), 9, user_id="u"))
        self.assertLess(time.monotonic() - inicio, 1.0)

    def test_endpoint_devolve_o_que_a_corretora_disse(self) -> None:
        api = SimpleNamespace(socket_option_closed={}, order_binary={}, get_options_v2_data=None)

        def pedir(_limite, _tipo):
            api.get_options_v2_data = {"msg": {"closed_options": [{"id": [4242], "win": "win", "amount": 10, "win_amount": 18.7}]}}

        api.get_options_v2 = pedir
        sessao = SimpleNamespace(client=SimpleNamespace(api=api, check_connect=lambda: True), requires_2fa=False)
        with (
            patch.object(main.session_manager, "run", side_effect=lambda _u, op: op(sessao)),
            patch.object(main, "read_digital_result", return_value={"result": "PENDING_RESULT"}),
        ):
            payload = main.order_result("4242", "u-lookup")
        self.assertEqual(payload["data"]["result"], "win")
        self.assertEqual(payload["data"]["profit"], 8.7)


class TravaDeEnvioTests(unittest.TestCase):
    def test_trava_e_liberada_quando_o_envio_falha(self) -> None:
        from bullexapi import api as modulo_api
        from bullexapi import global_value

        cliente = SimpleNamespace(websocket=SimpleNamespace(send=MagicMock(side_effect=ConnectionError("socket caiu"))))
        global_value.ssl_Mutual_exclusion = False
        global_value.ssl_Mutual_exclusion_write = False
        with self.assertRaises(ConnectionError):
            modulo_api.BullexAPI.send_websocket_request(cliente, "x", {})
        self.assertFalse(global_value.ssl_Mutual_exclusion_write)


class PayoutParAbertoTests(unittest.TestCase):
    def test_par_aberto_nao_consulta_o_digital(self) -> None:
        cliente = SimpleNamespace(get_digital_payout=MagicMock(return_value=0))
        mapa = {"EURUSD": {"turbo": 85.0}}
        payout = main.read_open_or_otc_payout(cliente, mapa, "EURUSD", True, None)
        self.assertEqual(payout, 85.0)
        cliente.get_digital_payout.assert_not_called()

    def test_otc_continua_pelo_digital(self) -> None:
        cliente = SimpleNamespace(get_digital_payout=MagicMock(return_value=87))
        self.assertEqual(main.read_open_or_otc_payout(cliente, {}, "EURUSD-OTC", None, None), 87)

    def test_erro_de_dado_no_digital_nao_vira_sessao_caida(self) -> None:
        cliente = SimpleNamespace(get_digital_payout=MagicMock(side_effect=KeyError("X")))
        self.assertIsNone(main.read_digital_payout(cliente, "X-OTC"))


class OfflineFantasmaTests(unittest.TestCase):
    def test_sessao_instalada_zera_o_offline(self) -> None:
        user = "u-offline"
        probe = main.session_manager.get_probe_state(user)
        probe.offline_until = time.time() + 60
        probe.failure_count = 3
        sessao = SimpleNamespace(user_id=user, client=SimpleNamespace(api=None))
        try:
            main.session_manager.upsert(sessao)
            self.assertEqual(probe.offline_until, 0.0)
            self.assertEqual(probe.failure_count, 0)
            probe.offline_until = time.time() + 60  # marca velha gravada no meio
            self.assertIsNone(main.session_manager.get_cached_probe(user, "/candles?x", path="/candles"))
        finally:
            main.session_manager.remove(user)


if __name__ == "__main__":
    unittest.main()

class PodaDeCacheTests(unittest.TestCase):
    """30/09 05:30: o serviço chegou a 8,9 GB e o kernel o matou (cache sem poda)."""

    def test_entradas_vencidas_saem_e_as_recentes_ficam(self) -> None:
        gerente = main.session_manager
        agora = time.time()
        with gerente._market_data_cache_lock:
            gerente._market_data_cache["/candles?endtime=1"] = main.CachedProbe(200, {}, agora - 3600)
            gerente._market_data_cache["/candles?endtime=2"] = main.CachedProbe(200, {}, agora + 30)
        estado = gerente.get_probe_state("u-poda")
        estado.responses["/candles?endtime=1"] = main.CachedProbe(200, {}, agora - 3600)
        estado.responses["/payouts?active=X"] = main.CachedProbe(200, {}, agora - 60)  # reserva de 2 min
        estado.last_request_at["/candles?endtime=1"] = agora - 3600
        try:
            self.assertGreaterEqual(gerente.podar_caches(force=True), 2)
            self.assertNotIn("/candles?endtime=1", gerente._market_data_cache)
            self.assertIn("/candles?endtime=2", gerente._market_data_cache)
            self.assertEqual(list(estado.responses), ["/payouts?active=X"])
            self.assertEqual(estado.last_request_at, {})
        finally:
            with gerente._market_data_cache_lock:
                gerente._market_data_cache.pop("/candles?endtime=2", None)
            gerente._probe_cache.pop("u-poda", None)

