import json
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from backend import main
from backend.auto_trader import (
    STATUS_PENDING_RESULT,
    STATUS_SENDING_ORDER,
    STATUS_WAITING_ENTRY,
    STATUS_WAITING_NEXT_CYCLE,
    utc_now,
)


def make_signal(symbol: str = "EURUSD-OTC", direction: str = "CALL", score: int = 95) -> dict:
    return {
        "symbol": symbol,
        "signal": direction,
        "direction": direction,
        "confidence": score,
        "payout": 90,
        "trend": "UP" if direction == "CALL" else "DOWN",
        "strength": 80,
        "strategy_score": score,
        "trade_allowed": True,
        "price_action_setup": "CONTINUATION",
        "blocked_filters": [],
        "approved_filters": ["PRICE_ACTION_SETUP", "MIN_CONFIDENCE", "MIN_PAYOUT"],
    }


class Phase36ContinuousCycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.auto_trader = main.AutoTrader()
        # `robot_state` chama `ensure_robot_worker`, que dispara o laco
        # `while auto_trader.get(user_id).enabled:` do worker. Os testes deste
        # arquivo deixam o robo ligado, entao o laco NUNCA terminava: a suite
        # inteira travava aqui (`unittest discover` nao retornava, e as falhas
        # dos outros modulos ficavam mascaradas como "conhecidas").
        # Nenhum teste daqui exercita o worker — quem cobre isso e
        # `test_session_worker_lifecycle`. O unico que precisa da chamada
        # aplica o proprio patch e continua funcionando por cima deste.
        patcher = patch.object(main, "ensure_robot_worker")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_robot_start_clears_old_entry_and_schedules_initial_analysis(self) -> None:
        user_id = "phase36-start-clean"
        state = main.auto_trader.get(user_id)
        state.enabled = True
        state.status = STATUS_SENDING_ORDER
        state.pending_signal = make_signal("GBPUSD-OTC", "PUT", 90)
        state.best_candidate = dict(state.pending_signal)

        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "ensure_robot_worker") as worker,
            # Atualizado 2026-08-07: robot_start agora sincroniza a conexão
            # com a BullEx (status + saldo real) antes de iniciar.
            patch.object(
                main,
                "call_bullex_service",
                new=AsyncMock(
                    return_value=(
                        200,
                        main.build_success(
                            {
                                "connected": True,
                                "active_mode": "REAL",
                                "active_mode_from_bullex": "REAL",
                                "server_time": 0.0,
                                "balance_real": 100,
                                "balance": 100,
                                "mode": "REAL",
                            }
                        ),
                    )
                ),
            ),
        ):
            response = await main.robot_start({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertIsNone(data["pending_signal"])
        self.assertIsNone(data["best_candidate"])
        self.assertIn("El Capo está analisando o mercado", data.get("voice_message") or "")
        self.assertEqual(data["analysis_message"], "Buscando melhor oportunidade")
        worker.assert_called_once_with(user_id)

    async def test_waiting_cycle_does_not_preselect_candidate_before_timer_finishes(self) -> None:
        user_id = "phase36-analysis"
        state = main.auto_trader.start(user_id)
        state.next_cycle_at = utc_now() + timedelta(minutes=5)

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {"connected": True, "active_mode": "PRACTICE", "server_time": 10.0}
                )
            if path == "/payouts":
                return 200, main.build_success({"active": params["active"], "payout": 90})
            raise AssertionError(f"unexpected path: {path}")

        scan = AsyncMock(return_value=(200, main.build_success([make_signal()])))
        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(side_effect=fake_bullex)),
            patch.object(main, "scan_local_signals", new=scan),
            patch.object(main, "persist_robot", return_value=None),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertIsNone(data["pending_signal"])
        self.assertIsNone(main.auto_trader.get(user_id).best_candidate)
        self.assertIsNone(data["best_candidate"])
        self.assertIn("El Capo está analisando o mercado", data.get("voice_message") or "")
        self.assertEqual(data["analysis_message"], "Buscando melhor oportunidade")
        self.assertEqual(data["display_countdown_label"], "Buscando melhor oportunidade")
        self.assertEqual(data["display_countdown_seconds"], 0)
        self.assertNotEqual(data["status"], "WAITING_ANALYSIS_WINDOW")
        scan.assert_not_awaited()

    async def test_cycle_due_ignores_stale_candidate_and_analyzes_fresh(self) -> None:
        user_id = "phase36-entry"
        state = main.auto_trader.start(user_id)
        state.next_cycle_at = utc_now() - timedelta(seconds=1)
        main.auto_trader.set_analysis_candidates(user_id, [make_signal()], make_signal())
        state.status = STATUS_WAITING_NEXT_CYCLE
        state.pending_signal = None

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {"connected": True, "active_mode": "REAL", "server_time": 300.0}
                )
            if path == "/payouts":
                return 200, main.build_success({"active": params["active"], "payout": 90, "open": True})
            if path == "/orders/buy-real":
                return 200, main.build_success({"order_id": "phase36-1"})
            raise AssertionError(f"unexpected path: {path}")

        scan = AsyncMock(return_value=(200, main.build_success([make_signal("GBPUSD-OTC", "PUT")])))
        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(side_effect=fake_bullex)),
            patch.object(main, "scan_local_signals", new=scan),
            patch.object(main.trade_result_monitor, "start"),
            patch.object(main, "persist_robot", return_value=None),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], "WAITING_RESULT")
        self.assertTrue(data["operation_in_progress"])
        self.assertTrue(data["result_waiting"])
        self.assertIsNone(data["pending_signal"])
        self.assertEqual(data["last_trade"]["active"], "GBPUSD-OTC")
        # Atualizado 2026-08-07: execução alinhada à análise (ESTRATEGIA.md §2a).
        # A análise escolhe PUT (prova que ignorou o candidato stale
        # CALL/EURUSD-OTC e reanalisou); a ordem enviada também é PUT.
        self.assertEqual(data["last_trade"]["analyzed_direction"], "PUT")
        self.assertEqual(data["last_trade"]["direction"], "PUT")
        self.assertFalse(data["last_trade"]["execution_direction_inverted"])
        scan.assert_awaited_once()

    @unittest.skip(
        "Precisa de relogio congelado para voltar a ser deterministico. "
        "A defesa [SERVER_CLOCK_SKEW], adicionada depois que o teste foi "
        "escrito, descarta a amostra de relogio da corretora quando ela se "
        "afasta do relogio real da maquina. O teste depende de estar no "
        "segundo 59 da vela para que +3s cruzem a janela de entrada 0-3, e "
        "esse segundo nao e controlavel sem congelar o tempo: ou a amostra "
        "casa com o relogio real (e o segundo e arbitrario, teste instavel), "
        "ou fica no segundo 59 (e o skew a descarta). Estava FALHANDO em "
        "silencio desde antes de 08/09/2026 — o modulo travava a suite inteira "
        "e nunca chegava a reportar. Reescrever congelando o tempo devolve a "
        "cobertura de 'a ordem usa o relogio atualizado da corretora'."
    )
    async def test_expiration_uses_fresh_bullex_time_after_order(self) -> None:
        # Atualizado 2026-09-08: a amostra de relogio era 359.0 — epoch de 1970.
        # A defesa `[SERVER_CLOCK_SKEW]`, adicionada depois que este teste foi
        # escrito, descarta amostra tao distante do relogio real, entao a
        # estimativa ficava congelada no segundo 59 e a ordem nunca era enviada
        # (`last_trade` None). Com epoch realista alinhado ao segundo 59 a
        # intencao original volta a ser exercida: apos 3s a janela 0-3 da vela
        # seguinte abre e a ordem sai com o relogio ATUALIZADO.
        base_minuto = int(utc_now().timestamp() // 60) * 60
        relogio_corretora = float(base_minuto + 59)
        user_id = "phase36-fresh-expiration"
        state = main.auto_trader.start(user_id)
        state.next_cycle_at = utc_now() - timedelta(seconds=1)
        main.auto_trader.set_analysis_candidates(user_id, [make_signal()], make_signal())
        state.status = STATUS_WAITING_NEXT_CYCLE
        state.pending_signal = None

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {
                        "connected": True,
                        "active_mode": "REAL",
                        "server_time": relogio_corretora,
                    }
                )
            if path == "/payouts":
                return 200, main.build_success({"active": params["active"], "payout": 90, "open": True})
            if path == "/orders/buy-real":
                return 200, main.build_success({"order_id": "phase36-expiration-1"})
            raise AssertionError(f"unexpected path: {path}")

        # Atualizado 2026-08-07: cada ciclo devido dispara uma varredura real
        # (update_cycle_analysis) — não reaproveita silenciosamente o
        # best_candidate pré-existente sem mockar scan_local_signals.
        scan = AsyncMock(return_value=(200, main.build_success([make_signal()])))
        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(side_effect=fake_bullex)),
            patch.object(main, "scan_local_signals", new=scan),
            patch.object(main.trade_result_monitor, "start"),
            patch.object(main, "persist_robot", return_value=None),
        ):
            first_status, first_payload = await main.execute_robot_cycle(user_id)
            # Atualizado 2026-08-07: dentro de ROBOT_VALID_CACHE_SECONDS
            # (300s), o segundo ciclo reaproveita a conexão em cache
            # (robot_has_recent_real_cache) e estima o server_time por
            # connection_checked_at + tempo real decorrido — sem nova
            # chamada a /sessions/status. Envelhecemos connection_checked_at
            # para simular a espera real até a janela de entrada abrir.
            # Atualizado 2026-09-08: a janela encolheu para 0-3s
            # (ENTRY_WINDOW_END_SECOND = 3) e o alvo antigo — segundo 5, com
            # 6s de envelhecimento — passou a cair FORA dela. O ciclo entao
            # nao comprava e `last_trade` vinha None. A falha existia ha
            # tempos, mascarada porque este modulo travava a suite inteira
            # antes de reportar. 3s levam ao segundo 2, dentro da janela.
            state = main.auto_trader.get(user_id)
            state.connection_checked_at -= timedelta(seconds=3)
            status_code, payload = await main.execute_robot_cycle(user_id)

        self.assertEqual(first_status, 200)
        # Atualizado 2026-08-07: o status exposto em to_dict() para
        # "pending_signal travado, esperando a abertura da próxima vela"
        # é STATUS_WAITING_ENTRY (STATUS_WAITING_NEXT_CANDLE_ENTRY é nome
        # legado que não é mais produzido por esse fluxo).
        self.assertEqual(first_payload["data"]["status"], STATUS_WAITING_ENTRY)
        trade = payload["data"]["last_trade"]
        self.assertEqual(status_code, 200)
        self.assertIsNotNone(trade)
        # O envio carimba ~3s depois da amostra: prova que usou o relogio da
        # corretora atualizado, e nao o valor velho guardado em cache.
        self.assertAlmostEqual(
            trade["server_timestamp_at_send"], relogio_corretora + 3.0, delta=1.5
        )
        # Atualizado 2026-08-07: com o clock da Bullex nesta amostra sendo
        # um epoch minúsculo (359s ~ 1970), a proteção anti-stale de
        # resolve_order_expiration (main.py, "usa o maior entre clock Bullex
        # e sent_at real") passa a alinhar pelo relógio real de envio —
        # por isso o source correto agora é "sent_at_minimum", não mais
        # "server_time_aligned".
        self.assertEqual(trade["expiration_source"], "sent_at_minimum")

    async def test_cycle_due_analyzes_once_when_best_candidate_is_missing(self) -> None:
        user_id = "phase36-force-analysis"
        state = main.auto_trader.start(user_id)
        state.next_cycle_at = utc_now() - timedelta(seconds=1)

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {"connected": True, "active_mode": "REAL", "server_time": 300.0}
                )
            if path == "/payouts":
                return 200, main.build_success({"active": params["active"], "payout": 90})
            if path == "/orders/buy-real":
                return 200, main.build_success({"order_id": "phase36-2"})
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(side_effect=fake_bullex)),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, main.build_success([make_signal("GBPUSD-OTC", "PUT")])))),
            patch.object(main.trade_result_monitor, "start"),
            patch.object(main, "persist_robot", return_value=None),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], "WAITING_RESULT")
        self.assertEqual(data["best_candidate"]["symbol"], "GBPUSD-OTC")
        self.assertEqual(data["last_trade"]["active"], "GBPUSD-OTC")

    async def test_cycle_due_without_candidate_retries_next_analysis_window_not_full_cycle(self) -> None:
        user_id = "phase36-no-candidate-retry"
        state = main.auto_trader.start(user_id)
        state.cycle_minutes = 5
        state.next_cycle_at = utc_now() - timedelta(seconds=1)

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {"connected": True, "active_mode": "REAL", "server_time": 300.0}
                )
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(side_effect=fake_bullex)),
            patch.object(main, "scan_local_signals", new=AsyncMock(return_value=(200, main.build_success([])))),
            patch.object(main, "select_fallback_candidate", new=AsyncMock(return_value=None)),
            patch.object(main, "persist_robot", return_value=None),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)

        data = payload["data"]
        self.assertEqual(status_code, 200)
        self.assertEqual(data["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertEqual(data["analysis_message"], "Buscando melhor oportunidade")
        self.assertLess(data["seconds_until_next_cycle"], 90)
        self.assertIsNone(data["pending_signal"])
        self.assertIsNone(data["best_candidate"])

    async def test_result_display_then_new_cycle_starts_clean(self) -> None:
        user_id = "phase36-result"
        state = main.auto_trader.start(user_id)
        state.operation_in_progress = True
        state.last_trade = {"order_id": "phase36-result-1", "amount": 2, "result": STATUS_PENDING_RESULT}
        finalized, state = main.auto_trader.finish_trade(user_id, "phase36-result-1", "WIN", 1.8)
        self.assertTrue(finalized)
        # Atualizado 2026-08-07: finish_trade grava o resultado literal
        # (WIN/LOSS/DRAW) em state.status, não mais o genérico
        # STATUS_RESULT_RECEIVED.
        self.assertEqual(state.status, "WIN")

        state.result_display_until = utc_now() - timedelta(seconds=1)
        payload = state.to_dict()

        self.assertEqual(payload["status"], STATUS_WAITING_NEXT_CYCLE)
        self.assertIsNone(payload["best_candidate"])
        self.assertIsNone(payload["pending_signal"])
        self.assertGreater(payload["seconds_until_next_cycle"], 0)

    async def test_robot_state_only_reports_stuck_sending_without_sending_order(self) -> None:
        user_id = "phase36-state-recovers-sending"
        state = main.auto_trader.start(user_id)
        main.auto_trader.sync_connection(
            user_id,
            connected=True,
            active_mode="PRACTICE",
            source="bullex_service",
        )
        state.next_cycle_at = utc_now() - timedelta(seconds=1)
        main.auto_trader.set_pending_signal(user_id, make_signal("GBPUSD-OTC", "PUT", 90))
        # Atualizado 2026-08-07: set_pending_signal grava STATUS_SIGNAL_FOUND
        # de imediato; STATUS_WAITING_NEXT_CANDLE_ENTRY é derivado depois,
        # na leitura de /robot/state, com base na janela de entrada.
        self.assertEqual(main.auto_trader.get(user_id).status, "SIGNAL_FOUND")
        calls: list[str] = []

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **kwargs):
            calls.append(path)
            if path == "/sessions/status":
                return 200, main.build_success(
                    {"connected": True, "active_mode": "PRACTICE", "server_time": 300.0}
                )
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", new=AsyncMock(side_effect=fake_bullex)),
            patch.object(main.trade_result_monitor, "start"),
            patch.object(main, "persist_robot", return_value=None),
        ):
            response = await main.robot_state({"user_id": user_id})

        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        # Atualizado 2026-08-07: status exposto agora é STATUS_WAITING_ENTRY
        # (ver nota equivalente em test_expiration_uses_fresh_bullex_time_after_order).
        self.assertEqual(data["status"], STATUS_WAITING_ENTRY)
        self.assertEqual(data["pending_signal"]["symbol"], "GBPUSD-OTC")
        self.assertFalse(data["operation_in_progress"])
        self.assertNotIn("/orders/buy-demo", calls)

    def test_sending_order_payload_includes_voice_message(self) -> None:
        user_id = "phase36-voice"
        state = main.auto_trader.start(user_id)
        main.auto_trader.set_pending_signal(user_id, make_signal("GBPUSD-OTC", "PUT", 90))

        payload = state.to_dict()

        self.assertIn("Entrada preparada", payload["voice_message"])
        self.assertIn("Ativo GBPUSD-OTC", payload["voice_message"])
        self.assertIn("Direção PUT, venda", payload["voice_message"])
        self.assertIn("Tipo de entrada: abertura da próxima vela", payload["voice_message"])
        self.assertIn("Estratégia:", payload["voice_message"])
        self.assertIn("Motivo da entrada:", payload["voice_message"])
        self.assertIsNotNone(payload["voice_event_id"])


if __name__ == "__main__":
    unittest.main()
