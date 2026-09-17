"""Garante que o worker Celery monta o WebhookService com e-mail."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.workers import webhook_tasks


BASE_ENV = {
    "APP_ENV": "dev",
    "SUPABASE_URL": "https://project.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "server-only-key",
    "FRONTEND_URL": "https://app.example.com",
    "DEV_ENCRYPTION_KEY": "0" * 43 + "=",
}


class WorkerEmailWiringTests(unittest.TestCase):
    """Regressão: trials.end rodava mudo porque faltava ``emails=``."""

    def setUp(self) -> None:
        """Zera o cache do EmailService entre os cenários de ambiente."""
        webhook_tasks._emails.cache_clear()
        self.addCleanup(webhook_tasks._emails.cache_clear)

    def test_service_carries_email_when_enabled(self) -> None:
        """Com e-mail ligado, o worker entrega eventos de domínio por e-mail."""
        env = {
            **BASE_ENV,
            "DEV_EMAILS_ENABLED": "true",
            "DEV_EMAIL_PROVIDER": "smtp",
            "DEV_SMTP_HOST": "smtp.example.com",
            "DEV_SMTP_USERNAME": "no-reply@example.com",
            "DEV_SMTP_PASSWORD": "secret",
            "DEV_EMAIL_FROM": "ElCapo <no-reply@example.com>",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNotNone(webhook_tasks._service().emails)

    def test_service_has_no_email_when_disabled(self) -> None:
        """Com e-mail desligado, o worker segue funcionando sem e-mail."""
        with patch.dict(os.environ, {**BASE_ENV, "DEV_EMAILS_ENABLED": "false"}, clear=True):
            self.assertIsNone(webhook_tasks._service().emails)

    def test_incomplete_email_config_does_not_break_worker(self) -> None:
        """Config de e-mail pela metade não pode derrubar trials.end."""
        env = {**BASE_ENV, "DEV_EMAILS_ENABLED": "true", "DEV_EMAIL_PROVIDER": "smtp"}
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(webhook_tasks._service().emails)


if __name__ == "__main__":
    unittest.main()
