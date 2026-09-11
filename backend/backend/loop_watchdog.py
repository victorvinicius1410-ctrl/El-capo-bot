"""Vigia do event loop do robot-runtime.

Todos os workers do robô dividem UM event loop. Qualquer chamada síncrona
(HTTP, disco, CPU pesado) dentro dele congela todos os usuários ao mesmo tempo:
o ``sleep`` de 0,15s perto da abertura da vela volta segundos depois e a ordem
perde a janela de compra (0-3s). Foi o que aconteceu em 10/09/2026 — 63% das
entradas perdidas por atraso, causadas por HTTPS síncrono ao Supabase no
``_snapshot_publisher`` — e nada no log apontava a causa; foi preciso py-spy.

Este vigia deixa isso visível sem ferramenta externa:

* ``[EVENT_LOOP_BLOCKED]`` (WARNING): o loop ficou parado mais que
  ``EVENT_LOOP_STALL_SECONDS``. Uma thread à parte fotografa a pilha da thread
  do loop NAQUELE instante, então o log diz qual função está travando.
* ``[EVENT_LOOP_LAG]`` (INFO; WARNING se o p90 passar de
  ``EVENT_LOOP_LAG_WARN_SECONDS``): resumo periódico do atraso do loop. Saudável
  é p90 de centésimos; acima de ~0,3s as entradas começam a sair tarde.

Desliga com ``EVENT_LOOP_WATCHDOG=false``. Só observa: não altera nada do robô.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from typing import Any


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


EVENT_LOOP_WATCHDOG_ENABLED = os.getenv("EVENT_LOOP_WATCHDOG", "true").strip().lower() in {"1", "true", "yes"}
EVENT_LOOP_STALL_SECONDS = _env_float("EVENT_LOOP_STALL_SECONDS", 0.5)
EVENT_LOOP_LAG_WARN_SECONDS = _env_float("EVENT_LOOP_LAG_WARN_SECONDS", 0.3)
EVENT_LOOP_BEAT_SECONDS = 0.1
EVENT_LOOP_SUMMARY_SECONDS = 60.0
# A mesma pilha só é repetida no log a cada tantos segundos (as outras contam
# no resumo), para um travamento recorrente não afogar o log.
EVENT_LOOP_STACK_REPEAT_SECONDS = 60.0
_STACK_FRAMES = 8


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[int(fraction * (len(ordered) - 1))]


def format_blocking_stack(frame: Any) -> tuple[str, str]:
    """Resume a pilha da thread do loop.

    Mantém os quadros do código do projeto (``backend/``) mais o quadro mais
    interno — que costuma ser a biblioteca que de fato bloqueia (``ssl.read``,
    ``socket.connect``...).

    Returns:
        ``(assinatura, pilha)``: a assinatura é o quadro do projeto mais interno,
        usado para não repetir a mesma pilha no log.
    """
    stack = traceback.extract_stack(frame)
    if not stack:
        return "", ""
    projeto = [f for f in stack if "/backend/" in f.filename.replace(os.sep, "/")]
    escolhidos = projeto[-_STACK_FRAMES:]
    if stack[-1] not in escolhidos:
        escolhidos.append(stack[-1])
    partes = [f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in escolhidos]
    assinatura = partes[-2] if len(partes) >= 2 and projeto else partes[-1]
    return assinatura, " > ".join(partes)


class EventLoopWatchdog:
    """Mede o atraso do event loop e denuncia quem o trava."""

    def __init__(
        self,
        logger: logging.Logger,
        *,
        stall_seconds: float = EVENT_LOOP_STALL_SECONDS,
        beat_seconds: float = EVENT_LOOP_BEAT_SECONDS,
        summary_seconds: float = EVENT_LOOP_SUMMARY_SECONDS,
        lag_warn_seconds: float = EVENT_LOOP_LAG_WARN_SECONDS,
    ) -> None:
        self.logger = logger
        self.stall_seconds = stall_seconds
        self.beat_seconds = beat_seconds
        self.summary_seconds = summary_seconds
        self.lag_warn_seconds = lag_warn_seconds
        self._last_beat = time.monotonic()
        self._loop_thread_id: int | None = None
        self._reported_beat: float | None = None
        self._stack_logged_at: dict[str, float] = {}
        self._stalls = 0
        self._lags: list[float] = []
        self._stopped = threading.Event()

    def check_stall(self, now: float | None = None) -> str | None:
        """Chamado pela thread vigia: loga a pilha se o loop está parado.

        Returns:
            A pilha logada, ou ``None`` se o loop está andando (ou se este
            travamento já foi reportado).
        """
        now = time.monotonic() if now is None else now
        beat = self._last_beat
        blocked_for = now - beat
        if blocked_for < self.stall_seconds or self._reported_beat == beat:
            return None
        self._reported_beat = beat
        self._stalls += 1
        frame = sys._current_frames().get(self._loop_thread_id) if self._loop_thread_id else None
        if frame is None:
            return None
        assinatura, pilha = format_blocking_stack(frame)
        ultimo = self._stack_logged_at.get(assinatura)
        if ultimo is not None and now - ultimo < EVENT_LOOP_STACK_REPEAT_SECONDS:
            return None
        self._stack_logged_at[assinatura] = now
        self.logger.warning(
            "[EVENT_LOOP_BLOCKED] blocked_for=%.2fs stack=%s",
            blocked_for,
            pilha,
        )
        return pilha

    def _watch(self) -> None:
        while not self._stopped.wait(self.beat_seconds):
            try:
                self.check_stall()
            except Exception:  # o vigia nunca pode derrubar o processo
                self.logger.debug("[EVENT_LOOP_WATCHDOG_ERROR]", exc_info=True)

    def _log_summary(self) -> None:
        lags = self._lags
        self._lags = []
        stalls = self._stalls
        self._stalls = 0
        if not lags:
            return
        p90 = _percentile(lags, 0.9)
        level = logging.WARNING if p90 > self.lag_warn_seconds or stalls else logging.INFO
        self.logger.log(
            level,
            "[EVENT_LOOP_LAG] p50=%.3fs p90=%.3fs max=%.3fs stalls=%s samples=%s",
            _percentile(lags, 0.5),
            p90,
            max(lags),
            stalls,
            len(lags),
        )

    async def run(self, stop: asyncio.Event) -> None:
        """Batimento no loop + thread vigia, até ``stop``."""
        self._loop_thread_id = threading.get_ident()
        self._last_beat = time.monotonic()
        vigia = threading.Thread(target=self._watch, name="event-loop-watchdog", daemon=True)
        vigia.start()
        next_summary = time.monotonic() + self.summary_seconds
        try:
            while not stop.is_set():
                before = time.monotonic()
                await asyncio.sleep(self.beat_seconds)
                now = time.monotonic()
                self._lags.append(max(0.0, now - before - self.beat_seconds))
                self._last_beat = now
                if now >= next_summary:
                    self._log_summary()
                    next_summary = now + self.summary_seconds
        finally:
            self._stopped.set()
