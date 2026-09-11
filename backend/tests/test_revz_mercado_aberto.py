"""REV-Z como motor do mercado aberto, OTC intacto (10/09/2026).

Pedido do dono: o mercado aberto passa a operar a estratégia simulada em 05/09
(média de 120 velas M1, |z| >= 4,5); o OTC continua com motor clássico + Vertex.

Os testes travam as armadilhas que este código já pagou:
- **dois portões**: `apply_strategy_guard` e `candidate_meets_cycle_threshold`
  reavaliam os filtros do motor clássico, que segue tendência e barra toda
  entrada de reversão;
- **lista fixa de campos**: o veredito `revz` precisa sobreviver à montagem do
  candidato do ciclo;
- **cache na reconferência**: a confirmação no disparo tem de ir à corretora;
- **vela em formação**: o robô analisa a vela N aos 5-20 s e entra na N+1; a
  condição simulada só existe no FECHAMENTO da N.
"""

from __future__ import annotations

import asyncio
import math
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from backend import main
from backend import robot_persistence
from backend import signal_engine as se
from backend.reversion_strategy import (
    REVZ_CONFIDENCE_FLOOR,
    REVZ_LOOKBACK,
    REVZ_NON_WAIVABLE,
    closes_of_closed_candles,
    is_revz_candidate,
    revz_confirm_at_entry,
    revz_min_confidence,
    revz_passa_portao,
)

INICIO = 1789000000 - (1789000000 % 60)


def serie(n: int = 160, *, queda_final: float = 0.0, inicio: int = INICIO) -> list[dict]:
    """Velas M1 no formato da corretora, oscilando em torno de 1.1000.

    Com ``queda_final`` a última vela fecha bem abaixo — |z| muito acima de 4,5.
    """
    velas = []
    anterior = 1.1000
    for i in range(n):
        fechamento = 1.1000 + 0.0002 * math.sin(i / 3.0)
        if i == n - 1:
            fechamento -= queda_final
        velas.append(
            {
                "from": inicio + 60 * i,
                "open": anterior,
                "close": fechamento,
                "max": max(anterior, fechamento) + 0.00003,
                "min": min(anterior, fechamento) - 0.00003,
                "volume": 10,
            }
        )
        anterior = fechamento
    return velas


class RevzLigada(unittest.TestCase):
    """Base: REV-Z e Vertex ligadas, como no `.env` da produção."""

    def setUp(self) -> None:
        for alvo, nome in ((se, "REVZ_ENABLED"), (main, "REVZ_ENABLED"), (se, "VERTEX_ENABLED")):
            patcher = mock.patch.object(alvo, nome, True)
            patcher.start()
            self.addCleanup(patcher.stop)


class MotorPorMercadoTest(RevzLigada):
    def test_otc_continua_com_o_motor_de_hoje(self) -> None:
        sinal = se.analyze_signal("EURUSD-OTC", serie(queda_final=0.002), timeframe="M1")
        self.assertNotIn("revz", sinal)
        self.assertNotEqual(sinal.get("confidence_model_version"), "revz-v1")

    def test_aberto_sem_extremo_fica_sem_entrada(self) -> None:
        sinal = se.analyze_signal("EURUSD", serie(), timeframe="M1")
        self.assertEqual(sinal["revz"]["blocked"], "REVZ_SEM_DESVIO_EXTREMO")
        self.assertEqual(sinal["signal"], "WAIT")
        self.assertFalse(sinal["trade_allowed"])
        self.assertEqual(sinal["strategy_key"], "REVZ")

    def test_aberto_no_extremo_decide_pela_reversao(self) -> None:
        sinal = se.analyze_signal("EURUSD", serie(queda_final=0.002), timeframe="M1")
        self.assertEqual(sinal["signal"], "CALL")
        self.assertTrue(sinal["trade_allowed"])
        self.assertLessEqual(sinal["revz"]["z"], -4.5)
        self.assertEqual(sinal["confidence_model_version"], "revz-v1")
        self.assertEqual(sinal["strategy_key"], "REVZ")
        self.assertGreaterEqual(sinal["confidence"], REVZ_CONFIDENCE_FLOOR)
        self.assertEqual(sinal["blocked_filters"], [])

    def test_vertex_nao_sobrescreve_o_aberto(self) -> None:
        sinal = se.analyze_signal("EURUSD", serie(), timeframe="M1")
        self.assertNotEqual(sinal.get("strategy_key"), se.STRATEGY_VERTEX)
        self.assertNotIn("vertex", sinal)

    def test_m5_sem_velas_m1_nao_opera(self) -> None:
        # O z das velas M5 mede outra coisa (49,59% em 05/09); sem as M1 a
        # vela fica sem entrada em vez de cair para o timeframe da operação.
        sinal = se.analyze_signal("EURUSD", serie(queda_final=0.002), timeframe="M5")
        self.assertEqual(sinal["revz"]["blocked"], "REVZ_VELAS_INSUFICIENTES")
        self.assertFalse(sinal["trade_allowed"])

    def test_m5_usa_o_z_das_velas_m1(self) -> None:
        sinal = se.analyze_signal(
            "EURUSD",
            serie(),
            timeframe="M5",
            m1_candles=serie(queda_final=0.002),
        )
        self.assertEqual(sinal["signal"], "CALL")

    def test_mercado_nao_e_mais_promovido(self) -> None:
        quarta = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(main.effective_market_mode("BOTH", now=quarta), "BOTH")
        self.assertEqual(main.effective_market_mode("OTC", now=quarta), "OTC")


def candidato_do_ciclo(sinal: dict, simbolo: str = "EURUSD", payout: float = 85.0) -> dict:
    """Reproduz a montagem de `main.py`: só os campos da lista fixa sobrevivem."""
    candidato = {k: sinal[k] for k in main.ANALYSIS_DETAIL_FIELDS if k in sinal}
    direcao = sinal.get("direction") or sinal.get("signal") or "WAIT"
    candidato.update(
        {
            "symbol": simbolo,
            "direction": direcao,
            "signal": direcao,
            "strategy_score": int(sinal.get("strategy_score") or 0),
            "score": int(sinal.get("strategy_score") or 0),
            "confidence": int(sinal.get("confidence") or 0),
            "payout": payout,
            "blocked_filters": list(sinal.get("blocked_filters") or []),
            "trade_allowed": bool(sinal.get("trade_allowed")),
            "metrics": dict(sinal.get("metrics") or {}),
            "timeframe": "M1",
        }
    )
    return candidato


class PortoesTest(RevzLigada):
    def setUp(self) -> None:
        super().setUp()
        self.state = SimpleNamespace(
            min_confidence=80,
            min_payout=80,
            strategy_mode="conservative",
            timeframe="M1",
        )
        sinal = se.analyze_signal("EURUSD", serie(queda_final=0.002), timeframe="M1")
        self.assertEqual(sinal["signal"], "CALL")
        # O pior caso para o motor clássico: tendência de baixa, RSI no chão,
        # verde-vermelho-verde nas últimas cores e continuação para baixo.
        sinal.update(
            {
                "ema9": 1.0990,
                "ema21": 1.1000,
                "rsi": 18.0,
                "trend": "DOWN",
                "price_action_setup": "CONTINUATION",
                "last_3_direction": "DOWN",
                "last_3_colors": ["GREEN", "RED", "GREEN"],
                "body_ratio": 0.1,
            }
        )
        self.sinal = sinal

    def test_guarda_nao_reaplica_filtros_de_tendencia(self) -> None:
        liberado, ranked, motivo = main.apply_strategy_guard(
            "user-revz-guarda", self.state, {**self.sinal, "symbol": "EURUSD"}, payout=85.0
        )
        self.assertTrue(liberado, ranked.get("blocked_filters"))
        self.assertIsNone(motivo)
        self.assertEqual(ranked["strategy_score"], self.sinal["confidence"])
        for classico in ("EMA_TREND", "RSI_RANGE", "LAST_3_ALIGNMENT", "CALL_GRG", "TREND_CLEAR"):
            self.assertNotIn(classico, ranked["blocked_filters"])

    def test_guarda_respeita_payout(self) -> None:
        liberado, ranked, motivo = main.apply_strategy_guard(
            "user-revz-payout", self.state, {**self.sinal, "symbol": "EURUSD"}, payout=70.0
        )
        self.assertFalse(liberado)
        self.assertEqual(motivo, "MIN_PAYOUT")

    def test_veredito_sobrevive_a_montagem_do_candidato(self) -> None:
        self.assertIn("revz", main.ANALYSIS_DETAIL_FIELDS)
        _, ranked, _ = main.apply_strategy_guard(
            "user-revz-montagem", self.state, {**self.sinal, "symbol": "EURUSD"}, payout=85.0
        )
        self.assertTrue(is_revz_candidate(candidato_do_ciclo(ranked)))

    def test_portao_do_ciclo_desce_o_piso_e_ignora_cortes_classicos(self) -> None:
        _, ranked, _ = main.apply_strategy_guard(
            "user-revz-ciclo", self.state, {**self.sinal, "symbol": "EURUSD"}, payout=85.0
        )
        candidato = candidato_do_ciclo(ranked)
        # Painel em 80, confiança da REV-Z na escala 55-75: sem o rescale no
        # SEGUNDO portão, 100% dos sinais morriam aqui sem erro no log.
        self.assertTrue(
            main.candidate_meets_cycle_threshold(candidato, self.state, minimum_confidence=80)
        )
        self.assertIsNone(
            main.resolve_entry_validation_reason(candidato, self.state, minimum_confidence=80)
        )

    def test_veredito_sobrevive_ao_sinal_pendente_ate_a_compra(self) -> None:
        # 10/09 12:34–14:40: a REV-Z indicou EURUSD/USDCAD e toda compra caiu
        # em SEM_VEREDITO_REVZ — `set_pending_signal` (auto_trader) monta o
        # sinal pendente com campos fixos e não levava `revz`.
        from backend.auto_trader import AutoTrader

        _, ranked, _ = main.apply_strategy_guard(
            "user-revz-pendente", self.state, {**self.sinal, "symbol": "EURUSD"}, payout=85.0
        )
        candidato = candidato_do_ciclo(ranked)
        trader = AutoTrader()
        pendente = trader.set_pending_signal("user-revz-pendente", candidato).pending_signal
        self.assertTrue(is_revz_candidate(pendente))
        tentativa = trader.set_order_attempt("user-revz-pendente", pendente, 1).pending_signal
        self.assertTrue(is_revz_candidate(tentativa))
        for item in main.order_attempt_candidates(trader.get("user-revz-pendente"), tentativa):
            self.assertTrue(is_revz_candidate(item))
        self.assertIsNone(
            main.resolve_entry_validation_reason(tentativa, self.state, minimum_confidence=80)
        )

    def test_portao_do_ciclo_respeita_bloqueio_de_execucao(self) -> None:
        _, ranked, _ = main.apply_strategy_guard(
            "user-revz-cooldown", self.state, {**self.sinal, "symbol": "EURUSD"}, payout=85.0
        )
        candidato = candidato_do_ciclo(ranked)
        candidato["blocked_filters"] = ["GLOBAL_LOSS_COOLDOWN"]
        self.assertFalse(
            main.candidate_meets_cycle_threshold(candidato, self.state, minimum_confidence=80)
        )
        candidato["blocked_filters"] = []
        candidato["payout"] = 70.0
        self.assertFalse(
            main.candidate_meets_cycle_threshold(candidato, self.state, minimum_confidence=80)
        )


class SemVereditoNaoOperaTest(RevzLigada):
    """No aberto, só a REV-Z abre ordem."""

    def setUp(self) -> None:
        super().setUp()
        self.state = SimpleNamespace(
            min_confidence=80, min_payout=80, strategy_mode="conservative", timeframe="M1"
        )

    def test_sem_extremo_o_guarda_nao_deduz_direcao(self) -> None:
        sinal = se.analyze_signal("EURUSD", serie(), timeframe="M1")
        liberado, ranked, motivo = main.apply_strategy_guard(
            "user-revz-wait", self.state, {**sinal, "symbol": "EURUSD"}, payout=85.0
        )
        self.assertFalse(liberado)
        self.assertEqual(motivo, "REVZ_SEM_DESVIO_EXTREMO")
        self.assertEqual(ranked["direction"], "WAIT")
        self.assertEqual(ranked["strategy_score"], 0)

    def test_candidato_classico_restaurado_nao_opera_no_aberto(self) -> None:
        # Deploy de 10/09 12:24: sinal pendente do motor clássico, analisado
        # antes do restart, virou ordem num par aberto sem passar pela REV-Z.
        classico = {
            "symbol": "EURJPY",
            "direction": "CALL",
            "signal": "CALL",
            "confidence": 100,
            "strategy_score": 100,
            "payout": 85.0,
            "trade_allowed": True,
            "blocked_filters": [],
            "metrics": {},
        }
        self.assertFalse(
            main.candidate_meets_cycle_threshold(classico, self.state, minimum_confidence=80)
        )
        self.assertEqual(
            main.resolve_entry_validation_reason(classico, self.state, minimum_confidence=80),
            main.LOW_QUALITY_SIGNAL,
        )


class PisoEPortaoPuroTest(unittest.TestCase):
    def test_piso_desce_ate_o_chao_e_nunca_sobe(self) -> None:
        self.assertEqual(revz_min_confidence(80), REVZ_CONFIDENCE_FLOOR)
        self.assertEqual(revz_min_confidence(50), 50)

    def test_nivel_e_stop_nao_sao_dispensados(self) -> None:
        for nome in ("SR_ZONE", "LEVEL_CONFLICT", "STOP_LOSS_HIT", "ACTIVE_CLOSED", "MIN_PAYOUT"):
            self.assertIn(nome, REVZ_NON_WAIVABLE)
        for classico in ("EMA_TREND", "RSI_RANGE", "LAST_3_ALIGNMENT", "CALL_GRG", "PUT_BODY"):
            self.assertNotIn(classico, REVZ_NON_WAIVABLE)

    def test_portao_puro(self) -> None:
        candidato = {"revz": {"direction": "PUT"}, "payout": 85, "strategy_score": 55}
        self.assertEqual(revz_passa_portao(candidato, min_payout=80, minimo_confianca=80), (True, None))
        candidato["blocked_filters"] = ["SR_ZONE"]
        self.assertEqual(revz_passa_portao(candidato, min_payout=80, minimo_confianca=80), (False, "SR_ZONE"))


class ConfirmacaoNoFechamentoTest(unittest.TestCase):
    def test_descarta_a_vela_em_formacao(self) -> None:
        velas = serie(5)
        atual = velas[-1]["from"]
        closes = closes_of_closed_candles(velas, current_candle_start=atual)
        self.assertEqual(closes, [v["close"] for v in velas[:-1]])

    def test_sem_a_vela_recem_fechada_o_dado_esta_velho(self) -> None:
        velas = serie(5)
        atual = velas[-1]["from"] + 120  # faltou a vela anterior à atual
        self.assertIsNone(closes_of_closed_candles(velas, current_candle_start=atual))

    def test_confirma_so_no_extremo_e_na_mesma_direcao(self) -> None:
        closes = [v["close"] for v in serie(REVZ_LOOKBACK + 5, queda_final=0.002)]
        self.assertTrue(revz_confirm_at_entry(closes, "CALL")[0])
        self.assertEqual(revz_confirm_at_entry(closes, "PUT")[2], "REVZ_DIRECAO_VIROU")
        calmo = [v["close"] for v in serie(REVZ_LOOKBACK + 5)]
        self.assertEqual(revz_confirm_at_entry(calmo, "CALL")[2], "REVZ_SEM_EXTREMO_NO_FECHAMENTO")
        self.assertEqual(revz_confirm_at_entry(None, "CALL")[2], "REVZ_CONFIRMACAO_SEM_DADOS")


class ConfirmacaoNoDisparoTest(unittest.TestCase):
    """`confirm_revz_before_entry`: vai à corretora e usa a vela FECHADA."""

    def _roda(self, velas: list[dict], direcao: str = "CALL", erro: Exception | None = None):
        atual = velas[-1]["from"]  # a última vela da resposta é a em formação
        candidato = {"symbol": "EURUSD", "direction": direcao, "revz": {"direction": direcao, "z": -3.9}}
        chamada = mock.AsyncMock(return_value=(200, {"ok": True, "data": velas}))
        if erro is not None:
            chamada.side_effect = erro
        with mock.patch.object(main, "call_bullex_service", chamada), mock.patch.object(
            main, "load_candles_for_active", side_effect=AssertionError("usou o cache")
        ):
            motivo = asyncio.run(
                main.confirm_revz_before_entry("user-revz-disparo", candidato, server_timestamp=atual + 1.5)
            )
        return motivo, candidato, chamada

    def test_confirma_com_a_vela_que_acabou_de_fechar(self) -> None:
        # Extremo na penúltima (a recém-fechada); a em formação volta ao normal.
        velas = serie(REVZ_LOOKBACK + 5, queda_final=0.002)
        formando = dict(velas[-1], **{"from": velas[-1]["from"] + 60, "close": 1.1000})
        motivo, candidato, chamada = self._roda(velas + [formando])
        self.assertIsNone(motivo)
        self.assertLessEqual(candidato["revz"]["z_confirmacao"], -4.5)
        self.assertEqual(candidato["revz"]["z_indicacao"], -3.9)
        self.assertEqual(chamada.await_args.kwargs["params"]["interval"], 60)

    def test_extremo_so_na_vela_em_formacao_nao_conta(self) -> None:
        velas = serie(REVZ_LOOKBACK + 5)
        formando = dict(velas[-1], **{"from": velas[-1]["from"] + 60, "close": 1.0980})
        motivo, _, _ = self._roda(velas + [formando])
        self.assertEqual(motivo, "REVZ_SEM_EXTREMO_NO_FECHAMENTO")

    def test_erro_na_corretora_nao_opera_e_nao_derruba_o_ciclo(self) -> None:
        velas = serie(REVZ_LOOKBACK + 5)
        motivo, _, _ = self._roda(velas, erro=RuntimeError("caiu"))
        self.assertEqual(motivo, "REVZ_CONFIRMACAO_SEM_DADOS")


class HistoricoTest(unittest.TestCase):
    def test_veredito_vai_para_o_historico(self) -> None:
        self.assertIn("revz", robot_persistence.TRADE_ANALYSIS_FIELDS)


if __name__ == "__main__":
    unittest.main()
