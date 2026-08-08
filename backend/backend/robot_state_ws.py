"""WebSocket de estado do robô + tickets one-shot + publish com debounce.

Substitui o poll agressivo de ``GET /robot/state`` por push. Auth preferencial
via cookie de sessão na handshake; fallback ``GET /robot/ws-ticket`` (TTL 60s,
uso único) para ambientes onde o cookie ``__Host-`` não cruza no upgrade WS.

Ver docs/ROBOT_STATE_WEBSOCKET.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger("backend-gateway")

WS_TICKET_TTL_SECONDS = 60.0
PUBLISH_DEBOUNCE_SECONDS = 0.2
PUSH_LOOP_INTERVAL_SECONDS = 1.0
MAINTENANCE_INTERVAL_SECONDS = 5.0
CLIENT_PING_STALE_SECONDS = 45.0


@dataclass
class _TicketEntry:
    user_id: str
    company_id: str
    expires_at: float


@dataclass
class _RobotWsClient:
    websocket: WebSocket
    user_id: str
    last_ping_at: float
    last_digest: str | None = None


@dataclass
class RobotStateWsHub:
    """Hub in-process: conexões WS + tickets + publish debounced."""

    _tickets: dict[str, _TicketEntry] = field(default_factory=dict)
    _clients: dict[str, list[_RobotWsClient]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _pending_publish: dict[str, float] = field(default_factory=dict)
    _push_task: asyncio.Task[None] | None = None
    _maintenance_task: asyncio.Task[None] | None = None
    snapshot_builder: Callable[[str], dict[str, Any]] | None = None
    maintenance_hook: Callable[[str], Awaitable[None]] | None = None

    def issue_ticket(self, user_id: str, company_id: str, *, now: float | None = None) -> str:
        """
        Emite ticket one-shot para upgrade WebSocket.

        Args:
            user_id: Usuário autenticado (da sessão).
            company_id: Tenant (auditoria; não vai no WS payload).
            now: Relógio injetável (testes).

        Returns:
            Token URL-safe de uso único.
        """
        current = time.monotonic() if now is None else now
        token = secrets.token_urlsafe(32)
        self._tickets[token] = _TicketEntry(
            user_id=user_id,
            company_id=company_id,
            expires_at=current + WS_TICKET_TTL_SECONDS,
        )
        self._prune_tickets(current)
        return token

    def consume_ticket(self, token: str, *, now: float | None = None) -> tuple[str, str] | None:
        """
        Consome ticket (single-use). Retorna ``(user_id, company_id)`` ou None.
        """
        current = time.monotonic() if now is None else now
        entry = self._tickets.pop(str(token or "").strip(), None)
        if entry is None:
            return None
        if entry.expires_at <= current:
            return None
        return entry.user_id, entry.company_id

    def ticket_count(self) -> int:
        """Quantidade de tickets vivos (testes)."""
        return len(self._tickets)

    async def register(self, user_id: str, websocket: WebSocket) -> _RobotWsClient:
        """Registra conexão WS (já aceita) para o usuário."""
        client = _RobotWsClient(
            websocket=websocket,
            user_id=user_id,
            last_ping_at=time.monotonic(),
        )
        async with self._lock:
            bucket = self._clients.setdefault(user_id, [])
            bucket.append(client)
        return client

    async def unregister(self, user_id: str, websocket: WebSocket) -> None:
        """Remove conexão específica."""
        async with self._lock:
            bucket = self._clients.get(user_id) or []
            self._clients[user_id] = [c for c in bucket if c.websocket is not websocket]
            if not self._clients[user_id]:
                self._clients.pop(user_id, None)

    def connected_user_ids(self) -> list[str]:
        """Usuários com pelo menos uma conexão WS aberta."""
        return list(self._clients.keys())

    def has_connections(self, user_id: str) -> bool:
        """True se o usuário tem WS ativo."""
        return bool(self._clients.get(user_id))

    def schedule_publish(self, user_id: str, *, now: float | None = None) -> None:
        """Agenda publish com debounce por usuário."""
        current = time.monotonic() if now is None else now
        self._pending_publish[user_id] = current + PUBLISH_DEBOUNCE_SECONDS

    async def mark_ping(self, user_id: str, websocket: WebSocket) -> None:
        """Atualiza heartbeat do client (mensagem ping)."""
        async with self._lock:
            for client in self._clients.get(user_id) or []:
                if client.websocket is websocket:
                    client.last_ping_at = time.monotonic()
                    return

    async def send_to_user(self, user_id: str, payload: dict[str, Any]) -> None:
        """Envia JSON a todas as conexões do usuário."""
        async with self._lock:
            clients = list(self._clients.get(user_id) or [])
        dead: list[WebSocket] = []
        for client in clients:
            try:
                await client.websocket.send_json(payload)
            except Exception:
                logger.warning(
                    "[ROBOT_WS_SEND_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
                dead.append(client.websocket)
        for websocket in dead:
            await self.unregister(user_id, websocket)

    async def push_snapshot_if_changed(self, user_id: str) -> bool:
        """
        Monta snapshot e envia só se o digest mudou.

        Returns:
            True se enviou pelo menos uma mensagem.
        """
        if self.snapshot_builder is None:
            return False
        if not self.has_connections(user_id):
            return False
        try:
            payload = self.snapshot_builder(user_id)
        except Exception:
            logger.exception("[ROBOT_WS_SNAPSHOT_FAILED] user_id=%s", user_id)
            return False
        digest = _payload_digest(payload)
        async with self._lock:
            clients = list(self._clients.get(user_id) or [])
            if not clients:
                return False
            if all(c.last_digest == digest for c in clients):
                return False
            for client in clients:
                client.last_digest = digest
        envelope = {"type": "robot_state", "data": payload.get("data", payload)}
        await self.send_to_user(user_id, envelope)
        return True

    async def force_snapshot(self, user_id: str) -> None:
        """Envia snapshot ignorando digest (connect inicial)."""
        if self.snapshot_builder is None:
            return
        try:
            payload = self.snapshot_builder(user_id)
        except Exception:
            logger.exception("[ROBOT_WS_SNAPSHOT_FAILED] user_id=%s", user_id)
            return
        digest = _payload_digest(payload)
        async with self._lock:
            for client in self._clients.get(user_id) or []:
                client.last_digest = digest
        await self.send_to_user(
            user_id,
            {"type": "robot_state", "data": payload.get("data", payload)},
        )

    def start_background_loops(self) -> None:
        """Inicia push + maintenance (idempotente)."""
        if self._push_task is None or self._push_task.done():
            self._push_task = asyncio.create_task(self._push_loop(), name="robot-ws-push")
        if self._maintenance_task is None or self._maintenance_task.done():
            self._maintenance_task = asyncio.create_task(
                self._maintenance_loop(),
                name="robot-ws-maintenance",
            )

    async def aclose(self) -> None:
        """Cancela loops e fecha conexões."""
        for task in (self._push_task, self._maintenance_task):
            if task is not None and not task.done():
                task.cancel()
                with_suppress = True
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    if with_suppress:
                        logger.exception("[ROBOT_WS_SHUTDOWN_TASK_ERROR]")
        self._push_task = None
        self._maintenance_task = None
        async with self._lock:
            clients = [(uid, list(bucket)) for uid, bucket in self._clients.items()]
            self._clients.clear()
        for user_id, bucket in clients:
            for client in bucket:
                try:
                    await client.websocket.close(code=1001)
                except Exception:
                    logger.debug("[ROBOT_WS_CLOSE_FAILED] user_id=%s", user_id, exc_info=True)

    async def _push_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(PUSH_LOOP_INTERVAL_SECONDS)
                now = time.monotonic()
                due = [uid for uid, at in list(self._pending_publish.items()) if at <= now]
                for uid in due:
                    self._pending_publish.pop(uid, None)
                    await self.push_snapshot_if_changed(uid)
                # Push periódico para conexões sem schedule (countdown / status).
                for uid in self.connected_user_ids():
                    if uid not in due:
                        await self.push_snapshot_if_changed(uid)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[ROBOT_WS_PUSH_LOOP_ERROR]")

    async def _maintenance_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)
                if self.maintenance_hook is None:
                    continue
                for uid in self.connected_user_ids():
                    try:
                        await self.maintenance_hook(uid)
                    except Exception:
                        logger.warning(
                            "[ROBOT_WS_MAINTENANCE_FAILED] user_id=%s",
                            uid,
                            exc_info=True,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[ROBOT_WS_MAINTENANCE_LOOP_ERROR]")

    def _prune_tickets(self, now: float) -> None:
        expired = [k for k, v in self._tickets.items() if v.expires_at <= now]
        for key in expired:
            self._tickets.pop(key, None)


def _payload_digest(payload: dict[str, Any]) -> str:
    """Hash estável do payload para dedupe de push."""
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def close_robot_websocket(websocket: WebSocket, payload: dict[str, Any]) -> None:
    """Envia erro final e fecha o socket (padrão do market WS)."""
    try:
        await websocket.send_json(payload)
    except Exception:
        logger.debug("falha ao enviar mensagem final do robot WS", exc_info=True)
    try:
        await websocket.close(code=1008)
    except Exception:
        logger.debug("falha ao fechar robot WS", exc_info=True)


async def robot_ws_receive_loop(
    hub: RobotStateWsHub,
    user_id: str,
    websocket: WebSocket,
) -> None:
    """
    Loop de leitura: ping do client mantém heartbeat; demais mensagens ignoradas.
    """
    try:
        while True:
            message = await websocket.receive_json()
            if isinstance(message, dict) and message.get("type") == "ping":
                await hub.mark_ping(user_id, websocket)
                await websocket.send_json({"type": "pong", "ts": time.time()})
    except WebSocketDisconnect:
        return
    except Exception:
        logger.debug("[ROBOT_WS_RECEIVE_END] user_id=%s", user_id, exc_info=True)
