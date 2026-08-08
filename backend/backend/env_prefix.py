"""Mapeamento APP_ENV → prefixo de variáveis (LEI 13)."""

from __future__ import annotations

import os


_PREFIX_BY_ENV = {
    "production": "PROD",
    "prod": "PROD",
    "staging": "STAGING",
    "stage": "STAGING",
    "development": "DEV",
    "dev": "DEV",
    "local": "DEV",
}


def environment_var_prefix(app_env: str | None = None) -> str:
    """
    Converte APP_ENV no prefixo canônico das env vars.

    Args:
        app_env: Valor de APP_ENV (ex.: ``production``). Se omitido, lê do ambiente.

    Returns:
        Prefixo sem underscore final: ``PROD``, ``STAGING`` ou ``DEV``.

    Example:
        >>> environment_var_prefix("production")
        'PROD'
    """
    raw = (app_env if app_env is not None else os.getenv("APP_ENV", "development")).strip().lower()
    return _PREFIX_BY_ENV.get(raw, raw.upper() if raw else "DEV")


def env_prefixed(name: str, default: str = "", *, app_env: str | None = None) -> str:
    """
    Lê ``{PREFIX}_{name}`` e faz fallback para ``name``.

    Args:
        name: Sufixo da variável (ex.: ``ENCRYPTION_KEY``).
        default: Valor padrão se nenhum existir.
        app_env: Override opcional de APP_ENV.

    Returns:
        Valor stripado da variável resolvida.
    """
    prefix = environment_var_prefix(app_env)
    return os.getenv(f"{prefix}_{name}", os.getenv(name, default)).strip()
