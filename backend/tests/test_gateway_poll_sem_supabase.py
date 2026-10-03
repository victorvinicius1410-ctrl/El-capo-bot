"""O poll do painel não pode ir ao Supabase a cada pedido.

Medição de 02/10/2026 (py-spy no backend-gateway + tempo por rota no nginx):
`/bullex/account` e `/bullex/status`, pedidos a cada 25 s por painel aberto,
faziam de 2 a 4 chamadas HTTPS síncronas ao Supabase DENTRO do event loop —
"tem senha salva?", "qual o email?" e um upsert da mesma linha de
`bullex_connections`. Com o loop parado, o clique em Iniciar Operação ou
Reiniciar placar esperava atrás (rotas diferentes terminando juntas após 1,5 s).

Estes testes falham se o poll voltar a fazer I/O a cada chamada, e garantem que
o que é lembrado some na hora em que o próprio gateway muda o dado.
"""

import unittest
from unittest.mock import Mock, patch

import httpx

from backend import main
from backend.user_store import SupabaseUserStore

USER = "11e0b3d5-711f-490b-99cd-68f09a30455e"


class CountingStore:
    """Store falso que conta idas ao 'Supabase'."""

    def __init__(self, email: str | None = "cliente@example.com") -> None:
        self.email = email
        self.reads = 0
        self.writes: list[dict] = []
        self.new_connections: list[dict] = []
        self.disconnects = 0

    def get_user(self, user_id):
        self.reads += 1
        return Mock(bullex_email=self.email)

    def update_connection(self, user_id, payload):
        self.writes.append(dict(payload))

    def save_connection(self, user_id, payload):
        self.new_connections.append(dict(payload))

    def disconnect(self, user_id):
        self.disconnects += 1


def conta(balance: float = 100.0) -> dict:
    return {
        "ok": True,
        "data": {
            "connected": True,
            "email": "cliente@example.com",
            "balance": balance,
            "currency": "BRL",
            "mode": "REAL",
        },
    }


class PollSemSupabaseBase(unittest.TestCase):
    def setUp(self) -> None:
        main.forget_bullex_store_memo(USER)
        self.store = CountingStore()
        self.credentials = Mock()
        self.credentials.has_saved.return_value = True
        patches = [
            patch.object(main, "user_store", self.store),
            patch.object(main, "bullex_credentials_service", self.credentials),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(main.forget_bullex_store_memo, USER)


class CredentialsMetaTests(PollSemSupabaseBase):
    def test_trinta_polls_fazem_uma_leitura_so(self) -> None:
        for _ in range(30):
            payload = main.attach_credentials_meta({"ok": True, "data": {"connected": True}}, USER)
        self.assertEqual(self.credentials.has_saved.call_count, 1)
        self.assertEqual(self.store.reads, 1)
        self.assertIs(payload["data"]["credentials_saved"], True)
        self.assertEqual(payload["data"]["email"], "cliente@example.com")

    def test_email_que_ja_veio_da_corretora_nao_consulta_o_banco(self) -> None:
        payload = main.attach_credentials_meta(conta(), USER)
        self.assertEqual(self.store.reads, 0)
        self.assertEqual(payload["data"]["email"], "cliente@example.com")

    def test_passado_o_prazo_pergunta_de_novo(self) -> None:
        with patch.object(main, "monotonic", return_value=1_000.0):
            main.credentials_saved_flag(USER)
        with patch.object(
            main,
            "monotonic",
            return_value=1_000.0 + main.BULLEX_CREDENTIALS_META_TTL_SECONDS + 1,
        ):
            main.credentials_saved_flag(USER)
        self.assertEqual(self.credentials.has_saved.call_count, 2)

    def test_salvar_credencial_vale_no_poll_seguinte(self) -> None:
        self.credentials.has_saved.return_value = False
        self.assertIs(main.credentials_saved_flag(USER), False)
        self.credentials.has_saved.return_value = True
        main.persist_bullex_credentials(USER, "cliente@example.com", "segredo")
        self.assertIs(main.credentials_saved_flag(USER), True)

    def test_falha_do_supabase_nao_e_lembrada(self) -> None:
        self.credentials.has_saved.side_effect = RuntimeError("supabase fora")
        with self.assertLogs("backend-gateway", level="WARNING"):
            self.assertIs(main.credentials_saved_flag(USER), False)
        self.credentials.has_saved.side_effect = None
        self.credentials.has_saved.return_value = True
        self.assertIs(main.credentials_saved_flag(USER), True)


class ConnectionSyncTests(PollSemSupabaseBase):
    def test_poll_repetido_grava_uma_vez(self) -> None:
        for _ in range(30):
            main.sync_user_store_from_payload(USER, conta())
        self.assertEqual(len(self.store.writes), 1)

    def test_saldo_novo_e_gravado_na_hora(self) -> None:
        main.sync_user_store_from_payload(USER, conta(100.0))
        main.sync_user_store_from_payload(USER, conta(100.0))
        main.sync_user_store_from_payload(USER, conta(108.5))
        self.assertEqual([item["last_balance"] for item in self.store.writes], [100.0, 108.5])

    def test_regrava_na_batida_do_prazo(self) -> None:
        # `last_connected_at` precisa seguir andando: é a prova de vida da conexão.
        with patch.object(main, "monotonic", return_value=5_000.0):
            main.sync_user_store_from_payload(USER, conta())
        with patch.object(
            main,
            "monotonic",
            return_value=5_000.0 + main.BULLEX_CONNECTION_SYNC_TTL_SECONDS + 1,
        ):
            main.sync_user_store_from_payload(USER, conta())
        self.assertEqual(len(self.store.writes), 2)

    def test_desconexao_esquece_e_a_volta_e_gravada(self) -> None:
        main.sync_user_store_from_payload(USER, conta())
        main.sync_user_store_from_payload(USER, {"ok": False, "error": main.SESSION_DISCONNECTED})
        self.assertEqual(self.store.disconnects, 1)
        main.sync_user_store_from_payload(USER, conta())
        self.assertEqual(len(self.store.writes), 2)

    def test_conexao_nova_sempre_grava(self) -> None:
        main.sync_user_store_from_payload(USER, conta())
        main.sync_user_store_from_payload(USER, conta(), is_new_connection=True)
        main.sync_user_store_from_payload(USER, conta(), is_new_connection=True)
        self.assertEqual(len(self.store.new_connections), 2)

    def test_falha_na_gravacao_nao_e_lembrada(self) -> None:
        self.store.update_connection = Mock(side_effect=[RuntimeError("supabase fora"), None])
        with self.assertLogs("backend-gateway", level="WARNING"):
            main.sync_user_store_from_payload(USER, conta())
        main.sync_user_store_from_payload(USER, conta())
        self.assertEqual(self.store.update_connection.call_count, 2)


class SharedHttpClientTests(unittest.TestCase):
    def test_reaproveita_a_conexao_entre_chamadas(self) -> None:
        store = SupabaseUserStore("https://example.supabase.co", "service-key")
        request = httpx.Request("GET", "https://example.supabase.co/rest/v1/users")
        client = Mock()
        client.is_closed = False
        client.request.return_value = httpx.Response(200, request=request, json=[])
        with patch("backend.user_store.httpx.Client", return_value=client) as factory:
            for _ in range(5):
                store._request("GET", "/users?select=id")
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(client.request.call_count, 5)


if __name__ == "__main__":
    unittest.main()
