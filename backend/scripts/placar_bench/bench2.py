"""Bancada 2: sequência de produção (painel aberto ANTES de operar) e as
superfícies que faltaram — Histórico, estatísticas, resumo de gestão, Shift+O
pelo comando real do runtime, stop win e as outras rotas do gateway que gravam.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from datetime import timedelta

from backend import main as M
from backend.auto_trader import utc_now

GW = "http://bench-gateway:8080"
RESULTS = []


def gw(method, path, user, body=None, email="bench@local"):
    req = urllib.request.Request(GW + path, method=method)
    req.add_header("x-api-key", "bench")
    req.add_header("x-user-id", user)
    req.add_header("x-user-email", email)
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


def settle():
    """Espera a persistência em background (executor) encostar no disco."""
    time.sleep(0.6)


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


def open_trade(u, order_id, amount=10.0, tf="M1", is_gale=False, gale_step=0, parent=None):
    st = M.auto_trader.get(u)
    st.enabled = True
    st.connected = True
    st.active_mode = "REAL"
    st.account_mode = "REAL"
    st.timeframe = tf
    st.entry_value = amount
    sent = utc_now()
    trade = {
        "order_id": str(order_id), "active": "EURUSD-OTC", "direction": "CALL",
        "amount": amount, "confidence": 90, "payout": 87.0, "timeframe": tf,
        "expiration": tf, "result": "PENDING_RESULT", "sent_at": sent.isoformat(),
        "is_gale": is_gale, "gale_step": gale_step, "parent_order_id": parent,
        "original_amount": amount, "mode": "REAL",
    }
    M.auto_trader.record_trade(u, trade)
    persist(u)
    publish(u)


async def opera(u, resultados):
    """Roda operações reais como o runtime faz, uma a uma."""
    for i, (res, lucro) in enumerate(resultados, start=1):
        oid = f"{u[-4:]}-{i}"
        open_trade(u, oid)
        await M.finish_monitored_trade(u, oid, res, lucro)
    publish(u)
    settle()


def check(nome, caminho, esperado, obtido, ok):
    RESULTS.append((nome, ok))
    print(f"[{'OK  ' if ok else 'FALHA'}] {nome}\n        caminho : {caminho}\n"
          f"        esperado: {esperado}\n        obtido  : {obtido}")


# ── A sequência real: o cliente abre o painel ANTES de operar ────────────────
async def s20_sequencia_real():
    u = "22222222-0000-4000-8000-000000000020"
    st0, p0 = gw("GET", "/robot/state", u)          # 1. cliente abre o painel
    await opera(u, [("WIN", 8.7), ("WIN", 8.7), ("LOSS", -10.0)])   # 2. opera
    st1, p1 = gw("GET", "/robot/state", u)          # 3. painel durante a sessão
    ok1 = spayload(p1) == (2, 1, 7.4)
    check("S20 painel mostra o placar durante a sessão",
          "runtime -> redis -> GET /robot/state",
          "2x1 lucro 7.4", f"abertura={spayload(p0)} durante={spayload(p1)} banco={sdb(u)}", ok1)

    antes = sdb(u)
    st2, p2 = gw("POST", "/robot/stop", u)          # 4. cliente para
    settle()
    depois = sdb(u)
    payload_estado = M.robot_persistence.load_state(u) or {}
    ok2 = depois == antes
    check("S21 'Parar Operação' preserva o placar no banco",
          "POST /robot/stop -> auto_trader.stop + persist_robot (memória do gateway)",
          f"banco continua {antes}",
          f"http={st2} banco_depois={depois} last_trade="
          f"{'presente' if payload_estado.get('last_trade') else 'APAGADO'}", ok2)

    M.robot_bus._get_client().delete(f"robot:snapshot:{u}")   # 5. TTL de 600s vence
    st3, p3 = gw("GET", "/robot/state", u)
    ok3 = spayload(p3) == (2, 1, 7.4)
    check("S22 cliente reabre o painel 10 min depois de parar",
          "snapshot expirado -> rehydrate_score_from_persistence_if_blank",
          "2x1 lucro 7.4 na tela",
          f"http={st3} placar_na_tela={spayload(p3)} banco={sdb(u)}", ok3)

    # 6. "Iniciar Operação": o runtime reidrata e recalcula pelo dia
    M.auto_trader._states.pop(u, None)
    M.auto_trader.restore(u, M.robot_persistence.load_state(u) or {},
                          M.robot_persistence.load_trades(u), source="supabase")
    ok4 = smem(u) == (2, 1, 7.4)
    check("S23 'Iniciar Operação' devolve o placar",
          "restore -> _recompute_score_from_history (robot_trades do dia)",
          "2x1 recalculado", f"placar_apos_reidratar={smem(u)}", ok4)
    return u


# ── Outras rotas do gateway que gravam estado ───────────────────────────────
async def s24_outras_rotas_do_gateway():
    u = "22222222-0000-4000-8000-000000000024"
    gw("GET", "/robot/state", u)                       # gateway memoriza 0x0
    await opera(u, [("WIN", 8.7), ("WIN", 8.7)])       # runtime conta 2x0
    antes = sdb(u)
    st, p = gw("POST", "/robot/config", u, {"entry_value": 25.0, "timeframe": "M1"})
    settle()
    depois = sdb(u)
    ok = depois == antes
    check("S24 salvar configuração não mexe no placar",
          "POST /robot/config -> persist_robot (memória do gateway)",
          f"banco continua {antes}", f"http={st} banco_depois={depois}", ok)


# ── Histórico e estatísticas do painel ──────────────────────────────────────
async def s25_historico_e_stats():
    u = "22222222-0000-4000-8000-000000000025"
    await opera(u, [("WIN", 8.7), ("LOSS", -10.0), ("WIN", 8.7), ("DRAW", 0.0)])
    st, p = gw("GET", "/robot/history?days=1", u)
    itens = (p.get("data") or {}).get("items") or []
    res = [str(i.get("result")) for i in itens]
    ok = st == 200 and len(itens) == 4 and res.count("DRAW") == 1
    check("S25 Histórico do painel lista as 4 operações",
          "GET /robot/history -> robot_trade_history",
          "4 linhas, incluindo o empate",
          f"http={st} linhas={len(itens)} resultados={res}", ok)

    st2, p2 = gw("GET", "/robot/stats?days=1", u)
    d = p2.get("data") or {}
    ok2 = st2 == 200 and int(d.get("wins") or 0) == 2 and int(d.get("losses") or 0) == 1
    check("S26 Estatísticas batem com o placar",
          "GET /robot/stats -> build_robot_stats",
          "2 WIN e 1 LOSS (empate não conta)",
          f"http={st2} wins={d.get('wins')} losses={d.get('losses')} "
          f"accuracy={d.get('accuracy')} total={d.get('total')}", ok2)


# ── Resumo de gestão (o que alimenta o Stop Win/Loss) ───────────────────────
async def s27_resumo_de_gestao():
    u = "22222222-0000-4000-8000-000000000027"
    await opera(u, [("WIN", 30.0), ("WIN", 30.0)])
    st = M.auto_trader.get(u)
    st.stop_win = 50.0
    st.stop_win_mode = "money"
    M.invalidate_daily_history_cache(u)
    resumo = M.build_management_summary(u, st)
    ok = resumo["net_profit"] == 60.0 and resumo["stop_reason"] == "STOP_WIN_HIT"
    check("S27 Stop Win dispara com lucro real",
          "build_management_summary -> resolve_robot_stop_reason",
          "lucro 60 e STOP_WIN_HIT",
          f"placar={smem(u)} lucro_liquido={resumo['net_profit']} "
          f"operacoes={resumo['trades_count']} motivo={resumo['stop_reason']}", ok)


# ── Shift+O pelo comando real do robot-runtime ─────────────────────────────
async def s28_shift_o_pelo_runtime():
    u = "22222222-0000-4000-8000-000000000028"
    gw("GET", "/robot/state", u)
    M.robot_bus.publish_command(u, "apply_score", {"wins": 8, "losses": 2, "profit": 480.0})
    time.sleep(2.5)                      # o bench-runtime consome o comando
    snap = sredis(u)
    st, p = gw("GET", "/robot/state", u)
    ok = snap == (8, 2, 480.0) and spayload(p) == (8, 2, 480.0)
    check("S28 'Gerar placar' do Shift+O chega ao painel",
          "robot:cmd apply_score -> runtime set_display_score -> snapshot -> GET /robot/state",
          "8x2 lucro 480 no Redis e no painel",
          f"redis={snap} painel={spayload(p)}", ok)

    # e o stop não pode disparar com placar de vitrine
    estado = M.robot_persistence.load_state(u) or {}
    ok2 = int(estado.get("stop_offset_wins") or 0) == 8
    check("S29 placar de vitrine fica marcado como sintético",
          "set_display_score -> stop_offset_* persistido",
          "stop_offset_wins = 8 no banco",
          f"offsets_no_banco=({estado.get('stop_offset_wins')},"
          f"{estado.get('stop_offset_losses')},{estado.get('stop_offset_profit')})", ok2)


# ── Restart do runtime no meio da sessão ───────────────────────────────────
async def s30_restart_do_runtime():
    u = "22222222-0000-4000-8000-000000000030"
    await opera(u, [("WIN", 8.7), ("LOSS", -10.0), ("WIN", 8.7)])
    antes = smem(u)
    M.auto_trader._states.pop(u, None)          # deploy: processo reiniciou
    M.auto_trader.restore(u, M.robot_persistence.load_state(u) or {},
                          M.robot_persistence.load_trades(u), source="supabase")
    depois = smem(u)
    ok = antes == depois
    check("S30 deploy no meio da sessão não muda o placar",
          "restore + _recompute_score_from_history",
          f"placar continua {antes}", f"antes={antes} depois={depois}", ok)


# ── Duas abas / dois polls simultâneos ─────────────────────────────────────
async def s31_concorrencia():
    u = "22222222-0000-4000-8000-000000000031"
    gw("GET", "/robot/state", u)
    await opera(u, [("WIN", 8.7)])
    leituras = []
    for _ in range(6):
        _, p = gw("GET", "/robot/state", u)
        leituras.append(spayload(p))
        time.sleep(0.2)
    ok = all(x == (1, 0, 8.7) for x in leituras)
    check("S31 placar não oscila entre polls",
          "GET /robot/state repetido (enrich + reconcile a cada leitura)",
          "1x0 estável em 6 leituras", f"leituras={leituras}", ok)


async def main():
    await s20_sequencia_real()
    await s24_outras_rotas_do_gateway()
    await s25_historico_e_stats()
    await s27_resumo_de_gestao()
    await s28_shift_o_pelo_runtime()
    await s30_restart_do_runtime()
    await s31_concorrencia()
    print("\n================ RESUMO ================")
    falhas = [n for n, ok in RESULTS if not ok]
    for nome, ok in RESULTS:
        print(f"{'OK   ' if ok else 'FALHA'} | {nome}")
    print(f"\n{len(RESULTS)} caminhos testados, {len(falhas)} com falha")


if __name__ == "__main__":
    asyncio.run(main())
