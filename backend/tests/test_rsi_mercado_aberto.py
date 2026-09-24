"""RSI extremo como motor do mercado aberto, por timeframe (24/09/2026).

Pedido do dono: trocar a REV-Z (49,2% nos dados de 14-24/09) pela reversão do
RSI(14), ajustada separadamente para M1, M5 e M15. O OTC não muda.

Os testes travam o que já custou caro neste caminho:
- **velas certas por timeframe**: M1 e M5 leem o RSI das velas M1; M15, da
  própria vela M15;
- **dois tempos**: a análise indica com o limite afrouxado; a ordem só sai se o
  RSI da vela FECHADA passa do limite cheio;
- **listas fixas**: o veredito viaja no campo ``revz`` até a compra;
- **cache**: ``AUDJPY`` não pode ler a vela do ``AUDJPY-OTC``, e a análise não
  pode usar a vela de minutos atrás.
"""

from __future__ import annotations

import asyncio
import random
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from backend import main
from backend import open_rsi_strategy as rsi
from backend import signal_engine as se
from backend.reversion_strategy import REVZ_CONFIDENCE_FLOOR, is_revz_candidate

INICIO = 1789000200 - (1789000200 % 900)


def serie(
    n: int = 160,
    *,
    queda_final: int = 0,
    passo: float = 0.0006,
    intervalo: int = 60,
    inicio: int = INICIO,
) -> list[dict]:
    """Velas no formato da corretora, oscilando em volta de 1.1000.

    Com ``queda_final=k`` as últimas ``k`` velas caem ``passo`` cada — RSI no chão.
    """
    velas = []
    anterior = 1.1000
    fechamento = 1.1000
    for i in range(n):
        if i >= n - queda_final:
            fechamento = fechamento - passo
        else:
            fechamento = 1.1000 + 0.0002 * ((-1) ** i) * (1 + (i % 3))
        velas.append(
            {
                "from": inicio + intervalo * i,
                "open": anterior,
                "close": fechamento,
                "max": max(anterior, fechamento) + 0.00003,
                "min": min(anterior, fechamento) - 0.00003,
                "volume": 10,
            }
        )
        anterior = fechamento
    return velas


def rsi_de_referencia(closes: list[float]) -> float:
    """O cálculo do backtest de 24/09, copiado como estava (`bt_real.py`)."""
    ag = al = None
    ganhos: list[float] = []
    perdas: list[float] = []
    saida = None
    for anterior, atual in zip(closes, closes[1:]):
        d = atual - anterior
        up, dn = max(d, 0), max(-d, 0)
        if ag is None:
            ganhos.append(up)
            perdas.append(dn)
            if len(ganhos) == 14:
                ag, al = sum(ganhos) / 14, sum(perdas) / 14
        else:
            ag, al = (ag * 13 + up) / 14, (al * 13 + dn) / 14
        if ag is not None:
            saida = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return saida


class RsiLigado(unittest.TestCase):
    """Base: encanamento do aberto ligado e estratégia RSI, como no `.env`."""

    def setUp(self) -> None:
        for alvo, nome, valor in (
            (se, "REVZ_ENABLED", True),
            (main, "REVZ_ENABLED", True),
            (se, "VERTEX_ENABLED", True),
            (rsi, "REVZ_ENABLED", True),
            (rsi, "OPEN_MARKET_STRATEGY", "RSI"),
        ):
            patcher = mock.patch.object(alvo, nome, valor)
            patcher.start()
            self.addCleanup(patcher.stop)


class CalculoTest(unittest.TestCase):
    def test_igual_ao_backtest(self) -> None:
        gerador = random.Random(24)
        for _ in range(20):
            closes = [1.1]
            for _ in range(159):
                closes.append(closes[-1] + gerador.gauss(0, 0.0002))
            self.assertAlmostEqual(rsi.rsi_wilder(closes), rsi_de_referencia(closes), places=9)

    def test_extremos(self) -> None:
        self.assertEqual(rsi.rsi_wilder([float(i) for i in range(30)]), 100.0)
        self.assertEqual(rsi.rsi_wilder([1.0] * 30), 50.0)
        self.assertIsNone(rsi.rsi_wilder([1.0] * 10))

    def test_limites_padrao_por_timeframe(self) -> None:
        # Padrões = o ajuste de maior acerto do backtest; o .env pode trocar.
        with mock.patch.dict(rsi.RSI_OPEN_LOW, {"M1": 20, "M5": 24, "M15": 28}):
            self.assertEqual(rsi.rsi_open_limits("M1"), (20, 80))
            self.assertEqual(rsi.rsi_open_limits("M5"), (24, 76))
            self.assertEqual(rsi.rsi_open_limits("m15"), (28, 72))
            baixo, alto = rsi.rsi_open_limits("M5", nominate=True)
            self.assertEqual((baixo, alto), (24 + rsi.RSI_OPEN_NOMINATE_MARGIN, 76 - rsi.RSI_OPEN_NOMINATE_MARGIN))
        self.assertIsNone(rsi.rsi_open_limits("M30"))

    def test_velas_do_rsi_por_timeframe(self) -> None:
        self.assertEqual(rsi.rsi_open_candle_timeframe("M1"), "M1")
        self.assertEqual(rsi.rsi_open_candle_timeframe("M5"), "M1")
        self.assertEqual(rsi.rsi_open_candle_timeframe("M15"), "M15")

    def test_otc_nunca_opera(self) -> None:
        closes = [c["close"] for c in serie(queda_final=20)]
        veredito = rsi.rsi_open_evaluate("EURUSD-OTC", closes, "M1")
        self.assertIsNone(veredito["direction"])
        self.assertEqual(veredito["blocked"], "RSI_OTC_SEM_VANTAGEM")

    def test_direcao_e_reversao(self) -> None:
        closes = [c["close"] for c in serie(queda_final=20)]
        self.assertEqual(rsi.rsi_open_evaluate("EURUSD", closes, "M1")["direction"], "CALL")
        subida = [2.2 - c for c in closes]
        self.assertEqual(rsi.rsi_open_evaluate("EURUSD", subida, "M1")["direction"], "PUT")

    def test_texto_sem_conviccao(self) -> None:
        closes = [c["close"] for c in serie(queda_final=20)]
        texto = rsi.rsi_open_text("EURUSD", rsi.rsi_open_evaluate("EURUSD", closes, "M1"))
        self.assertIn("Entrada de CALL", texto)
        for proibido in ("certeza", "margem confortável", "tendência é voltar"):
            self.assertNotIn(proibido, texto)


class MotorPorTimeframeTest(RsiLigado):
    def test_otc_continua_com_o_motor_de_hoje(self) -> None:
        sinal = se.analyze_signal("EURUSD-OTC", serie(queda_final=20), timeframe="M1")
        self.assertNotIn("revz", sinal)
        self.assertNotEqual(sinal.get("strategy_key"), rsi.STRATEGY_RSI_OPEN)

    def test_m1_no_extremo_entra_pela_reversao(self) -> None:
        sinal = se.analyze_signal("EURUSD", serie(queda_final=20), timeframe="M1")
        self.assertEqual(sinal["signal"], "CALL")
        self.assertTrue(sinal["trade_allowed"])
        self.assertEqual(sinal["revz"]["strategy"], "RSI")
        self.assertEqual(sinal["revz"]["timeframe"], "M1")
        self.assertEqual(sinal["strategy_key"], rsi.STRATEGY_RSI_OPEN)
        self.assertEqual(sinal["confidence_model_version"], "rsi-aberto-v1")
        self.assertGreaterEqual(sinal["confidence"], REVZ_CONFIDENCE_FLOOR)
        self.assertEqual(sinal["blocked_filters"], [])

    def test_m1_fora_do_extremo_fica_sem_entrada(self) -> None:
        sinal = se.analyze_signal("EURUSD", serie(), timeframe="M1")
        self.assertEqual(sinal["signal"], "WAIT")
        self.assertEqual(sinal["revz"]["blocked"], "RSI_SEM_EXTREMO")
        self.assertIn("fora do extremo", sinal["narrator_text"])

    def test_m5_le_o_rsi_das_velas_m1(self) -> None:
        m5_neutro = serie(intervalo=300)
        sinal = se.analyze_signal("EURUSD", m5_neutro, timeframe="M5", m1_candles=serie(queda_final=20))
        self.assertEqual(sinal["signal"], "CALL")
        self.assertEqual(sinal["revz"]["candle_timeframe"], "M1")
        # E o contrário: extremo só no M5, M1 neutro, não opera.
        sinal = se.analyze_signal(
            "EURUSD", serie(queda_final=20, intervalo=300), timeframe="M5", m1_candles=serie()
        )
        self.assertEqual(sinal["signal"], "WAIT")

    def test_m5_sem_velas_m1_nao_opera(self) -> None:
        sinal = se.analyze_signal("EURUSD", serie(queda_final=20, intervalo=300), timeframe="M5")
        self.assertEqual(sinal["revz"]["blocked"], "RSI_VELAS_INSUFICIENTES")

    def test_m15_le_a_propria_vela(self) -> None:
        sinal = se.analyze_signal(
            "EURUSD", serie(queda_final=20, intervalo=900), timeframe="M15", m1_candles=serie()
        )
        self.assertEqual(sinal["signal"], "CALL")
        self.assertEqual(sinal["revz"]["candle_timeframe"], "M15")

    def test_modo_revz_continua_como_estava(self) -> None:
        with mock.patch.object(rsi, "OPEN_MARKET_STRATEGY", "REVZ"):
            sinal = se.analyze_signal("EURUSD", serie(), timeframe="M1")
        self.assertEqual(sinal["strategy_key"], "REVZ")
        self.assertEqual(sinal["revz"]["strategy"], "REV-Z")


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


class PortoesTest(RsiLigado):
    def setUp(self) -> None:
        super().setUp()
        self.state = SimpleNamespace(
            min_confidence=80, min_payout=80, strategy_mode="conservative", timeframe="M1"
        )
        self.sinal = se.analyze_signal("EURUSD", serie(queda_final=20), timeframe="M1")
        self.assertEqual(self.sinal["signal"], "CALL")

    def test_passa_os_dois_portoes_com_painel_em_80(self) -> None:
        liberado, ranked, motivo = main.apply_strategy_guard(
            "user-rsi-guarda", self.state, {**self.sinal, "symbol": "EURUSD"}, payout=85.0
        )
        self.assertTrue(liberado, ranked.get("blocked_filters"))
        self.assertIsNone(motivo)
        candidato = candidato_do_ciclo(ranked)
        self.assertTrue(main.candidate_meets_cycle_threshold(candidato, self.state, minimum_confidence=80))

    def test_veredito_chega_ate_a_compra(self) -> None:
        from backend.auto_trader import AutoTrader

        _, ranked, _ = main.apply_strategy_guard(
            "user-rsi-pendente", self.state, {**self.sinal, "symbol": "EURUSD"}, payout=85.0
        )
        trader = AutoTrader()
        pendente = trader.set_pending_signal("user-rsi-pendente", candidato_do_ciclo(ranked)).pending_signal
        tentativa = trader.set_order_attempt("user-rsi-pendente", pendente, 1).pending_signal
        for item in main.order_attempt_candidates(trader.get("user-rsi-pendente"), tentativa):
            self.assertTrue(is_revz_candidate(item))
            self.assertEqual(item["revz"]["strategy"], "RSI")
        self.assertIsNone(main.resolve_entry_validation_reason(tentativa, self.state, minimum_confidence=80))


class ConfirmacaoNoDisparoTest(RsiLigado):
    """`confirm_revz_before_entry` encaminha o RSI e usa a vela FECHADA."""

    def _roda(self, velas: list[dict], *, timeframe: str = "M1", direcao: str = "CALL", erro=None):
        atual = velas[-1]["from"]  # a última vela da resposta é a em formação
        candidato = {
            "symbol": "EURUSD",
            "direction": direcao,
            "revz": {"strategy": "RSI", "direction": direcao, "rsi": 25.0, "timeframe": timeframe},
        }
        chamada = mock.AsyncMock(return_value=(200, {"ok": True, "data": velas}))
        if erro is not None:
            chamada.side_effect = erro
        with mock.patch.object(main, "call_bullex_service", chamada), mock.patch.object(
            main, "load_candles_for_active", side_effect=AssertionError("usou o cache")
        ):
            motivo = asyncio.run(
                main.confirm_revz_before_entry("user-rsi-disparo", candidato, server_timestamp=atual + 1.5)
            )
        return motivo, candidato, chamada

    def _com_formando(self, velas: list[dict], fechamento: float, intervalo: int = 60) -> list[dict]:
        formando = dict(velas[-1], **{"from": velas[-1]["from"] + intervalo, "close": fechamento})
        return velas + [formando]

    def test_m1_confirma_com_a_vela_que_acabou_de_fechar(self) -> None:
        velas = self._com_formando(serie(queda_final=20), 1.1000)
        motivo, candidato, chamada = self._roda(velas)
        self.assertIsNone(motivo)
        self.assertLess(candidato["revz"]["rsi_confirmacao"], 20)
        self.assertEqual(candidato["revz"]["rsi_indicacao"], 25.0)
        self.assertEqual(chamada.await_args.kwargs["params"]["interval"], 60)

    def test_m5_confirma_nas_velas_m1(self) -> None:
        velas = self._com_formando(serie(queda_final=20), 1.1000)
        motivo, _, chamada = self._roda(velas, timeframe="M5")
        self.assertIsNone(motivo)
        self.assertEqual(chamada.await_args.kwargs["params"]["interval"], 60)

    def test_m15_confirma_na_vela_m15(self) -> None:
        velas = self._com_formando(serie(queda_final=20, intervalo=900), 1.1000, intervalo=900)
        motivo, _, chamada = self._roda(velas, timeframe="M15")
        self.assertIsNone(motivo)
        self.assertEqual(chamada.await_args.kwargs["params"]["interval"], 900)

    def test_extremo_so_na_vela_em_formacao_nao_conta(self) -> None:
        velas = self._com_formando(serie(), 1.0950)
        motivo, _, _ = self._roda(velas)
        self.assertEqual(motivo, "RSI_SEM_EXTREMO_NO_FECHAMENTO")

    def test_direcao_virada_nao_opera(self) -> None:
        velas = self._com_formando(serie(queda_final=20), 1.1000)
        motivo, _, _ = self._roda(velas, direcao="PUT")
        self.assertEqual(motivo, "RSI_DIRECAO_VIROU")

    def test_erro_na_corretora_nao_opera(self) -> None:
        motivo, _, _ = self._roda(serie(), erro=RuntimeError("caiu"))
        self.assertEqual(motivo, "RSI_CONFIRMACAO_SEM_DADOS")


class CacheDeVelasTest(unittest.TestCase):
    """O defeito que escondia o aberto: vela de outro ativo e de minutos atrás."""

    def _grava(self, user_id: str, params: dict, payload: dict, *, idade: int = 0) -> None:
        cache = main.get_session_cache(user_id)
        agora = main.utc_now()
        cache.last_successful_responses[main.build_cache_key("/candles", params)] = main.BullexResponseCacheEntry(
            status_code=200,
            payload=payload,
            expires_at=agora + timedelta(seconds=60 - idade),
            received_at=agora - timedelta(seconds=idade),
        )

    def test_chave_e_lida_parametro_a_parametro(self) -> None:
        chave = main.build_cache_key("/candles", {"active": "AUDJPY-OTC", "interval": 60, "count": 160, "endtime": 120})
        self.assertEqual(main.cache_key_params(chave)["active"], "AUDJPY-OTC")

    def test_par_aberto_nao_le_a_vela_do_otc(self) -> None:
        usuario = "user-cache-otc"
        self._grava(
            usuario,
            {"active": "AUDJPY-OTC", "interval": 60, "count": main.ROBOT_CANDLE_COUNT, "endtime": INICIO},
            {"ok": True, "data": serie()},
        )
        self.assertEqual(main.cached_candles_for_active(usuario, "AUDJPY", "M1"), [])
        self.assertTrue(main.cached_candles_for_active(usuario, "AUDJPY-OTC", "M1"))

    def test_analise_busca_quando_o_cache_e_de_outra_vela(self) -> None:
        usuario = "user-cache-velho"
        velho = {"active": "EURUSD", "interval": 60, "count": main.ROBOT_CANDLE_COUNT, "endtime": INICIO}
        self._grava(usuario, velho, {"ok": True, "data": serie(inicio=INICIO - 9600)})
        novas = serie(inicio=INICIO - 9540)
        chamada = mock.AsyncMock(return_value=(200, {"ok": True, "data": novas}))
        with mock.patch.object(main, "call_bullex_service", chamada):
            velas, do_cache, erro = asyncio.run(
                main.load_candles_for_active(usuario, "EURUSD", "M1", endtime=INICIO + 60)
            )
        self.assertEqual(chamada.await_count, 1)
        self.assertFalse(do_cache)
        self.assertIsNone(erro)
        self.assertEqual(velas[-1]["from"], novas[-1]["from"])

    def test_mesma_vela_em_cache_nao_busca_de_novo(self) -> None:
        usuario = "user-cache-mesma"
        params = {"active": "EURUSD", "interval": 60, "count": main.ROBOT_CANDLE_COUNT, "endtime": INICIO}
        self._grava(usuario, params, {"ok": True, "data": serie()})
        chamada = mock.AsyncMock(side_effect=AssertionError("buscou de novo"))
        with mock.patch.object(main, "call_bullex_service", chamada):
            velas, do_cache, _ = asyncio.run(main.load_candles_for_active(usuario, "EURUSD", "M1", endtime=INICIO))
        self.assertTrue(do_cache)
        self.assertTrue(velas)

    def test_corretora_falhando_ainda_usa_o_cache_largo(self) -> None:
        usuario = "user-cache-falha"
        velho = {"active": "EURUSD", "interval": 60, "count": main.ROBOT_CANDLE_COUNT, "endtime": INICIO}
        self._grava(usuario, velho, {"ok": True, "data": serie()})
        chamada = mock.AsyncMock(return_value=(503, {"ok": False, "error": "CANDLES_TEMPORARY_UNAVAILABLE"}))
        with mock.patch.object(main, "call_bullex_service", chamada):
            velas, do_cache, erro = asyncio.run(
                main.load_candles_for_active(usuario, "EURUSD", "M1", endtime=INICIO + 60)
            )
        self.assertTrue(velas)
        self.assertTrue(do_cache)
        self.assertIsNone(erro)


if __name__ == "__main__":
    unittest.main()
