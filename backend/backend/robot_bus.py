"""Barramento Redis entre gateway (API/WS) e robot-runtime.

Canais (DB 1 do mesmo Redis do Celery, prefixo ``robot:``):
- Pub/sub ``robot:cmd`` — start/stop/ensure do robô (gateway → runtime)
- Pub/sub ``robot:state`` — snapshot JSON (runtime → gateway)
- Key ``robot:snapshot:{user_id}`` — último snapshot (TTL 120s)

Ver docs/PERFORMANCE_SISTEMA.md (Fase 4) e DEPLOY_VPS.md.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Callable, Iterator
from typing import Any

logger = logging.getLogger("backend-gateway")

ROBOT_REDIS_DB = 1
SNAPSHOT_TTL_SECONDS = 120
CMD_CHANNEL = "robot:cmd"
STATE_CHANNEL = "robot:state"


def robot_runtime_mode() -> str:
    """
    Modo do processo: ``embedded`` (default), ``external`` (gateway sem workers)
    ou ``worker`` (só robot-runtime).
    """
    return (os.getenv("ROBOT_RUNTIME_MODE") or "embedded").strip().lower()


def resolve_robot_redis_url() -> str | None:
    """
    URL Redis para o barramento do robô (DB 1).

    Returns:
        URL com path ``/1``, ou None se não configurado.
    """
    raw = (
        os.getenv("PROD_REDIS_URL")
        or os.getenv("DEV_REDIS_URL")
        or os.getenv("REDIS_URL")
        or ""
    ).strip()
    if not raw:
        return None
    # Força DB 1 para não colidir com Celery (DB 0).
    if raw.rstrip("/").endswith("/0"):
        return raw.rstrip("/")[:-1] + "1"
    if raw.count("/") >= 3 and raw.rstrip("/").split("/")[-1].isdigit():
        base = raw.rsplit("/", 1)[0]
        return f"{base}/{ROBOT_REDIS_DB}"
    return f"{raw.rstrip('/')}/{ROBOT_REDIS_DB}"


class RobotBus:
    """Cliente Redis síncrono leve (comandos/snapshots)."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url or resolve_robot_redis_url()
        self._client: Any | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.redis_url)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.redis_url:
            raise RuntimeError("Redis URL do robot bus não configurada")
        import redis

        self._client = redis.Redis.from_url(self.redis_url, decode_responses=True)
        return self._client

    def publish_command(self, user_id: str, action: str, **extra: Any) -> None:
        """
        Publica comando start/stop/ensure para o runtime.

        Args:
            user_id: Alvo do comando.
            action: ``start`` | ``stop`` | ``ensure``.
            **extra: Metadados opcionais.
        """
        if not self.enabled:
            return
        payload = {"user_id": user_id, "action": action, **extra}
        try:
            self._get_client().publish(CMD_CHANNEL, json.dumps(payload, default=str))
        except Exception:
            logger.warning(
                "[ROBOT_BUS_CMD_FAILED] user_id=%s action=%s",
                user_id,
                action,
                exc_info=True,
            )

    def publish_snapshot(self, user_id: str, payload: dict[str, Any]) -> None:
        """Grava snapshot + publica no canal de estado."""
        if not self.enabled:
            return
        try:
            client = self._get_client()
            raw = json.dumps(payload, default=str)
            client.setex(f"robot:snapshot:{user_id}", SNAPSHOT_TTL_SECONDS, raw)
            client.publish(STATE_CHANNEL, json.dumps({"user_id": user_id, "payload": payload}, default=str))
        except Exception:
            logger.warning(
                "[ROBOT_BUS_SNAPSHOT_FAILED] user_id=%s",
                user_id,
                exc_info=True,
            )

    def get_snapshot(self, user_id: str) -> dict[str, Any] | None:
        """Lê último snapshot do usuário (gateway em modo external)."""
        if not self.enabled:
            return None
        try:
            raw = self._get_client().get(f"robot:snapshot:{user_id}")
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            logger.warning(
                "[ROBOT_BUS_SNAPSHOT_READ_FAILED] user_id=%s",
                user_id,
                exc_info=True,
            )
            return None

    def close(self) -> None:
        """Fecha o client Redis."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.debug("[ROBOT_BUS_CLOSE_FAILED]", exc_info=True)
            self._client = None

    def iter_state_messages(
        self,
        should_stop: Callable[[], bool] | None = None,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """
        Itera mensagens do canal ``robot:state`` (bloqueante; use em thread).

        Yields:
            Tuplas ``(user_id, payload_dict)`` parseadas do pub/sub.

        Args:
            should_stop: Callback opcional; quando True, encerra o loop.
        """
        if not self.enabled:
            return
            yield  # pragma: no cover — torna generator tipado
        client = self._get_client()
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(STATE_CHANNEL)
        logger.info("[ROBOT_BUS] subscribed channel=%s", STATE_CHANNEL)
        try:
            for message in pubsub.listen():
                if should_stop is not None and should_stop():
                    break
                if message.get("type") != "message":
                    continue
                raw = message.get("data")
                try:
                    data = json.loads(raw)
                except Exception:
                    logger.warning("[ROBOT_BUS_STATE_BAD_JSON]", exc_info=True)
                    continue
                if not isinstance(data, dict):
                    continue
                user_id = str(data.get("user_id") or "").strip()
                payload = data.get("payload")
                if not user_id or not isinstance(payload, dict):
                    continue
                yield user_id, payload
        finally:
            with contextlib.suppress(Exception):
                pubsub.close()
