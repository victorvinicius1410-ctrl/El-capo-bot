"""Detecção e backoff global do rate limit de login da Bullex.

Quando a corretora responde ``requests_limit_exceeded`` (TTL típico 3600s),
novos logins pelo mesmo IP são bloqueados. Sem um gate global, o
auto-reconnect de dezenas de usuários martela a API e prolonga o bloqueio.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

logger = logging.getLogger("bullex-service")

BULLEX_REQUESTS_LIMIT_EXCEEDED = "BULLEX_REQUESTS_LIMIT_EXCEEDED"
DEFAULT_REQUESTS_LIMIT_TTL_SECONDS = 3600

_lock = threading.Lock()
_blocked_until_monotonic: float = 0.0
_last_ttl_seconds: int = DEFAULT_REQUESTS_LIMIT_TTL_SECONDS

_TTL_RE = re.compile(r'"ttl"\s*:\s*(\d+)', re.IGNORECASE)
_CODE_RE = re.compile(r'"code"\s*:\s*"requests_limit_exceeded"', re.IGNORECASE)


def _extract_json_blob(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # Motivo costuma vir como: falha ao conectar: {"code":"...","ttl":3600}
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def is_requests_limit_error(detail: Any) -> bool:
    """
    Indica se a falha de login é rate limit da corretora.

    Args:
        detail: Mensagem/objeto de erro do ``connect`` / ``ServiceError``.

    Returns:
        True quando o código ``requests_limit_exceeded`` está presente.
    """
    if detail is None:
        return False
    if isinstance(detail, dict):
        code = str(detail.get("code") or detail.get("error") or "").strip().lower()
        if code in {"requests_limit_exceeded", BULLEX_REQUESTS_LIMIT_EXCEEDED.lower()}:
            return True
        nested = detail.get("message") or detail.get("detail")
        if nested and nested is not detail:
            return is_requests_limit_error(nested)
    text = str(detail)
    if BULLEX_REQUESTS_LIMIT_EXCEEDED in text:
        return True
    if _CODE_RE.search(text):
        return True
    blob = _extract_json_blob(text)
    if blob is not None:
        return str(blob.get("code") or "").strip().lower() == "requests_limit_exceeded"
    return "requests_limit_exceeded" in text.lower()


def extract_requests_limit_ttl(detail: Any, default: int = DEFAULT_REQUESTS_LIMIT_TTL_SECONDS) -> int:
    """
    Extrai o TTL (segundos) sugerido pela corretora.

    Args:
        detail: Mensagem/objeto de erro.
        default: Fallback quando o TTL não vem no payload.

    Returns:
        TTL em segundos (>= 60).
    """
    ttl: int | None = None
    if isinstance(detail, dict):
        raw = detail.get("ttl") or detail.get("retry_after_seconds")
        try:
            if raw is not None:
                ttl = int(raw)
        except (TypeError, ValueError):
            ttl = None
        if ttl is None:
            nested = detail.get("message") or detail.get("detail")
            if nested and nested is not detail:
                return extract_requests_limit_ttl(nested, default=default)
    if ttl is None:
        text = str(detail or "")
        blob = _extract_json_blob(text)
        if blob is not None:
            try:
                ttl = int(blob.get("ttl"))
            except (TypeError, ValueError):
                ttl = None
        if ttl is None:
            match = _TTL_RE.search(text)
            if match:
                ttl = int(match.group(1))
    if ttl is None or ttl < 60:
        return max(60, int(default))
    return min(int(ttl), 6 * 3600)


def note_requests_limit(detail: Any = None) -> int:
    """
    Ativa o gate global de login após um ``requests_limit_exceeded``.

    Args:
        detail: Erro original (para ler o TTL).

    Returns:
        Segundos restantes de bloqueio global.
    """
    global _blocked_until_monotonic, _last_ttl_seconds
    ttl = extract_requests_limit_ttl(detail)
    with _lock:
        _last_ttl_seconds = ttl
        _blocked_until_monotonic = max(_blocked_until_monotonic, time.monotonic() + ttl)
        remaining = max(0, int(_blocked_until_monotonic - time.monotonic()))
    logger.warning(
        "[BULLEX_LOGIN_RATE_LIMIT] ttl=%s remaining=%s",
        ttl,
        remaining,
    )
    return remaining


def login_block_remaining_seconds() -> int:
    """Segundos restantes do bloqueio global de login (0 se liberado)."""
    with _lock:
        return max(0, int(_blocked_until_monotonic - time.monotonic()))


def clear_login_block() -> None:
    """Limpa o gate global (uso em testes / override manual)."""
    global _blocked_until_monotonic
    with _lock:
        _blocked_until_monotonic = 0.0


def rate_limit_error_payload(remaining: int | None = None) -> dict[str, Any]:
    """
    Payload padronizado para o gateway/frontend.

    Args:
        remaining: Segundos restantes; se omitido, usa o gate atual.

    Returns:
        Dict com ``retry_after_seconds`` e código estável.
    """
    seconds = login_block_remaining_seconds() if remaining is None else max(0, int(remaining))
    return {
        "retry_after_seconds": seconds,
        "code": BULLEX_REQUESTS_LIMIT_EXCEEDED,
    }
