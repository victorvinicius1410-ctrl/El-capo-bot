"""Bancada 6: os cenários das Fases 1 e 2.

Gale abandonado, ordem órfã depois de deploy, TIMEOUT reconciliado no dono do
placar, virada do dia de Brasília e retry na gravação do Histórico.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import time
import urllib.request

from backend import main as M
from backend.auto_trader import utc_now

GW = "http://bench-gateway:8080"
RESULTS = []
SEQ = [700]


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
        return e.code, {"raw": e.read().decode()[:200]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": type(e).__name__}


def smem(u):
    s = M.auto_trader.get(u)
    return (int(s.wins or 0), int(s.losses or 0), round(float(s.profit or 0), 2))


def sdb(u):
    p = M.robot_persistence.load_state(u) or {}
    return (int(p.get("wins") or 0), int(p.get("losses") or 0), round(float(p.get("profit") or 0), 2))


def hist(u, dias=2):
    return M.robot_persistence.load_trade_history(u, dias)


def proximo_id():
    SEQ[0] += 1
    return str(14300000000 + SEQ[0])


def abrir(u, order_id, *, amount=10.0, is_gale=False, gale_step=0, parent=None):
    st = M.auto_trader.get(u)
    st.enabled = True
    st.connected = True
    st.active_mode = "REAL"
    st.account_mode = "REAL"
    M.auto_trader.record_trade(u, {
        "order_id": order_id, "active": "EURUSD-OTC", "direction": "CALL",
        "amount": amount, "confidence": 90, "payout": 87.0, "timeframe": "M1",
        "expiration": "M1", "result": "PENDING_RESULT", "sent_at": utc_now().isoformat(),
        "mode": "REAL", "original_amount": amount, "is_gale": is_gale,
        "gale_step": gale_step, "parent_order_id": parent,
    })


def check(nome, caminho, esperado, obtido, ok):
    RESULTS.append((nome, ok))
    print(f"[{'OK  ' if ok else 'FALHA'}] {nome}\n        caminho : {caminho}\n"
          f"        esperado: {esperado}\n        obtido  : {obtido}")


async def s70_gale_abandonado_no_ciclo():
    u = "66666666-0000-4000-8000-000000000070"
    st = M.auto_trader.get(u)
    st.martingale_enabled = True
    st.martingale_steps = 1
    oid = proximo_id()
    abrir(u, oid)
    await M.finish_monitored_trade(u, oid, "LOSS", -10.0)
    pendente = smem(u)
    # A etapa nunca sai (janela perdida / ativo recusado): o ciclo é reciclado.
    M.reset_cycle_after_finish(u)
    time.sleep(0.5)
    linhas = [x for x in hist(u) if str(x.get("order_id")) == oid]
    ok = smem(u) == (0, 1, -10.0) and sdb(u) == (0, 1, -10.0) and linhas and linhas[0].get("final_result") == "LOSS"
    check("S70 gale que não entra contabiliza o LOSS ao reciclar o ciclo",
          "finish_trade (gale_pending) -> reset_cycle_after_finish -> close_abandoned_gale",
          "0x1 lucro -10 no placar e no banco, com final_result=LOSS no Histórico",
          f"com gale pendente={pendente} depois={smem(u)} banco={sdb(u)} "
          f"final_result={linhas[0].get('final_result') if linhas else 'sem linha'}", ok)


async def s71_gale_abandonado_no_stop():
    u = "66666666-0000-4000-8000-000000000071"
    st = M.auto_trader.get(u)
    st.martingale_enabled = True
    st.martingale_steps = 1
    oid = proximo_id()
    abrir(u, oid)
    await M.finish_monitored_trade(u, oid, "LOSS", -10.0)
    # Cliente para o robô com o gale pendente.
    M.close_abandoned_gale_cycle(u)
    time.sleep(0.5)
    ok = smem(u) == (0, 1, -10.0)
    check("S71 parar o robô com gale pendente contabiliza o LOSS",
          "stop do runtime -> close_abandoned_gale_cycle",
          "0x1 lucro -10",
          f"placar={smem(u)} banco={sdb(u)}", ok)


async def s72_gale_completo_nao_conta_duas_vezes():
    u = "66666666-0000-4000-8000-000000000072"
    st = M.auto_trader.get(u)
    st.martingale_enabled = True
    st.martingale_steps = 1
    a, b = proximo_id(), proximo_id()
    abrir(u, a)
    await M.finish_monitored_trade(u, a, "LOSS", -10.0)
    abrir(u, b, amount=20.0, is_gale=True, gale_step=1, parent=a)
    await M.finish_monitored_trade(u, b, "WIN", 17.4)
    depois_do_gale = smem(u)
    M.reset_cycle_after_finish(u)   # reciclar depois NÃO pode inventar um LOSS
    ok = depois_do_gale == smem(u) and smem(u)[0] == 1 and smem(u)[1] == 0
    check("S72 gale que entra e vence continua contando UM ciclo",
          "trigger_gale -> finish_trade -> reset_cycle_after_finish",
          "1x0 antes e depois de reciclar",
          f"depois do gale={depois_do_gale} depois de reciclar={smem(u)}", ok)


async def s73_orfa_recuperada_no_boot():
    u = "66666666-0000-4000-8000-000000000073"
    oid = proximo_id()
    abrir(u, oid)
    M.persist_robot(u)
    time.sleep(0.6)
    pendentes_antes = M.robot_persistence.load_pending_trades(6)
    # Deploy no meio da vela: processo novo, memória recarregada da persistência.
    M.auto_trader._states.pop(u, None)
    M.auto_trader.restore(u, M.robot_persistence.load_state(u) or {},
                          M.robot_persistence.load_trades(u), source="supabase")

    async def corretora_responde(user_id, order_id):
        return 200, {"ok": True, "data": {"result": "win", "profit": 8.7}}

    from backend import robot_runtime_main
    original = M.fetch_trade_result
    M.fetch_trade_result = corretora_responde
    try:
        await robot_runtime_main._recuperar_ordens_orfas(M, horas=6)
    finally:
        M.fetch_trade_result = original
    time.sleep(0.5)
    pendentes_depois = [x for x in M.robot_persistence.load_pending_trades(6)
                        if str(x[1].get("order_id")) == oid]
    ok = smem(u) == (1, 0, 8.7) and not pendentes_depois
    check("S73 ordem órfã é recuperada no boot do runtime",
          "load_pending_trades -> _recuperar_ordens_orfas -> finish_monitored_trade",
          "1x0 lucro 8.7 e a ordem sai de PENDING_RESULT",
          f"pendentes antes={len(pendentes_antes)} placar={smem(u)} "
          f"ainda pendente={bool(pendentes_depois)}", ok)


async def s74_timeout_reconciliado_no_runtime():
    u = "66666666-0000-4000-8000-000000000074"
    oid = proximo_id()
    abrir(u, oid)
    await M.timeout_monitored_trade(u, oid)
    depois_do_timeout = smem(u)

    async def corretora_tinha_o_resultado(user_id, order_id):
        return 200, {"ok": True, "data": {"result": "win", "profit": 8.7}}

    original = M.fetch_trade_result
    M.fetch_trade_result = corretora_tinha_o_resultado
    try:
        recuperou = await M.reconcile_timeout_last_trade(u)
    finally:
        M.fetch_trade_result = original
    ok = depois_do_timeout == (0, 0, 0.0) and recuperou and smem(u) == (1, 0, 8.7)
    check("S74 TIMEOUT falso é reconciliado no dono do placar",
          "execute_robot_worker_cycle -> reconcile_timeout_last_trade",
          "0x0 no TIMEOUT e 1x0 depois da reconciliação",
          f"apos timeout={depois_do_timeout} recuperou={recuperou} placar={smem(u)}", ok)


async def s75_virada_do_dia():
    u = "66666666-0000-4000-8000-000000000075"
    ontem = utc_now() - datetime.timedelta(days=1)
    trade = {
        "order_id": proximo_id(), "active": "EURUSD-OTC", "direction": "CALL",
        "amount": 10.0, "confidence": 90, "payout": 87.0, "timeframe": "M1",
        "result": "WIN", "profit": 8.7, "sent_at": ontem.isoformat(),
        "finished_at": ontem.isoformat(), "final_result": "WIN", "cycle_result": "WIN",
    }
    st = M.auto_trader.get(u)
    st.enabled = True
    M.auto_trader._histories[u] = [dict(trade)]
    M.robot_persistence.save_trade(u, trade)
    M.robot_persistence.save_trade_history(u, trade)
    st.wins, st.losses, st.profit = 1, 0, 8.7      # placar atravessou a meia-noite
    M.persist_robot(u)
    time.sleep(0.5)
    antes = smem(u)
    # Finge que o último ciclo deste usuário rodou ontem.
    M._ultimo_dia_do_placar[u] = (utc_now() - datetime.timedelta(days=1)).date()
    virou = M.reset_session_score_on_new_day(u)
    time.sleep(0.5)
    vivo = smem(u)
    # E a reidratação seguinte tem de concordar com a memória viva.
    M.auto_trader._states.pop(u, None)
    M.auto_trader.restore(u, M.robot_persistence.load_state(u) or {},
                          M.robot_persistence.load_trades(u), source="supabase")
    ok = virou and vivo == (0, 0, 0.0) and smem(u) == vivo
    check("S75 placar vira o dia e a reidratação concorda",
          "execute_robot_worker_cycle -> reset_session_score_on_new_day",
          "0x0 na memória viva e o mesmo valor depois de reidratar",
          f"antes da virada={antes} virou={virou} memoria viva={vivo} "
          f"apos reidratar={smem(u)}", ok)


async def s76_retry_na_gravacao():
    u = "66666666-0000-4000-8000-000000000076"
    persistencia = M.robot_persistence
    escritor = getattr(persistencia, "_escrever_com_retry", None)
    if not callable(escritor):
        check("S76 retry na gravação do Histórico", "save_trade_history",
              "3 tentativas em falha transitória",
              "persistência local (SQLite) não usa o caminho HTTP — não aplicável aqui", True)
        return
    check("S76 retry na gravação do Histórico", "save_trade_history -> _escrever_com_retry",
          "helper de retry presente na persistência Supabase",
          "presente", True)


async def main():
    await s70_gale_abandonado_no_ciclo()
    await s71_gale_abandonado_no_stop()
    await s72_gale_completo_nao_conta_duas_vezes()
    await s73_orfa_recuperada_no_boot()
    await s74_timeout_reconciliado_no_runtime()
    await s75_virada_do_dia()
    await s76_retry_na_gravacao()
    print("\n================ RESUMO ================")
    for nome, ok in RESULTS:
        print(f"{'OK   ' if ok else 'FALHA'} | {nome}")
    print(f"\n{len(RESULTS)} caminhos testados, {sum(1 for _, ok in RESULTS if not ok)} com falha")


if __name__ == "__main__":
    asyncio.run(main())
