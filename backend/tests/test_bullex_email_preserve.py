"""Garante que sync de conta não apaga bullex_email/saldo salvos (LEI 10)."""

from __future__ import annotations

import base64
import os
import unittest

from backend.main import build_connection_payload
from backend.services.bullex_credentials_service import BullexCredentialsService
from backend.services.encryption_service import EncryptionService
from backend.user_store import InMemoryUserStore


def _test_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


class BuildConnectionPayloadPreserveTests(unittest.TestCase):
    def test_null_email_does_not_enter_updates(self) -> None:
        updates = build_connection_payload(
            {
                "connected": True,
                "email": None,
                "balance": None,
                "currency": None,
                "mode": "REAL",
            }
        )
        self.assertNotIn("bullex_email", updates)
        self.assertNotIn("last_balance", updates)
        self.assertNotIn("currency", updates)
        self.assertEqual(updates.get("connected"), True)
        self.assertEqual(updates.get("account_mode"), "REAL")

    def test_empty_email_string_does_not_enter_updates(self) -> None:
        updates = build_connection_payload({"email": "  ", "connected": False})
        self.assertNotIn("bullex_email", updates)
        self.assertEqual(updates.get("connected"), False)

    def test_valid_email_and_balance_are_persisted(self) -> None:
        updates = build_connection_payload(
            {
                "email": "trader@bullex.com",
                "balance": 150.5,
                "currency": "BRL",
                "connected": True,
                "mode": "REAL",
            }
        )
        self.assertEqual(updates["bullex_email"], "trader@bullex.com")
        self.assertEqual(updates["last_balance"], 150.5)
        self.assertEqual(updates["currency"], "BRL")


class CredentialsIncompleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryUserStore()
        self.service = BullexCredentialsService(self.store, EncryptionService(_test_key()))

    def test_password_without_email_is_not_loadable(self) -> None:
        self.service.save("user-orphan", "keep@example.com", "Secret#123")
        # Simula o wipe histórico: senha fica, email some.
        record = self.store.users["user-orphan"]
        record.bullex_email = None
        self.assertIsNone(self.service.load("user-orphan"))
        self.assertFalse(self.service.has_saved("user-orphan"))

    def test_sync_null_email_does_not_wipe_saved_email(self) -> None:
        self.service.save("user-keep", "keep@example.com", "Secret#123")
        updates = build_connection_payload({"email": None, "connected": True, "mode": "REAL"})
        self.store.update_connection("user-keep", updates)
        loaded = self.service.load("user-keep")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.email, "keep@example.com")


if __name__ == "__main__":
    unittest.main()
