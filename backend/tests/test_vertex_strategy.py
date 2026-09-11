"""Estratégia Vertex — porte do indicador ``Reversion`` entregue em 2026-09-09.

O teste de fidelidade recalcula o indicador aqui dentro, direto da fórmula do
script, e compara com o módulo. É o que dá para afirmar sem ter medido a
estratégia contra velas reais: que o valor produzido é o do gráfico do dono.

Os demais cobrem as armadilhas que este projeto já pagou caro: o formato de
vela da corretora (``max``/``min``) contra o dos datasets (``high``/``low``), e
a escala de confiança própria que precisa de rescale em todo portão a jusante.
"""

import unittest
from statistics import fmean

from backend import signal_engine as se
from backend import vertex_strategy
from backend.vertex_strategy import (
    VERTEX_CONFIDENCE_FLOOR,
    VERTEX_CONFIDENCE_MAX,
    VERTEX_OTC_SCORE_FLOOR,
    VERTEX_EXT_BOT,
    VERTEX_EXT_TOP,
    VERTEX_MIN_CANDLES,
    is_vertex_candidate,
    vertex_confidence,
    vertex_direction,
    vertex_evaluate,
    vertex_min_confidence,
    vertex_value,
)


def vela(open_: float, high: float, low: float, close: float) -> dict[str, float]:
    """Vela no formato da corretora (``max``/``min``)."""
    return {"open": open_, "close": close, "max": high, "min": low}


def para_formato_dataset(candles: list[dict[str, float]]) -> list[dict[str, float]]:
    """Converte velas de ``max``/``min`` para ``high``/``low``."""
    return [
        {"open": c["open"], "close": c["close"], "high": c["max"], "low": c["min"]}
        for c in candles
    ]


def vertex_de_referencia(candles: list[dict[str, float]]) -> float:
    """Reimplementação direta do script, para o branch curto (``length = 5``).

        mba    = SMA((high+low)/2, 5)
        var0   = iff((cdelta > high-low) or (high==low), cdelta, high-low)
        lrange = SMA(var0, 5) * 0.2
        vertex = (close-mba)/lrange + 0.15*momDev + 0.15*maDev

    Args:
        candles: Velas em ordem cronológica, formato da corretora.

    Returns:
        O valor do indicador na última vela.
    """
    altas = [c["max"] for c in candles]
    baixas = [c["min"] for c in candles]
    fechos = [c["close"] for c in candles]

    var0 = []
    for i in range(len(candles)):
        amplitude = altas[i] - baixas[i]
        anterior = fechos[i - 1] if i > 0 else fechos[i]
        cdelta = abs(fechos[i] - anterior)
        var0.append(cdelta if (cdelta > amplitude or altas[i] == baixas[i]) else amplitude)
    lrange = fmean(var0[-5:]) * 0.2

    medios = [(altas[i] + baixas[i]) / 2 for i in range(len(candles))]
    mba = fmean(medios[-5:])

    vclose = (fechos[-1] - mba) / lrange
    mom_dev = (fechos[-1] - fechos[-1 - 14]) / lrange
    ma_dev = (fmean(fechos[-9:]) - fmean(fechos[-21:])) / lrange
    return vclose + 0.15 * mom_dev + 0.15 * ma_dev


def serie_lateral(n: int = 60, base: float = 1.10000) -> list[dict[str, float]]:
    """Série que oscila em torno de um valor, sem esticar para lado nenhum."""
    candles = []
    for i in range(n):
        desvio = 0.00010 if i % 2 == 0 else -0.00010
        preco = base + desvio
        candles.append(vela(base, preco + 0.00005, preco - 0.00005, preco))
    return candles


def serie_esticada(n: int = 60, direcao: int = 1) -> list[dict[str, float]]:
    """Série lateral que dispara no fim, deixando o preço longe da média.

    Args:
        n: Total de velas.
        direcao: ``1`` estica para cima, ``-1`` para baixo.

    Returns:
        Velas em ordem cronológica, formato da corretora.
    """
    candles = serie_lateral(n - 1)
    ultimo = candles[-1]["close"]
    salto = 0.00300 * direcao
    fecho = ultimo + salto
    candles.append(
        vela(ultimo, max(ultimo, fecho) + 0.00002, min(ultimo, fecho) - 0.00002, fecho)
    )
    return candles


class FidelidadeAoScriptTest(unittest.TestCase):
    """O valor calculado é o do indicador do dono."""

    def test_valor_bate_com_a_formula_do_script(self) -> None:
        candles = serie_esticada()
        self.assertAlmostEqual(
            vertex_value(candles), vertex_de_referencia(candles), places=9
        )

    def test_serie_lateral_fica_perto_de_zero(self) -> None:
        valor = vertex_value(serie_lateral())
        self.assertIsNotNone(valor)
        self.assertLess(abs(valor), VERTEX_EXT_TOP)

    def test_mesmo_resultado_nos_dois_formatos_de_vela(self) -> None:
        """A lição de 2026-09-04: o motor normaliza para ``max``/``min``."""
        candles = serie_esticada()
        self.assertAlmostEqual(
            vertex_value(candles),
            vertex_value(para_formato_dataset(candles)),
            places=12,
        )


class DirecaoTest(unittest.TestCase):
    """Reversão: extremo para cima entra PUT, para baixo entra CALL."""

    def test_acima_do_nivel_superior_entra_put(self) -> None:
        self.assertEqual(vertex_direction(VERTEX_EXT_TOP + 0.1), "PUT")

    def test_abaixo_do_nivel_inferior_entra_call(self) -> None:
        self.assertEqual(vertex_direction(VERTEX_EXT_BOT - 0.1), "CALL")

    def test_entre_os_niveis_nao_opera(self) -> None:
        self.assertIsNone(vertex_direction(0.0))
        self.assertIsNone(vertex_direction(VERTEX_EXT_TOP - 0.1))

    def test_valor_ausente_nao_opera(self) -> None:
        self.assertIsNone(vertex_direction(None))

    def test_serie_esticada_para_cima_gera_put(self) -> None:
        veredito = vertex_evaluate("EURUSD", serie_esticada(direcao=1))
        self.assertEqual(veredito["direction"], "PUT")
        self.assertGreaterEqual(veredito["vertex"], VERTEX_EXT_TOP)

    def test_serie_esticada_para_baixo_gera_call(self) -> None:
        veredito = vertex_evaluate("EURUSD", serie_esticada(direcao=-1))
        self.assertEqual(veredito["direction"], "CALL")
        self.assertLessEqual(veredito["vertex"], VERTEX_EXT_BOT)


class VereditoTest(unittest.TestCase):
    """Os motivos de recusa são explícitos, não um ``None`` mudo."""

    def test_velas_insuficientes(self) -> None:
        veredito = vertex_evaluate("EURUSD", serie_lateral(10))
        self.assertIsNone(veredito["direction"])
        self.assertEqual(veredito["blocked"], "VERTEX_VELAS_INSUFICIENTES")

    def test_fora_do_extremo(self) -> None:
        veredito = vertex_evaluate("EURUSD", serie_lateral())
        self.assertIsNone(veredito["direction"])
        self.assertEqual(veredito["blocked"], "VERTEX_FORA_DO_EXTREMO")

    def test_serie_travada_nao_tem_volatilidade(self) -> None:
        travada = [vela(1.1, 1.1, 1.1, 1.1) for _ in range(60)]
        veredito = vertex_evaluate("EURUSD", travada)
        self.assertIsNone(veredito["direction"])
        self.assertEqual(veredito["blocked"], "VERTEX_SEM_VOLATILIDADE")

    def test_otc_recusado_quando_a_flag_esta_desligada(self) -> None:
        original = vertex_strategy.VERTEX_ALLOW_OTC
        vertex_strategy.VERTEX_ALLOW_OTC = False
        try:
            veredito = vertex_evaluate("EURUSD-OTC", serie_esticada())
            self.assertIsNone(veredito["direction"])
            self.assertEqual(veredito["blocked"], "VERTEX_OTC_RECUSADO")
        finally:
            vertex_strategy.VERTEX_ALLOW_OTC = original

    def test_otc_aceito_no_padrao(self) -> None:
        """A BullEx praticamente só vende OTC; recusar seria não operar nunca."""
        veredito = vertex_evaluate("EURUSD-OTC", serie_esticada())
        self.assertEqual(veredito["direction"], "PUT")

    def test_minimo_de_velas_cobre_a_media_lenta(self) -> None:
        self.assertGreaterEqual(VERTEX_MIN_CANDLES, vertex_strategy.VERTEX_MA_SLOW)
        self.assertGreater(VERTEX_MIN_CANDLES, vertex_strategy.VERTEX_MOM_LENGTH)


class ConfiancaTest(unittest.TestCase):
    """Escala própria, curta, e o rescale que ela obriga a jusante."""

    def test_fica_dentro_da_escala(self) -> None:
        for valor in (-40.0, -12.0, 0.0, 12.0, 40.0, None):
            conf = vertex_confidence(valor)
            self.assertGreaterEqual(conf, VERTEX_CONFIDENCE_FLOOR)
            self.assertLessEqual(conf, VERTEX_CONFIDENCE_MAX)

    def test_cresce_com_a_distancia_do_extremo(self) -> None:
        self.assertLess(
            vertex_confidence(VERTEX_EXT_TOP + 1), vertex_confidence(VERTEX_EXT_TOP + 6)
        )
        self.assertLess(
            vertex_confidence(VERTEX_EXT_BOT - 1), vertex_confidence(VERTEX_EXT_BOT - 6)
        )

    def test_satura_no_teto(self) -> None:
        self.assertEqual(vertex_confidence(VERTEX_EXT_TOP + 100), VERTEX_CONFIDENCE_MAX)

    def test_rescale_rebaixa_o_piso_so_para_candidato_vertex(self) -> None:
        candidato = {"vertex": {"direction": "PUT"}, "symbol": "EURUSD"}
        self.assertEqual(vertex_min_confidence(80, candidato), VERTEX_CONFIDENCE_MAX)
        self.assertEqual(vertex_min_confidence(80, {"vertex": None}), 80)
        self.assertEqual(vertex_min_confidence(80, None), 80)

    def test_rescale_nunca_aumenta_o_piso(self) -> None:
        for simbolo in ("EURUSD", "EURUSD-OTC"):
            with self.subTest(simbolo=simbolo):
                candidato = {"vertex": {"direction": "CALL"}, "symbol": simbolo}
                self.assertEqual(vertex_min_confidence(50, candidato), 50)

    def test_otc_usa_o_piso_proprio_do_portao(self) -> None:
        """Pedido do dono em 2026-09-09: piso 50 em OTC (era 55).

        Com o teto (75) só passariam os |vertex| >= 20, que saturam a confiança;
        os disparos típicos saem em ~59 e morreriam em MIN_CONFIDENCE. E com o
        chão da escala (55) o disparo-limite não tolerava penalidade nenhuma.
        """
        candidato = {"vertex": {"direction": "PUT"}, "symbol": "EURUSD-OTC"}
        self.assertEqual(vertex_min_confidence(80, candidato), VERTEX_OTC_SCORE_FLOOR)

    def test_mercado_aberto_fica_como_estava(self) -> None:
        """A mudança é só em OTC — o aberto não foi tocado, de propósito."""
        for simbolo in ("EURUSD", "GBPJPY", "USDCAD"):
            with self.subTest(simbolo=simbolo):
                candidato = {"vertex": {"direction": "CALL"}, "symbol": simbolo}
                self.assertEqual(
                    vertex_min_confidence(80, candidato), VERTEX_CONFIDENCE_MAX
                )

    def test_ativo_tambem_serve_de_fonte_do_simbolo(self) -> None:
        """O candidato do ciclo carrega `active`; o do motor, `symbol`."""
        candidato = {"vertex": {"direction": "PUT"}, "active": "GBPUSD-OTC"}
        self.assertEqual(vertex_min_confidence(80, candidato), VERTEX_OTC_SCORE_FLOOR)

    def test_piso_do_portao_e_o_chao_da_escala_sao_coisas_diferentes(self) -> None:
        """O piso OTC precisa ficar ABAIXO do chão da escala, ou não afrouxa nada.

        `vertex_confidence` nunca emite menos que `VERTEX_CONFIDENCE_FLOOR`. Se o
        portão exigir esse mesmo valor, o disparo-limite (|vertex| = 12) passa
        com zero folga e qualquer penalidade do motor clássico o derruba — que é
        como o robô ficou sem operar em 09/09. Reconflar as duas constantes
        (mexer no `_FLOOR` para afrouxar o portão) reintroduz exatamente isso.
        """
        self.assertLess(VERTEX_OTC_SCORE_FLOOR, VERTEX_CONFIDENCE_FLOOR)
        folga = VERTEX_CONFIDENCE_FLOOR - VERTEX_OTC_SCORE_FLOOR
        self.assertEqual(folga, 5)

    def test_flag_desliga_o_piso_de_otc(self) -> None:
        original = vertex_strategy.VERTEX_OTC_FLOOR_ENABLED
        vertex_strategy.VERTEX_OTC_FLOOR_ENABLED = False
        try:
            candidato = {"vertex": {"direction": "PUT"}, "symbol": "EURUSD-OTC"}
            self.assertEqual(
                vertex_min_confidence(80, candidato), VERTEX_CONFIDENCE_MAX
            )
        finally:
            vertex_strategy.VERTEX_OTC_FLOOR_ENABLED = original

    def test_identifica_candidato_da_estrategia(self) -> None:
        self.assertTrue(is_vertex_candidate({"vertex": {"direction": "PUT"}}))
        self.assertFalse(is_vertex_candidate({"vertex": {"direction": None}}))
        self.assertFalse(is_vertex_candidate({}))


class BranchLongoTest(unittest.TestCase):
    """``length > 7`` usa a outra fórmula de volatilidade do script."""

    def test_length_maior_que_sete_muda_o_valor(self) -> None:
        candles = serie_esticada(120)
        curto = vertex_value(candles)
        original = vertex_strategy.VERTEX_LENGTH
        vertex_strategy.VERTEX_LENGTH = 20
        try:
            longo = vertex_value(candles)
        finally:
            vertex_strategy.VERTEX_LENGTH = original
        self.assertIsNotNone(longo)
        self.assertNotAlmostEqual(curto, longo, places=6)


if __name__ == "__main__":
    unittest.main()


class ModoParaleloTest(unittest.TestCase):
    """A Vertex roda AO LADO do motor clássico, não por cima dele.

    Até 09/09 uma vela sem disparo virava WAIT/confiança 0 e calava o clássico.
    Como o |vertex| só passa do extremo em ~4% das velas, isso zerava 96% das
    análises e as contas pararam de operar sem erro nenhum no log.
    """

    def _classico(self):
        return {
            "signal": "CALL",
            "direction": "CALL",
            "confidence": 82,
            "score": 82,
            "strategy_score": 74,
            "trade_allowed": True,
            "blocked_filters": ["RSI_RANGE"],
            "block_reasons": ["RSI_RANGE"],
            "strategy_name": "Motor clássico",
            "quality_reason": "OK",
        }

    def _velas_paradas(self, n=80):
        # Preço plano: o Vertex fica perto de zero e não dispara.
        return [{"open": 1.0, "close": 1.0, "max": 1.0, "min": 1.0} for _ in range(n)]

    def test_sem_disparo_preserva_o_parecer_do_classico(self) -> None:
        original = se.VERTEX_ENABLED, se.VERTEX_PARALLEL
        se.VERTEX_ENABLED, se.VERTEX_PARALLEL = True, True
        try:
            s = se.apply_vertex_override(self._classico(), "EURUSD-OTC", self._velas_paradas())
            self.assertEqual(s["signal"], "CALL")
            self.assertEqual(s["confidence"], 82)
            self.assertEqual(s["strategy_score"], 74)
            self.assertTrue(s["trade_allowed"])
            # O clássico não pode ser rotulado como Vertex quando ela ficou muda.
            self.assertEqual(s["strategy_name"], "Motor clássico")
            # O motivo fica registrado, mas só para leitura (o nome exato
            # depende de por que não disparou: fora do extremo, sem
            # volatilidade, velas insuficientes...).
            self.assertTrue(
                any(str(f).startswith("VERTEX_") for f in s["blocked_filters"]),
                s["blocked_filters"],
            )
            self.assertIn("RSI_RANGE", s["blocked_filters"])
        finally:
            se.VERTEX_ENABLED, se.VERTEX_PARALLEL = original

    def test_sem_disparo_nao_vira_candidato_vertex(self) -> None:
        """Sem direção da Vertex, o piso do portão tem de continuar o do painel."""
        original = se.VERTEX_ENABLED, se.VERTEX_PARALLEL
        se.VERTEX_ENABLED, se.VERTEX_PARALLEL = True, True
        try:
            s = se.apply_vertex_override(self._classico(), "EURUSD-OTC", self._velas_paradas())
            self.assertFalse(vertex_strategy.is_vertex_candidate(s))
            self.assertEqual(vertex_min_confidence(80, s), 80)
        finally:
            se.VERTEX_ENABLED, se.VERTEX_PARALLEL = original

    def test_flag_desligada_volta_a_sobrepor(self) -> None:
        original = se.VERTEX_ENABLED, se.VERTEX_PARALLEL
        se.VERTEX_ENABLED, se.VERTEX_PARALLEL = True, False
        try:
            s = se.apply_vertex_override(self._classico(), "EURUSD-OTC", self._velas_paradas())
            self.assertEqual(s["signal"], "WAIT")
            self.assertEqual(s["confidence"], 0)
            self.assertFalse(s["trade_allowed"])
        finally:
            se.VERTEX_ENABLED, se.VERTEX_PARALLEL = original
