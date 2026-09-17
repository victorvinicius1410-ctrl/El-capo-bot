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
    live_score = None
    has_state = getattr(auto_trader, "has_state", None)
    if callable(has_state) and has_state(user_id):
        if not force:
            return
        live_score = _capture_live_session_score(auto_trader, user_id)
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
            if live_score is not None:
                _prefer_live_session_score(auto_trader, user_id, live_score)
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
        try:
            # Placar do Shift+O é vitrine: a diferença vai para
            # `stop_offset_*` e o stop segue contando só ordem real. Sem isso
            # um "gerar placar" 8x2 disparava STOP_WIN_HIT (10/09 19:53).
            set_display_score(
                state,
                int(payload.get("wins") or 0),
                int(payload.get("losses") or 0),
                float(payload.get("profit") or 0),
            )
        except (TypeError, ValueError):
            logger.warning(
                "[ROBOT_RUNTIME_APPLY_SCORE_INVALID] user_id=%s payload=%s",
                user_id,
                payload,
            )
            return
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


async def _recuperar_ordens_orfas(gateway: object, horas: int = 6) -> None:
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
    logger.warning(
        "[ORPHAN_TRADE_SCAN] pendentes=%s janela_horas=%s", len(pendentes), horas
    )
    buscar = getattr(gateway, "fetch_trade_result", None)
    normalizar = getattr(gateway, "normalize_trade_result", None)
    finalizar = getattr(gateway, "finish_monitored_trade", None)
    if not all(callable(x) for x in (buscar, normalizar, finalizar)):
        return
    for user_id, trade in pendentes:
        order_id = str(trade.get("order_id") or "").strip()
        if not order_id or not _is_valid_account_user_id(user_id):
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
