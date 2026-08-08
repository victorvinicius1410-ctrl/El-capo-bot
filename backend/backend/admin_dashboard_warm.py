"""Background warmer do cache do dashboard administrativo.

Mantém `admin_dashboard_cache` quente para tenants que já acessaram o
painel, recalculando periodicamente os períodos 7d e 30d.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from backend.admin_dashboard_cache import write_admin_dashboard_cache

logger = logging.getLogger("backend-admin-dashboard-warm")

AdminDashboardCompute = Callable[[str, int], Awaitable[dict[str, Any]]]
CacheWriter = Callable[..., None]
SleepFn = Callable[[float], Awaitable[Any]]
ClockFn = Callable[[], float]

DEFAULT_WARM_INTERVAL_SECONDS = 30.0
DEFAULT_WARM_DAYS: tuple[int, ...] = (7, 30)


class AdminDashboardWarmer:
    """
    Agenda recomputes periódicos do dashboard para empresas registradas.

    Args:
        compute: Callable async ``(company_id, days) -> payload`` injetável.
        interval_seconds: Intervalo entre ciclos de warm (padrão 30s).
        days: Períodos a aquecer por empresa.
        write_cache: Gravação no cache (padrão ``write_admin_dashboard_cache``).
        sleep: Sleep async injetável (testes).
        now_monotonic: Relógio monotônico injetável (testes).
    """

    def __init__(
        self,
        compute: AdminDashboardCompute,
        *,
        interval_seconds: float = DEFAULT_WARM_INTERVAL_SECONDS,
        days: tuple[int, ...] = DEFAULT_WARM_DAYS,
        write_cache: CacheWriter = write_admin_dashboard_cache,
        sleep: SleepFn | None = None,
        now_monotonic: ClockFn | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds deve ser > 0")
        if not days:
            raise ValueError("days não pode ser vazio")
        self._compute = compute
        self._interval_seconds = float(interval_seconds)
        self._days = tuple(int(value) for value in days)
        self._write_cache = write_cache
        self._sleep: SleepFn = sleep or asyncio.sleep
        self._now: ClockFn = now_monotonic or time.monotonic
        self._companies: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._last_warm_at: float | None = None
        self._warm_count = 0

    @property
    def registered_companies(self) -> frozenset[str]:
        """Empresas atualmente registradas para warm."""
        return frozenset(self._companies)

    @property
    def interval_seconds(self) -> float:
        """Intervalo configurado entre ciclos."""
        return self._interval_seconds

    @property
    def last_warm_at(self) -> float | None:
        """Timestamp monotônico do último ``warm_once`` concluído."""
        return self._last_warm_at

    @property
    def warm_count(self) -> int:
        """Quantidade de ciclos ``warm_once`` concluídos."""
        return self._warm_count

    def register_company(self, company_id: str) -> None:
        """
        Registra um tenant para warm periódico.

        Args:
            company_id: Identificador da empresa (sessão autenticada).
        """
        normalized = (company_id or "").strip()
        if not normalized:
            return
        self._companies.add(normalized)

    def start(self) -> None:
        """
        Inicia o loop de background (idempotente).

        Cria uma ``asyncio.Task`` que chama ``warm_once`` e dorme
        ``interval_seconds`` entre ciclos.
        """
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._loop(),
            name="admin-dashboard-warm",
        )
        logger.info(
            "[ADMIN_DASHBOARD_WARMER_STARTED] interval_s=%s days=%s",
            self._interval_seconds,
            self._days,
        )

    async def aclose(self) -> None:
        """
        Cancela o loop de warm e aguarda o encerramento da task.

        Returns:
            None. Seguro chamar mais de uma vez.
        """
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[ADMIN_DASHBOARD_WARMER_SHUTDOWN_ERROR]")
        logger.info("[ADMIN_DASHBOARD_WARMER_STOPPED]")

    async def warm_once(self) -> None:
        """
        Executa um ciclo de warm para todas as empresas registradas.

        Para cada ``company_id`` e cada período em ``days``, chama
        ``compute`` e grava o resultado via ``write_cache``. Falhas por
        tenant/período são logadas e não interrompem o restante do ciclo.

        Preferível em testes a usar sleep real: chame este método
        diretamente com ``compute``/``write_cache``/relógio injetados.
        """
        companies = sorted(self._companies)
        for company_id in companies:
            for days in self._days:
                try:
                    payload = await self._compute(company_id, days)
                    self._write_cache(
                        company_id,
                        days,
                        payload,
                        now_monotonic=self._now(),
                    )
                except Exception:
                    logger.exception(
                        "[ADMIN_DASHBOARD_WARM_FAILED] company_id=%s days=%s",
                        company_id,
                        days,
                    )
        self._last_warm_at = self._now()
        self._warm_count += 1

    async def _loop(self) -> None:
        """Loop interno: warm + sleep até cancelamento."""
        while True:
            try:
                await self.warm_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[ADMIN_DASHBOARD_WARM_LOOP_ERROR]")
            await self._sleep(self._interval_seconds)
