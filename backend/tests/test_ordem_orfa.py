"""Ordem cujo resultado não chega nunca pode ficar PENDENTE para sempre.

Casos reais: 29/09 ordem 14307723702 (R$15) perdeu o evento de fechamento na
queda do websocket, o ciclo foi reciclado em 150 s com o monitor vivo, o robô
abriu outra ordem e o TIMEOUT da primeira saiu calado. 30/09: o WIN de 28/09 do
cliente 11e0b3d5 voltou a PENDENTE porque o restore carregou o last_trade velho.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest.mock
from unittest.mock import AsyncMock, patch

from backend import main, robot_runtime_main
from backend.auto_trader import AutoTrader, utc_now
from backend.robot_persistence import RestoreTrades, SQLiteRobotPersistence

USER = "8475d69b-0000-4000-8000-000000000001"


def _estado_esperando(segundos: int) -> SimpleNamespace:
    return SimpleNamespace(
        status="WAITING_RESULT",
        operation_in_progress=True,
        result_waiting=True,
        last_trade={"order_id": "14307723702", "result": "PENDING_RESULT"},
        last_entry_at=utc_now() - timedelta(seconds=segundos),
        current_cycle_started_at=None,
        cycle_minutes=1,
    )


class EsperaComMonitorVivoTests(unittest.TestCase):
    def test_nao_recicla_com_o_monitor_da_ordem_vivo(self) -> None:
        with patch.object(main.trade_result_monitor, "is_monitoring", return_value=True):
            self.assertFalse(main.waiting_result_stale(_estado_esperando(200), USER))
            # Teto: nunca trava o robô para sempre.
            self.assertTrue(main.waiting_result_stale(_estado_esperando(700), USER))

    def test_sem_monitor_recicla_como_antes(self) -> None:
        with patch.object(main.trade_result_monitor, "is_monitoring", return_value=False):
            self.assertTrue(main.waiting_result_stale(_estado_esperando(200), USER))


class TimeoutDeOrdemAntigaTests(unittest.TestCase):
    def test_fecha_no_espelho_e_avisa(self) -> None:
        pendente = {"order_id": "14307723702", "result": "PENDING_RESULT", "active": "EURCAD-OTC", "amount": 15.0}
        with (
            patch.object(main.robot_persistence, "load_trades", return_value=[pendente]),
            patch.object(main.robot_persistence, "save_trade") as gravar,
            self.assertLogs("backend-gateway", level="ERROR") as logs,
        ):
            self.assertTrue(main.marcar_timeout_no_espelho(USER, "14307723702"))
        self.assertEqual(gravar.call_args.args[1]["result"], "TIMEOUT")
        self.assertIn("ORDER_RESULT_UNKNOWN", "\n".join(logs.output))

    def test_linha_ja_final_nao_e_tocada(self) -> None:
        with (
            patch.object(main.robot_persistence, "load_trades", return_value=[{"order_id": "1", "result": "WIN"}]),
            patch.object(main.robot_persistence, "save_trade") as gravar,
        ):
            self.assertFalse(main.marcar_timeout_no_espelho(USER, "1"))
        gravar.assert_not_called()


class PendenteVelhoNoRestoreTests(unittest.TestCase):
    def _payload(self, horas: float) -> dict:
        estado = AutoTrader().get(USER)
        payload = estado.to_dict()
        payload["last_trade"] = {
            "order_id": "14303959340",
            "result": "PENDING_RESULT",
            "sent_at": (utc_now() - timedelta(hours=horas)).isoformat(),
        }
        return payload

    def test_pendente_de_dias_atras_nao_volta_para_a_memoria(self) -> None:
        estado = AutoTrader().restore(USER, self._payload(50), RestoreTrades([], authoritative=True))
        self.assertIsNone(estado.last_trade)

    def test_pendente_recente_fica(self) -> None:
        estado = AutoTrader().restore(USER, self._payload(0.01), RestoreTrades([], authoritative=True))
        self.assertEqual(estado.last_trade["order_id"], "14303959340")

    def test_espelho_nao_regrava_pendente_velho(self) -> None:
        velho = {"order_id": "14303959340", "result": "PENDING_RESULT", "sent_at": (utc_now() - timedelta(hours=50)).isoformat()}
        with patch.object(main, "robot_runtime_mode", return_value="worker"):
            self.assertIsNone(main._trade_for_mirror(USER, velho))


class VarreduraPeriodicaTests(unittest.TestCase):
    def _rodar(self, trade: dict, *, monitorando: bool = False, resultado=("PENDING_RESULT", None)):
        with tempfile.TemporaryDirectory() as pasta:
            persist = SQLiteRobotPersistence(str(Path(pasta) / "t.db"))
            persist.save_trade(USER, trade)
            fechar = unittest.mock.MagicMock()
            buscar = AsyncMock(return_value=(200, {}))
            gateway = SimpleNamespace(
                robot_persistence=persist,
                fetch_trade_result=buscar,
                normalize_trade_result=lambda _p: resultado,
                finish_monitored_trade=AsyncMock(),
                trade_result_monitor=SimpleNamespace(is_monitoring=lambda *_: monitorando, start=lambda *_: False),
                marcar_timeout_no_espelho=fechar,
            )
            asyncio.run(robot_runtime_main._recuperar_ordens_orfas(gateway, horas=24, periodica=True))
            return buscar, fechar

    def _ordem(self, minutos: int) -> dict:
        enviada = utc_now() - timedelta(minutes=minutos)
        return {
            "order_id": "14307723702", "result": "PENDING_RESULT", "active": "EURCAD-OTC", "amount": 15.0,
            "sent_at": enviada.isoformat(), "expected_expire_at": (enviada + timedelta(minutes=1)).isoformat(),
        }

    def test_ordem_com_monitor_vivo_e_ignorada(self) -> None:
        buscar, fechar = self._rodar(self._ordem(30), monitorando=True)
        buscar.assert_not_awaited()
        fechar.assert_not_called()

    def test_sem_resultado_depois_de_1h_fecha_como_timeout(self) -> None:
        buscar, fechar = self._rodar(self._ordem(90))
        buscar.assert_awaited()
        fechar.assert_called_once_with(USER, "14307723702")

    def test_antes_de_1h_so_tenta_de_novo_depois(self) -> None:
        _, fechar = self._rodar(self._ordem(30))
        fechar.assert_not_called()


if __name__ == "__main__":
    unittest.main()

class VarreduraSemSessaoTests(unittest.TestCase):
    """30/09: sem sessão, o ciclo pedia 10-32 ativos e todos voltavam 409."""

    def test_para_no_primeiro_409_de_sessao(self) -> None:
        erro = (409, main.build_error(main.SESSION_DISCONNECTED))
        analisar = AsyncMock(return_value=erro)
        ativos = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "AUDUSD-OTC"]
        with (
            patch.object(main, "analyze_active_signal", analisar),
            patch.object(main, "select_analysis_assets_for_cycle", return_value=ativos),
        ):
            asyncio.run(main.scan_local_signals(USER, timeframe="M1", market_mode="OTC"))
        self.assertEqual(analisar.await_count, 1)


class TempoDeCompraEsgotadoTests(unittest.TestCase):
    """30/09: "Time for purchasing options is over" na virada de M5 perdia a vela."""

    def _enviar(self, respostas):
        envio = AsyncMock(side_effect=respostas)
        with patch.object(main, "submit_bullex_order", envio), patch.object(main.asyncio, "sleep", AsyncMock()):
            status, payload = asyncio.run(
                main.submit_order_with_digital_fallback(
                    USER, "/bullex/buy-real", {"amount": 20, "active": "USDCHF-OTC"}, symbol="USDCHF-OTC", duration_minutes=5
                )
            )
        return envio, payload

    def test_tenta_de_novo_uma_vez(self) -> None:
        recusa = (409, {"ok": False, "error": "falha ao criar ordem real: Time for purchasing options is over, please try again later."})
        envio, payload = self._enviar([recusa, (200, {"ok": True, "data": {"order_id": 1}})])
        self.assertTrue(payload["ok"])
        self.assertEqual(envio.await_count, 2)

    def test_nao_insiste_mais_de_uma_vez(self) -> None:
        recusa = (409, {"ok": False, "error": "Time for purchasing options is over"})
        envio, payload = self._enviar([recusa, recusa])
        self.assertFalse(payload["ok"])
        self.assertEqual(envio.await_count, 2)

    def test_outra_recusa_nao_repete(self) -> None:
        envio, _ = self._enviar([(409, {"ok": False, "error": "Insufficient funds for this transaction."})])
        self.assertEqual(envio.await_count, 1)


class PainelFechadoTests(unittest.TestCase):
    """30/09 18:17: o painel fechou durante o envio e saiu um traceback no alerta."""

    def test_desconexao_do_cliente_nao_e_erro(self) -> None:
        from starlette.websockets import WebSocketDisconnect

        from backend.robot_state_ws import cliente_desconectou

        class ClientDisconnected(Exception):
            pass

        try:
            try:
                raise ClientDisconnected()
            except ClientDisconnected as causa:
                raise WebSocketDisconnect(code=1006) from causa
        except WebSocketDisconnect as exc:
            self.assertTrue(cliente_desconectou(exc))
        self.assertTrue(cliente_desconectou(RuntimeError('Cannot call "send" once a close message has been sent.')))
        self.assertFalse(cliente_desconectou(ValueError("json invalido")))

