"""Bancada 4: caminhos que faltaram — quando o defeito aparece e quando não,
reiniciar ciclo, exclusão do Shift+O, isolamento entre clientes e pausa por stop.
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
SEQ = [500]


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


def spayload(p):
    d = p.get("data") if isinstance(p, dict) else None
    if not isinstance(d, dict):
        return None
    return (int(d.get("wins") or 0), int(d.get("losses") or 0), round(float(d.get("profit") or 0), 2))


def publish(u):
    M.robot_bus.publish_snapshot(u, M.build_robot_state_snapshot_payload(u))


async def opera(u, resultados):
    ids = []
    for res, lucro in resultados:
        SEQ[0] += 1
        oid = str(14300000000 + SEQ[0])
        ids.append(oid)
        st = M.auto_trader.get(u)
        st.enabled = True
        st.connected = True
        st.active_mode = "REAL"
        st.account_mode = "REAL"
        M.auto_trader.record_trade(u, {
            "order_id": oid, "active": "EURUSD-OTC", "direction": "CALL",
            "amount": 10.0, "confidence": 90, "payout": 87.0, "timeframe": "M1",
            "expiration": "M1", "result": "PENDING_RESULT",
            "sent_at": utc_now().isoformat(), "mode": "REAL", "original_amount": 10.0,
        })
        await M.finish_monitored_trade(u, oid, res, lucro)
    publish(u)
    time.sleep(0.6)
    return ids


def check(nome, caminho, esperado, obtido, ok):
    RESULTS.append((nome, ok))
    print(f"[{'OK  ' if ok else 'FALHA'}] {nome}\n        caminho : {caminho}\n"
          f"        esperado: {esperado}\n        obtido  : {obtido}")


async def s50_quando_o_defeito_aparece():
    """Sem 'Reiniciar placar': depende de quem gravou por último."""
    a = "44444444-0000-4000-8000-000000000050"
    gw("GET", "/robot/state", a)
    await opera(a, [("WIN", 8.7), ("WIN", 8.7)])
    gw("GET", "/robot/state", a)          # painel leu DEPOIS da operação
    gw("POST", "/robot/stop", a)
    time.sleep(0.8)
    com_leitura = sdb(a)

    b = "44444444-0000-4000-8000-000000000051"
    gw("GET", "/robot/state", b)
    await opera(b, [("WIN", 8.7), ("WIN", 8.7)])
    gw("POST", "/robot/stop", b)          # painel NÃO leu depois da operação
    time.sleep(0.8)
    sem_leitura = sdb(b)

    ok = com_leitura == (2, 0, 17.4) and sem_leitura == (2, 0, 17.4)
    check("S50 parar preserva o placar com ou sem leitura recente do painel",
          "GET /robot/state (reconcile) x POST /robot/stop (persist)",
          "2x0 nos dois casos",
          f"com leitura antes do stop={com_leitura} | sem leitura antes do stop={sem_leitura}", ok)


async def s52_reset_cycle():
    u = "44444444-0000-4000-8000-000000000052"
    await opera(u, [("WIN", 8.7), ("LOSS", -10.0)])
    st, p = gw("POST", "/robot/reset-cycle", u, {})
    time.sleep(0.8)
    hist = M.robot_persistence.load_trade_history(u, 2)
    ok = spayload(p) == (0, 0, 0.0) and sdb(u) == (0, 0, 0.0) and len(hist) == 0
    check("S52 'Reiniciar ciclo' zera placar e histórico",
          "POST /robot/reset-cycle -> reset_cycle + clear_trade_history + clear_finished_trades",
          "placar 0x0 e histórico vazio",
          f"resposta={spayload(p)} banco={sdb(u)} historico={len(hist)} "
          f"robot_trades={len(M.robot_persistence.load_trades(u))}", ok)


async def s53_exclusao_shift_o():
    u = "44444444-0000-4000-8000-000000000053"
    ids = await opera(u, [("WIN", 8.7), ("WIN", 8.7), ("LOSS", -10.0)])
    antes = smem(u)
    linha = {"order_id": ids[0], "result": "WIN", "profit": 8.7}
    M.apply_marketing_score_removal(u, linha)
    time.sleep(0.6)
    depois = smem(u)
    st, p = gw("GET", "/robot/state", u)
    ok = depois == (1, 1, -1.3) and spayload(p) == (1, 1, -1.3)
    check("S53 excluir operação no Shift+O baixa o placar e não volta",
          "apply_marketing_score_removal -> score_authority -> GET /robot/state",
          "2x1 vira 1x1 lucro -1.3 e o painel concorda",
          f"antes={antes} depois={depois} painel={spayload(p)}", ok)


async def s54_isolamento_entre_clientes():
    a = "44444444-0000-4000-8000-000000000054"
    b = "44444444-0000-4000-8000-000000000055"
    await opera(a, [("WIN", 8.7), ("WIN", 8.7), ("WIN", 8.7)])
    await opera(b, [("LOSS", -10.0)])
    _, pa = gw("GET", "/robot/state", a)
    _, pb = gw("GET", "/robot/state", b)
    ok = spayload(pa) == (3, 0, 26.1) and spayload(pb) == (0, 1, -10.0)
    check("S54 placar de um cliente não vaza para o outro",
          "robot:snapshot:{user_id} + memória por usuário",
          "3x0 para o A e 0x1 para o B",
          f"A={spayload(pa)} B={spayload(pb)}", ok)


async def s55_pausa_por_stop_win():
    u = "44444444-0000-4000-8000-000000000056"
    st = M.auto_trader.get(u)
    st.stop_win = 15.0
    st.stop_win_mode = "money"
    await opera(u, [("WIN", 8.7), ("WIN", 8.7)])
    M.invalidate_daily_history_cache(u)
    publish(u)
    estado = M.auto_trader.get(u)
    _, p = gw("GET", "/robot/state", u)
    ok = smem(u) == (2, 0, 17.4) and spayload(p) == (2, 0, 17.4) and estado.status == "STOP_WIN_HIT"
    check("S55 pausa por Stop Win mantém o placar na tela",
          "finish_monitored_trade -> STOP_WIN_HIT -> stop_robot_worker",
          "2x0 lucro 17.4 na tela com status STOP_WIN_HIT",
          f"placar={smem(u)} tela={spayload(p)} status={estado.status}", ok)


async def s56_placar_apos_pausa_e_reabertura():
    """Depois da pausa por stop o runtime para de publicar: o painel segura?"""
    u = "44444444-0000-4000-8000-000000000057"
    st = M.auto_trader.get(u)
    st.stop_win = 15.0
    st.stop_win_mode = "money"
    await opera(u, [("WIN", 8.7), ("WIN", 8.7)])
    publish(u)
    M.robot_bus._get_client().delete(f"robot:snapshot:{u}")   # 10 min sem publish
    _, p = gw("GET", "/robot/state", u)
    ok = spayload(p) == (2, 0, 17.4)
    check("S56 placar sobrevive à pausa por stop + snapshot expirado",
          "worker pausado -> sem publisher -> GET /robot/state pela persistência",
          "2x0 lucro 17.4 na tela",
          f"tela={spayload(p)} banco={sdb(u)}", ok)


async def main():
    await s50_quando_o_defeito_aparece()
    await s52_reset_cycle()
    await s53_exclusao_shift_o()
    await s54_isolamento_entre_clientes()
    await s55_pausa_por_stop_win()
    await s56_placar_apos_pausa_e_reabertura()
    print("\n================ RESUMO ================")
    for nome, ok in RESULTS:
        print(f"{'OK   ' if ok else 'FALHA'} | {nome}")
    print(f"\n{len(RESULTS)} caminhos testados, {sum(1 for _, ok in RESULTS if not ok)} com falha")


if __name__ == "__main__":
    asyncio.run(main())
