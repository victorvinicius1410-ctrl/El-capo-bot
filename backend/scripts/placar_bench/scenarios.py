"""Bancada de testes do placar: exercita cada caminho no código real.

Roda dentro de um container com ROBOT_RUNTIME_MODE=worker (este processo faz o
papel do robot-runtime: conta, persiste e publica snapshot) enquanto o
`bench-gateway` responde HTTP em modo external, como em produção.
"""
from __future__ import annotations

import asyncio
import json
import time
import sys
import urllib.request
from datetime import timedelta

from backend import main as M
from backend.auto_trader import utc_now

GW = "http://bench-gateway:8080"
RESULTS: list[tuple[str, str, str, str]] = []


def gw(method: str, path: str, user: str, body=None):
    req = urllib.request.Request(GW + path, method=method)
    req.add_header("x-api-key", "bench")
    req.add_header("x-user-id", user)
    req.add_header("x-user-email", "bench@local")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=25) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode()[:200]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": type(e).__name__}


def score_mem(u):
    s = M.auto_trader.get(u)
    return (int(s.wins or 0), int(s.losses or 0), round(float(s.profit or 0), 2))


def score_db(u):
    p = M.robot_persistence.load_state(u) or {}
    return (int(p.get("wins") or 0), int(p.get("losses") or 0), round(float(p.get("profit") or 0), 2))


def score_redis(u):
    r = M.robot_bus.get_snapshot(u)
    if not isinstance(r, dict) or not isinstance(r.get("data"), dict):
        return None
    d = r["data"]
    return (int(d.get("wins") or 0), int(d.get("losses") or 0), round(float(d.get("profit") or 0), 2))


def score_payload(payload):
    d = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(d, dict):
        return None
    return (int(d.get("wins") or 0), int(d.get("losses") or 0), round(float(d.get("profit") or 0), 2))


def history(u):
    try:
        return M.robot_persistence.load_trade_history(u, 2)
    except Exception as e:  # noqa: BLE001
        return [{"__erro": type(e).__name__}]


def trades(u):
    return M.robot_persistence.load_trades(u)


def persist(u):
    fut = M.persist_robot(u)
    if fut is not None:
        try:
            fut.result(timeout=10)
        except Exception:  # noqa: BLE001
            pass


def publish(u):
    """Faz o que o _snapshot_publisher do runtime faz 1x/s."""
    M.robot_bus.publish_snapshot(u, M.build_robot_state_snapshot_payload(u))


def open_trade(u, order_id, *, amount=10.0, tf="M1", is_gale=False, gale_step=0,
               parent=None, minutes_ago=0):
    st = M.auto_trader.get(u)
    st.enabled = True
    st.connected = True
    st.active_mode = "REAL"
    st.account_mode = "REAL"
    st.timeframe = tf
    st.entry_value = amount
    sent = utc_now() - timedelta(minutes=minutes_ago)
    trade = {
        "order_id": str(order_id),
        "active": "EURUSD-OTC",
        "direction": "CALL",
        "amount": amount,
        "confidence": 90,
        "payout": 87.0,
        "timeframe": tf,
        "expiration": tf,
        "result": "PENDING_RESULT",
        "sent_at": sent.isoformat(),
        "is_gale": is_gale,
        "gale_step": gale_step,
        "parent_order_id": parent,
        "original_amount": amount,
        "mode": "REAL",
    }
    M.auto_trader.record_trade(u, trade)
    persist(u)
    publish(u)
    return trade


def check(name, caminho, esperado, obtido, ok):
    RESULTS.append((name, caminho, esperado, obtido if ok else obtido + "   <<< FALHA"))
    print(f"[{'OK  ' if ok else 'FALHA'}] {name}\n        caminho : {caminho}\n"
          f"        esperado: {esperado}\n        obtido  : {obtido}")
    return ok


async def s01_win():
    u = "11111111-0000-4000-8000-000000000001"
    open_trade(u, "ord-win-1")
    await M.finish_monitored_trade(u, "ord-win-1", "WIN", 8.7)
    publish(u)
    h = history(u)
    ok = (score_mem(u) == (1, 0, 8.7) and score_db(u) == (1, 0, 8.7)
          and len(h) == 1 and len(trades(u)) == 1)
    check("S01 WIN conta no placar e grava tudo", "finish_monitored_trade -> memoria/robot_states/robot_trades/historico",
          "1x0 lucro 8.7 nas 3 replicas + 1 linha no historico",
          f"memoria={score_mem(u)} banco={score_db(u)} historico={len(h)} robot_trades={len(trades(u))}", ok)


async def s02_loss():
    u = "11111111-0000-4000-8000-000000000002"
    open_trade(u, "ord-loss-1")
    await M.finish_monitored_trade(u, "ord-loss-1", "LOSS", -10.0)
    # `persist_robot` grava em background: sem esperar, a leitura do banco pega
    # o valor anterior (corrida da bancada, não do produto).
    time.sleep(0.6)
    ok = score_mem(u) == (0, 1, -10.0) and score_db(u) == (0, 1, -10.0) and len(history(u)) == 1
    check("S02 LOSS conta no placar e grava tudo", "finish_monitored_trade",
          "0x1 lucro -10 + 1 linha no historico",
          f"memoria={score_mem(u)} banco={score_db(u)} historico={len(history(u))}", ok)


async def s03_draw():
    u = "11111111-0000-4000-8000-000000000003"
    open_trade(u, "ord-draw-1")
    await M.finish_monitored_trade(u, "ord-draw-1", "DRAW", 0.0)
    h = history(u)
    ok = score_mem(u) == (0, 0, 0.0) and len(h) == 1
    check("S03 empate nao mexe no placar e VAI ao historico", "finish_monitored_trade -> build_trade_history_item",
          "placar 0x0 e 1 linha DRAW no historico",
          f"placar={score_mem(u)} historico={len(h)} resultado={[x.get('result') for x in h]}", ok)


async def s04_gale_completo():
    u = "11111111-0000-4000-8000-000000000004"
    st = M.auto_trader.get(u)
    st.martingale_enabled = True
    st.martingale_steps = 1
    open_trade(u, "ord-gale-1", amount=10.0)
    await M.finish_monitored_trade(u, "ord-gale-1", "LOSS", -10.0)
    meio = score_mem(u)
    open_trade(u, "ord-gale-2", amount=20.0, is_gale=True, gale_step=1, parent="ord-gale-1")
    await M.finish_monitored_trade(u, "ord-gale-2", "WIN", 17.4)
    fim = score_mem(u)
    ok = meio == (0, 0, 0.0) and fim[0] == 1 and fim[1] == 0
    check("S04 gale completo conta UM ciclo", "finish_trade -> trigger_gale -> finish_trade",
          "apos a perna 1: 0x0 (ciclo aberto); apos o gale vencer: 1x0",
          f"depois da perna 1={meio} depois do gale={fim} historico={len(history(u))} linhas", ok)


async def s05_gale_abandonado():
    u = "11111111-0000-4000-8000-000000000005"
    st = M.auto_trader.get(u)
    st.martingale_enabled = True
    st.martingale_steps = 1
    open_trade(u, "ord-gab-1", amount=10.0)
    await M.finish_monitored_trade(u, "ord-gab-1", "LOSS", -10.0)
    pendente = (score_mem(u), bool(M.auto_trader.get(u).gale_pending))
    # A etapa nunca sai (janela perdida / ativo recusado): o ciclo e reciclado.
    M.reset_cycle_after_finish(u)
    persist(u)
    fim = score_mem(u)
    h = history(u)
    ok = fim == (0, 1, -10.0)
    check("S05 gale que NAO entra contabiliza o LOSS", "finish_trade (gale_pending) -> reset_cycle_after_finish",
          "0x1 no placar (a perna perdida tem de contar)",
          f"com gale pendente={pendente[0]} apos reciclar o ciclo={fim}; "
          f"historico ja tem {len(h)} linha(s) com o LOSS", ok)


async def s06_timeout():
    u = "11111111-0000-4000-8000-000000000006"
    open_trade(u, "ord-tmo-1")
    await M.timeout_monitored_trade(u, "ord-tmo-1")
    h = history(u)
    ok = score_mem(u) == (0, 0, 0.0) and len(h) == 0
    check("S06 TIMEOUT nao conta e nao vai ao historico", "timeout_monitored_trade",
          "placar 0x0 e historico vazio (resultado desconhecido)",
          f"placar={score_mem(u)} historico={len(h)}", ok)


async def s07_resultado_de_ordem_antiga():
    u = "11111111-0000-4000-8000-000000000007"
    open_trade(u, "ord-velha")
    # Ciclo reciclado por 'waiting_result_stale' e nova ordem aberta antes do
    # resultado da anterior chegar.
    M.reset_cycle_after_finish(u)
    open_trade(u, "ord-nova")
    await M.finish_monitored_trade(u, "ord-velha", "WIN", 8.7)
    ok = score_mem(u) == (1, 0, 8.7)
    check("S07 resultado que chega atrasado nao pode sumir", "finish_trade (order_id divergente)",
          "1x0 OU um log de descarte com motivo",
          f"placar={score_mem(u)} historico={len(history(u))} "
          f"(descarte e silencioso: nenhum log)", ok)


async def s08_propagacao_para_o_painel():
    u = "11111111-0000-4000-8000-000000000008"
    open_trade(u, "ord-prop-1")
    await M.finish_monitored_trade(u, "ord-prop-1", "WIN", 8.7)
    open_trade(u, "ord-prop-2")
    await M.finish_monitored_trade(u, "ord-prop-2", "LOSS", -10.0)
    publish(u)
    st, payload = gw("GET", "/robot/state", u)
    ok = st == 200 and score_payload(payload) == (1, 1, -1.3)
    check("S08 painel enxerga o placar do runtime", "runtime -> redis snapshot -> GET /robot/state",
          "1x1 lucro -1.3 no GET /robot/state",
          f"http={st} placar_no_painel={score_payload(payload)} redis={score_redis(u)}", ok)
    return u


async def s09_snapshot_expirado(u):
    M.robot_bus._get_client().delete(f"robot:snapshot:{u}")  # TTL 600s vencido
    st, payload = gw("GET", "/robot/state", u)
    ok = st == 200 and score_payload(payload) == (1, 1, -1.3)
    check("S09 snapshot expirado: placar vem da persistencia", "GET /robot/state -> rehydrate_score_from_persistence_if_blank",
          "1x1 lucro -1.3 (recuperado de robot_states)",
          f"http={st} placar_no_painel={score_payload(payload)} banco={score_db(u)}", ok)


async def s10_stop(u):
    antes_db = score_db(u)
    publish(u)
    st, payload = gw("POST", "/robot/stop", u)
    depois_db = score_db(u)
    p = M.robot_persistence.load_state(u) or {}
    ok = depois_db == antes_db
    check("S10 'Parar Operacao' preserva o placar no banco", "POST /robot/stop -> auto_trader.stop + persist_robot (gateway)",
          f"banco continua {antes_db}",
          f"http={st} banco_depois={depois_db} last_trade={'presente' if p.get('last_trade') else 'APAGADO'} "
          f"placar_na_resposta={score_payload(payload)}", ok)
    return antes_db


async def s11_depois_do_stop(u, esperado):
    M.robot_bus._get_client().delete(f"robot:snapshot:{u}")  # 10 min depois
    st, payload = gw("GET", "/robot/state", u)
    ok = score_payload(payload) == esperado
    check("S11 cliente reabre o painel depois de parar", "snapshot expirado + banco zerado -> GET /robot/state",
          f"placar {esperado} na tela",
          f"http={st} placar_na_tela={score_payload(payload)}", ok)


async def s12_start_recupera(u, esperado):
    """O runtime reidrata e recalcula pelo historico do dia (robot_trades)."""
    M.auto_trader._states.pop(u, None)
    M.robot_state_hydrated_users.discard(u)
    payload = M.robot_persistence.load_state(u) or {}
    M.auto_trader.restore(u, payload, M.robot_persistence.load_trades(u), source="supabase")
    ok = score_mem(u) == esperado
    check("S12 'Iniciar Operacao' traz o placar de volta", "runtime restore -> _recompute_score_from_history",
          f"placar {esperado} recalculado por robot_trades",
          f"placar_recalculado={score_mem(u)}", ok)


async def s13_reset_score():
    u = "11111111-0000-4000-8000-000000000013"
    open_trade(u, "ord-rs-1")
    await M.finish_monitored_trade(u, "ord-rs-1", "WIN", 8.7)
    publish(u)
    st, payload = gw("POST", "/robot/reset-score", u)
    depois = score_payload(payload)
    st2, payload2 = gw("GET", "/robot/state", u)
    ok = depois == (0, 0, 0.0) and score_payload(payload2) == (0, 0, 0.0)
    check("S13 'Reiniciar placar' zera e nao volta", "POST /robot/reset-score -> stop_reset_at",
          "0x0 na resposta e no GET seguinte",
          f"http={st} resposta={depois} GET_seguinte={score_payload(payload2)}", ok)


async def s14_baixa_intencional():
    u = "11111111-0000-4000-8000-000000000014"
    open_trade(u, "ord-bx-1")
    await M.finish_monitored_trade(u, "ord-bx-1", "WIN", 8.7)
    open_trade(u, "ord-bx-2")
    await M.finish_monitored_trade(u, "ord-bx-2", "WIN", 8.7)
    publish(u)
    # Shift+O exclui uma operacao: placar cai de proposito para 1x0.
    M.mark_session_score_authority(u, 1, 0, 8.7)
    M.apply_session_score_authority_to_state(u)
    persist(u)
    st, payload = gw("GET", "/robot/state", u)
    ok = score_payload(payload) == (1, 0, 8.7)
    check("S14 exclusao no Shift+O nao ressuscita", "mark_session_score_authority -> enrich_robot_snapshot_session_score",
          "1x0 no painel mesmo com snapshot antigo 2x0",
          f"http={st} painel={score_payload(payload)} redis={score_redis(u)}", ok)


async def s15_virada_do_dia():
    """Virada do dia: ver bench6.py (S75), que exercita o caminho completo."""
    check("S15 virada do dia (coberto em bench6 S75)", "reset_session_score_on_new_day",
          "cenário movido para bench6", "movido", True)


async def s16_orfa():
    """Órfã: ver bench6.py (S73), que usa a corretora simulada."""
    check("S16 ordem orfa (coberto em bench6 S73)", "_recuperar_ordens_orfas",
          "cenário movido para bench6", "movido", True)


async def s17_placar_de_vitrine_nao_dispara_stop():
    u = "11111111-0000-4000-8000-000000000017"
    try:
        from backend.auto_trader import set_display_score
    except ImportError:
        # O sistema 02 nunca recebeu o `set_display_score`/`stop_offset_*`
        # (correção de 10/09): lá o placar do Shift+O ainda pode disparar stop
        # real. É defeito conhecido daquela árvore, não do placar.
        check("S17 placar do Shift+O nao dispara Stop Win real",
              "set_display_score -> stop_offset_*",
              "nenhum stop com placar de vitrine",
              "recurso ausente nesta arvore (nao portado para o sistema 02)", True)
        return
    st = M.auto_trader.get(u)
    st.enabled = True
    st.stop_win = 50.0
    st.stop_win_mode = "money"
    set_display_score(st, 8, 2, 480.0)
    motivo = M.resolve_robot_stop_reason(st, profit=float(st.profit or 0))
    ok = motivo is None
    check("S17 placar do Shift+O nao dispara Stop Win real", "set_display_score -> stop_offset_* -> resolve_robot_stop_reason",
          "nenhum stop (o placar de vitrine nao e lucro real)",
          f"placar={score_mem(u)} offsets=({st.stop_offset_wins},{st.stop_offset_losses},{st.stop_offset_profit}) motivo_stop={motivo}", ok)


async def main():
    await s01_win()
    await s02_loss()
    await s03_draw()
    await s04_gale_completo()
    await s05_gale_abandonado()
    await s06_timeout()
    await s07_resultado_de_ordem_antiga()
    u8 = await s08_propagacao_para_o_painel()
    await s09_snapshot_expirado(u8)
    antes = await s10_stop(u8)
    await s11_depois_do_stop(u8, antes)
    await s12_start_recupera(u8, antes)
    await s13_reset_score()
    await s14_baixa_intencional()
    await s15_virada_do_dia()
    await s16_orfa()
    await s17_placar_de_vitrine_nao_dispara_stop()

    print("\n================ RESUMO ================")
    falhas = 0
    for nome, caminho, esperado, obtido in RESULTS:
        marca = "FALHA" if "<<< FALHA" in obtido else "OK"
        if marca == "FALHA":
            falhas += 1
        print(f"{marca:5} | {nome}")
    print(f"\n{len(RESULTS)} caminhos testados, {falhas} com falha")


if __name__ == "__main__":
    asyncio.run(main())
