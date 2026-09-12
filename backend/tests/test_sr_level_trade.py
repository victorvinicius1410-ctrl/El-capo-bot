"""S/R como SINAL: encostou na resistência vende, no suporte compra.

Pedido do dono em 11/09/2026, depois de um dia em que o suporte e a
resistência só barravam: de 101 entradas anunciadas, 66 foram canceladas no
disparo porque o preço havia chegado num topo ou fundo. A regra passou a ter os
dois papéis — filtro (ninguém entra contra o nível) e sinal (entra a favor).

Série de teste: zigue-zague entre 1.1000 e 1.1010 em passos de 0.0002, então
cada vela tem range de um passo e o ATR é exatamente um passo. Isso deixa as
distâncias em ATR legíveis: 0.00005 do nível = 0.25 ATR.
"""

import unittest
from unittest import mock

from backend import main, signal_engine, sr_level_trade
from backend.sr_level_trade import (
    LEVEL_WICK_AGAINST,
    LEVEL_WICK_SEQUENCE,
    SR_LEVEL_MAX_PER_HOUR,
    LEVEL_WICK_OK,
    LEVEL_WICK_TWO_SIDED,
    STRATEGY_SR_LEVEL,
    find_level_trade,
    is_level_candidate,
    level_wick_ok,
)

BAIXO = 1.1000
ALTO = 1.1010
PASSO = 0.0002


def _vela(o, c, t):
    return {"from": t, "open": round(o, 6), "close": round(c, 6), "max": round(max(o, c), 6), "min": round(min(o, c), 6)}


def serie(n: int = 140) -> list[dict[str, float]]:
    """Zigue-zague com topos em ALTO e fundos em BAIXO (vários toques)."""
    velas: list[dict[str, float]] = []
    preco = BAIXO
    subindo = True
    for i in range(n):
        proximo = preco + PASSO if subindo else preco - PASSO
        velas.append(_vela(preco, proximo, i * 60))
        preco = round(proximo, 6)
        if preco >= ALTO:
            subindo = False
        elif preco <= BAIXO:
            subindo = True
    return velas


def _caminha(velas: list[dict[str, float]], destino: float, passo: float) -> list[dict[str, float]]:
    """Anda com o preço até `destino` em velas de corpo cheio, sem criar extremo novo.

    Serve para deixar o MOVIMENTO explícito nos testes: subindo, só a
    resistência à frente vale; descendo, só o suporte.
    """
    velas = list(velas)
    preco = velas[-1]["close"]
    t = velas[-1]["from"]
    while abs(destino - preco) > passo:
        proximo = preco + passo if destino > preco else preco - passo
        t += 60
        velas.append(_vela(preco, proximo, t))
        preco = round(proximo, 6)
    if abs(destino - preco) > 1e-9:
        velas.append(_vela(preco, destino, t + 60))
    return velas


def subindo_ate(preco: float) -> list[dict[str, float]]:
    """Série com os níveis antigos em ALTO/BAIXO e o preço SUBINDO até `preco`."""
    base = serie()
    meio = (ALTO + BAIXO) / 2
    return _caminha(_caminha(base, meio, PASSO), preco, PASSO / 2)


def descendo_ate(preco: float) -> list[dict[str, float]]:
    """Idem, com o preço DESCENDO até `preco`."""
    base = serie()
    meio = (ALTO + BAIXO) / 2
    return _caminha(_caminha(base, meio, PASSO), preco, PASSO / 2)


def com_preco(preco: float, velas=None) -> list[dict[str, float]]:
    """A série mais a vela atual (em formação) no preço pedido."""
    velas = velas or serie()
    return [*velas, {"from": len(velas) * 60, "open": preco, "close": preco, "max": preco, "min": preco}]


def fechadas_em(preco: float, velas=None) -> list[dict[str, float]]:
    """Série que TERMINA numa vela fechada no preço pedido.

    É a lista que a corretora devolve no disparo: a reconferência acrescenta a
    vela em formação por conta própria (`candles_with_current_candle`).
    """
    velas = list(velas or serie())
    anterior = velas[-1]["close"]
    velas.append(_vela(anterior, preco, velas[-1]["from"] + 60))
    return velas


class NivelPertoTest(unittest.TestCase):
    def test_perto_da_resistencia_e_venda(self) -> None:
        veredito = find_level_trade(com_preco(ALTO - 0.00005, subindo_ate(ALTO - 0.1 * PASSO)))
        self.assertIsNotNone(veredito)
        self.assertEqual(veredito["direction"], "PUT")
        self.assertEqual(veredito["side"], "RESISTENCIA")
        self.assertAlmostEqual(veredito["level"], ALTO, places=5)

    def test_perto_do_suporte_e_compra(self) -> None:
        veredito = find_level_trade(com_preco(BAIXO + 0.00005, descendo_ate(BAIXO + 0.1 * PASSO)))
        self.assertIsNotNone(veredito)
        self.assertEqual(veredito["direction"], "CALL")
        self.assertEqual(veredito["side"], "SUPORTE")

    def test_nao_precisa_estar_grudado(self) -> None:
        """Regra do dono: "se estiver próximo, pode operar, não precisa grudar"."""
        # ~0,3 ATR do nível: longe de grudar, dentro do "perto" (0,5 ATR).
        veredito = find_level_trade(com_preco(ALTO - 0.25 * PASSO, subindo_ate(ALTO - 0.3 * PASSO)))
        self.assertIsNotNone(veredito)
        self.assertGreater(abs(veredito["distance_atr"]), 0.15)
        self.assertIsNotNone(veredito)
        self.assertEqual(veredito["direction"], "PUT")

    def test_suporte_atras_de_preco_que_sobe_nao_e_compra(self) -> None:
        """O caso do vídeo (CHFJPY 12/09 15:44): preço subindo, suporte atrás.

        O robô comprava por causa do fundo logo abaixo, com resistência à
        frente. Agora, subindo, só a resistência acima vale.
        """
        velas = com_preco(ALTO - 0.3 * PASSO, subindo_ate(ALTO - 0.35 * PASSO))
        veredito = find_level_trade(velas)
        self.assertIsNotNone(veredito)
        self.assertEqual(veredito["direction"], "PUT")
        self.assertEqual(veredito["side"], "RESISTENCIA")
        self.assertGreater(veredito["movement_atr"], 0)

    def test_resistencia_atras_de_preco_que_desce_nao_e_venda(self) -> None:
        velas = com_preco(BAIXO + 0.3 * PASSO, descendo_ate(BAIXO + 0.35 * PASSO))
        veredito = find_level_trade(velas)
        self.assertIsNotNone(veredito)
        self.assertEqual(veredito["direction"], "CALL")
        self.assertEqual(veredito["side"], "SUPORTE")

    def test_extremo_recem_criado_nao_e_nivel(self) -> None:
        """Mínima nova feita pela vela que acabou de fechar não é suporte.

        Caso EURAUD 12/09 00:37: preço caindo 2,8 ATR, o robô comprou na mínima
        nova. Nível é o que já se provou, não o extremo do movimento em curso.
        """
        velas = descendo_ate(BAIXO - 2 * PASSO)   # rompe e faz mínima nova
        preco = velas[-1]["close"]
        self.assertIsNone(find_level_trade(com_preco(preco, velas)))

    def test_longe_de_tudo_nao_e_sinal(self) -> None:
        meio = (ALTO + BAIXO) / 2
        self.assertIsNone(find_level_trade(com_preco(meio, _caminha(serie(), meio, PASSO / 4))))

    def test_nivel_rompido_deixa_de_ser_nivel(self) -> None:
        self.assertIsNone(find_level_trade(com_preco(ALTO + 1.5 * PASSO)))

    def test_vela_que_passou_da_linha_nao_opera(self) -> None:
        """Regra do dono (11/09 23h): fechou do outro lado do nível, não opera.

        É o caso que produziu 9 perdas de um sinal só: o preço fechou abaixo do
        suporte e o robô comprou no meio do rompimento.
        """
        self.assertIsNone(find_level_trade(fechadas_em(BAIXO - 0.1 * PASSO)[:-1] + [
            {"from": 99999, "open": BAIXO - 0.1 * PASSO, "close": BAIXO - 0.1 * PASSO,
             "max": BAIXO - 0.1 * PASSO, "min": BAIXO - 0.1 * PASSO}
        ]))

    def test_furar_com_o_pavio_e_voltar_continua_valendo(self) -> None:
        """Pavio abaixo do suporte com fechamento acima é rejeição, não rompimento."""
        velas = serie()
        velas.append(_vela(BAIXO + 0.2 * PASSO, BAIXO + 0.1 * PASSO, velas[-1]["from"] + 60))
        velas[-1]["min"] = BAIXO - 0.5 * PASSO  # o pavio furou a linha
        veredito = find_level_trade(com_preco(BAIXO + 0.1 * PASSO, velas))
        self.assertIsNotNone(veredito)
        self.assertEqual(veredito["direction"], "CALL")

    def test_espremido_entre_os_dois_lados_nao_opera(self) -> None:
        meio = (ALTO + BAIXO) / 2
        velas = com_preco(meio, _caminha(serie(), meio, PASSO / 4))
        with mock.patch.object(sr_level_trade, "SR_LEVEL_PROXIMITY_ATR", 10.0), mock.patch.object(
            sr_level_trade, "SR_LEVEL_TREND_ATR", 99.0
        ):
            self.assertIsNone(find_level_trade(velas))

    def test_desligado_nao_devolve_nada(self) -> None:
        with mock.patch.object(sr_level_trade, "SR_LEVEL_TRADE_ENABLED", False):
            self.assertIsNone(find_level_trade(com_preco(ALTO - 0.00005)))

    def test_poucas_velas_nao_devolve_nada(self) -> None:
        self.assertIsNone(find_level_trade(com_preco(ALTO, serie(20))))


class PavioNoNivelTest(unittest.TestCase):
    """O pavio de rejeição a favor libera; contra a entrada, barra."""

    def _com_ultima(self, o, c, h, l):
        velas = serie()
        velas[-1] = {"from": velas[-1]["from"], "open": o, "close": c, "max": h, "min": l}
        return com_preco(ALTO - 0.00005, velas)

    def test_pavio_de_rejeicao_na_resistencia_libera_a_venda(self) -> None:
        # Pavio de cima grande: o preço subiu, bateu e voltou. É a confirmação.
        velas = self._com_ultima(ALTO - PASSO, ALTO - 0.8 * PASSO, ALTO + 0.2 * PASSO, ALTO - PASSO)
        self.assertEqual(level_wick_ok(velas, "PUT"), (True, LEVEL_WICK_OK))

    def test_pavio_contra_a_venda_barra(self) -> None:
        velas = self._com_ultima(ALTO - 0.2 * PASSO, ALTO - 0.1 * PASSO, ALTO, ALTO - PASSO)
        pode, motivo = level_wick_ok(velas, "PUT")
        self.assertFalse(pode)
        self.assertEqual(motivo, LEVEL_WICK_AGAINST)

    def test_velas_seguidas_cheias_de_pavio_barram(self) -> None:
        """Dono, 12/09: "pegou operação em uma vela que deixou bastante pavio".

        Caso real USDCAD 15:58: as 3 velas anteriores com 57%, 84% e 49% de
        pavio. O pavio CONTRA a entrada era pequeno, então a regra direcional
        sozinha liberava. Mercado indeciso barra, de qualquer lado.
        """
        velas = serie()
        # duas das três últimas com pavio grande A FAVOR da compra (embaixo)
        for i in (-1, -3):
            base = velas[i]["open"]
            velas[i] = {"from": velas[i]["from"], "open": base, "close": base + 0.2 * PASSO,
                        "max": base + 0.25 * PASSO, "min": base - 0.6 * PASSO}
        velas = descendo_ate(BAIXO + 0.2 * PASSO)[:-3] + velas[-3:]
        pode, motivo = level_wick_ok(com_preco(velas[-1]["close"], velas), "CALL")
        self.assertFalse(pode)
        self.assertEqual(motivo, LEVEL_WICK_SEQUENCE)

    def test_um_pavio_de_rejeicao_sozinho_continua_liberando(self) -> None:
        velas = serie()
        base = velas[-1]["open"]
        velas[-1] = {"from": velas[-1]["from"], "open": base, "close": base + 0.3 * PASSO,
                     "max": base + 0.35 * PASSO, "min": base - 0.55 * PASSO}
        pode, motivo = level_wick_ok(com_preco(velas[-1]["close"], velas), "CALL")
        self.assertTrue(pode, motivo)

    def test_vela_indecisa_dos_dois_lados_barra(self) -> None:
        meio = ALTO - 0.5 * PASSO
        velas = self._com_ultima(meio - 0.05 * PASSO, meio + 0.05 * PASSO, meio + 0.45 * PASSO, meio - 0.45 * PASSO)
        pode, motivo = level_wick_ok(velas, "PUT")
        self.assertFalse(pode)
        self.assertIn(motivo, {LEVEL_WICK_TWO_SIDED, LEVEL_WICK_AGAINST})


class MotorDecidePeloNivelTest(unittest.TestCase):
    def _analisa(self, preco, velas=None):
        return signal_engine.analyze_signal("EURUSD-OTC", com_preco(preco, velas), "M1", payout=87.0)

    def test_na_resistencia_o_sinal_vira_venda(self) -> None:
        sinal = self._analisa(ALTO - 0.00005, subindo_ate(ALTO - 0.1 * PASSO))
        self.assertEqual(sinal["signal"], "PUT")
        self.assertEqual(sinal["strategy_key"], STRATEGY_SR_LEVEL)
        self.assertTrue(sinal["trade_allowed"])
        self.assertEqual(sinal["sr_level"]["side"], "RESISTENCIA")
        self.assertTrue(is_level_candidate(sinal))

    def test_no_suporte_o_sinal_vira_compra(self) -> None:
        sinal = self._analisa(BAIXO + 0.00005, descendo_ate(BAIXO + 0.1 * PASSO))
        self.assertEqual(sinal["signal"], "CALL")
        self.assertEqual(sinal["sr_level"]["side"], "SUPORTE")

    def test_longe_do_nivel_quem_decide_e_o_classico(self) -> None:
        sinal = self._analisa((ALTO + BAIXO) / 2)
        self.assertNotEqual(sinal.get("strategy_key"), STRATEGY_SR_LEVEL)
        self.assertIsNone(sinal.get("sr_level"))

    def test_mercado_aberto_continua_da_revz(self) -> None:
        sinal = {"revz": {"direction": None, "blocked": "REVZ_SEM_DESVIO_EXTREMO"}, "signal": "CALL"}
        igual = signal_engine.apply_level_trade(dict(sinal), "EURUSD", com_preco(ALTO - 0.00005))
        self.assertEqual(igual["signal"], "CALL")
        self.assertNotIn("sr_level", igual)


class PortoesDoNivelTest(unittest.TestCase):
    """Os dois portões que já calaram REV-Z, SR-R, Vertex e modo LIVE."""

    def _candidato(self):
        return {
            "symbol": "EURUSD-OTC",
            "signal": "PUT",
            "direction": "PUT",
            "confidence": 90,
            "payout": 87.0,
            "strategy_key": STRATEGY_SR_LEVEL,
            "sr_level": {"direction": "PUT", "side": "RESISTENCIA", "level": ALTO, "touches": 3,
                         "source": "PIVO_2_TOQUES", "distance_atr": 0.25, "atr": PASSO, "confidence": 90},
            # Filtros do motor clássico que uma entrada de reversão sempre leva.
            "blocked_filters": ["EMA_TREND", "RSI_RANGE", "LAST_3_ALIGNMENT", "PUT_BODY"],
            "trade_allowed": True,
        }

    def test_portao_de_estrategia_libera_com_filtros_do_classico_barrados(self) -> None:
        state = main.auto_trader.start("user-nivel-guard")
        state.min_confidence = 80
        state.min_payout = 80
        liberado, selecionado, motivo = main.apply_strategy_guard(
            "user-nivel-guard", state, self._candidato(), payout=87.0
        )
        self.assertTrue(liberado, motivo)
        self.assertEqual(selecionado["direction"], "PUT")
        self.assertEqual(selecionado["strategy_score"], 90)

    def test_portao_de_estrategia_respeita_o_minimo_do_painel(self) -> None:
        state = main.auto_trader.start("user-nivel-min")
        state.min_confidence = 95
        state.min_payout = 80
        liberado, _, motivo = main.apply_strategy_guard(
            "user-nivel-min", state, self._candidato(), payout=87.0
        )
        self.assertFalse(liberado)
        self.assertEqual(motivo, "MIN_CONFIDENCE")

    def test_portao_do_ciclo_libera_a_entrada_de_nivel(self) -> None:
        state = main.auto_trader.start("user-nivel-ciclo")
        state.min_payout = 80
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                self._candidato(), state, minimum_confidence=80, user_id="user-nivel-ciclo"
            )
        )

    def test_portao_do_ciclo_barra_payout_baixo(self) -> None:
        state = main.auto_trader.start("user-nivel-payout")
        state.min_payout = 90
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                self._candidato(), state, minimum_confidence=80, user_id="user-nivel-payout"
            )
        )


class DisparoDecidePeloNivelTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        sr_level_trade._entradas_de_nivel.clear()

    async def _rodar(self, candidato, velas):
        with (
            mock.patch.object(
                main, "call_bullex_service",
                new=mock.AsyncMock(return_value=(200, {"ok": True, "candles": velas})),
            ),
            mock.patch.object(main, "extract_candles", return_value=velas),
        ):
            return await main.revalidate_level_before_entry("user-disparo", candidato, "M1")

    async def test_troca_a_direcao_em_vez_de_cancelar(self) -> None:
        """O caso das 66 entradas canceladas: análise CALL, preço na resistência."""
        candidato = {"symbol": "EURUSD-OTC", "direction": "CALL", "signal": "CALL"}
        motivo = await self._rodar(candidato, fechadas_em(ALTO - 0.00005))
        self.assertIsNone(motivo)
        self.assertEqual(candidato["direction"], "PUT")
        self.assertEqual(candidato["signal"], "PUT")
        self.assertEqual(candidato["strategy_key"], STRATEGY_SR_LEVEL)

    async def test_mantem_a_direcao_quando_ja_esta_a_favor(self) -> None:
        candidato = {"symbol": "EURUSD-OTC", "direction": "CALL", "signal": "CALL"}
        motivo = await self._rodar(candidato, fechadas_em(BAIXO + 0.00005))
        self.assertIsNone(motivo)
        self.assertEqual(candidato["direction"], "CALL")

    async def test_nivel_que_sumiu_cancela_a_entrada_de_nivel(self) -> None:
        candidato = {
            "symbol": "EURUSD-OTC", "direction": "PUT", "signal": "PUT",
            "strategy_key": STRATEGY_SR_LEVEL, "sr_level": {"direction": "PUT", "side": "RESISTENCIA"},
        }
        motivo = await self._rodar(candidato, fechadas_em((ALTO + BAIXO) / 2))
        self.assertEqual(motivo, "SR_ZONE_NA_ENTRADA")

    async def test_no_teto_da_hora_nao_troca_a_direcao_e_cancela(self) -> None:
        """Sem entrada de nível disponível, entrar como a análise queria seria
        entrar CONTRA o nível — então não há entrada nesta vela."""
        for _ in range(SR_LEVEL_MAX_PER_HOUR):
            sr_level_trade.registrar_entrada_de_nivel("user-disparo")
        candidato = {"symbol": "EURUSD-OTC", "direction": "CALL", "signal": "CALL"}
        motivo = await self._rodar(candidato, fechadas_em(ALTO - 0.00005))
        self.assertEqual(motivo, "SR_ZONE_NA_ENTRADA")
        self.assertEqual(candidato["direction"], "CALL")

    async def test_pavio_contra_no_nivel_cancela(self) -> None:
        velas = serie()
        velas[-1] = {"from": velas[-1]["from"], "open": ALTO - 0.2 * PASSO, "close": ALTO - 0.1 * PASSO,
                     "max": ALTO, "min": ALTO - PASSO}
        candidato = {"symbol": "EURUSD-OTC", "direction": "PUT", "signal": "PUT"}
        motivo = await self._rodar(candidato, velas)
        self.assertEqual(motivo, "PAVIO_NA_ENTRADA")


class TetoDeNivelPorHoraTest(unittest.TestCase):
    """No máximo 3 entradas de nível por conta por hora (dono, 11/09 23h)."""

    def setUp(self) -> None:
        sr_level_trade._entradas_de_nivel.clear()

    def _candidato(self):
        return {
            "symbol": "EURUSD-OTC", "signal": "PUT", "direction": "PUT", "confidence": 90,
            "payout": 87.0, "strategy_key": STRATEGY_SR_LEVEL, "trade_allowed": True,
            "sr_level": {"direction": "PUT", "side": "RESISTENCIA", "level": ALTO, "touches": 3,
                         "source": "PIVO_2_TOQUES", "distance_atr": 0.25, "atr": PASSO, "confidence": 90},
        }

    def test_conta_so_a_ultima_hora(self) -> None:
        agora = 1_000_000.0
        sr_level_trade.registrar_entrada_de_nivel("u", agora - 4000)  # mais de 1h
        for i in range(2):
            sr_level_trade.registrar_entrada_de_nivel("u", agora - 60 * i)
        self.assertEqual(sr_level_trade.entradas_de_nivel_na_hora("u", agora), 2)
        self.assertFalse(sr_level_trade.teto_de_nivel_atingido("u", agora))

    def test_teto_atingido_no_terceiro(self) -> None:
        agora = 1_000_000.0
        for _ in range(SR_LEVEL_MAX_PER_HOUR):
            sr_level_trade.registrar_entrada_de_nivel("u", agora)
        self.assertTrue(sr_level_trade.teto_de_nivel_atingido("u", agora))

    def test_portao_de_estrategia_barra_no_teto(self) -> None:
        state = main.auto_trader.start("user-teto")
        state.min_confidence = 80
        state.min_payout = 80
        for _ in range(SR_LEVEL_MAX_PER_HOUR):
            sr_level_trade.registrar_entrada_de_nivel("user-teto")
        liberado, _, motivo = main.apply_strategy_guard("user-teto", state, self._candidato(), payout=87.0)
        self.assertFalse(liberado)
        self.assertEqual(motivo, "SR_LEVEL_HOURLY_LIMIT")

    def test_portao_do_ciclo_barra_no_teto(self) -> None:
        state = main.auto_trader.start("user-teto-ciclo")
        state.min_payout = 80
        for _ in range(SR_LEVEL_MAX_PER_HOUR):
            sr_level_trade.registrar_entrada_de_nivel("user-teto-ciclo")
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                self._candidato(), state, minimum_confidence=80, user_id="user-teto-ciclo"
            )
        )


class CamposDoNivelTest(unittest.TestCase):
    def test_veredito_sobrevive_as_listas_fixas(self) -> None:
        from backend import robot_persistence

        self.assertIn("sr_level", main.ANALYSIS_DETAIL_FIELDS)
        self.assertIn("sr_level", robot_persistence.TRADE_ANALYSIS_FIELDS)


if __name__ == "__main__":
    unittest.main()
