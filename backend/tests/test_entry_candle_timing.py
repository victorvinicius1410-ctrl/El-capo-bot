"""Entrada no início da vela: relógio, janela de compra e trava no envio.

Auditoria de 2026-08-30 sobre 11.859 operações reais: só 33,6% das ordens M1
caíam nos primeiros 8 segundos da vela; metade saía entre 9 e 20s. Duas causas
medidas em produção, ambas cobertas aqui:

1. ``/sessions/status`` tem TTL e throttle de 20s. A resposta vinha do cache já
   velha e mesmo assim virava âncora carimbada como nova — atraso de p95 12,9s
   e máximo de 27s entre o segundo que o robô achava estar e o segundo real.
2. A janela era conferida ANTES da revalidação de canal e do hop HTTP. Tudo que
   corria depois era tempo cego, e nada reconferia no instante do POST.

Entrar tarde não piora só o preço: a partir de ~53s a corretora rola a
expiração para o fim da vela seguinte, e a operação passa a apostar numa vela
que nunca foi analisada (durações gravadas: 50-56s cedo, 61-66s tarde).
"""

import unittest
from datetime import timedelta

from backend import main


class ServerClockResolutionTests(unittest.TestCase):
    """``resolve_server_clock_timestamp``: idade da amostra e guard de desvio."""

    def test_corrige_timestamp_pela_idade_da_resposta_cacheada(self):
        vps = 1_800_000_000.0
        # Resposta do broker com 8s de cache: o server_time veio de 8s atrás.
        timestamp, source, skew = main.resolve_server_clock_timestamp(
            vps - 8.0,
            payload_age_seconds=8.0,
            vps_timestamp=vps,
        )
        self.assertEqual(source, "bullex")
        self.assertAlmostEqual(timestamp, vps, places=3)
        self.assertAlmostEqual(skew, 0.0, places=3)

    def test_sem_correcao_a_amostra_velha_fica_atrasada(self):
        # Regressão: sem somar a idade, o robô acharia estar no segundo 2
        # enquanto o mercado está no 10 — exatamente o defeito medido.
        vps = 1_800_000_000.0
        timestamp, _source, skew = main.resolve_server_clock_timestamp(
            vps - 8.0,
            payload_age_seconds=0.0,
            vps_timestamp=vps,
        )
        self.assertAlmostEqual(timestamp, vps - 8.0, places=3)
        self.assertAlmostEqual(skew, -8.0, places=3)

    def test_desvio_e_reportado_mas_nao_troca_a_fonte(self):
        # A corretora define os limites de vela. Trocar para o relógio local
        # por conta própria esconderia uma dessincronia real atrás de um
        # relógio que não é o da expiração — o desvio vira log, não decisão.
        vps = 1_800_000_000.0
        timestamp, source, skew = main.resolve_server_clock_timestamp(
            vps + 25.0,
            payload_age_seconds=0.0,
            vps_timestamp=vps,
        )
        self.assertEqual(source, "bullex")
        self.assertAlmostEqual(timestamp, vps + 25.0, places=3)
        self.assertGreater(abs(skew), main.MAX_SERVER_CLOCK_SKEW_SECONDS)

    def test_desvio_pequeno_fica_abaixo_do_limite_de_alarme(self):
        vps = 1_800_000_000.0
        timestamp, source, skew = main.resolve_server_clock_timestamp(
            vps + 1.0,
            payload_age_seconds=0.0,
            vps_timestamp=vps,
        )
        self.assertEqual(source, "bullex")
        self.assertAlmostEqual(timestamp, vps + 1.0, places=3)
        self.assertLessEqual(abs(skew), main.MAX_SERVER_CLOCK_SKEW_SECONDS)

    def test_sem_server_time_usa_relogio_local(self):
        vps = 1_800_000_000.0
        timestamp, source, skew = main.resolve_server_clock_timestamp(
            None,
            payload_age_seconds=None,
            vps_timestamp=vps,
        )
        self.assertEqual(source, "vps_fallback")
        self.assertEqual(timestamp, vps)
        self.assertEqual(skew, 0.0)

    def test_idade_negativa_nao_adianta_o_relogio(self):
        vps = 1_800_000_000.0
        timestamp, source, _ = main.resolve_server_clock_timestamp(
            vps,
            payload_age_seconds=-30.0,
            vps_timestamp=vps,
        )
        self.assertEqual(source, "bullex")
        self.assertAlmostEqual(timestamp, vps, places=3)


class EntryWindowShapeTests(unittest.TestCase):
    """A janela de compra cobre só o início da vela."""

    def test_janela_termina_no_segundo_3(self):
        for timeframe in ("M1", "M5", "M15", "M30"):
            with self.subTest(timeframe=timeframe):
                self.assertEqual(main.ENTRY_WINDOWS[timeframe], (0, 3))

    def test_limite_de_envio_da_folga_mas_nao_alcanca_o_meio_da_vela(self):
        self.assertGreater(main.ENTRY_SEND_MAX_SECOND, main.ENTRY_WINDOW_END_SECOND)
        # 6s é 10% de uma vela M1. Acima disso a entrada deixa de ser "início".
        self.assertLessEqual(main.ENTRY_SEND_MAX_SECOND, 6)

    def test_meio_da_vela_nao_abre_a_janela(self):
        base = 1_800_000_000  # múltiplo de 60
        for second in (4, 9, 20, 30, 45, 56, 59):
            with self.subTest(second=second):
                window = main.get_entry_window("M1", base + second)
                self.assertFalse(window["entry_window_open"])
                self.assertTrue(window["missed_entry_window"])

    def test_inicio_da_vela_abre_a_janela(self):
        base = 1_800_000_000
        for second in (0, 1, 2, 3):
            with self.subTest(second=second):
                window = main.get_entry_window("M1", base + second)
                self.assertTrue(window["entry_window_open"])
                self.assertFalse(window["missed_entry_window"])

    def test_janela_perdida_aponta_para_a_abertura_da_proxima_vela(self):
        base = 1_800_000_000
        window = main.get_entry_window("M1", base + 40)
        self.assertEqual(window["buy_target_second"], 0)
        self.assertEqual(window["seconds_until_entry_window"], 20)


class EntrySendCandleSecondTests(unittest.TestCase):
    """``entry_send_candle_second``: segundo real no instante do POST."""

    def test_janela_recem_capturada_ainda_esta_no_segundo_dela(self):
        base = 1_800_000_000  # múltiplo de 60
        window = main.get_entry_window("M1", base + 1)
        self.assertAlmostEqual(main.entry_send_candle_second(window), 1.0, delta=0.5)

    def test_avanca_pelo_tempo_gasto_entre_autorizacao_e_envio(self):
        base = 1_800_000_000
        window = main.get_entry_window("M1", base + 1)
        # Simula 9s consumidos por revalidação de canal e hop HTTP: é
        # exatamente esse tempo cego que colocava a ordem no meio da vela.
        window["_captured_monotonic"] = window["_captured_monotonic"] - 9.0
        self.assertAlmostEqual(main.entry_send_candle_second(window), 10.0, delta=0.5)
        self.assertGreater(main.entry_send_candle_second(window), main.ENTRY_SEND_MAX_SECOND)

    def test_atravessa_a_virada_da_vela(self):
        base = 1_800_000_000
        window = main.get_entry_window("M1", base + 58)
        window["_captured_monotonic"] = window["_captured_monotonic"] - 4.0
        self.assertAlmostEqual(main.entry_send_candle_second(window), 2.0, delta=0.5)

    def test_respeita_o_timeframe(self):
        base = 1_800_000_000  # múltiplo de 300 também
        window = main.get_entry_window("M5", base + 2)
        self.assertEqual(window["expiration_seconds"], 300)
        self.assertAlmostEqual(main.entry_send_candle_second(window), 2.0, delta=0.5)


class CachedResponseAgeTests(unittest.TestCase):
    """A idade da resposta cacheada é o que corrige a âncora de relógio."""

    def setUp(self):
        self.user_id = "clock-age-user"
        main.session_response_cache.pop(self.user_id, None)

    def tearDown(self):
        main.session_response_cache.pop(self.user_id, None)

    def test_sem_cache_devolve_none(self):
        self.assertIsNone(main.cached_response_age_seconds(self.user_id, "/sessions/status"))

    def test_resposta_recem_gravada_tem_idade_quase_zero(self):
        main.cache_bullex_response(
            self.user_id,
            "/sessions/status",
            200,
            {"ok": True, "data": {"server_time": 1_800_000_000}},
        )
        age = main.cached_response_age_seconds(self.user_id, "/sessions/status")
        self.assertIsNotNone(age)
        self.assertLess(age, 1.0)

    def test_idade_cresce_com_o_instante_de_recebimento(self):
        main.cache_bullex_response(
            self.user_id,
            "/sessions/status",
            200,
            {"ok": True, "data": {"server_time": 1_800_000_000}},
        )
        entry = main.session_response_cache[self.user_id].responses["/sessions/status"]
        entry.received_at = entry.received_at - timedelta(seconds=15)
        age = main.cached_response_age_seconds(self.user_id, "/sessions/status")
        self.assertGreater(age, 14.0)
        self.assertLess(age, 17.0)


class EntryTelemetryFieldsTests(unittest.TestCase):
    """O segundo da entrada tem que sobreviver até o histórico."""

    def test_campos_de_timing_persistem_no_historico(self):
        from backend.robot_persistence import TRADE_ANALYSIS_FIELDS, build_trade_history_item

        for field in (
            "entry_candle_second",
            "entry_candle_second_authorized",
            "entry_window_end_second",
            "entry_server_time_source",
        ):
            self.assertIn(field, TRADE_ANALYSIS_FIELDS)

        item = build_trade_history_item(
            "user-1",
            {
                "result": "WIN",
                "order_id": "123",
                "sent_at": "2026-08-30T22:00:01+00:00",
                "finished_at": "2026-08-30T22:01:00+00:00",
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": 5.0,
                "payout": 87.0,
                "entry_candle_second": 1.42,
                "entry_candle_second_authorized": 0.9,
                "entry_window_end_second": 3,
                "entry_server_time_source": "bullex",
            },
        )
        self.assertEqual(item["analysis_json"]["entry_candle_second"], 1.42)
        self.assertEqual(item["analysis_json"]["entry_server_time_source"], "bullex")


if __name__ == "__main__":
    unittest.main()
