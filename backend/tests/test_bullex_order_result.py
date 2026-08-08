import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bullex_service import main


def build_session(closed_orders: dict, binary_orders: dict | None = None) -> SimpleNamespace:
    client = SimpleNamespace(
        api=SimpleNamespace(socket_option_closed=closed_orders, order_binary=binary_orders or {}),
        check_connect=lambda: True,
    )
    return SimpleNamespace(client=client, requires_2fa=False)


class BullexOrderResultTests(unittest.TestCase):
    def test_returns_pending_without_blocking(self) -> None:
        session = build_session({})

        with patch.object(
            main.session_manager,
            "run",
            side_effect=lambda _user_id, operation: operation(session),
        ):
            payload = main.order_result("123", "user-result")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["result"], "PENDING_RESULT")
        self.assertIsNone(payload["data"]["profit"])

    def test_returns_closed_win_and_real_profit(self) -> None:
        session = build_session(
            {
                123: {
                    "msg": {
                        "win": "win",
                        "sum": 2,
                        "win_amount": 3.76,
                    }
                }
            }
        )

        with patch.object(
            main.session_manager,
            "run",
            side_effect=lambda _user_id, operation: operation(session),
        ):
            payload = main.order_result("123", "user-result")

        self.assertEqual(payload["data"]["result"], "win")
        self.assertAlmostEqual(payload["data"]["profit"], 1.76)

    def test_returns_closed_result_from_binary_order_cache(self) -> None:
        session = build_session(
            {},
            {
                123: {
                    "win": "loose",
                    "sum": 2,
                    "win_amount": 0,
                }
            },
        )

        with patch.object(
            main.session_manager,
            "run",
            side_effect=lambda _user_id, operation: operation(session),
        ):
            payload = main.order_result("123", "user-result")

        self.assertEqual(payload["data"]["result"], "loose")
        self.assertAlmostEqual(payload["data"]["profit"], -2.0)

    def test_rejects_result_when_message_id_does_not_match_requested_order(self) -> None:
        # Cinto de segurança contra o bug real corrigido em 2026-08-07:
        # socket_option_closed/order_binary eram compartilhados entre TODAS
        # as sessões (atributo de classe). Se por qualquer motivo a chave
        # 123 apontar para uma mensagem de OUTRA ordem (id/option_id
        # diferente), não podemos confiar no WIN/LOSS dela.
        session = build_session(
            {
                123: {
                    "msg": {
                        "id": 999,
                        "win": "win",
                        "sum": 2,
                        "win_amount": 3.76,
                    }
                }
            }
        )

        with patch.object(
            main.session_manager,
            "run",
            side_effect=lambda _user_id, operation: operation(session),
        ):
            payload = main.order_result("123", "user-result")

        self.assertEqual(payload["data"]["result"], "PENDING_RESULT")
        self.assertIsNone(payload["data"]["profit"])

    def test_accepts_result_when_message_id_matches_requested_order(self) -> None:
        session = build_session(
            {
                123: {
                    "msg": {
                        "id": 123,
                        "win": "win",
                        "sum": 2,
                        "win_amount": 3.76,
                    }
                }
            }
        )

        with patch.object(
            main.session_manager,
            "run",
            side_effect=lambda _user_id, operation: operation(session),
        ):
            payload = main.order_result("123", "user-result")

        self.assertEqual(payload["data"]["result"], "win")
        self.assertAlmostEqual(payload["data"]["profit"], 1.76)

    def test_rejects_binary_result_when_option_id_does_not_match(self) -> None:
        session = build_session(
            {},
            {
                123: {
                    "option_id": 555,
                    "win": "loose",
                    "sum": 2,
                    "win_amount": 0,
                }
            },
        )

        with patch.object(
            main.session_manager,
            "run",
            side_effect=lambda _user_id, operation: operation(session),
        ):
            payload = main.order_result("123", "user-result")

        self.assertEqual(payload["data"]["result"], "PENDING_RESULT")
        self.assertIsNone(payload["data"]["profit"])
