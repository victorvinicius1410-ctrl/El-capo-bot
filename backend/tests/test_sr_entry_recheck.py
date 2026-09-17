"""Reconferência de suporte/resistência no disparo da ordem.

Reproduz o caso real de 10/09: `GBPUSD-OTC PUT`, R$ 200, LOSS. Na análise
(01:26:08) não havia suporte nenhum e a regra liberou; na execução (01:27:03) a
vela fechou, nasceu um pivô em 1.318665 e o preço estava em 1.318725 — ou seja,
venda em cima do suporte. Reconferir durante a espera NÃO pega: o pivô só existe
depois do fechamento.
"""

import asyncio
import unittest
from unittest import mock

from backend import main


def _velas_planas(n: int, preco: float) -> list[dict[str, float]]:
    return [{"open": preco, "close": preco, "max": preco, "min": preco} for _ in range(n)]


class RevalidacaoDeNivelNaEntradaTest(unittest.IsolatedAsyncioTestCase):
    def _candidato(self, direction="PUT"):
        return {
            "symbol": "GBPUSD-OTC",
            "direction": direction,
            "signal": direction,
            "sr_respect_reason": "OK_FORA_DA_REGIAO",
        }

    async def _rodar(self, respeita, motivo="CONTRA_O_NIVEL", velas=None):
        velas = velas if velas is not None else _velas_planas(160, 1.3187)
        with mock.patch.object(
            main, "call_bullex_service",
            new=mock.AsyncMock(return_value=(200, {"ok": True, "candles": velas})),
        ), mock.patch.object(
            main, "extract_candles", return_value=velas
        ), mock.patch.object(
            main, "build_zone", return_value={"support": 1.318665, "resistance": None}
        ), mock.patch.object(
            main, "evaluate_respect", return_value=(respeita, motivo)
        ):
            return await main.revalidate_level_before_entry(
                "user-sr", self._candidato(), "M1"
            )

    async def test_barra_quando_o_nivel_virou_contra(self) -> None:
        self.assertEqual(await self._rodar(False), "SR_ZONE_NA_ENTRADA")

    async def test_libera_quando_continua_respeitando(self) -> None:
        self.assertIsNone(await self._rodar(True, "OK_FORA_DA_REGIAO"))

    async def test_modo_live_dispensa_a_reconferencia(self) -> None:
        """Decisão do dono em 15/09/2026: no LIVE não vale nível nem pavio.

        A entrada do modo é curta (teto de ``LIVE_MAX_EXPIRATION_MINUTES``), e
        nessa escala a regra de nível não manda na vela. Sem o desvio, esta
        reconferência refaz do zero — com velas novas — o veto que
        ``apply_live_demo`` acabou de dispensar: é a armadilha dos "dois
        portões" que já pegou três vezes neste código. Medido em 15/09 na conta
        11e0b3d5: das 3 liberações do modo, 1 morreu aqui com
        ``PAVIO_NA_ENTRADA`` e virou "sem oportunidade" em silêncio.

        O cenário abaixo é o que BARRA uma entrada normal (``respeita=False``,
        nível virou contra): o candidato do LIVE tem que passar mesmo assim.
        """
        candidato = self._candidato()
        candidato["live_demo"] = True
        velas = _velas_planas(160, 1.3187)
        with mock.patch.object(
            main, "call_bullex_service",
            new=mock.AsyncMock(return_value=(200, {"ok": True, "candles": velas})),
        ), mock.patch.object(
            main, "extract_candles", return_value=velas
        ), mock.patch.object(
            main, "build_zone", return_value={"support": 1.318665, "resistance": None}
        ), mock.patch.object(
            main, "evaluate_respect", return_value=(False, "CONTRA_O_NIVEL")
        ):
            motivo = await main.revalidate_level_before_entry(
                "user-sr", candidato, "M1"
            )
        self.assertIsNone(motivo)
        self.assertEqual(
            candidato["sr_entry_recheck_reason"], "LIVE_DEMO_DISPENSA_NIVEL_E_PAVIO"
        )

    async def test_entrada_normal_continua_barrada_no_mesmo_cenario(self) -> None:
        """Contraprova do teste acima: sem a marca ``live_demo``, barra."""
        self.assertEqual(await self._rodar(False), "SR_ZONE_NA_ENTRADA")

    async def test_sem_direcao_nao_ha_nivel_a_respeitar(self) -> None:
        cand = {"symbol": "GBPUSD-OTC", "signal": "WAIT"}
        self.assertIsNone(
            await main.revalidate_level_before_entry("user-sr", cand, "M1")
        )

    async def test_timeout_bloqueia(self) -> None:
        """Sem dado, a ordem não sai (decisão do dono em 10/09: S/R antes de volume).

        Antes liberava: 26 timeouts em 4h e vários viraram ordem sem verificação
        nenhuma — um deles uma compra de R$ 200 colada na máxima do gráfico.
        """
        async def _trava(*a, **k):
            await asyncio.sleep(5)
        cand = self._candidato()
        with mock.patch.object(main, "call_bullex_service", new=_trava):
            reason = await main.revalidate_level_before_entry(
                "user-sr", cand, "M1", timeout_seconds=0.01
            )
        self.assertEqual(reason, "SR_ZONE_SEM_VERIFICACAO")
        self.assertEqual(cand["sr_entry_recheck_reason"], "SEM_VERIFICACAO_TIMEOUT")

    async def test_sem_velas_bloqueia(self) -> None:
        with mock.patch.object(
            main, "call_bullex_service", new=mock.AsyncMock(return_value=(500, {"ok": False}))
        ):
            reason = await main.revalidate_level_before_entry(
                "user-sr", self._candidato(), "M1"
            )
        self.assertEqual(reason, "SR_ZONE_SEM_VERIFICACAO")

    async def test_erro_inesperado_bloqueia_sem_derrubar_o_ciclo(self) -> None:
        """Exceção dentro do laço de compra derruba o ciclo — tem de morrer aqui."""
        with mock.patch.object(
            main, "call_bullex_service", new=mock.AsyncMock(side_effect=RuntimeError("x"))
        ):
            reason = await main.revalidate_level_before_entry(
                "user-sr", self._candidato(), "M1"
            )
        self.assertEqual(reason, "SR_ZONE_SEM_VERIFICACAO")

    async def test_flag_fail_closed_desligada_volta_a_liberar(self) -> None:
        original = main.SR_ENTRY_RECHECK_FAIL_CLOSED
        main.SR_ENTRY_RECHECK_FAIL_CLOSED = False
        try:
            with mock.patch.object(
                main, "call_bullex_service", new=mock.AsyncMock(return_value=(500, {"ok": False}))
            ):
                reason = await main.revalidate_level_before_entry(
                    "user-sr", self._candidato(), "M1"
                )
        finally:
            main.SR_ENTRY_RECHECK_FAIL_CLOSED = original
        self.assertIsNone(reason)

    async def test_grava_o_veredito_no_candidato(self) -> None:
        cand = self._candidato()
        velas = _velas_planas(160, 1.3187)
        with mock.patch.object(
            main, "call_bullex_service",
            new=mock.AsyncMock(return_value=(200, {"ok": True, "candles": velas})),
        ), mock.patch.object(main, "extract_candles", return_value=velas), \
             mock.patch.object(main, "build_zone", return_value={}), \
             mock.patch.object(main, "evaluate_respect", return_value=(False, "NIVEL_VISIVEL_A_FRENTE")):
            reason = await main.revalidate_level_before_entry("user-sr", cand, "M1")
        self.assertEqual(reason, "SR_ZONE_NA_ENTRADA")
        self.assertEqual(cand["sr_entry_recheck_reason"], "NIVEL_VISIVEL_A_FRENTE")

    async def test_flag_desliga(self) -> None:
        original = main.SR_ENTRY_RECHECK
        main.SR_ENTRY_RECHECK = False
        try:
            self.assertIsNone(await self._rodar(False))
        finally:
            main.SR_ENTRY_RECHECK = original


if __name__ == "__main__":
    unittest.main()


class NuncaUsaCacheTest(unittest.IsolatedAsyncioTestCase):
    """O TTL de velas é 60s e a entrada acontece ~40-55s depois da análise.

    Se a reconferência ler o cache, recebe as velas da própria análise, chega ao
    mesmo veredito e não barra nada — foi assim que ela rodou 64 ordens sendo
    inerte. Este teste trava a consulta direta à corretora.
    """

    async def test_nao_chama_o_carregador_cache_first(self) -> None:
        velas = _velas_planas(160, 1.3187)
        carregador = mock.AsyncMock(return_value=(velas, True, None))
        with mock.patch.object(main, "load_candles_for_active", new=carregador), \
             mock.patch.object(
                 main, "call_bullex_service",
                 new=mock.AsyncMock(return_value=(200, {"ok": True, "candles": velas})),
             ) as direto, \
             mock.patch.object(main, "extract_candles", return_value=velas), \
             mock.patch.object(main, "build_zone", return_value={"support": None, "resistance": None}), \
             mock.patch.object(main, "evaluate_respect", return_value=(True, "OK_FORA_DA_REGIAO")):
            await main.revalidate_level_before_entry(
                "user-sr", {"symbol": "GBPUSD-OTC", "direction": "PUT"}, "M1"
            )
        carregador.assert_not_called()
        direto.assert_awaited_once()
        self.assertEqual(direto.await_args.args[1], "/candles")

    async def test_chave_por_vela_do_relogio_da_corretora(self) -> None:
        """Uma busca por ativo/vela, dividida entre as contas, sem servir o minuto anterior.

        Sem `endtime`, a chave ficava 60s no cache compartilhado: a segunda conta
        no mesmo ativo no minuto seguinte (AUDCHF 17:18 e 17:19 em 10/09)
        reconferia com o gráfico de antes do fechamento. O `count` +1 separa a
        chave da análise, que usa o mesmo `endtime`.
        """
        velas = _velas_planas(160, 1.3187)
        with mock.patch.object(
                 main, "call_bullex_service",
                 new=mock.AsyncMock(return_value=(200, {"ok": True, "candles": velas})),
             ) as direto, \
             mock.patch.object(main, "extract_candles", return_value=velas), \
             mock.patch.object(main, "build_zone", return_value={}), \
             mock.patch.object(main, "evaluate_respect", return_value=(True, "OK_FORA_DA_REGIAO")):
            await main.revalidate_level_before_entry(
                "user-sr", {"symbol": "GBPUSD-OTC", "direction": "PUT"}, "M1",
                server_timestamp=1789065362.4,
            )
        params = direto.await_args.kwargs["params"]
        self.assertEqual(params["endtime"], 1789065360)
        self.assertEqual(params["count"], main.ROBOT_CANDLE_COUNT + 1)


class VereditoChegaAoHistoricoTest(unittest.TestCase):
    """O motivo de S/R atravessa as listas fixas até o registro da operação.

    Até 10/09 `sr_respect_reason` morria em `set_pending_signal` (o log da
    reconferência mostrava `motivo_analise=None` em toda ordem) e nenhum dos
    dois vereditos era gravado — provar a um cliente que a entrada respeitou o
    nível exigia remontar o gráfico à mão.
    """

    def test_sinal_pendente_e_tentativa_levam_os_dois_vereditos(self) -> None:
        from backend.auto_trader import AutoTrader

        trader = AutoTrader()
        sinal = {
            "symbol": "EURGBP-OTC", "signal": "CALL", "direction": "CALL",
            "confidence": 90, "payout": 87.0, "trade_allowed": True,
            "sr_respect_reason": "OK_FORA_DA_REGIAO",
        }
        pendente = trader.set_pending_signal("user-sr-hist", sinal).pending_signal
        self.assertEqual(pendente["sr_respect_reason"], "OK_FORA_DA_REGIAO")
        pendente["sr_entry_recheck_reason"] = "OK_FORA_DA_REGIAO"
        tentativa = trader.set_order_attempt("user-sr-hist", pendente, 1).pending_signal
        self.assertEqual(tentativa["sr_entry_recheck_reason"], "OK_FORA_DA_REGIAO")

    def test_campos_persistidos_e_do_candidato(self) -> None:
        from backend.robot_persistence import TRADE_ANALYSIS_FIELDS

        self.assertIn("sr_respect_reason", TRADE_ANALYSIS_FIELDS)
        self.assertIn("sr_entry_recheck_reason", TRADE_ANALYSIS_FIELDS)
        self.assertIn("sr_respect_reason", main.ANALYSIS_DETAIL_FIELDS)
        self.assertIn("sr_entry_recheck_reason", main.ANALYSIS_DETAIL_FIELDS)


class VelaAtualGarantidaTest(unittest.TestCase):
    """No segundo 0 a corretora pode ainda não ter aberto a vela nova.

    10/09 19:19 UTC, EURNZD-OTC PUT R$ 200: a lista terminava na vela que tinha
    acabado de fechar, `build_zone` a tratava como "em formação" e o suporte que
    ela confirmava ficava de fora — a reconferência liberou uma venda que era
    CONTRA_O_NIVEL.
    """

    def test_acrescenta_vela_em_formacao_quando_falta(self) -> None:
        velas = [{"from": 1789067820, "open": 1.0, "close": 1.1, "max": 1.2, "min": 0.9},
                 {"from": 1789067880, "open": 1.1, "close": 1.15, "max": 1.2, "min": 1.0}]
        saida = main.candles_with_current_candle(velas, 1789067940)
        self.assertEqual(len(saida), 3)
        self.assertEqual(saida[-1]["from"], 1789067940)
        self.assertEqual(saida[-1]["close"], 1.15)
        self.assertEqual(saida[-1]["max"], 1.15)

    def test_nao_mexe_quando_a_vela_atual_ja_veio(self) -> None:
        velas = [{"from": 1789067880, "open": 1.1, "close": 1.15, "max": 1.2, "min": 1.0},
                 {"from": 1789067940, "open": 1.15, "close": 1.16, "max": 1.16, "min": 1.15}]
        self.assertIs(main.candles_with_current_candle(velas, 1789067940), velas)

    def test_sem_carimbo_de_tempo_nao_inventa(self) -> None:
        velas = [{"open": 1.0, "close": 1.0, "max": 1.0, "min": 1.0}]
        self.assertIs(main.candles_with_current_candle(velas, 1789067940), velas)
