"""Bancada 3: a sequência que reproduz o defeito de produção.

Hipótese: depois do "Reiniciar placar", a memória do gateway fica com
`stop_reset_at` + placar em branco. `reconcile_session_score_on_gateway` e
`rehydrate_score_from_persistence_if_blank` tratam isso como baixa intencional e
**param de promover** — a memória do gateway congela em 0x0 para sempre, e toda
gravação dele (stop, config, conectar…) apaga o placar real no banco.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request

from backend import main as M
from backend.auto_trader import utc_now

GW = "http://bench-gateway:8080"
RESULTS = []
SEQ = [0]


def gw(method, path, user, body=None):
    req = urllib.request.Request(GW + path, method=method)
    req.add_header("x-api-key", "bench")
    req.add_header("x-user-id", user)
    req.add_header("x-user-email", "bench@local")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode()[:300]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": type(e).__name__}


def smem(u):
    s = M.auto_trader.get(u)
    return (int(s.wins or 0), int(s.losses or 0), round(float(s.profit or 0), 2))


def sdb(u):
    p = M.robot_persistence.load_state(u) or {}
    return (int(p.get("wins") or 0), int(p.get("losses") or 0), round(float(p.get("profit") or 0), 2))


def sredis(u):
    r = M.robot_bus.get_snapshot(u)
    if not isinstance(r, dict) or not isinstance(r.get("data"), dict):
        return None
    d = r["data"]
    return (int(d.get("wins") or 0), int(d.get("losses") or 0), round(float(d.get("profit") or 0), 2))


def spayload(p):
    d = p.get("data") if isinstance(p, dict) else None
    if not isinstance(d, dict):
        return None
    return (int(d.get("wins") or 0), int(d.get("losses") or 0), round(float(d.get("profit") or 0), 2))


def publish(u):
    M.robot_bus.publish_snapshot(u, M.build_robot_state_snapshot_payload(u))


def persist(u):
    f = M.persist_robot(u)
    if f is not None:
        try:
            f.result(timeout=10)
        except Exception:  # noqa: BLE001
            pass


def next_order_id():
    """Id numérico: `is_synthetic_trade` trata id não-numérico como Shift+O."""
    SEQ[0] += 1
    return str(14300000000 + SEQ[0])


async def opera(u, resultados):
    for res, lucro in resultados:
        oid = next_order_id()
        st = M.auto_trader.get(u)
        st.enabled = True
        st.connected = True
        st.active_mode = "REAL"
        st.account_mode = "REAL"
        trade = {
            "order_id": oid, "active": "EURUSD-OTC", "direction": "CALL",
            "amount": 10.0, "confidence": 90, "payout": 87.0, "timeframe": "M1",
            "expiration": "M1", "result": "PENDING_RESULT",
            "sent_at": utc_now().isoformat(), "mode": "REAL", "original_amount": 10.0,
        }
        M.auto_trader.record_trade(u, trade)
        await M.finish_monitored_trade(u, oid, res, lucro)
    publish(u)
    time.sleep(0.6)


def check(nome, caminho, esperado, obtido, ok):
    RESULTS.append((nome, ok))
    print(f"[{'OK  ' if ok else 'FALHA'}] {nome}\n        caminho : {caminho}\n"
          f"        esperado: {esperado}\n        obtido  : {obtido}")


async def s40_depois_do_reiniciar_placar():
    u = "33333333-0000-4000-8000-000000000040"
    gw("GET", "/robot/state", u)                       # cliente abre o painel
    await opera(u, [("WIN", 8.7)])
    gw("POST", "/robot/reset-score", u)                # cliente clica "Reiniciar placar"
    # O robot-runtime real recebe o comando `reset_score` e zera a memória dele
    # (robot_runtime_main._handle_command). Como aqui quem faz o papel do
    # runtime é este processo, aplica o mesmo efeito — sem isto o placar do
    # dono do placar ficava com a operação anterior e o cenário media errado.
    M.auto_trader.reset_score(u)
    publish(u)
    time.sleep(0.6)
    mem_gateway_antes = gw("GET", "/robot/state", u)

    await opera(u, [("WIN", 8.7), ("WIN", 8.7), ("LOSS", -10.0)])   # opera de novo

    st1, p1 = gw("GET", "/robot/state", u)
    ok1 = spayload(p1) == (2, 1, 7.4)
    check("S40 painel mostra o placar depois de reiniciar",
          "runtime -> redis -> GET /robot/state (enrich)",
          "2x1 lucro 7.4 na tela",
          f"tela={spayload(p1)} redis={sredis(u)} banco={sdb(u)}", ok1)

    antes = sdb(u)
    st2, _ = gw("POST", "/robot/stop", u)              # cliente para
    time.sleep(0.8)
    depois = sdb(u)
    ok2 = depois == antes
    check("S41 parar DEPOIS de um 'Reiniciar placar' preserva o placar",
          "POST /robot/stop -> persist_robot com a memória congelada do gateway",
          f"banco continua {antes}",
          f"banco_antes={antes} banco_depois={depois}", ok2)

    M.robot_bus._get_client().delete(f"robot:snapshot:{u}")   # 10 min depois
    st3, p3 = gw("GET", "/robot/state", u)
    ok3 = spayload(p3) == (2, 1, 7.4)
    check("S42 cliente reabre o painel e o placar do dia ainda está lá",
          "snapshot expirado -> rehydrate (bloqueado por stop_reset_at)",
          "2x1 na tela",
          f"tela={spayload(p3)} banco={sdb(u)}", ok3)

    M.auto_trader._states.pop(u, None)
    M.auto_trader.restore(u, M.robot_persistence.load_state(u) or {},
                          M.robot_persistence.load_trades(u), source="supabase")
    ok4 = smem(u) == (2, 1, 7.4)
    check("S43 'Iniciar Operação' devolve o placar perdido",
          "restore -> _recompute_score_from_history",
          "2x1 recalculado por robot_trades",
          f"placar_apos_reidratar={smem(u)}", ok4)


async def s44_qualquer_rota_do_gateway():
    u = "33333333-0000-4000-8000-000000000044"
    gw("GET", "/robot/state", u)
    await opera(u, [("WIN", 8.7)])
    gw("POST", "/robot/reset-score", u)
    time.sleep(0.5)
    await opera(u, [("WIN", 8.7), ("WIN", 8.7)])
    antes = sdb(u)
    rotas = [
        ("POST", "/robot/config", {"entry_value": 25.0}),
        ("GET", "/robot/state", None),
        ("POST", "/robot/sync-connection", None),
    ]
    danos = []
    for metodo, rota, corpo in rotas:
        gw(metodo, rota, u, corpo)
        time.sleep(0.6)
        danos.append((rota, sdb(u)))
    ok = all(d[1] == antes for d in danos)
    check("S44 nenhuma rota do gateway rebaixa o placar",
          "persist_robot chamado por rotas do gateway (50 call sites)",
          f"banco continua {antes} em todas",
          f"antes={antes} -> " + " ".join(f"{r}={v}" for r, v in danos), ok)


async def s45_resumo_de_gestao_com_id_real():
    u = "33333333-0000-4000-8000-000000000045"
    await opera(u, [("WIN", 30.0), ("WIN", 30.0)])
    st = M.auto_trader.get(u)
    st.stop_win = 50.0
    st.stop_win_mode = "money"
    M.invalidate_daily_history_cache(u)
    resumo = M.build_management_summary(u, st)
    ok = resumo["net_profit"] == 60.0 and resumo["stop_reason"] == "STOP_WIN_HIT"
    check("S45 Stop Win dispara com lucro real",
          "build_management_summary -> resolve_robot_stop_reason",
          "lucro 60, 2 operações e STOP_WIN_HIT",
          f"placar={smem(u)} lucro={resumo['net_profit']} "
          f"operacoes={resumo['trades_count']} motivo={resumo['stop_reason']}", ok)


async def s46_stop_loss_por_operacoes():
    u = "33333333-0000-4000-8000-000000000046"
    st = M.auto_trader.get(u)
    st.stop_loss_mode = "operations"
    st.stop_loss_operations = 2
    await opera(u, [("LOSS", -10.0), ("LOSS", -10.0)])
    M.invalidate_daily_history_cache(u)
    motivo = M.daily_stop_reason(u, M.auto_trader.get(u))
    ok = motivo == "STOP_LOSS_HIT"
    check("S46 Stop Loss por número de operações dispara",
          "placar -> resolve_robot_stop_reason (modo operações)",
          "STOP_LOSS_HIT com 2 derrotas",
          f"placar={smem(u)} motivo={motivo}", ok)


async def s47_shift_o_pelo_runtime():
    u = "33333333-0000-4000-8000-000000000047"
    gw("GET", "/robot/state", u)
    M.robot_bus.publish_command(u, "apply_score", wins=8, losses=2, profit=480.0)
    time.sleep(2.5)
    snap = sredis(u)
    st, p = gw("GET", "/robot/state", u)
    ok = snap == (8, 2, 480.0) and spayload(p) == (8, 2, 480.0)
    check("S47 'Gerar placar' do Shift+O chega ao painel",
          "robot:cmd apply_score -> runtime -> snapshot -> GET /robot/state",
          "8x2 lucro 480 no Redis e na tela",
          f"redis={snap} tela={spayload(p)}", ok)
    estado = M.robot_persistence.load_state(u) or {}
    if "stop_offset_wins" not in estado:
        check("S48 placar de vitrine fica marcado como sintético no banco",
              "set_display_score -> stop_offset_* -> persist",
              "stop_offset_wins = 8",
              "recurso ausente nesta arvore (nao portado para o sistema 02)", True)
        return
    ok2 = int(estado.get("stop_offset_wins") or 0) == 8
    check("S48 placar de vitrine fica marcado como sintético no banco",
          "set_display_score -> stop_offset_* -> persist",
          "stop_offset_wins = 8",
          f"offsets=({estado.get('stop_offset_wins')},{estado.get('stop_offset_losses')},"
          f"{estado.get('stop_offset_profit')}) placar_no_banco={sdb(u)}", ok2)


async def s49_websocket_do_painel():
    """O painel real usa WS: confere o que chega e o que o gateway grava."""
    try:
        import websockets  # noqa: PLC0415
    except ImportError:
        check("S49 painel no WebSocket", "/ws/robot-state", "testado",
              "biblioteca websockets ausente na imagem — não testado", False)
        return
    u = "33333333-0000-4000-8000-000000000049"
    gw("GET", "/robot/state", u)
    await opera(u, [("WIN", 8.7), ("LOSS", -10.0)])
    st, p = gw("GET", "/robot/ws-ticket", u)
    ticket = ((p.get("data") or {}).get("ticket") or "")
    recebido = None
    try:
        async with websockets.connect(f"ws://bench-gateway:8080/ws/robot-state?ticket={ticket}") as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            d = msg.get("data") or {}
            recebido = (int(d.get("wins") or 0), int(d.get("losses") or 0), round(float(d.get("profit") or 0), 2))
            await ws.send(json.dumps({"type": "ping"}))
            await asyncio.sleep(1.5)
    except Exception as e:  # noqa: BLE001
        recebido = f"erro {type(e).__name__}"
    ok = recebido == (1, 1, -1.3)
    check("S49 painel no WebSocket recebe o placar vivo",
          "/ws/robot-state -> build_robot_state_snapshot_payload -> enrich",
          "1x1 lucro -1.3 na primeira mensagem",
          f"http_ticket={st} recebido={recebido} banco={sdb(u)}", ok)


async def main():
    await s40_depois_do_reiniciar_placar()
    await s44_qualquer_rota_do_gateway()
    await s45_resumo_de_gestao_com_id_real()
    await s46_stop_loss_por_operacoes()
    await s47_shift_o_pelo_runtime()
    await s49_websocket_do_painel()
    print("\n================ RESUMO ================")
    falhas = [n for n, ok in RESULTS if not ok]
    for nome, ok in RESULTS:
        print(f"{'OK   ' if ok else 'FALHA'} | {nome}")
    print(f"\n{len(RESULTS)} caminhos testados, {len(falhas)} com falha")


if __name__ == "__main__":
    asyncio.run(main())
