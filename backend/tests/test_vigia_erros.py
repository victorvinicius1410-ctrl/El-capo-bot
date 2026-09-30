"""O vigia pega todo tipo de erro, agrupa as repetições e não repete e-mail."""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import vigia_erros as V  # noqa: E402

TRACEBACK = """2026-09-29 16:45:01,531 WARNING [RSI_ENTRY_CONFIRM_ERRO] user_id=e3d5e7d0-f6c7-4f4f-b187-4139620281a7 symbol=EURGBP
Traceback (most recent call last):
  File "/usr/local/lib/python3.11/asyncio/tasks.py", line 500, in wait_for
    return fut.result()
asyncio.exceptions.CancelledError

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/backend/main.py", line 10239, in confirm_rsi_before_entry
    status_code, payload = await asyncio.wait_for(
TimeoutError
2026-09-29 16:45:01,537 INFO [ENTRY_BLOCKED] user_id=x reason=RSI"""


class AnaliseTests(unittest.TestCase):
    def _eventos(self, texto: str, origem: str = "robot-runtime") -> list[dict]:
        eventos: dict = {}
        V.analisar_linhas(origem, texto.splitlines(), eventos)
        return list(eventos.values())

    def test_traceback_vira_um_erro_com_onde_e_marca(self) -> None:
        (ev,) = self._eventos(TRACEBACK)
        self.assertEqual(ev["tipo"], "exceção")
        self.assertIn("TimeoutError em main.py:confirm_rsi_before_entry", ev["assinatura"])
        self.assertIn("RSI_ENTRY_CONFIRM_ERRO", ev["assinatura"])

    def test_repeticoes_com_ids_diferentes_agrupam(self) -> None:
        texto = "\n".join(
            f"2026-09-29 16:4{i}:0{i},1 ERROR [ORDER_SEND_FAILED] user_id=0000000{i}-f6c7-4f4f-b187-4139620281a7 order=1430{i}"
            for i in range(5)
        )
        (ev,) = self._eventos(texto)
        self.assertEqual(ev["vezes"], 5)

    def test_marca_failed_em_warning_conta(self) -> None:
        (ev,) = self._eventos("[BULLEX_SSID_RECONNECT_FAILED] user_id=abc detail=X", "backend-gateway")
        self.assertEqual(ev["tipo"], "falha registrada")

    def test_http_5xx(self) -> None:
        (ev,) = self._eventos('INFO:     172.18.0.1:5 - "GET /robot/state?x=1 HTTP/1.1" 502 Bad Gateway', "backend-gateway")
        self.assertIn("HTTP 502 GET /robot/state", ev["assinatura"])

    def test_linha_normal_nao_e_erro(self) -> None:
        texto = "2026-09-29 INFO [ENTRY_BLOCKED] reason=RSI\n[SCORE_RECONCILED_ON_GATEWAY] previous=1x0"
        self.assertEqual(self._eventos(texto), [])


class SaudeTests(unittest.TestCase):
    def test_oom_do_kernel_vira_erro(self) -> None:
        from unittest.mock import patch, MagicMock

        saida = (
            "2026-09-30T08:30:57+00:00 srv kernel: Out of memory: Killed process 2001810 (uvicorn) "
            "total-vm:10780784kB, anon-rss:8901192kB\n-- cursor: s=abc;i=1\n"
        )
        estado: dict = {"cursores": {}}
        eventos: dict = {}
        with patch.object(V.subprocess, "run", return_value=MagicMock(stdout=saida)):
            V.checar_oom(estado, eventos)
        (ev,) = eventos.values()
        self.assertIn("FALTA DE MEMÓRIA (uvicorn)", ev["assinatura"])
        self.assertEqual(estado["cursores"]["kernel"], "s=abc;i=1")

    def test_memoria_baixa_avisa_uma_vez_e_avisa_a_volta(self) -> None:
        from unittest.mock import patch, MagicMock

        estado: dict = {}
        with (
            patch.object(V, "_meminfo", return_value={"MemTotal": 100, "MemAvailable": 10}),
            patch.object(V.subprocess, "run", return_value=MagicMock(stdout="robot-runtime 9GiB")),
        ):
            eventos: dict = {}
            V.checar_memoria(estado, eventos)
            self.assertEqual(len(eventos), 1)
            eventos2: dict = {}
            V.checar_memoria(estado, eventos2)
            self.assertEqual(eventos2, {})  # incidente já aberto
        with patch.object(V, "_meminfo", return_value={"MemTotal": 100, "MemAvailable": 60}):
            avisos = V.checar_memoria(estado, {})
        self.assertEqual(len(avisos), 1)


class AvisoTests(unittest.TestCase):
    def test_avisa_uma_vez_e_de_novo_depois_de_24h(self) -> None:
        estado: dict = {}
        eventos = {"k": {"chave": "k", "assinatura": "a", "origem": "o", "tipo": "t", "amostra": "x", "vezes": 1}}
        t0 = datetime.datetime(2026, 9, 29, 12, tzinfo=datetime.timezone.utc)
        self.assertEqual(len(V.registrar(estado, dict(eventos), t0)), 1)
        estado["assinaturas"]["k"]["avisado"] = t0.isoformat()
        self.assertEqual(V.registrar(estado, dict(eventos), t0 + datetime.timedelta(hours=1)), [])
        self.assertEqual(len(V.registrar(estado, dict(eventos), t0 + datetime.timedelta(hours=25))), 1)

    def test_resumo_conta_as_ultimas_24h(self) -> None:
        estado: dict = {}
        t0 = datetime.datetime(2026, 9, 29, 11, tzinfo=datetime.timezone.utc)
        for h in range(3):
            V.registrar(
                estado,
                {"k": {"chave": "k", "assinatura": "a", "origem": "o", "tipo": "t", "amostra": "x", "vezes": 2}},
                t0 - datetime.timedelta(hours=h),
            )
        assunto, corpo = V.email_resumo(estado, t0)
        self.assertIn("6 erro(s) em 1 tipo(s)", assunto)
        self.assertIn("6x", corpo)


if __name__ == "__main__":
    unittest.main()
