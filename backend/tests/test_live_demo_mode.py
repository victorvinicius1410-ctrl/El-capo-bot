"""Testes do modo LIVE (cadência de demonstração)."""

from __future__ import annotations

import unittest
from datetime import datetime

from backend import signal_engine
from backend.live_demo_mode import (
    LIVE_CONFIDENCE,
    LIVE_MAX_EXPIRATION_MINUTES,
    LIVE_MAX_SECONDS_BETWEEN_ENTRIES,
    LIVE_MIN_SECONDS_BETWEEN_ENTRIES,
    LIVE_NON_WAIVABLE,
    STRATEGY_LIVE_DEMO,
    apply_live_demo,
    is_live_demo,
    live_demo_allows,
    live_cadencia_estourada,
    live_espera_espacamento,
    live_demo_passa_portao,
    live_expiration_minutes,
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


class LiveDemoExpiracaoTests(unittest.TestCase):
    """Teto de duração da entrada de demonstração (pedido do dono, 15/09/2026).

    A duração normal sai do timeframe da conta. Uma conta em M15 abriria
    entrada de 15 minutos no meio da transmissão, que é o oposto do que o modo
    existe para fazer.
    """

    def test_conta_longa_e_encurtada_para_o_teto(self) -> None:
        for minutos in (15, 30):
            with self.subTest(minutos=minutos):
                sinal = {"live_demo": True}
                self.assertEqual(
                    live_expiration_minutes(minutos, sinal),
                    LIVE_MAX_EXPIRATION_MINUTES,
                )

    def test_conta_curta_nao_muda(self) -> None:
        """M1 e M5 já entram abaixo do teto — em 15/09 as 5 contas de
        marketing estavam todas em M1, então na prática o teto é uma garantia
        e não uma mudança de comportamento."""
        for minutos in (1, 5):
            with self.subTest(minutos=minutos):
                self.assertEqual(
                    live_expiration_minutes(minutos, {"live_demo": True}), minutos
                )

    def test_entrada_normal_nunca_e_encurtada(self) -> None:
        for candidato in ({}, {"live_demo": False}, {"live_demo": None}, None):
            with self.subTest(candidato=candidato):
                self.assertEqual(live_expiration_minutes(15, candidato), 15)

    def test_valor_invalido_cai_no_teto(self) -> None:
        self.assertEqual(
            live_expiration_minutes("nao-e-numero", {"live_demo": True}),
            LIVE_MAX_EXPIRATION_MINUTES,
        )

    def test_valor_invalido_de_entrada_normal_passa_intacto(self) -> None:
        """O teto do LIVE não pode virar validador da ordem normal: quem
        valida duração é ``validate_buy_real_order_payload``. Converter antes
        de checar o modo fazia uma operação normal com valor esquisito virar
        5 minutos sem ninguém ver."""
        self.assertEqual(live_expiration_minutes("nao-e-numero", {}), "nao-e-numero")


class LiveDemoCadenciaTests(unittest.TestCase):
    """Teto de tempo sem entrar (pedido do dono, 15/09/2026).

    "Preciso que pegue no máximo a cada 5 minutos, pode ser antes."
    """

    def test_sem_entrada_nenhuma_conta_como_estourado(self) -> None:
        """Robô que acabou de ligar não espera 5 min para começar a contar."""
        self.assertTrue(live_cadencia_estourada(None, datetime(2026, 9, 15, 20, 0, 0)))

    def test_dentro_do_teto_nao_estoura(self) -> None:
        agora = datetime(2026, 9, 15, 20, 5, 0)
        self.assertFalse(
            live_cadencia_estourada(datetime(2026, 9, 15, 20, 1, 0), agora)
        )

    def test_no_teto_exato_estoura(self) -> None:
        agora = datetime(2026, 9, 15, 20, 5, 0)
        self.assertTrue(
            live_cadencia_estourada(datetime(2026, 9, 15, 20, 0, 0), agora)
        )
        self.assertEqual(LIVE_MAX_SECONDS_BETWEEN_ENTRIES, 300)

    def test_payout_abaixo_do_minimo_passa_quando_a_cadencia_estourou(self) -> None:
        """A seca vale mais que 2 pontos de payout — mas só na seca."""
        sinal = apply_live_demo(
            barrado(blocked_filters=["MIN_CONFIDENCE"], payout=78.0),
            "EURUSD-OTC",
            live_enabled=True,
        )
        passa, motivo = live_demo_passa_portao(
            sinal, min_payout=80, minimo_confianca=LIVE_CONFIDENCE
        )
        self.assertFalse(passa, "sem seca, o payout mínimo do painel vale")
        self.assertIn("PAYOUT_ABAIXO_DO_MINIMO", str(motivo))

        passa, motivo = live_demo_passa_portao(
            sinal,
            min_payout=80,
            minimo_confianca=LIVE_CONFIDENCE,
            cadencia_estourada=True,
        )
        self.assertTrue(passa, motivo)
        self.assertIsNone(motivo)

    def test_bloqueio_de_execucao_nao_cede_nem_na_seca(self) -> None:
        """Forçar ordem em ativo fechado não gera entrada, gera recusa."""
        for impeditivo in ("ACTIVE_CLOSED", "STOP_LOSS_HIT", "OPERATION_IN_PROGRESS"):
            with self.subTest(impeditivo=impeditivo):
                sinal = barrado(blocked_filters=[impeditivo], payout=87.0)
                sinal["live_demo"] = True
                passa, motivo = live_demo_passa_portao(
                    sinal,
                    min_payout=80,
                    minimo_confianca=LIVE_CONFIDENCE,
                    cadencia_estourada=True,
                )
                self.assertFalse(passa)
                self.assertEqual(motivo, impeditivo)


class LiveDemoEspacamentoTests(unittest.TestCase):
    """Piso de tempo entre entradas (decisão do dono, 15/09/2026).

    "Não é praticamente toda vela... no máximo a cada 5 minutos, não a cada 1."
    Piso 3 min + teto 5 min = 12 a 20 entradas por hora.
    """

    def test_piso_e_teto_sao_coerentes(self) -> None:
        self.assertEqual(LIVE_MIN_SECONDS_BETWEEN_ENTRIES, 180)
        self.assertLess(
            LIVE_MIN_SECONDS_BETWEEN_ENTRIES, LIVE_MAX_SECONDS_BETWEEN_ENTRIES
        )

    def test_dentro_do_piso_espera(self) -> None:
        """Vela seguinte em M1: 1 min depois ainda não pode entrar."""
        agora = datetime(2026, 9, 15, 20, 1, 0)
        self.assertTrue(
            live_espera_espacamento(datetime(2026, 9, 15, 20, 0, 0), agora)
        )

    def test_passado_o_piso_libera(self) -> None:
        agora = datetime(2026, 9, 15, 20, 3, 0)
        self.assertFalse(
            live_espera_espacamento(datetime(2026, 9, 15, 20, 0, 0), agora)
        )

    def test_primeira_entrada_da_sessao_nao_espera(self) -> None:
        """Sem entrada anterior não há o que espaçar — a transmissão começa."""
        self.assertFalse(
            live_espera_espacamento(None, datetime(2026, 9, 15, 20, 0, 0))
        )

    def test_janela_entre_piso_e_teto_nao_estoura(self) -> None:
        """Entre 3 e 5 min: pode entrar, mas ainda não é seca."""
        agora = datetime(2026, 9, 15, 20, 4, 0)
        ultima = datetime(2026, 9, 15, 20, 0, 0)
        self.assertFalse(live_espera_espacamento(ultima, agora))
        self.assertFalse(live_cadencia_estourada(ultima, agora))


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

    def test_nivel_e_pavio_sao_dispensados(self) -> None:
        """Decisão do dono em 2026-09-15: o modo LIVE dispensa nível e pavio.

        Reverte a regra de 09/09 (``SR_ZONE``/``LEVEL_CONFLICT``/
        ``LEVEL_REJECTION``) e a de 11/09 (``WICK_EXCESS``), que tinham entrado
        depois de ``LIVE_DEMO_RELEASE NZDUSD-OTC PUT`` virar ordem real colada
        no suporte. A justificativa nova é a duração: a entrada do modo LIVE é
        curta (teto de ``LIVE_MAX_EXPIRATION_MINUTES``) e nessa escala nível e
        pavio não mandam na vela.

        O efeito colateral é conhecido e aceito: o robô VAI entrar contra
        suporte e resistência durante a transmissão. Este teste existe para
        que a reversão seja uma decisão explícita e não um acidente — se
        alguém recolocar esses filtros em ``LIVE_NON_WAIVABLE``, é aqui que
        aparece.
        """
        for filtro in ("SR_ZONE", "LEVEL_CONFLICT", "LEVEL_REJECTION", "WICK_EXCESS"):
            with self.subTest(filtro=filtro):
                self.assertNotIn(filtro, LIVE_NON_WAIVABLE)
                sinal = apply_live_demo(
                    barrado(blocked_filters=[filtro], payout=87.0),
                    "NZDUSD-OTC",
                    live_enabled=True,
                )
                passa, motivo = live_demo_passa_portao(
                    sinal, min_payout=80, minimo_confianca=LIVE_CONFIDENCE
                )
                self.assertTrue(passa, motivo)
                self.assertIsNone(motivo)

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
