"""A auditoria do placar enxerga os defeitos de 28/09 e o alerta não faz barulho à toa."""

from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import alerta_auditoria_placar  # noqa: E402
import auditoria_placar  # noqa: E402

AGORA = datetime.datetime(2026, 9, 28, 20, 0, tzinfo=datetime.timezone.utc)
U1 = "11e0b3d5-711f-490b-99cd-68f09a30455e"
U2 = "7ac17d6d-772d-4039-b6f9-5bc84d372b27"


def _t(minutos_atras: float) -> str:
    return (AGORA - datetime.timedelta(minutes=minutos_atras)).isoformat()


def _tabelas(estados, historico, espelhos):
    def listar(_base, _chave, tabela, _params):
        return {"robot_states": estados, "robot_trade_history": historico, "robot_trades": espelhos}[tabela]

    return listar


class AuditoriaTests(unittest.TestCase):
    def _dia(self):
        estados = [{"user_id": U1, "state_json": {"wins": 2, "losses": 3}}]
        historico = [
            {"user_id": U1, "order_id": "1", "result": "WIN", "cycle_result": "WIN", "finished_at": _t(200)},
            {"user_id": U1, "order_id": "2", "result": "LOSS", "cycle_result": "LOSS", "finished_at": _t(180)},
            {"user_id": U1, "order_id": "4", "result": "LOSS", "cycle_result": "LOSS", "finished_at": _t(160)},
            {"user_id": U1, "order_id": "5", "result": "WIN", "cycle_result": "WIN", "finished_at": _t(100)},
            {"user_id": U1, "order_id": "6", "result": "LOSS", "cycle_result": "LOSS", "finished_at": _t(20)},
        ]
        espelhos = [
            # WIN regravado como pendente por cima.
            {"user_id": U1, "order_id": "1", "result": "PENDING_RESULT", "executed_at": _t(205),
             "created_at": _t(205), "trade_json": {}},
            # Loss apagado no Shift+O que voltou 60 min depois de fechar.
            {"user_id": U1, "order_id": "3", "result": "LOSS", "executed_at": _t(175),
             "created_at": _t(110), "trade_json": {"finished_at": _t(170)}},
            # Perda oculta do LIVE: fora do Histórico de propósito.
            {"user_id": U2, "order_id": "7", "result": "LOSS", "executed_at": _t(50),
             "created_at": _t(50), "trade_json": {"finished_at": _t(49), "live_mode_active": True}},
            # Ordem em voo: nem órfã nem problema.
            {"user_id": U1, "order_id": "8", "result": "PENDING_RESULT", "executed_at": _t(1),
             "created_at": _t(1), "trade_json": {}},
            # Órfã de verdade.
            {"user_id": U1, "order_id": "9", "result": "PENDING_RESULT", "executed_at": _t(150),
             "created_at": _t(150), "trade_json": {}},
        ]
        return estados, historico, espelhos

    def test_classifica_os_defeitos_de_28_09(self) -> None:
        with patch.object(auditoria_placar, "_listar", _tabelas(*self._dia())):
            achados = auditoria_placar.auditar("b", "k", agora=AGORA)
        self.assertEqual(achados["divergentes"], [])
        self.assertEqual([i["order_id"] for i in achados["pendentes_com_final"]], ["1"])
        self.assertEqual([i["order_id"] for i in achados["orfas"]], ["9"])
        self.assertEqual([(i["order_id"], i["ressuscitada"]) for i in achados["sem_historico"]], [("3", True)])

    def test_divergencia_logo_apos_resultado_espera(self) -> None:
        estados, historico, espelhos = self._dia()
        historico.append(
            {"user_id": U1, "order_id": "10", "result": "WIN", "cycle_result": "WIN", "finished_at": _t(1)}
        )
        with patch.object(auditoria_placar, "_listar", _tabelas(estados, historico, [])):
            achados = auditoria_placar.auditar("b", "k", agora=AGORA)
        self.assertEqual(achados["divergentes"], [])

    def test_divergencia_de_placar(self) -> None:
        estados, historico, _ = self._dia()
        estados[0]["state_json"] = {"wins": 1, "losses": 4}
        with patch.object(auditoria_placar, "_listar", _tabelas(estados, historico, [])):
            achados = auditoria_placar.auditar("b", "k", agora=AGORA)
        self.assertEqual(
            [(d["banco"], d["historico"]) for d in achados["divergentes"]], [((1, 4), (2, 3))]
        )

    def test_placar_de_varios_dias_desde_o_reiniciar(self) -> None:
        """Sem virada à meia-noite: ontem conta; antes do Reiniciar, não."""
        reset = (AGORA - datetime.timedelta(days=2)).isoformat()
        estados = [{"user_id": U1, "state_json": {"wins": 2, "losses": 1, "stop_reset_at": reset}}]
        historico = [
            {"user_id": U1, "order_id": "20", "result": "WIN", "cycle_result": "WIN", "finished_at": _t(60 * 72)},
            {"user_id": U1, "order_id": "21", "result": "WIN", "cycle_result": "WIN", "finished_at": _t(60 * 30)},
            {"user_id": U1, "order_id": "22", "result": "LOSS", "cycle_result": "LOSS", "finished_at": _t(60 * 26)},
            {"user_id": U1, "order_id": "23", "result": "WIN", "cycle_result": "WIN", "finished_at": _t(30)},
        ]
        with patch.object(auditoria_placar, "_listar", _tabelas(estados, historico, [])):
            achados = auditoria_placar.auditar("b", "k", agora=AGORA)
        self.assertEqual(achados["divergentes"], [])
        # Controle: o placar "só de hoje" (regra antiga) agora é divergência.
        estados[0]["state_json"] = {"wins": 1, "losses": 0, "stop_reset_at": reset}
        with patch.object(auditoria_placar, "_listar", _tabelas(estados, historico, [])):
            achados = auditoria_placar.auditar("b", "k", agora=AGORA)
        self.assertEqual([d["historico"] for d in achados["divergentes"]], [(2, 1)])

    def test_contador_em_sombra_aparece_sem_disparar_alerta(self) -> None:
        estados, historico, _ = self._dia()
        estados[0]["state_json"]["score_day"] = "continuo"
        # Cliente parado desde antes da regra: placar velho no banco, contador 0x0.
        estados.append({"user_id": U2, "state_json": {"wins": 5, "losses": 5}})
        contador = [
            {"user_id": U1, "wins": 2, "losses": 2, "profit": 0, "atualizado_em": _t(30)},
            {"user_id": U2, "wins": 0, "losses": 0, "profit": 0, "atualizado_em": _t(600)},
        ]

        def listar(_base, _chave, tabela, _params):
            return {"robot_states": estados, "robot_trade_history": historico,
                    "robot_trades": [], "placar": contador}[tabela]

        with patch.object(auditoria_placar, "_listar", listar):
            achados = auditoria_placar.auditar("b", "k", agora=AGORA)
        self.assertTrue(achados["contador_ativo"])
        self.assertEqual(
            [(d["banco"], d["contador"]) for d in achados["contador_divergente"]], [((2, 3), (2, 2))]
        )
        self.assertEqual(auditoria_placar.total(achados), 0)  # sombra: sem e-mail
        self.assertIn("contador x placar (sombra)", "\n".join(
            auditoria_placar.descrever(achados, detalhe=True, horas_orfa=1)))

    def test_sem_tabela_do_contador_a_checagem_some(self) -> None:
        with patch.object(auditoria_placar, "_listar", _tabelas(*self._dia())):
            achados = auditoria_placar.auditar("b", "k", agora=AGORA)
        self.assertFalse(achados["contador_ativo"])

    def test_corrigir_so_mexe_no_espelho(self) -> None:
        with patch.object(auditoria_placar, "_listar", _tabelas(*self._dia())):
            achados = auditoria_placar.auditar("b", "k", agora=AGORA)
        with patch.object(auditoria_placar, "_requisitar") as req:
            feitos = auditoria_placar.corrigir("b", "k", achados)
        chamadas = [(c.args[2], c.args[3].split("?")[0]) for c in req.call_args_list]
        self.assertEqual(chamadas, [("PATCH", "robot_trades"), ("DELETE", "robot_trades")])
        self.assertEqual(req.call_args_list[0].args[4]["result"], "WIN")
        self.assertEqual(len(feitos), 2)


class AlertaTests(unittest.TestCase):
    def _rodar(self, estado: Path, achados: dict):
        argv = ["alerta", "--para", "dono@exemplo.com", "--estado", str(estado)]
        with (
            patch.object(sys, "argv", argv),
            patch.dict("os.environ", {"SUPABASE_URL": "b", "SUPABASE_SERVICE_ROLE_KEY": "k"}),
            patch.object(auditoria_placar, "auditar", return_value=achados),
            patch.object(alerta_auditoria_placar, "_enviar") as enviar,
            patch("builtins.print"),
        ):
            codigo = alerta_auditoria_placar.main()
        return codigo, enviar

    def _achados(self, *chaves: str) -> dict:
        return {
            "corte": AGORA.isoformat(),
            "clientes": 1,
            "divergentes": [],
            "pendentes_com_final": [
                {"chave": c, "user_id": U1, "order_id": c, "final": {"result": "WIN"}} for c in chaves
            ],
            "orfas": [],
            "sem_historico": [],
        }

    def test_so_avisa_o_que_persiste_e_uma_vez(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            estado = Path(pasta) / "estado.json"
            _, enviar = self._rodar(estado, self._achados("a"))
            enviar.assert_not_called()  # 1ª rodada: pode ser passageiro
            codigo, enviar = self._rodar(estado, self._achados("a"))
            self.assertEqual(codigo, 1)
            enviar.assert_called_once()
            _, enviar = self._rodar(estado, self._achados("a"))
            enviar.assert_not_called()  # já avisado
            self._rodar(estado, self._achados())  # sumiu
            self._rodar(estado, self._achados("a"))  # voltou (1ª vez)
            _, enviar = self._rodar(estado, self._achados("a"))
            enviar.assert_called_once()
            self.assertEqual(json.loads(estado.read_text())["avisados"], ["a"])

    def test_falha_no_email_tenta_de_novo(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            estado = Path(pasta) / "estado.json"
            self._rodar(estado, self._achados("a"))
            argv = ["alerta", "--para", "d@e.com", "--estado", str(estado)]
            with (
                patch.object(sys, "argv", argv),
                patch.dict("os.environ", {"SUPABASE_URL": "b", "SUPABASE_SERVICE_ROLE_KEY": "k"}),
                patch.object(auditoria_placar, "auditar", return_value=self._achados("a")),
                patch.object(alerta_auditoria_placar, "_enviar", side_effect=OSError("smtp")),
                patch("builtins.print"),
            ):
                alerta_auditoria_placar.main()
            self.assertEqual(json.loads(estado.read_text())["avisados"], [])


if __name__ == "__main__":
    unittest.main()
