"""O marcador do modo LIVE tem que sobreviver até o portão do ciclo.

Incidente de 08/09/2026, conta de marketing. Depois de corrigido o portão
(`live_demo_passa_portao`), o modo LIVE **continuou sem operar**: o log mostrava
`[LIVE_DEMO_RELEASE]` seguido de `[NO_OPPORTUNITY_NEXT_SESSION]` com
`confidence=60`, e nenhum `[LIVE_DEMO_GATE_BLOCK]` — ou seja, o desvio do modo
nunca era alcançado.

Causa: o candidato do ciclo é montado em `main.py` como um dicionário de campos
**fixos** (`ANALYSIS_DETAIL_FIELDS` + um punhado de chaves literais). `live_demo`
e `strategy_key` não estavam na lista, então morriam na montagem. O portão via
um candidato comum e reaplicava os filtros de qualidade que o motor tinha
acabado de dispensar.

Perder `strategy_key` tinha um segundo custo, silencioso: a operação entraria no
histórico sem identificação, sujando a medição de estratégia — que é justamente
o que a auditoria de 03/09 teve que desfazer.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace

from backend import main
from backend.auto_trader import AutoTrader
from backend.live_demo_mode import (
    LIVE_CONFIDENCE,
    STRATEGY_LIVE_DEMO,
    apply_live_demo,
    live_demo_restaura,
)


def sinal_barrado() -> dict:
    """Sinal que o portão clássico recusa — o caso chato numa transmissão.

    Só bloqueios de QUALIDADE. Os três de nível (``SR_ZONE``,
    ``LEVEL_CONFLICT``, ``LEVEL_REJECTION``) estavam nesta lista até
    2026-09-09 e saíram porque o modo LIVE deixou de dispensá-los: a região de
    suporte e resistência não é negociável nem em transmissão. O que estes
    testes medem é o marcador do modo sobreviver ao caminho do ciclo, e isso
    independe de quais filtros o motor barrou.
    """
    return {
        "signal": "PUT",
        "direction": "PUT",
        "trade_allowed": False,
        "confidence": 38,
        "strategy_score": 38,
        "blocked_filters": [
            "MIN_CONFIDENCE",
            "PRICE_ACTION_SETUP",
            "PUT_BODY",
        ],
        "metrics": {},
        "body_ratio": 0.18,
    }


def monta_candidato_do_ciclo(sinal: dict, simbolo: str) -> dict:
    """Reproduz a montagem de `main.py`: só os campos da lista sobrevivem."""
    candidato = {
        chave: sinal[chave] for chave in main.ANALYSIS_DETAIL_FIELDS if chave in sinal
    }
    direcao = sinal.get("direction") or sinal.get("signal") or "WAIT"
    candidato.update(
        {
            "symbol": simbolo,
            "active": simbolo,
            "direction": direcao,
            "signal": direcao,
            "strategy_score": int(sinal.get("strategy_score") or 0),
            "score": int(sinal.get("strategy_score") or 0),
            "confidence": int(sinal.get("confidence") or 0),
            "payout": 87.0,
            "blocked_filters": list(sinal.get("blocked_filters") or []),
            "trade_allowed": bool(sinal.get("trade_allowed")),
            "timeframe": "M1",
        }
    )
    return candidato


class MarcadorSobreviveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = SimpleNamespace(min_confidence=80, min_payout=80, timeframe="M1")
        liberado = apply_live_demo(sinal_barrado(), "EURGBP-OTC", live_enabled=True)
        self.assertTrue(liberado["trade_allowed"], "o motor deveria ter liberado")
        self.candidato = monta_candidato_do_ciclo(liberado, "EURGBP-OTC")

    def test_lista_de_campos_carrega_o_marcador(self) -> None:
        self.assertIn("live_demo", main.ANALYSIS_DETAIL_FIELDS)
        self.assertIn("strategy_key", main.ANALYSIS_DETAIL_FIELDS)

    def test_marcador_chega_ao_candidato_do_ciclo(self) -> None:
        # O marcador do modo é o booleano. `strategy_key` leva a chave real do
        # setup desde 09/09/2026: ela é campo de tela (badge do Histórico e
        # primeiro item da lista de estratégias faladas) e entregava o modo.
        self.assertIs(self.candidato.get("live_demo"), True)
        self.assertNotEqual(self.candidato.get("strategy_key"), STRATEGY_LIVE_DEMO)

    def test_passa_no_portao_estrito(self) -> None:
        # O piso do painel (80) tem que ser rebaixado para o do modo.
        piso = main.live_min_confidence(self.state.min_confidence, self.candidato)
        self.assertEqual(piso, LIVE_CONFIDENCE)
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                self.candidato, self.state, minimum_confidence=piso, user_id=None
            )
        )

    def test_passa_no_portao_visivel_de_70(self) -> None:
        # 70 é fixo no código e era onde o candidato de 60 morria calado.
        piso = main.live_min_confidence(70, self.candidato)
        self.assertEqual(piso, LIVE_CONFIDENCE)
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                self.candidato, self.state, minimum_confidence=piso, user_id=None
            )
        )

    def test_candidato_normal_nao_e_afrouxado(self) -> None:
        # O mesmo sinal, sem a marca, continua barrado nos dois pisos.
        normal = dict(self.candidato)
        normal.pop("live_demo")
        normal["confidence"] = normal["score"] = normal["strategy_score"] = 92
        for piso in (70, 80):
            with self.subTest(piso=piso):
                self.assertEqual(main.live_min_confidence(piso, normal), piso)
                self.assertFalse(
                    main.candidate_meets_cycle_threshold(
                        normal, self.state, minimum_confidence=piso, user_id=None
                    )
                )

    def test_stop_continua_barrando_a_demonstracao(self) -> None:
        parado = dict(self.candidato)
        parado["blocked_filters"] = ["STOP_LOSS_HIT"]
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                parado,
                self.state,
                minimum_confidence=main.live_min_confidence(70, parado),
                user_id=None,
            )
        )



class GuardaDeEstrategiaTests(unittest.TestCase):
    """`apply_strategy_guard` era o ponto que realmente derrubava a entrada.

    Ele recalcula ``trade_allowed`` cruzando ``blocked_filters`` com o conjunto
    crítico. Como `apply_live_demo` deixa a lista intacta de propósito, o
    candidato chegava ao portão já com ``trade_allowed=False`` e morria ANTES do
    desvio do modo — daí o log mostrar `[NO_OPPORTUNITY]` sem nenhum
    `[LIVE_DEMO_GATE_BLOCK]`.

    Medido em 08/09/2026 com o motor real, 60 ciclos em série OTC:
    produção 59 liberadas → 0 passavam na guarda; com o patch, 59 → 59.
    """

    def setUp(self) -> None:
        self.state = SimpleNamespace(
            min_confidence=80,
            min_payout=80,
            timeframe="M1",
            strategy_mode="conservative",
        )

    def test_guarda_nao_derruba_mais_a_demonstracao(self) -> None:
        liberado = apply_live_demo(sinal_barrado(), "EURGBP-OTC", live_enabled=True)
        permitido, avaliado, _ = main.apply_strategy_guard(
            "u", self.state, {**liberado, "symbol": "EURGBP-OTC"}, payout=87.0
        )
        self.assertTrue(permitido, avaliado.get("quality_reason"))
        self.assertTrue(avaliado["trade_allowed"])
        # e a escala do modo tem que sobreviver ao desconto de penalidades
        self.assertEqual(avaliado["strategy_score"], LIVE_CONFIDENCE)

    def test_guarda_continua_barrando_sinal_normal(self) -> None:
        normal = sinal_barrado()
        permitido, avaliado, _ = main.apply_strategy_guard(
            "u", self.state, {**normal, "symbol": "EURGBP-OTC"}, payout=87.0
        )
        self.assertFalse(permitido)
        self.assertFalse(avaliado["trade_allowed"])

    def test_guarda_respeita_bloqueio_de_execucao(self) -> None:
        liberado = apply_live_demo(sinal_barrado(), "EURGBP-OTC", live_enabled=True)
        liberado["blocked_filters"] = ["STOP_LOSS_HIT"]
        permitido, avaliado, _ = main.apply_strategy_guard(
            "u", self.state, {**liberado, "symbol": "EURGBP-OTC"}, payout=87.0
        )
        self.assertFalse(permitido)
        self.assertFalse(avaliado["trade_allowed"])


class RestauraTests(unittest.TestCase):
    """`live_demo_restaura` reafirma a liberação após cada reavaliação."""

    def test_desfaz_veto_de_qualidade(self) -> None:
        c = {
            "live_demo": True,
            "trade_allowed": False,
            "blocked_filters": ["MTF_CONFLUENCE", "PRICE_ACTION_SETUP"],
            "strategy_score": 12,
        }
        restaurado = live_demo_restaura(c)
        self.assertTrue(restaurado["trade_allowed"])
        self.assertEqual(restaurado["strategy_score"], LIVE_CONFIDENCE)
        self.assertEqual(restaurado["confidence"], LIVE_CONFIDENCE)

    def test_mantem_veto_de_execucao(self) -> None:
        c = {
            "live_demo": True,
            "trade_allowed": False,
            "blocked_filters": ["MTF_CONFLUENCE", "STOP_WIN_HIT"],
        }
        restaurado = live_demo_restaura(c)
        self.assertFalse(restaurado["trade_allowed"])
        self.assertIn("STOP_WIN_HIT", restaurado["quality_reason"])

    def test_nao_toca_em_sinal_normal(self) -> None:
        c = {"trade_allowed": False, "blocked_filters": ["MTF_CONFLUENCE"], "strategy_score": 12}
        antes = dict(c)
        self.assertEqual(live_demo_restaura(c), antes)

    def test_e_idempotente(self) -> None:
        c = {"live_demo": True, "trade_allowed": False, "blocked_filters": ["SR_ZONE"]}
        uma = dict(live_demo_restaura(c))
        duas = dict(live_demo_restaura(live_demo_restaura(dict(c))))
        self.assertEqual(uma, duas)

class ValidacaoPreCompraTests(unittest.TestCase):
    """O `pending_signal` é a ÚLTIMA lista fixa do caminho até a compra.

    Sintoma em 08/09/2026, conta de marketing com o LIVE ligado: o candidato
    chegava à janela de entrada e morria ali —
    `[ENTRY_BLOCKED] reason=LEVEL_CONFLICT trade_allowed=True confidence=60`
    seguido de `[ORDER_REJECTED] reason=NO_AVAILABLE_ASSET`.
    `AutoTrader.set_pending_signal` remonta o sinal campo a campo e não
    carregava `live_demo`, então a validação pré-compra tratava a entrada como
    comum e barrava por filtro de qualidade.
    """

    def monta_pendente(self) -> tuple:
        negociador = AutoTrader()
        estado = negociador.get("u")
        estado.timeframe = "M1"
        estado.min_confidence = 80
        estado.min_payout = 80
        sinal = apply_live_demo(
            {**sinal_barrado(), "symbol": "EURAUD-OTC", "payout": 87.0},
            "EURAUD-OTC",
            live_enabled=True,
        )
        pendente = dict(negociador.set_pending_signal("u", sinal).pending_signal or {})
        pendente["active"] = "EURAUD-OTC"
        pendente["is_open"] = True
        return estado, pendente

    def test_marcador_sobrevive_ao_pending_signal(self) -> None:
        _, pendente = self.monta_pendente()
        self.assertIs(pendente.get("live_demo"), True)
        self.assertNotEqual(pendente.get("strategy_key"), STRATEGY_LIVE_DEMO)

    def test_validacao_pre_compra_libera_a_demonstracao(self) -> None:
        estado, pendente = self.monta_pendente()
        motivo = main.resolve_entry_validation_reason(
            pendente,
            estado,
            minimum_confidence=main.live_min_confidence(estado.min_confidence, pendente),
            user_id=None,
        )
        self.assertIsNone(motivo, f"compra barrada por {motivo}")

    def test_validacao_pre_compra_barra_sinal_normal(self) -> None:
        estado, pendente = self.monta_pendente()
        pendente.pop("live_demo")
        motivo = main.resolve_entry_validation_reason(
            pendente,
            estado,
            minimum_confidence=main.live_min_confidence(80, pendente),
            user_id=None,
        )
        self.assertIsNotNone(motivo)

    def test_stop_ainda_barra_na_hora_da_compra(self) -> None:
        estado, pendente = self.monta_pendente()
        pendente["blocked_filters"] = ["STOP_WIN_HIT"]
        motivo = main.resolve_entry_validation_reason(
            pendente,
            estado,
            minimum_confidence=main.live_min_confidence(estado.min_confidence, pendente),
            user_id=None,
        )
        self.assertEqual(motivo, "STOP_WIN_HIT")


class EspacamentoNoPortaoDoCicloTest(unittest.TestCase):
    """O piso de ritmo barra no portão do ciclo, para TODO tipo de candidato.

    Decisão do dono em 15/09/2026, depois que a remoção dos freios de nível e
    pavio deixou o modo entrar praticamente a cada vela em M1: "não é
    praticamente toda vela... no máximo a cada 5 minutos, não a cada 1".

    O escopo escolhido foi "todas as entradas com o LIVE ligado" — inclusive as
    aprovadas pela estratégia normal, porque quem manda no ritmo da transmissão
    é o relógio, não o setup.
    """

    def _state(self, *, live_demo: bool, ultima_entrada):
        return SimpleNamespace(
            min_confidence=80,
            min_payout=80,
            timeframe="M1",
            strategy_mode="conservative",
            live_demo=live_demo,
            last_entry_at=ultima_entrada,
        )

    def _candidato_do_live(self) -> dict:
        liberado = apply_live_demo(sinal_barrado(), "EURGBP-OTC", live_enabled=True)
        return monta_candidato_do_ciclo(liberado, "EURGBP-OTC")

    def test_dentro_do_piso_o_portao_barra(self) -> None:
        agora = main.utc_now()
        state = self._state(
            live_demo=True, ultima_entrada=agora - timedelta(seconds=60)
        )
        self.assertFalse(
            main.candidate_meets_cycle_threshold(
                self._candidato_do_live(),
                state,
                minimum_confidence=LIVE_CONFIDENCE,
                user_id=None,
            )
        )

    def test_passado_o_piso_o_portao_libera(self) -> None:
        agora = main.utc_now()
        state = self._state(
            live_demo=True, ultima_entrada=agora - timedelta(seconds=200)
        )
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                self._candidato_do_live(),
                state,
                minimum_confidence=LIVE_CONFIDENCE,
                user_id=None,
            )
        )

    def test_com_o_modo_desligado_o_piso_nao_existe(self) -> None:
        """Cliente pagante não herda o ritmo da transmissão."""
        agora = main.utc_now()
        state = self._state(
            live_demo=False, ultima_entrada=agora - timedelta(seconds=10)
        )
        candidato = self._candidato_do_live()
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                candidato, state, minimum_confidence=LIVE_CONFIDENCE, user_id=None
            )
        )

    def test_primeira_entrada_da_sessao_nao_espera(self) -> None:
        state = self._state(live_demo=True, ultima_entrada=None)
        self.assertTrue(
            main.candidate_meets_cycle_threshold(
                self._candidato_do_live(),
                state,
                minimum_confidence=LIVE_CONFIDENCE,
                user_id=None,
            )
        )


if __name__ == "__main__":
    unittest.main()
