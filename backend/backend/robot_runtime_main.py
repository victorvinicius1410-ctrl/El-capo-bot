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
import uuid

from backend import masaniello


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("robot-runtime")


def _is_valid_account_user_id(user_id: str) -> bool:
    """Confere se `user_id` é um UUID (formato do Supabase Auth).

    Defesa contra o incidente 2026-08-18 ~22h18: 35 usuários de teste
    (`user-demo`, etc.) ficaram com `enabled=true` na tabela `robot_states`
    de produção e o runtime religou workers reais para eles, dobrando a
    carga sobre o `_call_gate` do bullex-service e zerando o cache
    compartilhado de mercado para TODOS os usuários por 40+ minutos. Todo
    usuário real chega aqui com `user_id` = UUID da sessão autenticada
    (Supabase Auth); nenhum fluxo legítimo de `/robot/start` usa outro
    formato. Ver docs/ROBO_E_SUPORTE.md e docs/PERFORMANCE_SISTEMA.md.
    """
    try:
        uuid.UUID(str(user_id))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _capture_live_session_score(auto_trader: object, user_id: str) -> dict[str, object]:
    """Lê wins/losses/profit da memória viva antes de um restore forçado.

    Args:
        auto_trader: Instância do AutoTrader do runtime.
        user_id: Cliente alvo.

    Returns:
        Placar e ``stop_reset_at`` atuais em memória.
    """
    state = auto_trader.get(user_id)  # type: ignore[attr-defined]
    return {
        "wins": int(getattr(state, "wins", 0) or 0),
        "losses": int(getattr(state, "losses", 0) or 0),
        "profit": float(getattr(state, "profit", 0) or 0),
        "stop_reset_at": getattr(state, "stop_reset_at", None),
        "stop_offset_wins": int(getattr(state, "stop_offset_wins", 0) or 0),
        "stop_offset_losses": int(getattr(state, "stop_offset_losses", 0) or 0),
        "stop_offset_profit": float(getattr(state, "stop_offset_profit", 0) or 0),
    }


def _prefer_live_session_score(
    auto_trader: object,
    user_id: str,
    live: dict[str, object],
) -> None:
    """Mantém o placar vivo quando a persistência está atrasada ou após reset.

    Args:
        auto_trader: Instância do AutoTrader do runtime.
        user_id: Cliente alvo.
        live: Placar capturado antes do ``restore``.
    """
    state = auto_trader.get(user_id)  # type: ignore[attr-defined]
    live_wins = int(live.get("wins") or 0)
    live_losses = int(live.get("losses") or 0)
    live_profit = float(live.get("profit") or 0)
    live_reset = live.get("stop_reset_at")
    live_blank = live_wins == 0 and live_losses == 0 and abs(live_profit) < 1e-9
    if live_reset is not None and live_blank:
        state.wins = 0
        state.losses = 0
        state.profit = 0.0
        state.stop_offset_wins = 0
        state.stop_offset_losses = 0
        state.stop_offset_profit = 0.0
        state.stop_reset_at = live_reset
        return
    restored_total = int(getattr(state, "wins", 0) or 0) + int(getattr(state, "losses", 0) or 0)
    if live_wins + live_losses > restored_total:
        state.wins = live_wins
        state.losses = live_losses
        state.profit = live_profit
        # O placar vivo vem com a parte do Shift+O que ele carrega.
        state.stop_offset_wins = int(live.get("stop_offset_wins") or 0)
        state.stop_offset_losses = int(live.get("stop_offset_losses") or 0)
        state.stop_offset_profit = float(live.get("stop_offset_profit") or 0)


def _hydrate_user_from_persistence(
    gateway: object,
    user_id: str,
    *,
    force: bool = False,
) -> None:
    """
    Recarrega estado/trades do usuário a partir da persistência.

    Args:
        gateway: Módulo/objeto com ``robot_persistence`` e ``auto_trader``.
        user_id: Cliente alvo.
        force: Recarrega mesmo com estado em memória. Use no ``start``
            (enabled veio do gateway). No ``stop`` deixe False para não
            sobrescrever o placar vivo com a DB atrasada.
    """
    persistence = getattr(gateway, "robot_persistence", None)
    auto_trader = getattr(gateway, "auto_trader", None)
    if persistence is None or auto_trader is None:
        return
    # Enquanto o robô roda, a MEMÓRIA do runtime é a fonte de verdade — não a
    # persistência. O comando `ensure` chega a cada ~5s do polling do painel e
    # re-hidratar aqui sobrescrevia o placar vivo com o último snapshot gravado
    # (que é assíncrono, então quase sempre atrasado). Em 08/08 isso derrubou o
    # placar de um cliente de 4x0/+343 para 1x0/+85 no meio da sessão.
    #
    # `force=True` é OBRIGATÓRIO no `start`: quem liga o robô é o gateway, que
    # grava `enabled=True` na persistência. Sem hidratar aqui o runtime ficaria
    # com o `enabled=False` antigo e `ensure_robot_worker` recusaria subir o
    # worker — painel dizendo "ativo" e nada operando.
    #
    # `stop` NÃO usa force: a memória viva do runtime pode estar à frente da
    # DB (último WIN ainda em persistência async). Re-hidratar no stop
    # recalculava o placar pelo histórico atrasado (ex.: 10x12 → 9x12).
    # Sem virada à meia-noite (placar contínuo até o Reiniciar, ver
    # backend.placar_janela), a memória nunca é "de outro dia": a regra de
    # descartar o placar da véspera (29/09, d353ab80) saiu com ela.
    live_score = None
    live_cycle = None
    has_state = getattr(auto_trader, "has_state", None)
    if callable(has_state) and has_state(user_id):
        if not force:
            return
        live_score = _capture_live_session_score(auto_trader, user_id)
        # Ciclo do Gerenciamento Consistente: a memória viva pode estar à
        # frente do banco (resultado ainda em gravação assíncrona).
        live_cycle = getattr(auto_trader.get(user_id), "masaniello_cycle", None)  # type: ignore[attr-defined]
    try:
        for uid, state_payload in persistence.load_states():
            if str(uid) != user_id:
                continue
            # Placar pelo Histórico, não pelo espelho robot_trades (ver
            # RobotPersistence.load_trades_for_restore).
            trades = persistence.load_trades_for_restore(
                user_id, stop_reset_at=(state_payload or {}).get("stop_reset_at")
            )
            source = getattr(gateway, "robot_persistence_source", lambda: "runtime")()
            auto_trader.restore(user_id, state_payload, trades, source=source)
            if live_score is not None:
                _prefer_live_session_score(auto_trader, user_id, live_score)
            if isinstance(live_cycle, dict):
                restored = auto_trader.get(user_id)  # type: ignore[attr-defined]
                restored.masaniello_cycle = masaniello.pick_freshest_cycle(
                    getattr(restored, "masaniello_cycle", None), live_cycle
                )
            # `restore` recalcula o placar pelo histórico e
            # `_prefer_live_session_score` mantém o MAIOR total: os dois
            # desfaziam uma exclusão recente. A baixa intencional vigente
            # tem a última palavra.
            enforce = getattr(gateway, "apply_session_score_authority_to_state", None)
            if callable(enforce):
                try:
                    enforce(user_id)
                except Exception:
                    logger.warning(
                        "[ROBOT_RUNTIME_SCORE_AUTHORITY_FAILED] user_id=%s",
                        user_id,
                        exc_info=True,
                    )
            return
    except Exception:
        logger.warning(
            "[ROBOT_RUNTIME_HYDRATE_FAILED] user_id=%s",
            user_id,
            exc_info=True,
        )


def apagar_operacao_do_runtime(gateway: object, user_id: str, removida: dict) -> None:
    """Tira do runtime a ordem apagada no Shift+O: memória, stop e padrões.

    A memória de padrões é desfeita com a MESMA chave com que foi registrada:
    a da operação que o runtime tinha em memória; se ela já não estiver aqui
    (restart), a chave calculada pelo gateway a partir da linha apagada.
    """
    order_id = str(removida.get("order_id") or "").strip()
    na_memoria = gateway.auto_trader.mark_trade_removed(user_id, order_id, removida)  # type: ignore[attr-defined]
    padroes = getattr(gateway, "pattern_memory", None)
    esquecer = getattr(padroes, "forget_outcome", None)
    if not callable(esquecer):
        return
    try:
        if isinstance(na_memoria, dict) and na_memoria.get("result"):
            esquecer(user_id, na_memoria)
        elif removida.get("pattern_key") and not removida.get("is_gale"):
            esquecer(
                user_id,
                key=str(removida["pattern_key"]),
                result=str(removida.get("result") or ""),
                profit=float(removida.get("profit") or 0),
            )
    except Exception:
        logger.warning("[PATTERN_MEMORY_FORGET_FAILED] user_id=%s order_id=%s", user_id, order_id, exc_info=True)


def _begin_masaniello_cycle(gateway: object, user_id: str) -> None:
    """Confere o ciclo do Gerenciamento Consistente depois de recarregar o estado.

    O gateway abre (ou continua) o ciclo no "Iniciar" com a visão dele, que
    pode estar atrasada. O runtime é quem lança resultado no ciclo, então a
    última palavra é daqui: mesma função, idempotente.

    Args:
        gateway: Módulo ``backend.main`` carregado no runtime.
        user_id: Cliente alvo.
    """
    auto_trader = getattr(gateway, "auto_trader", None)
    begin = getattr(auto_trader, "masaniello_begin_if_needed", None)
    if not callable(begin):
        return
    try:
        antes = getattr(auto_trader.get(user_id), "masaniello_cycle", None)  # type: ignore[union-attr]
        min_entry = gateway.masaniello_min_entry(user_id)  # type: ignore[attr-defined]
        state = begin(user_id, min_entry=min_entry)
        if getattr(state, "masaniello_cycle", None) is not antes:
            gateway.persist_robot(user_id)  # type: ignore[attr-defined]
    except Exception:
        logger.warning("[MASANIELLO_RUNTIME_BEGIN_FAILED] user_id=%s", user_id, exc_info=True)


async def _handle_command(gateway: object, payload: dict) -> None:
    """Aplica start/stop/ensure no auto_trader local do runtime."""
    user_id = str(payload.get("user_id") or "").strip()
    action = str(payload.get("action") or "").strip().lower()
    if not user_id or not action:
        return
    if action in {"start", "ensure"} and not _is_valid_account_user_id(user_id):
        # Guarda contra fixtures/dados de teste com `enabled=true` em produção
        # (incidente 2026-08-18 ~22h18 — ver docs/ROBO_E_SUPORTE.md). Nunca
        # cria worker real para um user_id que não é UUID de sessão autenticada.
        logger.warning(
            "[ROBOT_WORKER_REJECTED_INVALID_USER_ID] user_id=%s action=%s",
            user_id,
            action,
        )
        return
    if action in {"start", "ensure"}:
        # `start` = decisão explícita do cliente, chegou pelo gateway: precisa
        # recarregar. `ensure` = polling do painel a cada ~5s: NÃO pode
        # sobrescrever o placar vivo (ver comentário em _hydrate_user...).
        _hydrate_user_from_persistence(gateway, user_id, force=(action == "start"))
        if action == "start":
            _begin_masaniello_cycle(gateway, user_id)
        # Painel online no gateway → marca ativo no runtime para ensure passar.
        mark = getattr(gateway, "mark_user_active", None)
        if callable(mark):
            mark(user_id)
        gateway.ensure_robot_worker(user_id)  # type: ignore[attr-defined]
        logger.info("[ROBOT_RUNTIME_CMD] action=%s user_id=%s", action, user_id)
    elif action in {"disconnect", "reconnected"}:
        # Só o gateway atende /bullex/disconnect, mas quem publica o snapshot
        # do painel é este processo. Sem propagar, o runtime seguia mandando
        # `connected: true` e o painel voltava para "Conectado" sozinho.
        manual = getattr(gateway, "bullex_manual_disconnect", None)
        if action == "disconnect":
            if manual is not None:
                manual.add(user_id)
            gateway.auto_trader.disconnect_account(user_id)  # type: ignore[attr-defined]
            task = getattr(gateway, "robot_tasks", {}).pop(user_id, None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            # Publica snapshot desconectado AGORA. O loop `_snapshot_publisher`
            # só cobre users em `robot_tasks` — sem isso o Redis fica com o
            # snapshot antigo (connected=true, TTL 600s) e o gateway external
            # serve "Conectado" no painel.
            publish = getattr(gateway, "publish_manual_disconnect_robot_snapshot", None)
            if callable(publish):
                try:
                    publish(user_id)
                except Exception:
                    logger.warning(
                        "[ROBOT_RUNTIME_DISCONNECT_SNAPSHOT_FAILED] user_id=%s",
                        user_id,
                        exc_info=True,
                    )
        elif manual is not None:
            manual.discard(user_id)
        logger.info("[ROBOT_RUNTIME_CMD] action=%s user_id=%s", action, user_id)
    elif action == "stop":
        # Não force-hydrate: o placar vivo do worker é a fonte de verdade.
        # `stop()` só desliga; wins/losses permanecem.
        _hydrate_user_from_persistence(gateway, user_id, force=False)
        # Cliente parou com gale disparado: a etapa nunca mais entra, então o
        # LOSS da perna perdida precisa entrar no placar agora.
        fechar_gale = getattr(gateway, "close_abandoned_gale_cycle", None)
        if callable(fechar_gale):
            try:
                fechar_gale(user_id)
            except Exception:
                logger.warning(
                    "[ROBOT_RUNTIME_GALE_CLOSE_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
        stop_fn = getattr(gateway.auto_trader, "stop", None)  # type: ignore[attr-defined]
        if callable(stop_fn):
            stop_fn(user_id)
        else:
            state = gateway.auto_trader.get(user_id)  # type: ignore[attr-defined]
            state.enabled = False
        # Remove do dict ANTES do snapshot: o loop `_snapshot_publisher` senão
        # republica worker_running=true por cima do stop do gateway.
        task = getattr(gateway, "robot_tasks", {}).pop(user_id, None)
        publish = getattr(gateway, "publish_robot_control_snapshot", None)
        if callable(publish):
            try:
                publish(user_id, worker_running=False)
            except Exception:
                logger.warning(
                    "[ROBOT_RUNTIME_STOP_SNAPSHOT_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
        if task is not None and not task.done():
            task.cancel()
            # Não prender o listener no cancel (Bullex pode levar vários segundos).
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=2.0)
        logger.info("[ROBOT_RUNTIME_CMD] action=stop user_id=%s", user_id)
    elif action == "reset_score":
        # Gateway já zerou + persistiu; aqui só alinha a memória do runtime
        # para o `_snapshot_publisher` não republicar wins/losses antigos.
        # Não hidratar da persistência: a memória viva pode ter placar à
        # frente da DB; `reset_score` zera o que está na sessão agora.
        reset_fn = getattr(gateway.auto_trader, "reset_score", None)  # type: ignore[attr-defined]
        if callable(reset_fn):
            reset_fn(user_id)
        # Marca de baixa intencional: 0-0 marcado substitui a marca de uma
        # exclusão anterior E autoriza a gravação do zero nos dois processos —
        # o `persist_robot` passou a recusar rebaixamento sem marca.
        mark_authority = getattr(gateway, "mark_session_score_authority", None)
        if callable(mark_authority):
            mark_authority(user_id, 0, 0, 0.0)
        publish = getattr(gateway, "publish_robot_control_snapshot", None)
        if callable(publish):
            try:
                publish(user_id)
            except Exception:
                logger.warning(
                    "[ROBOT_RUNTIME_RESET_SCORE_SNAPSHOT_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
        logger.info("[ROBOT_RUNTIME_CMD] action=reset_score user_id=%s", user_id)
    elif action == "live_mode":
        # O gateway atende POST /robot/live-mode, mas a memória que o motor lê
        # (`analyze_signal(live_demo=...)`) é a deste processo. Sem aplicar aqui
        # o modo nunca ligava com o robô rodando, e o persist do runtime
        # sobrescrevia o True do gateway.
        state = gateway.auto_trader.get(user_id)  # type: ignore[attr-defined]
        state.live_demo = bool(payload.get("enabled"))
        persist = getattr(gateway, "persist_robot", None)
        if callable(persist):
            try:
                persist(user_id)
            except Exception:
                logger.warning(
                    "[ROBOT_RUNTIME_LIVE_MODE_PERSIST_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
        publish = getattr(gateway, "publish_robot_control_snapshot", None)
        if callable(publish):
            try:
                publish(user_id)
            except Exception:
                logger.warning(
                    "[ROBOT_RUNTIME_LIVE_MODE_SNAPSHOT_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
        logger.warning(
            "[ROBOT_RUNTIME_CMD] action=live_mode user_id=%s enabled=%s",
            user_id,
            state.live_demo,
        )
    elif action == "study_mode":
        # Modo Estudo: espelho do `live_mode` — sem aplicar aqui, o persist do
        # runtime sobrescreveria o valor do gateway.
        state = gateway.auto_trader.get(user_id)  # type: ignore[attr-defined]
        state.study_mode = bool(payload.get("enabled"))
        persist = getattr(gateway, "persist_robot", None)
        if callable(persist):
            try:
                persist(user_id)
            except Exception:
                logger.warning(
                    "[ROBOT_RUNTIME_STUDY_MODE_PERSIST_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
        publish = getattr(gateway, "publish_robot_control_snapshot", None)
        if callable(publish):
            try:
                publish(user_id)
            except Exception:
                logger.warning(
                    "[ROBOT_RUNTIME_STUDY_MODE_SNAPSHOT_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
        logger.warning(
            "[ROBOT_RUNTIME_CMD] action=study_mode user_id=%s enabled=%s",
            user_id,
            state.study_mode,
        )
    elif action == "apply_score":
        # Shift+O atualiza o placar no gateway; o publisher do runtime
        # republicaria 0-0 a cada 1s se a memória daqui não acompanhar.
        from backend.auto_trader import set_display_score

        state = gateway.auto_trader.get(user_id)  # type: ignore[attr-defined]
        removida = payload.get("removed_trade")
        try:
            wins = int(payload.get("wins") or 0)
            losses = int(payload.get("losses") or 0)
            profit = float(payload.get("profit") or 0)
        except (TypeError, ValueError):
            logger.warning(
                "[ROBOT_RUNTIME_APPLY_SCORE_INVALID] user_id=%s payload=%s",
                user_id,
                payload,
            )
            return
        if isinstance(removida, dict) and removida.get("order_id"):
            # Ordem REAL apagada no Shift+O some de tudo (decisão do dono,
            # 30/09): placar real (sem `stop_offset_*`, então o stop também
            # deixa de contar), histórico em memória e memória de padrões.
            apagar_operacao_do_runtime(gateway, user_id, removida)
            state.wins, state.losses, state.profit = wins, losses, round(profit, 2)
        else:
            # Placar do Shift+O é vitrine: a diferença vai para
            # `stop_offset_*` e o stop segue contando só ordem real. Sem isso
            # um "gerar placar" 8x2 disparava STOP_WIN_HIT (10/09 19:53).
            set_display_score(state, wins, losses, profit)
        # Exclusão do Shift+O: se a ordem apagada é o `last_trade` daqui, marca
        # para o `persist_robot` abaixo não recriá-la no espelho robot_trades.
        apagada = getattr(gateway, "is_deleted_order", None)
        ultima = str((getattr(state, "last_trade", None) or {}).get("order_id") or "").strip()
        if ultima and callable(apagada):
            try:
                if apagada(user_id, ultima):
                    gateway.auto_trader.mark_trade_removed(user_id, ultima)  # type: ignore[attr-defined]
            except Exception:
                logger.warning(
                    "[ROBOT_RUNTIME_DELETED_ORDER_CHECK_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
        # Marca a baixa também aqui: o `_snapshot_publisher` roda 1x/s e
        # republicaria o placar antigo se um restore/reconcile promovesse a
        # persistência atrasada antes do próximo comando.
        mark_authority = getattr(gateway, "mark_session_score_authority", None)
        if callable(mark_authority):
            try:
                mark_authority(user_id, state.wins, state.losses, state.profit)
            except Exception:
                logger.warning(
                    "[ROBOT_RUNTIME_APPLY_SCORE_AUTHORITY_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
        persist = getattr(gateway, "persist_robot", None)
        if callable(persist):
            try:
                persist(user_id)
            except Exception:
                logger.warning(
                    "[ROBOT_RUNTIME_APPLY_SCORE_PERSIST_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
        publish = getattr(gateway, "publish_robot_control_snapshot", None)
        if callable(publish):
            try:
                # Não reconciliar com Redis antigo: senão a baixa de exclusão
                # marketing (5x3 → 4x3) era desfeita no snapshot do runtime.
                publish(user_id, trust_local_score=True)
            except TypeError:
                try:
                    publish(user_id)
                except Exception:
                    logger.warning(
                        "[ROBOT_RUNTIME_APPLY_SCORE_SNAPSHOT_FAILED] user_id=%s",
                        user_id,
                        exc_info=True,
                    )
            except Exception:
                logger.warning(
                    "[ROBOT_RUNTIME_APPLY_SCORE_SNAPSHOT_FAILED] user_id=%s",
                    user_id,
                    exc_info=True,
                )
        logger.info(
            "[ROBOT_RUNTIME_CMD] action=apply_score user_id=%s wins=%s losses=%s profit=%s",
            user_id,
            state.wins,
            state.losses,
            state.profit,
        )


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


MEMORY_REPORT_INTERVAL_SECONDS = 600.0


def _rss_mb() -> float:
    """Memória residente do processo em MB (lida de /proc/self/status)."""
    try:
        for linha in open("/proc/self/status", encoding="ascii"):
            if linha.startswith("VmRSS:"):
                return round(int(linha.split()[1]) / 1024, 1)
    except OSError:
        pass
    return -1.0


async def _memory_reporter(gateway: object, stop: asyncio.Event) -> None:
    """A cada 10 min: poda o cache de mercado e registra memória e caches.

    Em 29–30/09 o runtime cresceu ~290 MB/h até o kernel matar processos por
    falta de memória, e não havia uma linha de log que mostrasse a curva. Com
    ``ROBOT_TRACEMALLOC=1`` o relatório traz também as linhas que mais cresceram
    desde o anterior (custa CPU; ligar só para investigar).
    """
    import tracemalloc

    rastrear = os.getenv("ROBOT_TRACEMALLOC", "").strip() in {"1", "true", "yes"}
    anterior = None
    if rastrear:
        tracemalloc.start(10)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=MEMORY_REPORT_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass
        try:
            removidas = gateway.prune_market_caches(force=True)  # type: ignore[attr-defined]
            logger.warning(
                "[RUNTIME_MEMORY] rss_mb=%s workers=%s podadas=%s caches=%s",
                _rss_mb(),
                len(getattr(gateway, "robot_tasks", {}) or {}),
                removidas,
                gateway.market_cache_sizes(),  # type: ignore[attr-defined]
            )
            if rastrear:
                foto = tracemalloc.take_snapshot()
                if anterior is not None:
                    for linha in foto.compare_to(anterior, "lineno")[:10]:
                        logger.warning("[RUNTIME_MEMORY_GROWTH] %s", linha)
                anterior = foto
        except Exception:
            logger.warning("[RUNTIME_MEMORY_REPORT_FAILED]", exc_info=True)


def _ainda_pode_fechar(trade: dict, margem_segundos: int = 180) -> bool:
    """A vela desta ordem ainda não fechou (ou fechou agora há pouco)?

    Importa porque a corretora só sabe o resultado enquanto a sessão viva tem a
    ordem em memória (`socket_option_closed`/`order_binary` do bullex-service):
    se a vela fecha sem ninguém ouvindo, o resultado é **irrecuperável** por
    esse caminho. Medido em 15/09: ordem de 5 horas antes ainda respondia
    `PENDING_RESULT`.

    Args:
        trade: Operação como foi enviada.
        margem_segundos: Folga depois da expiração.

    Returns:
        True se vale religar o monitor em vez de desistir.
    """
    from datetime import datetime, timezone

    bruto = trade.get("expires_at") or trade.get("expected_expire_at")
    if not bruto:
        return False
    try:
        expira = datetime.fromisoformat(str(bruto).replace("Z", "+00:00"))
    except ValueError:
        return False
    if expira.tzinfo is None:
        expira = expira.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - expira).total_seconds() < margem_segundos


ORPHAN_SCAN_INTERVAL_SECONDS = 300.0
ORPHAN_GIVE_UP_MINUTES = 60


def _idade_minutos(trade: dict) -> float | None:
    from datetime import datetime, timezone

    bruto = trade.get("sent_at") or trade.get("opened_at") or trade.get("created_at")
    try:
        enviada = datetime.fromisoformat(str(bruto).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if enviada.tzinfo is None:
        enviada = enviada.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - enviada).total_seconds() / 60


async def _reconciliar_orfas_periodicamente(gateway: object, stop: asyncio.Event) -> None:
    """Procura órfãs a cada 5 min, não só no boot.

    Em 29/09 uma ordem perdeu o resultado com o runtime no ar há horas: a
    recuperação só rodava no boot e a linha ficou PENDENTE até sumir do
    relatório na virada do dia.
    """
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=ORPHAN_SCAN_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass
        try:
            await _recuperar_ordens_orfas(gateway, horas=24, periodica=True)
        except Exception:
            logger.warning("[ORPHAN_TRADE_PERIODIC_FAILED]", exc_info=True)


async def _recuperar_ordens_orfas(gateway: object, horas: int = 6, *, periodica: bool = False) -> None:
    """Busca o resultado das ordens que ficaram abertas quando o processo caiu.

    O monitor de resultado (`TradeResultMonitor`) é um ``asyncio.Task``: deploy,
    restart ou queda no meio da vela mata o monitor e ninguém mais busca o
    resultado. A linha fica ``PENDING_RESULT`` para sempre — fora do placar e
    fora do Histórico. Foram 35 órfãs entre 01 e 15/09, **15 num único dia de
    deploy**. Ver docs/PLACAR_DIAGNOSTICO_2026-09-15.md §F6.

    Faz UMA tentativa por ordem, não religa o monitor de 35 minutos: se o
    cliente está desconectado da corretora não adianta insistir, e o próximo
    boot (ou o painel) tenta de novo.

    Args:
        gateway: Módulo ``backend.main``.
        horas: Janela para trás.
    """
    persistence = getattr(gateway, "robot_persistence", None)
    carregar = getattr(persistence, "load_pending_trades", None)
    if not callable(carregar):
        return
    try:
        pendentes = carregar(horas)
    except Exception:
        logger.warning("[ORPHAN_TRADE_SCAN_FAILED]", exc_info=True)
        return
    if not pendentes:
        logger.info("[ORPHAN_TRADE_SCAN] pendentes=0 janela_horas=%s", horas)
        return
    (logger.info if periodica else logger.warning)(
        "[ORPHAN_TRADE_SCAN] pendentes=%s janela_horas=%s", len(pendentes), horas
    )
    buscar = getattr(gateway, "fetch_trade_result", None)
    normalizar = getattr(gateway, "normalize_trade_result", None)
    finalizar = getattr(gateway, "finish_monitored_trade", None)
    if not all(callable(x) for x in (buscar, normalizar, finalizar)):
        return
    finais_por_usuario: dict[str, dict[str, dict]] = {}
    monitor = getattr(gateway, "trade_result_monitor", None)
    for user_id, trade in pendentes:
        order_id = str(trade.get("order_id") or "").strip()
        if not order_id or not _is_valid_account_user_id(user_id):
            continue
        if periodica:
            # Ordem com monitor vivo ou que ainda está na vela é caso normal.
            acompanhando = getattr(monitor, "is_monitoring", None)
            if callable(acompanhando) and acompanhando(user_id, order_id):
                continue
            if _ainda_pode_fechar(trade):
                continue
        # Linha pendente com resultado já no Histórico não é órfã: é o espelho
        # que foi regravado por cima (28/09). Fechar de novo contaria o WIN
        # duas vezes — só corrige a linha do espelho.
        if user_id not in finais_por_usuario:
            try:
                finais_por_usuario[user_id] = {
                    str(item.get("order_id") or "").strip(): item
                    for item in persistence.load_trade_history(user_id, 2)
                    if str(item.get("result") or "").upper() in {"WIN", "LOSS", "DRAW"}
                }
            except Exception:
                logger.warning(
                    "[ORPHAN_TRADE_HISTORY_READ_FAILED] user_id=%s", user_id, exc_info=True
                )
                finais_por_usuario[user_id] = {}
        final = finais_por_usuario[user_id].get(order_id)
        if final is not None:
            try:
                persistence.save_trade(user_id, {**trade, **final})
            except Exception:
                logger.warning(
                    "[ORPHAN_TRADE_MIRROR_REPAIR_FAILED] user_id=%s order_id=%s",
                    user_id,
                    order_id,
                    exc_info=True,
                )
            logger.warning(
                "[ORPHAN_TRADE_ALREADY_FINAL] user_id=%s order_id=%s result=%s",
                user_id,
                order_id,
                final.get("result"),
            )
            continue
        try:
            _, payload = await asyncio.wait_for(buscar(user_id, order_id), timeout=20)
            resultado = normalizar(payload)
        except Exception as erro:  # noqa: BLE001
            logger.warning(
                "[ORPHAN_TRADE_FETCH_FAILED] user_id=%s order_id=%s erro=%s",
                user_id,
                order_id,
                erro.__class__.__name__,
            )
            continue
        if resultado is None or resultado[0] not in {"WIN", "LOSS", "DRAW"}:
            # Ordem que ainda não fechou: desistir aqui é perder o resultado
            # para sempre, porque a corretora só o entrega no momento do
            # fechamento. Religa o monitor para estar ouvindo na hora.
            if _ainda_pode_fechar(trade):
                monitor = getattr(gateway, "trade_result_monitor", None)
                iniciar = getattr(monitor, "start", None)
                if callable(iniciar) and iniciar(
                    user_id,
                    order_id,
                    trade.get("expires_at") or trade.get("expected_expire_at"),
                ):
                    logger.warning(
                        "[ORPHAN_TRADE_MONITOR_RESTARTED] user_id=%s order_id=%s expira=%s",
                        user_id,
                        order_id,
                        trade.get("expires_at") or trade.get("expected_expire_at"),
                    )
                    continue
            logger.warning(
                "[ORPHAN_TRADE_STILL_UNKNOWN] user_id=%s order_id=%s resultado=%s",
                user_id,
                order_id,
                None if resultado is None else resultado[0],
            )
            idade = _idade_minutos(trade)
            if periodica and idade is not None and idade > ORPHAN_GIVE_UP_MINUTES:
                # Nem a corretora respondeu em 1 h: fecha como TIMEOUT e avisa
                # em ERROR (vira e-mail) para alguém conferir e lançar.
                fechar = getattr(gateway, "marcar_timeout_no_espelho", None)
                if callable(fechar):
                    fechar(user_id, order_id)
            continue
        nome, lucro = resultado
        try:
            await finalizar(user_id, order_id, nome, lucro)
            logger.warning(
                "[ORPHAN_TRADE_RECOVERED] user_id=%s order_id=%s result=%s profit=%s",
                user_id,
                order_id,
                nome,
                lucro,
            )
        except Exception:
            logger.warning(
                "[ORPHAN_TRADE_RECOVER_FAILED] user_id=%s order_id=%s",
                user_id,
                order_id,
                exc_info=True,
            )


# Religar no boot quem estava ligado (28/09/2026). Desde 18/08 o boot não subia
# worker nenhum: o painel seguia dizendo "ligado" e a conta ficava parada em
# silêncio até alguém abrir o painel. A conta do dono (81c49f33, mercado
# aberto) ficou de 27/09 08:54 UTC — restart do runtime — até 28/09 09:36 sem
# worker, com o mercado aberto pagando para as outras contas; be431b01 e
# b2ddb2c4 idem. O que derrubou a religação automática em 18/08 foram 35
# usuários de teste (`user-demo`...) com enabled=true: aqui só entra UUID,
# só quem operou nos últimos dias, e um por vez, pelo mesmo `ensure` do painel
# (preserva o placar; conta desligada ou em stop é ignorada lá dentro).
# `ROBOT_BOOT_RESUME=false` no .env desliga sem deploy.
BOOT_RESUME_ENABLED = os.getenv("ROBOT_BOOT_RESUME", "true").strip().lower() in {"1", "true", "yes"}
BOOT_RESUME_MAX_IDLE_DAYS = float(os.getenv("ROBOT_BOOT_RESUME_MAX_IDLE_DAYS", "7"))
BOOT_RESUME_SPACING_SECONDS = 2.0


def _contas_para_religar(gateway: object, agora: object) -> list[str]:
    """Contas que estavam ligadas antes do restart e devem voltar sozinhas.

    Args:
        gateway: Módulo ``backend.main`` já com os estados restaurados.
        agora: ``datetime`` UTC de referência.

    Returns:
        ``user_id`` reais com ``enabled=True`` e última entrada há no máximo
        ``BOOT_RESUME_MAX_IDLE_DAYS`` dias (sem entrada nenhuma também entra:
        é quem ligou e ainda não operou). Contas abandonadas ficam de fora.
    """
    from datetime import timedelta

    auto_trader = getattr(gateway, "auto_trader", None)
    restauradas = getattr(gateway, "restorable_robot_states", {}) or {}
    if auto_trader is None:
        return []
    limite = agora - timedelta(days=BOOT_RESUME_MAX_IDLE_DAYS)  # type: ignore[operator]
    contas: list[str] = []
    for user_id in list(restauradas):
        if not _is_valid_account_user_id(user_id) or not auto_trader.has_state(user_id):
            continue
        state = auto_trader.get(user_id)
        if not getattr(state, "enabled", False):
            continue
        ultima = getattr(state, "last_entry_at", None)
        if ultima is not None and ultima.tzinfo is None:
            ultima = ultima.replace(tzinfo=limite.tzinfo)
        if ultima is not None and ultima < limite:
            logger.info(
                "[BOOT_RESUME_SKIPPED] user_id=%s reason=inativa ultima_entrada=%s",
                user_id,
                ultima,
            )
            continue
        contas.append(user_id)
    return contas


async def _religar_quem_estava_ligado(gateway: object, stop: asyncio.Event) -> None:
    """Manda ``ensure`` para cada conta de ``_contas_para_religar``, uma por vez."""
    from datetime import datetime, timezone

    if not BOOT_RESUME_ENABLED:
        logger.warning("[BOOT_RESUME_DISABLED] ROBOT_BOOT_RESUME=false")
        return
    contas = _contas_para_religar(gateway, datetime.now(timezone.utc))
    logger.warning("[BOOT_RESUME_START] contas=%s", len(contas))
    for indice, user_id in enumerate(contas):
        if stop.is_set():
            return
        if indice:
            await asyncio.sleep(BOOT_RESUME_SPACING_SECONDS)
        try:
            await _handle_command(gateway, {"action": "ensure", "user_id": user_id})
            logger.warning("[BOOT_RESUME_ENSURE] user_id=%s", user_id)
        except Exception:
            logger.warning("[BOOT_RESUME_FAILED] user_id=%s", user_id, exc_info=True)
    logger.warning("[BOOT_RESUME_DONE] contas=%s", len(contas))


async def amain() -> None:
    """Boot do robot-runtime."""
    os.environ.setdefault("ROBOT_RUNTIME_MODE", "worker")
    # Import tardio: carrega gateway module (estado/auto_trader) sem uvicorn.
    from backend import main as gateway
    from backend.robot_bus import RobotBus

    logger.info("[ROBOT_RUNTIME_START] mode=%s", os.getenv("ROBOT_RUNTIME_MODE"))
    # Reusa a restauração de estados do startup HTTP.
    await gateway.restore_robot_states()
    # Ordens que ficaram abertas quando este processo caiu (deploy no meio da
    # vela): busca o resultado uma vez, senão elas nunca entram no placar.
    await _recuperar_ordens_orfas(gateway)
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
    religar = asyncio.create_task(_religar_quem_estava_ligado(gateway, stop), name="boot-resume")
    memoria = asyncio.create_task(_memory_reporter(gateway, stop), name="memory-reporter")
    orfas = asyncio.create_task(_reconciliar_orfas_periodicamente(gateway, stop), name="orphan-scan")
    # Denuncia no log qualquer chamada síncrona que trave o loop (ver
    # backend/loop_watchdog.py — incidente de entradas atrasadas de 10/09).
    from backend.loop_watchdog import EVENT_LOOP_WATCHDOG_ENABLED, EventLoopWatchdog

    watchdog = (
        asyncio.create_task(EventLoopWatchdog(logger).run(stop), name="event-loop-watchdog")
        if EVENT_LOOP_WATCHDOG_ENABLED
        else None
    )
    await stop.wait()
    listener.cancel()
    publisher.cancel()
    religar.cancel()
    memoria.cancel()
    orfas.cancel()
    if watchdog is not None:
        watchdog.cancel()
    await gateway.shutdown_robot_workers()
    bus.close()
    logger.info("[ROBOT_RUNTIME_STOP]")


def main() -> None:
    """Entry point ``python -m backend.robot_runtime_main``."""
    asyncio.run(amain())


if __name__ == "__main__":
    main()
