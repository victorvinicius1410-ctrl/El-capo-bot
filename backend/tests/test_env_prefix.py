"""Testes do mapeamento APP_ENV → prefixo PROD_/DEV_/STAGING_."""

from __future__ import annotations

import os
import unittest

from backend.env_prefix import env_prefixed, environment_var_prefix


class TestEnvironmentVarPrefix(unittest.TestCase):
    def test_production_maps_to_prod(self) -> None:
        self.assertEqual(environment_var_prefix("production"), "PROD")
        self.assertEqual(environment_var_prefix("prod"), "PROD")

    def test_development_maps_to_dev(self) -> None:
        self.assertEqual(environment_var_prefix("development"), "DEV")
        self.assertEqual(environment_var_prefix("dev"), "DEV")

    def test_staging_maps_to_staging(self) -> None:
        self.assertEqual(environment_var_prefix("staging"), "STAGING")

    def test_env_prefixed_reads_prod_key(self) -> None:
        previous = os.environ.get("PROD_ENCRYPTION_KEY")
        os.environ["APP_ENV"] = "production"
        os.environ["PROD_ENCRYPTION_KEY"] = "test-key-value"
        try:
            self.assertEqual(env_prefixed("ENCRYPTION_KEY"), "test-key-value")
        finally:
            if previous is None:
                os.environ.pop("PROD_ENCRYPTION_KEY", None)
            else:
                os.environ["PROD_ENCRYPTION_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
