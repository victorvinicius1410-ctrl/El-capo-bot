"""Testes do cofre de credenciais Bullex (LEI 04 / LEI 10)."""

from __future__ import annotations

import base64
import os
import unittest

from backend.services.bullex_credentials_service import BullexCredentialsService
from backend.services.encryption_service import EncryptionService
from backend.user_store import InMemoryUserStore


def _test_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


class TestBullexCredentialsService(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryUserStore()
        self.encryption = EncryptionService(_test_key())
        self.service = BullexCredentialsService(self.store, self.encryption)

    def test_save_and_load_roundtrip(self) -> None:
        self.service.save("user-1", "trader@example.com", "Secret#123")
        loaded = self.service.load("user-1")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.email, "trader@example.com")
        self.assertEqual(loaded.password, "Secret#123")
        self.assertTrue(self.service.has_saved("user-1"))

    def test_password_is_encrypted_at_rest(self) -> None:
        self.service.save("user-2", "a@b.com", "PlainPassword1")
        record = self.store.get_saved_credentials("user-2")
        self.assertIsNotNone(record)
        assert record is not None
        encrypted = record["encrypted_password"]
        self.assertIsInstance(encrypted, str)
        self.assertTrue(str(encrypted).startswith("v1."))
        self.assertNotIn("PlainPassword1", str(encrypted))

    def test_clear_removes_credentials(self) -> None:
        self.service.save("user-3", "a@b.com", "PlainPassword1")
        self.service.clear("user-3")
        self.assertFalse(self.service.has_saved("user-3"))
        self.assertIsNone(self.service.load("user-3"))

    def test_disconnect_keeps_encrypted_password(self) -> None:
        self.service.save("user-4", "a@b.com", "PlainPassword1")
        self.store.disconnect("user-4")
        self.assertTrue(self.service.has_saved("user-4"))
        loaded = self.service.load("user-4")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.password, "PlainPassword1")

    def test_empty_credentials_raise(self) -> None:
        with self.assertRaises(ValueError):
            self.service.save("user-5", "", "x")
        with self.assertRaises(ValueError):
            self.service.save("user-5", "a@b.com", "")

    def test_missing_user_returns_none(self) -> None:
        self.assertIsNone(self.service.load("nobody"))
        self.assertFalse(self.service.has_saved("nobody"))


if __name__ == "__main__":
    unittest.main()
