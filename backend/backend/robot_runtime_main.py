"""Processo dedicado dos workers do robô (Fase 4).

Sobe sem HTTP público: restaura estados, escuta comandos Redis
(``robot:cmd``) e publica snapshots (``robot:state``). O gateway em
``ROBOT_RUNTIME_MODE=external`` deixa de criar ``asyncio.Task`` locais.

Uso (Compose)::

    command: ["python", "-m", "backend.robot_runtime_main"]
    environment:
      ROBOT_RUNTIME_MODE: worker

Ver docs/DEPLOY_VPS.md e PERFORMANCE_SISTEMA.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("robot-runtime")


def _hydrate_user_from_persistence(gateway: object, user_id: str) -> None:
    """Recarrega estado/trades do usuário a partir da persistência (Supabase/SQLite)."""
    persistence = getattr(gateway, "robot_persistence", None)
    auto_trader = getattr(gateway, "auto_trader", None)
    if persistence is None or auto_trader is None:
        return
    try:
        for uid, state_payload in persistence.load_states():
            if str(uid) != user_id:
                continue
            trades = persistence.load_trades(user_id) or []
            if not trades:
                try:
                    trades = [
                        item
                        for item in persistence.load_trade_history(user_id, 30)
                        if str(item.get("result") or "").upper()
                        in {"WIN", "LOSS", "TIMEOUT", "DRAW"}
                    ]
                except Exception:
                    trades = []
            source = getattr(gateway, "robot_persistence_source", lambda: "runtime")()
            auto_trader.restore(user_id, state_payload, trades, source=source)
            return
    except Exception:
        logger.warning(
            "[ROBOT_RUNTIME_HYDRATE_FAILED] user_id=%s",
            user_id,
            exc_info=True,
        )


async def _handle_command(gateway: object, payload: dict) -> None:
    """Aplica start/stop/ensure no auto_trader local do runtime."""
    user_id = str(payload.get("user_id") or "").strip()
    action = str(payload.get("action") or "").strip().lower()
    if not user_id or not action:
        return
    if action in {"start", "ensure"}:
        _hydrate_user_from_persistence(gateway, user_id)
        # Painel online no gateway → marca ativo no runtime para ensure passar.
        mark = getattr(gateway, "mark_user_active", None)
        if callable(mark):
            mark(user_id)
        gateway.ensure_robot_worker(user_id)  # type: ignore[attr-defined]
        logger.info("[ROBOT_RUNTIME_CMD] action=%s user_id=%s", action, user_id)
    elif action == "stop":
        _hydrate_user_from_persistence(gateway, user_id)
        state = gateway.auto_trader.get(user_id)  # type: ignore[attr-defined]
        state.enabled = False
        task = getattr(gateway, "robot_tasks", {}).pop(user_id, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        logger.info("[ROBOT_RUNTIME_CMD] action=stop user_id=%s", user_id)


async def _cmd_listener(gateway: object, stop: asyncio.Event) -> None:
    """Loop blocking Redis pubsub em thread → comandos no event loop."""
    from backend.robot_bus import CMD_CHANNEL, RobotBus

    bus = RobotBus()
    if not bus.enabled:
        logger.error("[ROBOT_RUNTIME] Redis não configurado — abortando listener")
        return

    def _listen_forever() -> None:
        client = bus._get_client()
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(CMD_CHANNEL)
        logger.info("[ROBOT_RUNTIME] subscribed channel=%s", CMD_CHANNEL)
        for message in pubsub.listen():
            if stop.is_set():
                break
            if message.get("type") != "message":
                continue
            raw = message.get("data")
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            if isinstance(payload, dict):
                asyncio.run_coroutine_threadsafe(_handle_command(gateway, payload), loop)

    loop = asyncio.get_running_loop()
    await asyncio.to_thread(_listen_forever)


async def _snapshot_publisher(gateway: object, stop: asyncio.Event) -> None:
    """Publica snapshots periódicos dos usuários com worker ativo."""
    from backend.robot_bus import RobotBus

    bus = RobotBus()
    while not stop.is_set():
        try:
            for user_id in list(getattr(gateway, "robot_tasks", {}) or {}):
                try:
                    payload = gateway.build_robot_state_snapshot_payload(user_id)  # type: ignore[attr-defined]
                    bus.publish_snapshot(user_id, payload)
                    hub = getattr(gateway, "robot_state_ws_hub", None)
                    if hub is not None:
                        hub.schedule_publish(user_id)
                except Exception:
                    logger.warning(
                        "[ROBOT_RUNTIME_SNAPSHOT_FAILED] user_id=%s",
                        user_id,
                        exc_info=True,
                    )
        except Exception:
            logger.exception("[ROBOT_RUNTIME_SNAPSHOT_LOOP]")
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            continue


async def amain() -> None:
    """Boot do robot-runtime."""
    os.environ.setdefault("ROBOT_RUNTIME_MODE", "worker")
    # Import tardio: carrega gateway module (estado/auto_trader) sem uvicorn.
    from backend import main as gateway
    from backend.robot_bus import RobotBus

    logger.info("[ROBOT_RUNTIME_START] mode=%s", os.getenv("ROBOT_RUNTIME_MODE"))
    # Reusa a restauração de estados do startup HTTP.
    await gateway.restore_robot_states()
    stop = asyncio.Event()

    def _signal_handler(*_args: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with_suppress = True
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            if with_suppress:
                signal.signal(sig, lambda *_: stop.set())

    bus = RobotBus()
    if not bus.enabled:
        raise SystemExit("PROD_REDIS_URL/DEV_REDIS_URL obrigatório para robot-runtime")

    listener = asyncio.create_task(_cmd_listener(gateway, stop), name="robot-cmd-listener")
    publisher = asyncio.create_task(_snapshot_publisher(gateway, stop), name="robot-snapshot")
    await stop.wait()
    listener.cancel()
    publisher.cancel()
    await gateway.shutdown_robot_workers()
    bus.close()
    logger.info("[ROBOT_RUNTIME_STOP]")


def main() -> None:
    """Entry point ``python -m backend.robot_runtime_main``."""
    asyncio.run(amain())


if __name__ == "__main__":
    main()
