"""Gerenciamento Consistente dentro do robô: o ciclo acompanha as ordens reais.

Os testes mandam a ordem do mesmo jeito que o laço de ordens de
``backend/main.py``: valor = ``masaniello_next_order``, e a operação leva a
marca ``masaniello`` com o id do ciclo. O que se confere é sempre o fim da
cadeia — o valor que sairia para a corretora e o estado do robô depois do
resultado —, não log intermediário.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from backend import masaniello
from backend.auto_trader import (
    MASANIELLO_BUST_MESSAGE,
    MASANIELLO_TARGET_MESSAGE,
    STATUS_STOP_LOSS_HIT,
    STATUS_STOP_WIN_HIT,
    AutoTrader,
    RobotConfigUpdate,
    masaniello_active,
    resolve_robot_stop_reason,
    utc_now,
)

MIN_ENTRY = 5.0
PAYOUT = 88.0


class _Base(unittest.TestCase):
    def _start(
        self,
        user_id: str,
        *,
        capital: float = 100.0,
        profile: str = "conservador",
        operations: int | None = None,
        wins: int | None = None,
    ) -> AutoTrader:
        trader = AutoTrader()
        update = {"masaniello_enabled": True, "masaniello_capital": capital, "masaniello_profile": profile}
        if operations is not None:
            update["masaniello_operations"] = operations
        if wins is not None:
            update["masaniello_wins"] = wins
        trader.update_config(user_id, RobotConfigUpdate(**update))
        trader.start(user_id)
        trader.masaniello_begin_if_needed(user_id, min_entry=MIN_ENTRY)
        return trader

    def _send(self, trader: AutoTrader, user_id: str, order_id: str) -> float:
        """Envia a próxima ordem do ciclo, como o laço de ordens do robô."""
        order = trader.masaniello_next_order(user_id, MIN_ENTRY)
        self.assertIsNotNone(order, "o ciclo tinha de ter próxima entrada")
        trader.record_trade(
            user_id,
            {
                "order_id": order_id,
                "active": "EURUSD-OTC",
                "direction": "CALL",
                "amount": order["stake"],
                "confidence": 90,
                "payout": PAYOUT,
                "result": "PENDING_RESULT",
                "masaniello": {
                    "cycle_id": order["cycle_id"],
                    "seq": order["seq"],
                    "stake_planned": order["stake_planned"],
                    "adjusted_to_min": order["adjusted_to_min"],
                    "capital_before": order["capital_before"],
                },
            },
        )
        return order["stake"]

    def _play(self, trader: AutoTrader, user_id: str, results: str, first_id: int = 1000):
        """Roda uma sequência W/L/D e devolve os valores enviados."""
        stakes = []
        state = trader.get(user_id)
        for index, letter in enumerate(results):
            order_id = str(first_id + index)
            stake = self._send(trader, user_id, order_id)
            stakes.append(stake)
            if letter == "W":
                _, state = trader.finish_trade(user_id, order_id, "WIN", round(stake * PAYOUT / 100, 2))
            elif letter == "L":
                _, state = trader.finish_trade(user_id, order_id, "LOSS", -stake)
            else:
                _, state = trader.finish_trade(user_id, order_id, "DRAW", 0.0)
        return stakes, state


class MasanielloConfigTests(_Base):
    def test_perfil_manda_em_operacoes_e_acertos(self) -> None:
        trader = AutoTrader()
        state = trader.update_config(
            "u-perfil",
            RobotConfigUpdate(
                masaniello_enabled=True,
                masaniello_profile="Moderado",
                masaniello_operations=99,
                masaniello_wins=1,
            ),
        )
        self.assertEqual(
            (state.masaniello_profile, state.masaniello_operations, state.masaniello_wins),
            ("moderado", 10, 5),
        )

    def test_personalizado_fica_dentro_da_faixa(self) -> None:
        trader = AutoTrader()
        state = trader.update_config(
            "u-custom",
            RobotConfigUpdate(
                masaniello_profile="personalizado",
                masaniello_operations=500,
                masaniello_wins=500,
            ),
        )
        self.assertEqual(state.masaniello_operations, masaniello.MAX_OPERATIONS)
        self.assertEqual(state.masaniello_wins, masaniello.MAX_OPERATIONS - 1)

    def test_aliases_camel_case_do_painel(self) -> None:
        update = RobotConfigUpdate.model_validate(
            {
                "masanielloEnabled": True,
                "masanielloCapital": 250,
                "masanielloOperations": 12,
                "masanielloWins": 5,
                "masanielloProfile": "personalizado",
            }
        )
        state = AutoTrader().update_config("u-alias", update)
        self.assertTrue(state.masaniello_enabled)
        self.assertEqual(
            (state.masaniello_capital, state.masaniello_operations, state.masaniello_wins),
            (250.0, 12, 5),
        )

    def test_ligar_o_modo_desliga_o_gale(self) -> None:
        trader = AutoTrader()
        trader.update_config("u-gale", RobotConfigUpdate(martingale_enabled=True))
        state = trader.update_config(
            "u-gale", RobotConfigUpdate(masaniello_enabled=True, martingale_enabled=True)
        )
        self.assertTrue(state.masaniello_enabled)
        self.assertFalse(state.martingale_enabled)

    def test_ligar_o_gale_desliga_o_modo(self) -> None:
        trader = AutoTrader()
        trader.update_config("u-gale2", RobotConfigUpdate(masaniello_enabled=True))
        state = trader.update_config("u-gale2", RobotConfigUpdate(martingale_enabled=True))
        self.assertTrue(state.martingale_enabled)
        self.assertFalse(state.masaniello_enabled)

    def test_desligar_o_modo_encerra_o_ciclo_em_andamento(self) -> None:
        trader = self._start("u-off")
        trader.stop("u-off")
        state = trader.update_config("u-off", RobotConfigUpdate(masaniello_enabled=False))
        self.assertEqual(state.masaniello_cycle["status"], masaniello.STATUS_ABANDONED)

    def test_modo_live_fica_de_fora(self) -> None:
        trader = AutoTrader()
        state = trader.update_config("u-live", RobotConfigUpdate(masaniello_enabled=True))
        state.live_demo = True
        self.assertFalse(masaniello_active(state))


class MasanielloOrderTests(_Base):
    def test_valor_da_ordem_vem_do_plano_e_nao_do_valor_fixo(self) -> None:
        trader = self._start("u-valor")
        trader.get("u-valor").entry_value = 50.0
        self.assertEqual(self._send(trader, "u-valor", "1"), 6.82)

    def test_sequencia_real_segue_o_plano(self) -> None:
        trader = self._start("u-seq", capital=2000)
        stakes, state = self._play(trader, "u-seq", "LWL")
        esperado = masaniello.simulate(2000, 10, 4, 80, list("LWL"), min_entry=5, payout_real=PAYOUT)
        self.assertEqual(stakes, [row["stake"] for row in esperado["rows"]])
        self.assertEqual(state.masaniello_cycle["capital_atual"], esperado["capital_atual"])
        self.assertEqual(state.masaniello_cycle["next_stake"], esperado["next_stake"])

    def test_ordem_em_voo_bloqueia_a_proxima(self) -> None:
        trader = self._start("u-voo")
        self._send(trader, "u-voo", "1")
        self.assertEqual(trader.get("u-voo").masaniello_cycle["pending"]["order_id"], "1")
        self.assertIsNone(trader.masaniello_next_order("u-voo", MIN_ENTRY))

    def test_placar_do_robo_continua_contando_igual(self) -> None:
        trader = self._start("u-placar")
        _, state = self._play(trader, "u-placar", "WLW")
        self.assertEqual((state.wins, state.losses), (2, 1))
        self.assertEqual(
            (state.masaniello_cycle["wins"], state.masaniello_cycle["losses"]), (2, 1)
        )

    def test_empate_nao_anda_o_plano(self) -> None:
        trader = self._start("u-empate")
        stakes, state = self._play(trader, "u-empate", "DD")
        self.assertEqual(stakes, [6.82, 6.82])
        ciclo = state.masaniello_cycle
        self.assertEqual((ciclo["wins"], ciclo["losses"], ciclo["capital_atual"]), (0, 0, 100.0))
        self.assertEqual([row["result"] for row in ciclo["rows"]], ["DRAW", "DRAW"])

    def test_sem_capital_para_o_minimo_encerra_como_perdido(self) -> None:
        trader = self._start("u-curto", capital=20, profile="agressivo")
        _, state = self._play(trader, "u-curto", "LLL")
        self.assertEqual(state.masaniello_cycle["capital_atual"], 3.86)
        self.assertIsNone(trader.masaniello_next_order("u-curto", MIN_ENTRY))
        ciclo = trader.get("u-curto").masaniello_cycle
        self.assertEqual((ciclo["status"], ciclo["end_reason"]), (masaniello.STATUS_BUST, masaniello.REASON_NO_CAPITAL))


class MasanielloCycleEndTests(_Base):
    def test_meta_batida_para_o_robo_com_stop_win(self) -> None:
        trader = self._start("u-meta")
        _, state = self._play(trader, "u-meta", "WWWW")
        self.assertFalse(state.enabled)
        self.assertEqual(state.status, STATUS_STOP_WIN_HIT)
        self.assertEqual(state.masaniello_cycle["status"], masaniello.STATUS_TARGET_HIT)
        self.assertEqual(state.to_dict()["operation_message"], MASANIELLO_TARGET_MESSAGE)

    def test_erros_esgotados_param_o_robo_com_stop_loss(self) -> None:
        trader = self._start("u-quebra")
        stakes, state = self._play(trader, "u-quebra", "LLLLLLL")
        self.assertFalse(state.enabled)
        self.assertEqual(state.status, STATUS_STOP_LOSS_HIT)
        self.assertEqual(state.masaniello_cycle["status"], masaniello.STATUS_BUST)
        self.assertEqual(state.masaniello_cycle["capital_atual"], 0.0)
        self.assertEqual(round(sum(stakes), 2), 100.0, "perde exatamente o capital do ciclo")
        self.assertEqual(state.to_dict()["operation_message"], MASANIELLO_BUST_MESSAGE)

    def test_stop_digitado_nao_vale_neste_modo(self) -> None:
        trader = self._start("u-stop")
        state = trader.get("u-stop")
        state.stop_loss, state.stop_loss_mode = 5.0, "money"
        state.stop_win_operations, state.stop_win_mode = 1, "operations"
        _, state = self._play(trader, "u-stop", "WL")
        self.assertTrue(state.enabled, "só o fim do ciclo para o robô")
        self.assertIsNone(resolve_robot_stop_reason(state, net_profit=-500.0))

    def test_robo_parado_nao_tem_stop_pendente(self) -> None:
        trader = self._start("u-parado")
        _, state = self._play(trader, "u-parado", "WWWW")
        # Ciclo encerrado + robô parado: o Iniciar não pode ser recusado.
        self.assertIsNone(resolve_robot_stop_reason(state))

    def test_modo_desligado_mantem_o_stop_de_sempre(self) -> None:
        trader = AutoTrader()
        state = trader.start("u-classico")
        state.stop_loss, state.stop_loss_mode = 10.0, "money"
        self.assertEqual(resolve_robot_stop_reason(state, net_profit=-10.0), STATUS_STOP_LOSS_HIT)


class MasanielloStartTests(_Base):
    def test_parar_e_iniciar_continua_o_mesmo_ciclo(self) -> None:
        trader = self._start("u-continua")
        self._play(trader, "u-continua", "WL")
        antes = trader.get("u-continua").masaniello_cycle
        trader.stop("u-continua")
        trader.start("u-continua")
        state = trader.masaniello_begin_if_needed("u-continua", min_entry=MIN_ENTRY)
        self.assertIs(state.masaniello_cycle, antes)

    def test_iniciar_depois_da_meta_abre_ciclo_novo(self) -> None:
        trader = self._start("u-novo")
        self._play(trader, "u-novo", "WWWW")
        antigo = trader.get("u-novo").masaniello_cycle["id"]
        trader.start("u-novo")
        state = trader.masaniello_begin_if_needed("u-novo", min_entry=MIN_ENTRY)
        ciclo = state.masaniello_cycle
        self.assertNotEqual(ciclo["id"], antigo)
        self.assertEqual((ciclo["status"], ciclo["capital_atual"], ciclo["rows"]), ("ACTIVE", 100.0, []))
        self.assertEqual(state.wins, 4, "o placar só zera no Reiniciar placar")

    def test_mudar_o_capital_abre_ciclo_novo(self) -> None:
        trader = self._start("u-plano")
        self._play(trader, "u-plano", "W")
        trader.stop("u-plano")
        trader.update_config("u-plano", RobotConfigUpdate(masaniello_capital=300))
        trader.start("u-plano")
        state = trader.masaniello_begin_if_needed("u-plano", min_entry=MIN_ENTRY)
        self.assertEqual(state.masaniello_cycle["capital_inicial"], 300.0)
        self.assertEqual(state.masaniello_cycle["rows"], [])

    def test_pendente_recente_continua_e_vencido_abre_outro(self) -> None:
        trader = self._start("u-pend")
        self._send(trader, "u-pend", "1")
        ciclo = trader.get("u-pend").masaniello_cycle
        state = trader.masaniello_begin_if_needed("u-pend", min_entry=MIN_ENTRY)
        self.assertIs(state.masaniello_cycle, ciclo, "resultado ainda em voo: mesmo ciclo")

        velho = (utc_now() - timedelta(hours=1)).isoformat()
        state.masaniello_cycle = {**ciclo, "pending": {**ciclo["pending"], "at": velho}}
        state = trader.masaniello_begin_if_needed("u-pend", min_entry=MIN_ENTRY)
        self.assertNotEqual(state.masaniello_cycle["id"], ciclo["id"])
        self.assertIsNone(state.masaniello_cycle["pending"])

    def test_encerrar_ciclo_e_iniciar_abre_outro(self) -> None:
        trader = self._start("u-encerra")
        self._play(trader, "u-encerra", "L")
        trader.stop("u-encerra")
        state = trader.masaniello_end_cycle("u-encerra")
        self.assertEqual(state.masaniello_cycle["status"], masaniello.STATUS_ABANDONED)
        trader.start("u-encerra")
        state = trader.masaniello_begin_if_needed("u-encerra", min_entry=MIN_ENTRY)
        self.assertEqual((state.masaniello_cycle["status"], state.masaniello_cycle["losses"]), ("ACTIVE", 0))


class MasanielloResultadoForaDoCaminhoTests(_Base):
    def test_timeout_mantem_o_ciclo_esperando(self) -> None:
        trader = self._start("u-timeout")
        self._send(trader, "u-timeout", "1")
        trader.timeout_trade("u-timeout", "1")
        ciclo = trader.get("u-timeout").masaniello_cycle
        self.assertTrue(ciclo["pending"]["unknown"])
        self.assertEqual(ciclo["status"], masaniello.STATUS_ACTIVE)
        self.assertIsNone(trader.masaniello_next_order("u-timeout", MIN_ENTRY))

    def test_resultado_que_chega_depois_do_timeout_entra_na_linha(self) -> None:
        trader = self._start("u-recupera")
        stake = self._send(trader, "u-recupera", "1")
        trader.timeout_trade("u-recupera", "1")
        finalized, state = trader.finish_trade("u-recupera", "1", "LOSS", -stake)
        self.assertTrue(finalized)
        ciclo = state.masaniello_cycle
        self.assertIsNone(ciclo["pending"])
        self.assertEqual((ciclo["losses"], ciclo["capital_atual"]), (1, 93.18))

    def test_resultado_atrasado_de_ordem_que_saiu_do_ciclo_do_robo(self) -> None:
        trader = self._start("u-atrasado")
        stake = self._send(trader, "u-atrasado", "1")
        state = trader.get("u-atrasado")
        enviada = dict(state.last_trade)
        # O robô reciclou a operação sem resposta (`waiting_result_stale`).
        state.last_trade = None
        state.operation_in_progress = False
        fechado = trader.count_late_result("u-atrasado", enviada, "WIN", round(stake * 0.88, 2))
        self.assertIsNotNone(fechado)
        ciclo = trader.get("u-atrasado").masaniello_cycle
        self.assertEqual((ciclo["wins"], ciclo["capital_atual"]), (1, 106.0))
        self.assertIsNone(ciclo["pending"])

    def test_resultado_de_ciclo_antigo_nao_entra_no_novo(self) -> None:
        trader = self._start("u-antigo")
        stake = self._send(trader, "u-antigo", "1")
        state = trader.get("u-antigo")
        enviada = dict(state.last_trade)
        state.last_trade = None
        state.operation_in_progress = False
        trader.masaniello_end_cycle("u-antigo")
        trader.start("u-antigo")
        trader.masaniello_begin_if_needed("u-antigo", min_entry=MIN_ENTRY)
        trader.count_late_result("u-antigo", enviada, "LOSS", -stake)
        ciclo = trader.get("u-antigo").masaniello_cycle
        self.assertEqual((ciclo["losses"], ciclo["capital_atual"], ciclo["rows"]), (0, 100.0, []))


class MasanielloRestoreTests(_Base):
    def test_ciclo_sobrevive_a_gravar_e_restaurar(self) -> None:
        trader = self._start("u-restore")
        self._play(trader, "u-restore", "WL")
        salvo = trader.get("u-restore").to_dict()
        outro = AutoTrader()
        state = outro.restore("u-restore", salvo, [])
        self.assertTrue(state.masaniello_enabled)
        self.assertEqual(state.masaniello_cycle, salvo["masaniello_cycle"])
        self.assertEqual(len(state.masaniello_cycle["rows"]), 2)
        for campo in ("masaniello_capital", "masaniello_operations", "masaniello_wins", "masaniello_profile"):
            self.assertEqual(getattr(state, campo), salvo[campo], campo)

    def test_restore_completa_o_ciclo_pelo_historico(self) -> None:
        trader = self._start("u-conserto")
        # Estado gravado ANTES do resultado (gravação assíncrona atrasada).
        stake = self._send(trader, "u-conserto", "1")
        salvo = trader.get("u-conserto").to_dict()
        trader.finish_trade("u-conserto", "1", "LOSS", -stake)
        historico = [dict(trader.get("u-conserto").last_trade)]

        state = AutoTrader().restore("u-conserto", salvo, historico)
        ciclo = state.masaniello_cycle
        self.assertEqual((ciclo["losses"], ciclo["capital_atual"]), (1, 93.18))
        self.assertIsNone(ciclo["pending"])
        self.assertEqual(len(ciclo["rows"]), 1)

        # Idempotente: restaurar de novo com o mesmo Histórico não soma outra vez.
        de_novo = AutoTrader().restore("u-conserto", state.to_dict(), historico)
        self.assertEqual(len(de_novo.masaniello_cycle["rows"]), 1)

    def test_reiniciar_ciclo_apaga_o_ciclo(self) -> None:
        trader = self._start("u-reset")
        self._play(trader, "u-reset", "W")
        state = trader.reset_cycle("u-reset", reset_score=True)
        self.assertIsNone(state.masaniello_cycle)

    def test_reiniciar_placar_nao_mexe_no_ciclo(self) -> None:
        trader = self._start("u-placar-zero")
        self._play(trader, "u-placar-zero", "WL")
        antes = trader.get("u-placar-zero").masaniello_cycle
        state = trader.reset_score("u-placar-zero")
        self.assertEqual((state.wins, state.losses), (0, 0))
        self.assertIs(state.masaniello_cycle, antes)


if __name__ == "__main__":
    unittest.main()
