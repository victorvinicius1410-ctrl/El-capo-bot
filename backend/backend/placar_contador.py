"""Placar como contador único no banco — escritor em segundo plano.

Pedido do dono (01/10/2026): o placar é um contador no banco que soma cada
resultado e zera no "Reiniciar placar". Desenho em docs/PLACAR_CONTADOR.md;
tabelas e funções em backend/migration_placar_contador.sql.

Fase 1 (``PLACAR_CONTADOR=sombra``): o robô continua mostrando o placar de
hoje e, em paralelo, lança cada resultado no contador. A auditoria compara os
dois; só depois de dias sem divergência o painel passa a ler do contador.

Quem chama nunca espera o banco: tudo entra numa fila e uma thread grava na
ordem de chegada (reiniciar e lançar do mesmo processo não se atropelam).
Falha transitória tenta de novo; função ausente no banco (migration não
rodou) avisa UMA vez em WARNING e desliga — a lição de 03/09, quando uma
migration "aplicada" nunca tinha rodado e o código tolerava em silêncio.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("backend-gateway")

MODOS = {"off", "sombra"}
TENTATIVAS = 4
ESPERA_ENTRE_TENTATIVAS_SEGUNDOS = (0.5, 2.0, 5.0)
FILA_MAXIMA = 10_000


def modo_do_contador() -> str:
    """``off`` (padrão) ou ``sombra``, pela variável ``PLACAR_CONTADOR``."""
    modo = os.getenv("PLACAR_CONTADOR", "off").strip().lower()
    return modo if modo in MODOS else "off"


def _sem_funcao_no_banco(exc: BaseException) -> bool:
    """True quando o PostgREST diz que a função/tabela não existe."""
    resposta = getattr(exc, "response", None)
    status = getattr(resposta, "status_code", None)
    texto = ""
    try:
        texto = resposta.text if resposta is not None else ""
    except Exception:  # noqa: BLE001 - só para diagnóstico
        texto = ""
    return status == 404 or "PGRST202" in texto or "PGRST205" in texto or "42P01" in texto


class ContadorDoPlacar:
    """Fila de escrita do contador do placar.

    Args:
        persistencia: ``RobotPersistence`` com os métodos ``placar_*``, ou uma
            função sem argumentos que a devolve (o gateway troca a instância
            em testes e na bancada).
        modo: ``off`` ou ``sombra``.
        dormir: Relógio de espera (testes).
    """

    def __init__(
        self,
        persistencia: Any,
        modo: str | None = None,
        *,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self.persistencia = persistencia
        self.modo = modo or modo_do_contador()
        self._dormir = dormir
        self._fila: queue.Queue[tuple[str, str, dict[str, Any]]] = queue.Queue(maxsize=FILA_MAXIMA)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._sem_tabela = False
        self.gravados = 0
        self.perdidos = 0

    @property
    def ligado(self) -> bool:
        return self.modo != "off" and not self._sem_tabela

    # --- API: nunca bloqueia quem chama -----------------------------------

    def lancar(
        self,
        user_id: str,
        chave: str,
        *,
        wins: int = 0,
        losses: int = 0,
        profit: float = 0.0,
        dinheiro: float = 0.0,
        sintetica: bool = False,
    ) -> None:
        """Enfileira o efeito de uma ordem (ignora lançamento vazio)."""
        if not (wins or losses or abs(profit) > 1e-9 or abs(dinheiro) > 1e-9):
            return
        self._enfileirar(
            "placar_lancar",
            user_id,
            {
                "chave": str(chave),
                "wins": int(wins),
                "losses": int(losses),
                "profit": round(float(profit), 2),
                "dinheiro": round(float(dinheiro), 2),
                "sintetica": bool(sintetica),
            },
        )

    def apagar(self, user_id: str, order_id: str) -> None:
        self._enfileirar("placar_apagar", user_id, {"order_id": str(order_id)})

    def reiniciar(self, user_id: str, desde: Any = None) -> None:
        self._enfileirar("placar_reiniciar", user_id, {"desde": desde})

    def vitrine(self, user_id: str, wins: int, losses: int, profit: float) -> None:
        self._enfileirar(
            "placar_vitrine",
            user_id,
            {"wins": int(wins), "losses": int(losses), "profit": round(float(profit), 2)},
        )

    def semear(self, user_id: str, desde: Any, **numeros: Any) -> None:
        self._enfileirar("placar_semear", user_id, {"desde": desde, **numeros})

    def esperar_fila(self, timeout: float = 10.0) -> bool:
        """Espera a fila esvaziar (testes e desligamento). True se esvaziou."""
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            if self._fila.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return False

    # --- interno ------------------------------------------------------------

    def _enfileirar(self, metodo: str, user_id: str, argumentos: dict[str, Any]) -> None:
        if not self.ligado or not str(user_id or "").strip():
            return
        try:
            self._fila.put_nowait((metodo, str(user_id), argumentos))
        except queue.Full:
            self.perdidos += 1
            logger.error(
                "[PLACAR_CONTADOR_FILA_CHEIA] user_id=%s metodo=%s pendentes=%s",
                user_id,
                metodo,
                self._fila.qsize(),
            )
            return
        self._garantir_thread()

    def _garantir_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._laco, name="placar-contador", daemon=True
            )
            self._thread.start()

    def _laco(self) -> None:
        while True:
            metodo, user_id, argumentos = self._fila.get()
            try:
                self._gravar(metodo, user_id, argumentos)
            finally:
                self._fila.task_done()

    def _gravar(self, metodo: str, user_id: str, argumentos: dict[str, Any]) -> None:
        if self._sem_tabela:
            return
        persistencia = self.persistencia
        if not hasattr(persistencia, "placar_lancar") and callable(persistencia):
            persistencia = persistencia()
        funcao = getattr(persistencia, metodo, None)
        if not callable(funcao):
            return
        for tentativa in range(TENTATIVAS):
            try:
                if metodo == "placar_lancar":
                    funcao(user_id, argumentos["chave"], **{k: v for k, v in argumentos.items() if k != "chave"})
                elif metodo == "placar_apagar":
                    funcao(user_id, argumentos["order_id"])
                elif metodo == "placar_reiniciar":
                    funcao(user_id, argumentos.get("desde"))
                elif metodo == "placar_vitrine":
                    funcao(user_id, argumentos["wins"], argumentos["losses"], argumentos["profit"])
                elif metodo == "placar_semear":
                    numeros = {k: v for k, v in argumentos.items() if k != "desde"}
                    funcao(user_id, argumentos["desde"], **numeros)
                self.gravados += 1
                return
            except Exception as exc:  # noqa: BLE001 - qualquer falha vira log
                if _sem_funcao_no_banco(exc):
                    self._sem_tabela = True
                    logger.warning(
                        "[PLACAR_CONTADOR_SEM_TABELA] metodo=%s — rode "
                        "backend/migration_placar_contador.sql no Supabase; contador desligado",
                        metodo,
                    )
                    return
                if tentativa + 1 >= TENTATIVAS:
                    self.perdidos += 1
                    logger.error(
                        "[PLACAR_CONTADOR_PERDEU] user_id=%s metodo=%s args=%s erro=%s",
                        user_id,
                        metodo,
                        argumentos,
                        type(exc).__name__,
                    )
                    return
                self._dormir(ESPERA_ENTRE_TENTATIVAS_SEGUNDOS[min(tentativa, len(ESPERA_ENTRE_TENTATIVAS_SEGUNDOS) - 1)])
