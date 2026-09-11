"""Testes do modo LIVE (cadência de demonstração)."""

from __future__ import annotations

import unittest

from backend import signal_engine
from backend.live_demo_mode import (
    LIVE_CONFIDENCE,
    STRATEGY_LIVE_DEMO,
    apply_live_demo,
    is_live_demo,
    live_demo_allows,
    live_demo_passa_portao,
    live_min_confidence,
)


_VERTEX_ORIGINAL = signal_engine.VERTEX_ENABLED


def setUpModule() -> None:
    """Desliga a Vertex: o modo LIVE só age sobre o que o clássico barrou.

    Com a Vertex ligada no ambiente, o override roda antes do modo e devolve
    WAIT fora do extremo, então a cadência que estes testes medem some.
    """
    signal_engine.VERTEX_ENABLED = False


def tearDownModule() -> None:
    signal_engine.VERTEX_ENABLED = _VERTEX_ORIGINAL


def barrado(**extra) -> dict:
    base = {
        "signal": "CALL",
        "direction": "CALL",
        "trade_allowed": False,
        "blocked_filters": ["PRICE_ACTION_SETUP", "MIN_CONFIDENCE"],
        "quality_reason": "PRICE_ACTION_SETUP",
        "confidence": 40,
    }
    base.update(extra)
    return base


class LiveDemoTests(unittest.TestCase):
    def test_desligado_nao_mexe_em_nada(self) -> None:
        sinal = barrado()
        saida = apply_live_demo(dict(sinal), "EURUSD-OTC", live_enabled=False)
        self.assertFalse(saida["trade_allowed"])
        self.assertNotIn("live_demo", saida)

    def test_libera_entrada_barrada_em_otc(self) -> None:
        saida = apply_live_demo(barrado(), "EURUSD-OTC", live_enabled=True)
        self.assertTrue(saida["trade_allowed"])
        self.assertEqual(saida["confidence"], LIVE_CONFIDENCE)
        self.assertTrue(saida["live_demo"])

    def test_marca_a_operacao_para_nao_sujar_medicao(self) -> None:
        # 1.220 operações de conta de marketing já poluíram os relatórios uma
        # vez (auditoria de 03/09). Toda entrada deste modo tem de ser
        # identificável no histórico — pelos campos de auditoria, que não
        # aparecem na narração.
        saida = apply_live_demo(barrado(), "EURUSD-OTC", live_enabled=True)
        self.assertIs(saida["live_demo"], True)
        self.assertEqual(saida["quality_reason"], f"OK_{STRATEGY_LIVE_DEMO}")

    def test_chave_da_estrategia_nao_entrega_o_modo(self) -> None:
        # `strategy_key` é campo de tela: vira badge no Histórico e é o PRIMEIRO
        # item da lista que o robô fala em "Estratégia utilizada"
        # (`build_strategy_narration`). Com LIVE_DEMO ali, o robô anunciava o
        # modo em voz alta na transmissão.
        for setup, chave in (
            ("REVERSAL", "EXHAUSTION_REVERSAL"),
            ("CONTINUATION", "CONTINUATION"),
            ("WEAK", "CANDLE_FLOW"),
        ):
            with self.subTest(setup=setup):
                saida = apply_live_demo(
                    barrado(metrics={"price_action_setup": setup}),
                    "EURUSD-OTC",
                    live_enabled=True,
                )
                self.assertEqual(saida["strategy_key"], chave)
                self.assertNotEqual(saida["strategy_key"], STRATEGY_LIVE_DEMO)

    def test_narra_como_operacao_normal(self) -> None:
        # O overlay lê `narrator_text` em voz alta na transmissão. Enquanto
        # esses campos diziam "entrada de demonstração / Modo LIVE ligado /
        # portão afrouxado", o robô anunciava sozinho que aquilo não era
        # operação de verdade.
        leitura = "Olhando o gráfico de EURUSD-OTC: as médias curtas confirmam a direção."
        saida = apply_live_demo(
            barrado(candle_reading=leitura, timeframe="M1", metrics={"price_action_setup": "CONTINUATION"}),
            "EURUSD-OTC",
            live_enabled=True,
        )
        # A leitura das velas é a de sempre: o modo não a toca.
        self.assertEqual(saida["candle_reading"], leitura)
        # E o detalhe tem a mesma forma da entrada nomeada — repetir a leitura
        # nos dois campos fazia a voz dizer a mesma frase duas vezes seguidas.
        self.assertEqual(
            saida["analysis_detail"],
            "Estratégia: Continuação de tendência. Direção CALL. "
            "Continuação do movimento em andamento (M1).",
        )
        self.assertNotEqual(saida["analysis_detail"], saida["candle_reading"])
        for campo in (
            "entry_reason",
            "signal_explanation",
            "analysis_detail",
            "narrator_text",
            "speech_preview",
            "strategy_name",
            "strategy_summary",
        ):
            texto = str(saida[campo]).lower()
            with self.subTest(campo=campo):
                for proibido in ("demonstra", "modo live", "afrouxad", "live_demo", "convicção"):
                    self.assertNotIn(proibido, texto)

    def test_nome_da_estrategia_vem_do_setup_medido(self) -> None:
        # Nada de rótulo inventado: o nome sai do `price_action_setup` que o
        # motor mediu, no mesmo vocabulário das estratégias nomeadas.
        casos = {
            "REVERSAL": "Padrões de Reversão em Zonas de Exaustão",
            "CONTINUATION": "Continuação de tendência",
            "WEAK": "Fluxo de Velas (Seguimento de Força)",
        }
        for setup, rotulo in casos.items():
            with self.subTest(setup=setup):
                saida = apply_live_demo(
                    barrado(metrics={"price_action_setup": setup}),
                    "EURUSD-OTC",
                    live_enabled=True,
                )
                self.assertEqual(saida["strategy_name"], rotulo)

    def test_sem_metricas_ainda_narra_sem_entregar_o_modo(self) -> None:
        saida = apply_live_demo(barrado(), "EURUSD-OTC", live_enabled=True)
        texto = saida["narrator_text"]
        self.assertTrue(texto.strip())
        self.assertNotIn("demonstra", texto.lower())
        self.assertNotIn("live", texto.lower())

    def test_nao_toca_em_mercado_aberto(self) -> None:
        # No aberto existe vantagem real (REV-Z); afrouxar ali jogaria fora a
        # única coisa que funciona.
        saida = apply_live_demo(barrado(), "EURUSD", live_enabled=True)
        self.assertFalse(saida["trade_allowed"])
        self.assertEqual(saida["live_demo_blocked"], "LIVE_SOMENTE_OTC")

    def test_nao_atropela_entrada_ja_aprovada(self) -> None:
        sinal = barrado(trade_allowed=True, strategy_key="RETRACEMENT_SR")
        saida = apply_live_demo(dict(sinal), "EURUSD-OTC", live_enabled=True)
        self.assertEqual(saida["strategy_key"], "RETRACEMENT_SR")

    def test_nao_dispensa_bloqueio_de_execucao(self) -> None:
        for bloqueio in ("STOP_LOSS_HIT", "OPERATION_IN_PROGRESS", "MIN_PAYOUT",
                         "PAYOUT_UNAVAILABLE", "ACCOUNT_DISCONNECTED"):
            with self.subTest(bloqueio=bloqueio):
                sinal = barrado(blocked_filters=[bloqueio])
                saida = apply_live_demo(sinal, "EURUSD-OTC", live_enabled=True)
                self.assertFalse(saida["trade_allowed"])
                self.assertEqual(saida["live_demo_blocked"], bloqueio)

    def test_sem_direcao_nao_inventa_uma(self) -> None:
        sinal = barrado(signal="WAIT", direction="WAIT")
        saida = apply_live_demo(sinal, "EURUSD-OTC", live_enabled=True)
        self.assertFalse(saida["trade_allowed"])
        self.assertEqual(saida["live_demo_blocked"], "LIVE_SEM_DIRECAO")

    def test_allows_devolve_o_motivo(self) -> None:
        pode, motivo = live_demo_allows(barrado(), "EURUSD-OTC")
        self.assertTrue(pode)
        self.assertIsNone(motivo)


class CadenciaTests(unittest.TestCase):
    def test_libera_o_que_o_portao_classico_barraria(self) -> None:
        """O ponto do modo: a vela chata vira entrada.

        A série precisa ser chata **e** estar longe dos níveis. Uma série
        totalmente travada — que era o que este teste usava — deixa o preço
        colado no suporte e na resistência ao mesmo tempo, e desde 2026-09-09
        isso é conflito de nível, que o modo LIVE não dispensa. O canal
        senoidal abaixo termina no meio do range, onde a região não tem nada a
        dizer e só a qualidade do setup barra.

        Desde 11/09 as velas também precisam ter corpo: vela com 75% de pavio
        é barrada pelo filtro de pavio (`WICK_EXCESS`), que o modo LIVE não
        dispensa — o teste deixaria de medir a cadência.
        """
        import math

        from backend import signal_engine as SE

        candles = []
        for i in range(200):
            base = 1.10000 + 0.00100 * math.sin(2 * math.pi * (i % 40) / 40)
            candles.append(
                {
                    "open": round(base - 0.000015, 6),
                    "close": round(base + 0.000015, 6),
                    "high": round(base + 0.00002, 6),
                    "low": round(base - 0.00002, 6),
                }
            )

        normal = SE.analyze_signal(
            "EURUSD-OTC", candles, "M1", strategy_mode="conservative", payout=87.0,
        )
        live = SE.analyze_signal(
            "EURUSD-OTC", candles, "M1", strategy_mode="conservative", payout=87.0,
            live_demo=True,
        )

        # Se a série deixar de estar fora da região, o teste deixa de medir o
        # que se propõe — falhar aqui é melhor do que passar por engano.
        self.assertEqual(normal["sr_respect_reason"], "OK_FORA_DA_REGIAO")
        self.assertFalse(normal["trade_allowed"])
        self.assertTrue(live["trade_allowed"])
        self.assertIs(live["live_demo"], True)


if __name__ == "__main__":
    unittest.main()


class PortaoDeConfiancaTests(unittest.TestCase):
    """A armadilha das duas escalas — a mesma que já pegou a REV-Z.

    O modo LIVE pontua 60 e o `min_confidence` do painel vem 80. Sem rebaixar
    o piso, TODA entrada de demonstração morria em NO_OPPORTUNITY e o robô
    continuava lento — o problema que o modo existe para resolver. Medido em
    produção 06/09: `[BEST_CANDIDATE] confidence=58` seguido de
    `[NO_OPPORTUNITY_NEXT_SESSION]`.
    """

    def test_entrada_live_rebaixa_o_piso(self) -> None:
        from backend.live_demo_mode import live_min_confidence

        self.assertEqual(live_min_confidence(80, {"live_demo": True}), LIVE_CONFIDENCE)

    def test_entrada_normal_mantem_o_minimo_do_usuario(self) -> None:
        from backend.live_demo_mode import live_min_confidence

        self.assertEqual(live_min_confidence(80, {}), 80)
        self.assertEqual(live_min_confidence(80, None), 80)
        self.assertEqual(live_min_confidence(80, {"live_demo": False}), 80)

    def test_nunca_sobe_o_piso_de_quem_configurou_menos(self) -> None:
        # Usuário com min_confidence 50 não pode ter o piso AUMENTADO para 60.
        from backend.live_demo_mode import live_min_confidence

        self.assertEqual(live_min_confidence(50, {"live_demo": True}), 50)

    def test_o_teto_do_modo_passa_no_portao_padrao_do_painel(self) -> None:
        from backend.live_demo_mode import live_min_confidence

        self.assertLessEqual(live_min_confidence(80, {"live_demo": True}), LIVE_CONFIDENCE)


class LiveDemoPortaoTests(unittest.TestCase):
    """O portão do ciclo estava refazendo o veto que o modo já dispensou.

    Incidente de 07/09/2026: conta de marketing relatou 30 minutos sem operar
    com o modo LIVE ligado. Medição de 24h de log de produção: 138 de 138
    liberações bloqueadas, zero ordens.
    """

    def liberado(self, **extra) -> dict:
        sinal = apply_live_demo(barrado(**extra), "EURGBP-OTC", live_enabled=True)
        self.assertTrue(sinal["trade_allowed"])
        return sinal

    def test_filtro_de_qualidade_dispensado_nao_barra_mais(self) -> None:
        # A lista que mais matava em produção, menos os três de nível: desde
        # 2026-09-09 a região de S/R não é dispensável nem em transmissão.
        sinal = self.liberado(
            blocked_filters=[
                "PRICE_ACTION_SETUP",
                "PUT_BODY",
                "TREND_CLEAR",
                "MIN_CONFIDENCE",
            ],
            payout=87.0,
        )
        passa, motivo = live_demo_passa_portao(
            sinal, min_payout=80, minimo_confianca=LIVE_CONFIDENCE
        )
        self.assertTrue(passa, motivo)
        self.assertIsNone(motivo)

    def test_regiao_de_suporte_e_resistencia_nao_e_dispensada(self) -> None:
        """A violação relatada pelo dono em 2026-09-09.

        Log de produção, conta de marketing com o modo ligado:
        ``LIVE_DEMO_RELEASE NZDUSD-OTC PUT
        barrados=LEVEL_CONFLICT,LEVEL_REJECTION,SR_ZONE`` seguido de
        ``ORDER_ACCEPTED order_id=14247169254`` 42 segundos depois — PUT colado
        no suporte, ordem real. Foram 4 dessas em 6 liberações num intervalo de
        48 h. Cadência de transmissão não justifica operar contra o nível.
        """
        for filtro in ("SR_ZONE", "LEVEL_CONFLICT", "LEVEL_REJECTION"):
            with self.subTest(filtro=filtro):
                sinal = apply_live_demo(
                    barrado(blocked_filters=[filtro], payout=87.0),
                    "NZDUSD-OTC",
                    live_enabled=True,
                )
                passa, motivo = live_demo_passa_portao(
                    sinal, min_payout=80, minimo_confianca=LIVE_CONFIDENCE
                )
                self.assertFalse(passa)
                self.assertEqual(motivo, filtro)

    def test_bloqueio_de_execucao_continua_barrando(self) -> None:
        for impeditivo in ("STOP_LOSS_HIT", "ACTIVE_CLOSED", "OPERATION_IN_PROGRESS"):
            with self.subTest(impeditivo=impeditivo):
                sinal = barrado(blocked_filters=[impeditivo], payout=87.0)
                # live_demo_allows já recusa; o portão é a segunda barreira.
                sinal["live_demo"] = True
                passa, motivo = live_demo_passa_portao(
                    sinal, min_payout=80, minimo_confianca=LIVE_CONFIDENCE
                )
                self.assertFalse(passa)
                self.assertIn(impeditivo, motivo or "")

    def test_payout_abaixo_do_minimo_barra(self) -> None:
        sinal = self.liberado(payout=70.0)
        passa, motivo = live_demo_passa_portao(
            sinal, min_payout=80, minimo_confianca=LIVE_CONFIDENCE
        )
        self.assertFalse(passa)
        self.assertIn("PAYOUT", motivo or "")

    def test_piso_de_confianca_do_modo_e_atendido(self) -> None:
        sinal = self.liberado(payout=87.0)
        # apply_live_demo pontua LIVE_CONFIDENCE; o piso vem de
        # live_min_confidence, que rebaixa o 80 do painel para o mesmo valor.
        passa, _ = live_demo_passa_portao(
            sinal,
            min_payout=80,
            minimo_confianca=live_min_confidence(80, sinal),
        )
        self.assertTrue(passa)

    def test_piso_acima_do_modo_ainda_barra(self) -> None:
        sinal = self.liberado(payout=87.0)
        passa, motivo = live_demo_passa_portao(
            sinal, min_payout=80, minimo_confianca=LIVE_CONFIDENCE + 10
        )
        self.assertFalse(passa)
        self.assertIn("PONTUACAO", motivo or "")

    def test_is_live_demo_so_marca_o_que_veio_do_modo(self) -> None:
        self.assertTrue(is_live_demo(self.liberado(payout=87.0)))
        self.assertFalse(is_live_demo(barrado()))
        self.assertFalse(is_live_demo(None))
        self.assertFalse(is_live_demo({"live_demo": "sim"}))
