"""Gerenciamento Consistente pelo caminho real do backend.

Três coisas que só aparecem com os dois processos e a persistência de verdade:

1. O campo novo sobrevive ao caminho inteiro (painel → ``POST /robot/config``
   → estado → gravação → restauração). É o padrão que mais custou tempo neste
   projeto: campo descartado em silêncio por uma lista fixa.
2. O valor que SAI para a corretora é o do plano, não ``entry_value``.
3. Gateway e runtime têm cada um a sua cópia do estado: nenhum dos dois pode
   gravar um ciclo mais velho por cima do mais novo.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from backend import main, masaniello
from backend.auto_trader import (
    MASANIELLO_RESULT_PENDING,
    STATUS_STOP_WIN_HIT,
    STATUS_STOPPED,
    STATUS_WAITING_RESULT,
    AutoTrader,
    utc_now,
)
from backend.robot_persistence import SQLiteRobotPersistence, build_trade_history_item
from backend.robot_runtime_main import _begin_masaniello_cycle, _hydrate_user_from_persistence

# Mesma fixação de `test_auto_trader`: a corretora falsa não devolve velas, e a
# reconferência de nível fechada bloquearia toda compra daqui.
_FAIL_CLOSED_ORIGINAL = main.SR_ENTRY_RECHECK_FAIL_CLOSED
SERVER_TIME_M1_OPEN = 60.0


def setUpModule() -> None:
    main.SR_ENTRY_RECHECK_FAIL_CLOSED = False


def tearDownModule() -> None:
    main.SR_ENTRY_RECHECK_FAIL_CLOSED = _FAIL_CLOSED_ORIGINAL


def _config(**extra: Any) -> dict[str, Any]:
    """Corpo que o painel manda com o modo ligado (camelCase, como o front)."""
    body: dict[str, Any] = {
        "entryValue": 5,
        "minPayout": 80,
        "masanielloEnabled": True,
        "masanielloCapital": 100,
        "masanielloProfile": "conservador",
        "masanielloOperations": 10,
        "masanielloWins": 4,
    }
    body.update(extra)
    return body


class _GatewayCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.old_trader = main.auto_trader
        main.auto_trader = AutoTrader()
        self.addCleanup(setattr, main, "auto_trader", self.old_trader)
        for alvo, valor in (
            ("resolve_user_account_currency", "BRL"),
            ("ensure_robot_worker", None),
        ):
            patcher = patch.object(main, alvo, return_value=valor)
            patcher.start()
            self.addCleanup(patcher.stop)
        main._unavailable_asset_strikes.clear()
        main._ativos_com_velas_velhas.clear()
        main.active_cooldowns.clear()

    async def asyncTearDown(self) -> None:
        for task in list(main.robot_tasks.values()):
            task.cancel()
        for task in list(main.robot_tasks.values()):
            with suppress(asyncio.CancelledError):
                await task
        main.robot_tasks.clear()

    async def _save(self, user_id: str, body: dict[str, Any], account_type: str = "marketing"):
        with (
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            return await main.robot_config(body, {"user_id": user_id, "account_type": account_type})


class MasanielloConfigEndpointTests(_GatewayCase):
    async def test_campos_chegam_ao_estado_e_voltam_no_payload(self) -> None:
        response = await self._save("u-cfg", _config(masanielloCapital=250, masanielloProfile="moderado"))
        data = json.loads(response.body)["data"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["masaniello_enabled"])
        self.assertEqual(
            (data["masaniello_capital"], data["masaniello_profile"], data["masaniello_operations"], data["masaniello_wins"]),
            (250.0, "moderado", 10, 5),
        )
        self.assertFalse(data["martingale_enabled"])

    async def test_filtro_de_campos_nao_descarta_snake_case(self) -> None:
        filtrado = main.filter_robot_config_payload(
            {
                "masaniello_enabled": True,
                "masaniello_capital": 100,
                "masaniello_operations": 10,
                "masaniello_wins": 4,
                "masaniello_profile": "conservador",
                "campo_que_nao_existe": 1,
            }
        )
        self.assertEqual(
            sorted(filtrado),
            [
                "masaniello_capital",
                "masaniello_enabled",
                "masaniello_operations",
                "masaniello_profile",
                "masaniello_wins",
            ],
        )

    async def test_capital_abaixo_do_minimo_e_recusado_com_o_valor_certo(self) -> None:
        response = await self._save("u-baixo", _config(masanielloCapital=50))
        corpo = json.loads(response.body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(corpo["error"], "MASANIELLO_CAPITAL_TOO_LOW")
        self.assertEqual(corpo["data"]["min_capital"], masaniello.min_capital(10, 4, 80, 5))
        self.assertFalse(main.auto_trader.get("u-baixo").masaniello_enabled, "nada foi salvo")

    async def test_plano_invalido_e_recusado(self) -> None:
        response = await self._save(
            "u-plano",
            _config(masanielloProfile="personalizado", masanielloOperations=5, masanielloWins=5),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.body)["error"], "MASANIELLO_INVALID_PLAN")

    async def test_modo_desligado_nao_valida_o_capital(self) -> None:
        response = await self._save("u-off", _config(masanielloEnabled=False, masanielloCapital=1))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(json.loads(response.body)["data"]["masaniello_enabled"])

    async def test_conta_de_cliente_fica_no_em_breve(self) -> None:
        for tipo in ("client", "trial", "", None):
            user_id = f"u-cliente-{tipo}"
            auth = {"user_id": user_id} if tipo is None else {"user_id": user_id, "account_type": tipo}
            with (
                patch.object(main, "persist_robot", return_value=None),
                patch.object(main, "stop_robot_worker", new=AsyncMock()),
            ):
                response = await main.robot_config(_config(entryValue=9), auth)
            with self.subTest(tipo):
                self.assertEqual(response.status_code, 200)
                self.assertFalse(json.loads(response.body)["data"]["masaniello_enabled"])
                state = main.auto_trader.get(user_id)
                self.assertFalse(main.masaniello_active(state))
                self.assertEqual(main.required_order_amount(state), 9.0)

    async def test_conta_que_perde_a_liberacao_volta_ao_valor_fixo_ao_salvar(self) -> None:
        await self._save("u-rebaixada", _config(), account_type="marketing")
        self.assertTrue(main.auto_trader.get("u-rebaixada").masaniello_enabled)
        # Mesmo sem mandar o campo, salvar como cliente desliga.
        await self._save("u-rebaixada", {"entryValue": 7}, account_type="client")
        state = main.auto_trader.get("u-rebaixada")
        self.assertFalse(state.masaniello_enabled)
        self.assertEqual(state.masaniello_cycle, None)

    async def test_conta_marketing_liga_e_o_live_suspende(self) -> None:
        response = await self._save("u-mkt", _config(entryValue=9), account_type="marketing")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.body)["data"]["masaniello_enabled"])
        state = main.auto_trader.get("u-mkt")
        self.assertTrue(main.masaniello_active(state))
        self.assertEqual(main.required_order_amount(state), 100.0)
        # Modo LIVE ligado: a configuração fica, mas o robô volta ao valor fixo
        # e não abre ciclo.
        state.live_demo = True
        self.assertFalse(main.masaniello_active(state))
        self.assertEqual(main.required_order_amount(state), 9.0)
        main.auto_trader.start("u-mkt")
        main.auto_trader.masaniello_begin_if_needed("u-mkt", min_entry=5)
        self.assertIsNone(main.auto_trader.get("u-mkt").masaniello_cycle)
        self.assertIsNone(main.auto_trader.masaniello_next_order("u-mkt", 5))

    async def test_salvar_republica_o_snapshot_no_modo_external(self) -> None:
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main, "publish_robot_control_snapshot") as publish,
            patch.object(main.robot_bus, "get_snapshot", return_value=None),
            patch.object(main.robot_persistence, "load_state", return_value=None),
        ):
            response = await self._save("u-snap", _config())
        self.assertEqual(response.status_code, 200)
        publish.assert_called_once_with("u-snap")

    async def test_encerrar_ciclo_so_com_o_robo_parado(self) -> None:
        await self._save("u-fim", _config())
        main.auto_trader.start("u-fim")
        main.auto_trader.masaniello_begin_if_needed("u-fim", min_entry=5)
        with patch.object(main, "persist_robot", return_value=None):
            ligado = await main.robot_masaniello_end_cycle({"user_id": "u-fim"})
            self.assertEqual(ligado.status_code, 409)
            main.auto_trader.stop("u-fim")
            parado = await main.robot_masaniello_end_cycle({"user_id": "u-fim"})
        self.assertEqual(parado.status_code, 200)
        self.assertEqual(
            json.loads(parado.body)["data"]["masaniello_cycle"]["status"],
            masaniello.STATUS_ABANDONED,
        )


class MasanielloOrderFlowTests(_GatewayCase):
    """O valor que sai para a corretora, pelo laço de ordens de verdade."""

    async def _run_cycle(self, user_id: str, order_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        main.auto_trader.set_pending_signal(
            user_id,
            {
                "symbol": "EURUSD-OTC",
                "signal": "CALL",
                "confidence": 94,
                "payout": 90,
                "strategy_score": 94,
                "trade_allowed": True,
            },
        )
        enviados: list[dict[str, Any]] = []

        async def fake_bullex(method, path, call_user_id, json_body=None, params=None, **_kwargs):
            if path == "/sessions/status":
                return 200, main.build_success(
                    {"connected": True, "active_mode": "REAL", "server_time": SERVER_TIME_M1_OPEN}
                )
            if path == "/orders/buy-real":
                enviados.append(dict(json_body))
                return 200, main.build_success({"order_id": order_id})
            raise AssertionError(f"unexpected path: {path}")

        with (
            patch.object(main, "call_bullex_service", side_effect=fake_bullex),
            patch.object(main.trade_result_monitor, "start", return_value=True),
            patch.object(main, "persist_robot", return_value=None),
            patch.object(main, "stop_robot_worker", new=AsyncMock()),
        ):
            status_code, payload = await main.execute_robot_cycle(user_id)
        self.assertEqual(status_code, 200)
        return enviados, payload["data"]

    async def _prepare(self, user_id: str, **config: Any) -> None:
        response = await self._save(user_id, _config(**config))
        self.assertEqual(response.status_code, 200)
        main.auto_trader.start(user_id)
        main.auto_trader.masaniello_begin_if_needed(user_id, min_entry=5)

    async def test_ordem_sai_com_o_valor_do_plano(self) -> None:
        await self._prepare("u-ordem", entryValue=37)
        enviados, data = await self._run_cycle("u-ordem", "501")
        self.assertEqual(len(enviados), 1)
        self.assertEqual(enviados[0]["amount"], 6.82, "valor do plano, não os 37 do valor fixo")
        trade = data["last_trade"]
        self.assertEqual(data["status"], STATUS_WAITING_RESULT)
        self.assertEqual(trade["amount"], 6.82)
        self.assertEqual(trade["original_amount"], 6.82)
        ciclo = data["masaniello_cycle"]
        self.assertEqual(trade["masaniello"]["cycle_id"], ciclo["id"])
        self.assertEqual(trade["masaniello"]["seq"], 1)
        self.assertEqual(ciclo["pending"]["order_id"], "501")

    async def test_marca_do_ciclo_chega_ao_historico(self) -> None:
        await self._prepare("u-hist")
        await self._run_cycle("u-hist", "601")
        main.auto_trader.finish_trade("u-hist", "601", "WIN", 6.14)
        item = build_trade_history_item("u-hist", main.auto_trader.get("u-hist").last_trade)
        self.assertEqual(item["amount"], 6.82)
        self.assertEqual(item["original_amount"], 6.82)
        self.assertEqual(item["analysis_json"]["masaniello"]["seq"], 1)
        self.assertEqual(item["analysis_json"]["masaniello"]["stake_planned"], 6.82)

    async def test_segunda_ordem_usa_o_capital_depois_do_resultado(self) -> None:
        await self._prepare("u-duas")
        await self._run_cycle("u-duas", "701")
        main.auto_trader.finish_trade("u-duas", "701", "LOSS", -6.82)
        state = main.auto_trader.get("u-duas")
        state.result_display_until = None
        state.next_cycle_at = None
        enviados, _ = await self._run_cycle("u-duas", "702")
        esperado = masaniello.simulate(100, 10, 4, 80, ["L"], min_entry=5)["next_stake"]
        self.assertEqual(enviados[0]["amount"], esperado)
        self.assertEqual(esperado, 10.23)

    async def test_resultado_vencido_para_o_robo_sem_enviar_ordem(self) -> None:
        await self._prepare("u-vencido")
        state = main.auto_trader.get("u-vencido")
        ciclo = masaniello.mark_pending(
            state.masaniello_cycle,
            order_id="900",
            stake=6.82,
            at="2026-01-01T00:00:00+00:00",
        )
        state.masaniello_cycle = ciclo
        enviados, data = await self._run_cycle("u-vencido", "901")
        self.assertEqual(enviados, [], "sem resultado da anterior não há próxima ordem")
        self.assertFalse(data["enabled"])
        self.assertEqual(data["status"], STATUS_STOPPED)
        self.assertEqual(data["last_order_error"], MASANIELLO_RESULT_PENDING)
        self.assertEqual(data["masaniello_cycle"]["status"], masaniello.STATUS_ACTIVE)

    async def test_resultado_em_voo_espera_sem_parar(self) -> None:
        await self._prepare("u-voo")
        state = main.auto_trader.get("u-voo")
        state.masaniello_cycle = masaniello.mark_pending(
            state.masaniello_cycle, order_id="950", stake=6.82, at=utc_now().isoformat()
        )
        enviados, data = await self._run_cycle("u-voo", "951")
        self.assertEqual(enviados, [])
        self.assertTrue(data["enabled"])

    async def test_saldo_exigido_e_o_capital_no_inicio_e_a_entrada_no_meio(self) -> None:
        await self._prepare("u-saldo")
        state = main.auto_trader.get("u-saldo")
        self.assertEqual(main.required_order_amount(state), 6.82)
        state.masaniello_cycle = None
        self.assertEqual(main.required_order_amount(state), 100.0)
        self.assertEqual(
            main.balance_shortfall_message(state), main.MASANIELLO_CAPITAL_EXCEEDS_BALANCE_MESSAGE
        )
        state.masaniello_enabled = False
        self.assertEqual(main.required_order_amount(state), state.entry_value)


class MasanielloFimDeCicloNoMonitorTests(unittest.IsolatedAsyncioTestCase):
    """Resultado chegando pelo monitor: grava o Histórico e para o robô na meta."""

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.old_persistence = main.robot_persistence
        self.old_trader = main.auto_trader
        main.robot_persistence = SQLiteRobotPersistence(str(Path(self.directory.name) / "robot.db"))
        main.auto_trader = AutoTrader()

    async def asyncTearDown(self) -> None:
        main.robot_persistence = self.old_persistence
        main.auto_trader = self.old_trader
        self.directory.cleanup()

    async def test_quarto_acerto_para_o_robo_e_grava_as_quatro_linhas(self) -> None:
        user_id = "u-monitor"
        trader = main.auto_trader
        from backend.auto_trader import RobotConfigUpdate

        trader.update_config(
            user_id, RobotConfigUpdate(masaniello_enabled=True, masaniello_capital=100)
        )
        trader.start(user_id)
        trader.masaniello_begin_if_needed(user_id, min_entry=5)
        with patch.object(main, "stop_robot_worker", new=AsyncMock()) as stop_worker:
            for indice in range(4):
                order = trader.masaniello_next_order(user_id, 5)
                order_id = str(8000 + indice)
                trader.record_trade(
                    user_id,
                    {
                        "order_id": order_id,
                        "active": "EURUSD-OTC",
                        "direction": "CALL",
                        "amount": order["stake"],
                        "confidence": 90,
                        "payout": 88,
                        "result": "PENDING_RESULT",
                        "expiration": "M1",
                        "masaniello": {
                            "cycle_id": order["cycle_id"],
                            "seq": order["seq"],
                            "stake_planned": order["stake_planned"],
                            "adjusted_to_min": order["adjusted_to_min"],
                            "capital_before": order["capital_before"],
                        },
                    },
                )
                await main.finish_monitored_trade(
                    user_id, order_id, "WIN", round(order["stake"] * 0.88, 2)
                )
                trader.get(user_id).result_display_until = None

        state = trader.get(user_id)
        self.assertFalse(state.enabled)
        self.assertEqual(state.status, STATUS_STOP_WIN_HIT)
        self.assertEqual(state.masaniello_cycle["status"], masaniello.STATUS_TARGET_HIT)
        stop_worker.assert_awaited()
        historico = main.robot_persistence.load_trade_history(user_id, 30)
        self.assertEqual(len(historico), 4)
        self.assertEqual(
            sorted(item["masaniello"]["seq"] for item in historico), [1, 2, 3, 4]
        )
        self.assertEqual(
            {item["masaniello"]["cycle_id"] for item in historico}, {state.masaniello_cycle["id"]}
        )


class MasanielloDonoUnicoTests(unittest.IsolatedAsyncioTestCase):
    """Gateway e runtime: quem tem a cópia mais nova do ciclo ganha."""

    def setUp(self) -> None:
        self.old_trader = main.auto_trader
        main.auto_trader = AutoTrader()
        self.addCleanup(setattr, main, "auto_trader", self.old_trader)
        self.user_id = "11111111-2222-3333-4444-555555555555"
        from backend.auto_trader import RobotConfigUpdate

        main.auto_trader.update_config(
            self.user_id, RobotConfigUpdate(masaniello_enabled=True, masaniello_capital=100)
        )
        main.auto_trader.start(self.user_id)
        state = main.auto_trader.masaniello_begin_if_needed(self.user_id, min_entry=5)
        self.velho = state.masaniello_cycle
        # O runtime andou duas ordens que o gateway não viu.
        novo = masaniello.mark_pending(self.velho, order_id="1", stake=6.82)
        novo = masaniello.apply_result(novo, order_id="1", result="LOSS", profit=-6.82)
        self.novo = novo

    def test_gateway_adota_o_ciclo_do_snapshot_do_runtime(self) -> None:
        snapshot = {"data": {"masaniello_cycle": self.novo}}
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main.robot_bus, "get_snapshot", return_value=snapshot),
        ):
            main.sync_masaniello_cycle_on_gateway(self.user_id)
        ciclo = main.auto_trader.get(self.user_id).masaniello_cycle
        self.assertEqual((ciclo["rev"], ciclo["losses"]), (self.novo["rev"], 1))

    def test_snapshot_velho_nao_regride_o_gateway(self) -> None:
        main.auto_trader.get(self.user_id).masaniello_cycle = self.novo
        snapshot = {"data": {"masaniello_cycle": self.velho}}
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(main.robot_bus, "get_snapshot", return_value=snapshot),
        ):
            main.sync_masaniello_cycle_on_gateway(self.user_id)
        self.assertIs(main.auto_trader.get(self.user_id).masaniello_cycle, self.novo)

    def test_modo_embutido_nao_consulta_o_redis(self) -> None:
        with (
            patch.object(main, "robot_runtime_mode", return_value="worker"),
            patch.object(main.robot_bus, "get_snapshot") as get_snapshot,
        ):
            main.sync_masaniello_cycle_on_gateway(self.user_id)
        get_snapshot.assert_not_called()

    def test_gravacao_nao_troca_ciclo_novo_do_banco_por_um_velho(self) -> None:
        payload = {"masaniello_enabled": True, "masaniello_cycle": self.velho}
        with patch.object(
            main.robot_persistence, "load_state", return_value={"masaniello_cycle": self.novo}
        ):
            gravado = main._com_ciclo_mais_novo_do_banco(self.user_id, payload)
        self.assertEqual(gravado["masaniello_cycle"]["rev"], self.novo["rev"])

    def test_gravacao_mantem_o_ciclo_novo_quando_o_banco_esta_atrasado(self) -> None:
        payload = {"masaniello_enabled": True, "masaniello_cycle": self.novo}
        with patch.object(
            main.robot_persistence, "load_state", return_value={"masaniello_cycle": self.velho}
        ):
            gravado = main._com_ciclo_mais_novo_do_banco(self.user_id, payload)
        self.assertIs(gravado, payload)

    def test_persist_grava_o_ciclo_mais_novo_de_ponta_a_ponta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistencia = SQLiteRobotPersistence(str(Path(directory) / "robot.db"))
            # O runtime já gravou o ciclo adiantado.
            adiantado = main.auto_trader.get(self.user_id).to_dict()
            adiantado["masaniello_cycle"] = self.novo
            persistencia.save_state(self.user_id, adiantado)
            with (
                patch.object(main, "robot_persistence", persistencia),
                patch.object(main, "robot_runtime_mode", return_value="external"),
                patch.object(main.robot_bus, "get_snapshot", return_value=None),
                patch.object(main.robot_state_ws_hub, "has_connections", return_value=False),
            ):
                futuro = main.persist_robot(self.user_id)
                if futuro is not None:
                    futuro.result(timeout=10)
            salvo = persistencia.load_state(self.user_id)
        self.assertEqual(salvo["masaniello_cycle"]["rev"], self.novo["rev"])
        self.assertEqual(salvo["masaniello_cycle"]["losses"], 1)
        self.assertNotIn(main.CONFERIR_CICLO_NO_BANCO, salvo)

    def test_runtime_no_start_mantem_o_ciclo_vivo_a_frente_do_banco(self) -> None:
        trader = main.auto_trader
        trader.get(self.user_id).masaniello_cycle = self.novo
        atrasado = trader.get(self.user_id).to_dict()
        atrasado["masaniello_cycle"] = self.velho
        atrasado["enabled"] = True
        persistence = MagicMock()
        persistence.load_states.return_value = [(self.user_id, atrasado)]
        persistence.load_trades_for_restore.return_value = []
        gateway = MagicMock()
        gateway.robot_persistence = persistence
        gateway.auto_trader = trader
        gateway.robot_persistence_source = lambda: "runtime"
        gateway.apply_session_score_authority_to_state = None

        _hydrate_user_from_persistence(gateway, self.user_id, force=True)

        ciclo = trader.get(self.user_id).masaniello_cycle
        self.assertEqual((ciclo["rev"], ciclo["losses"]), (self.novo["rev"], 1))

    def test_runtime_no_start_adota_o_ciclo_novo_aberto_pelo_gateway(self) -> None:
        trader = main.auto_trader
        # Memória do runtime: ciclo antigo, encerrado.
        encerrado = masaniello.close(self.novo, masaniello.STATUS_BUST, masaniello.REASON_ERRORS)
        trader.get(self.user_id).masaniello_cycle = encerrado
        aberto = masaniello.new_cycle(100, 10, 4, 80, min_entry=5)
        do_gateway = trader.get(self.user_id).to_dict()
        do_gateway["masaniello_cycle"] = aberto
        do_gateway["enabled"] = True
        persistence = MagicMock()
        persistence.load_states.return_value = [(self.user_id, do_gateway)]
        persistence.load_trades_for_restore.return_value = []
        gateway = MagicMock()
        gateway.robot_persistence = persistence
        gateway.auto_trader = trader
        gateway.robot_persistence_source = lambda: "runtime"
        gateway.apply_session_score_authority_to_state = None
        gateway.masaniello_min_entry = lambda _uid: 5.0

        _hydrate_user_from_persistence(gateway, self.user_id, force=True)
        _begin_masaniello_cycle(gateway, self.user_id)

        ciclo = trader.get(self.user_id).masaniello_cycle
        self.assertEqual(ciclo["id"], aberto["id"])
        self.assertEqual(ciclo["status"], masaniello.STATUS_ACTIVE)
        gateway.persist_robot.assert_not_called()

    def test_runtime_abre_ciclo_se_o_gateway_nao_abriu(self) -> None:
        trader = main.auto_trader
        trader.get(self.user_id).masaniello_cycle = None
        gateway = MagicMock()
        gateway.auto_trader = trader
        gateway.masaniello_min_entry = lambda _uid: 5.0

        _begin_masaniello_cycle(gateway, self.user_id)

        self.assertEqual(trader.get(self.user_id).masaniello_cycle["status"], masaniello.STATUS_ACTIVE)
        gateway.persist_robot.assert_called_once_with(self.user_id)

    def test_reconcile_do_gateway_guarda_o_motivo_do_fim_do_ciclo(self) -> None:
        # Runtime bateu a meta e desligou; o snapshot dele saiu com status WIN.
        meta = masaniello.close(self.novo, masaniello.STATUS_TARGET_HIT, masaniello.REASON_TARGET)
        snapshot = {
            "data": {
                "enabled": False,
                "status": "WIN",
                "wins": 4,
                "losses": 0,
                "profit": 10.0,
                "masaniello_cycle": meta,
            }
        }
        state = main.auto_trader.get(self.user_id)
        with (
            patch.object(main, "robot_runtime_mode", return_value="external"),
            patch.object(type(main.robot_bus), "enabled", new_callable=PropertyMock, return_value=True),
            patch.object(main.robot_bus, "get_snapshot", return_value=snapshot),
        ):
            state = main.reconcile_gateway_enabled_from_runtime_snapshot(self.user_id, state)
        self.assertFalse(state.enabled)
        self.assertEqual(state.status, STATUS_STOP_WIN_HIT)

    def test_empate_de_rev_fica_com_o_ciclo_encerrado(self) -> None:
        encerrado = masaniello.close(self.velho, masaniello.STATUS_ABANDONED, masaniello.REASON_USER)
        ativo = masaniello.mark_pending(self.velho, order_id="x", stake=6.82)
        self.assertEqual(encerrado["rev"], ativo["rev"])
        self.assertIs(masaniello.pick_freshest_cycle(ativo, encerrado), encerrado)
        self.assertIs(masaniello.pick_freshest_cycle(encerrado, ativo), encerrado)


if __name__ == "__main__":
    unittest.main()
