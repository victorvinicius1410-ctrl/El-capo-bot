"""Bancada 7: o "Iniciar Operação" de 28/09 que virou 2x3 em 1x4.

S80 refaz o dia do cliente 11e0b3d5: loss apagado no Shift+O logo depois de
fechar (era o `last_trade`), outro apagado no fim, o WIN da primeira ordem
regravado como pendente no espelho (como o gateway fazia) e parar/iniciar pelo
caminho real do runtime (`_hydrate_user_from_persistence(force=True)`).
S81: `last_trade` pendente de ordem já fechada volta do `robot_states` num
restart e não pode rebaixar o espelho.

Ver docs/PLACAR_OVERLAY.md §2026-09-29.
"""
from __future__ import annotations

import asyncio
import time

from backend import main as M
from backend import robot_runtime_main as R
from backend.auto_trader import utc_now

RESULTS = []
SEQ = [800]


def smem(u):
    s = M.auto_trader.get(u)
    return (int(s.wins or 0), int(s.losses or 0), round(float(s.profit or 0), 2))


def espelho(u):
    return {str(t.get("order_id")): t for t in M.robot_persistence.load_trades(u)}


def check(nome, caminho, esperado, obtido, ok):
    RESULTS.append((nome, ok))
    print(f"[{'OK  ' if ok else 'FALHA'}] {nome}\n        caminho : {caminho}\n"
          f"        esperado: {esperado}\n        obtido  : {obtido}")


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
            "amount": 10.0, "confidence": 90, "payout": 87.0, "timeframe": "M5",
            "expiration": "M5", "result": "PENDING_RESULT",
            "sent_at": utc_now().isoformat(), "mode": "REAL", "original_amount": 10.0,
        })
        await M.finish_monitored_trade(u, oid, res, lucro)
    time.sleep(0.6)
    return ids


async def s80_dia_do_sergio():
    u = "88888888-0000-4000-8000-000000000080"
    (w1,) = await opera(u, [("WIN", 8.7)])
    await opera(u, [("LOSS", -10.0)])
    (x,) = await opera(u, [("LOSS", -10.0)])
    M.delete_marketing_robot_history_item(u, x)          # apagado sendo o last_trade
    time.sleep(0.6)
    await opera(u, [("LOSS", -10.0), ("WIN", 8.7)])
    (y,) = await opera(u, [("LOSS", -10.0)])
    M.delete_marketing_robot_history_item(u, y)
    time.sleep(0.6)
    await opera(u, [("LOSS", -10.0)])                    # 2x3 no painel
    antes = smem(u)
    # O que o gateway fazia: regrava o primeiro WIN como pendente no espelho.
    corrompido = {**espelho(u)[w1], "result": "PENDING_RESULT", "profit": None}
    corrompido.pop("finished_at", None)
    M.robot_persistence.save_trade(u, corrompido)
    # Parar, trocar M5→M1, iniciar: o start re-hidrata à força.
    M.auto_trader.stop(u)
    R._hydrate_user_from_persistence(M, u, force=True)
    depois = smem(u)
    mirror = espelho(u)
    ok = antes == (2, 3, -12.6) and depois == antes and x not in mirror and y not in mirror
    check("S80 apagar no Shift+O + parar/iniciar não muda a composição do placar",
          "delete_marketing_robot_history_item -> persist_robot -> stop -> "
          "_hydrate_user_from_persistence(force) -> load_trades_for_restore",
          "2x3 lucro -12.6 antes e depois do start; ordens apagadas fora do espelho",
          f"antes={antes} depois={depois} apagadas_no_espelho={[o for o in (x, y) if o in mirror]}", ok)


async def s81_last_trade_pendente_no_restart():
    u = "88888888-0000-4000-8000-000000000081"
    (oid,) = await opera(u, [("WIN", 8.7)])
    payload = M.robot_persistence.load_state(u) or {}
    payload["last_trade"] = {**(payload.get("last_trade") or {}), "order_id": oid, "result": "PENDING_RESULT"}
    M.robot_persistence.save_state(u, payload)
    M.auto_trader._states.pop(u, None)                   # deploy: processo reiniciou
    R._hydrate_user_from_persistence(M, u)
    futuro = M.persist_robot(u)
    if futuro is not None:
        futuro.result(timeout=5)
    linha = espelho(u).get(oid) or {}
    ultimo = (M.auto_trader.get(u).last_trade or {}).get("result")
    ok = smem(u) == (1, 0, 8.7) and linha.get("result") == "WIN" and ultimo == "WIN"
    check("S81 last_trade pendente de ordem fechada não rebaixa o espelho no restart",
          "robot_states.last_trade PENDENTE -> restore(_close_stale_last_trade) -> persist_robot",
          "1x0; espelho e last_trade continuam WIN",
          f"placar={smem(u)} espelho={linha.get('result')} last_trade={ultimo}", ok)


async def main():
    await s80_dia_do_sergio()
    await s81_last_trade_pendente_no_restart()
    print("\n================ RESUMO ================")
    falhas = [n for n, ok in RESULTS if not ok]
    for nome, ok in RESULTS:
        print(f"{'OK   ' if ok else 'FALHA'} | {nome}")
    print(f"\n{len(RESULTS)} caminhos testados, {len(falhas)} com falha")


if __name__ == "__main__":
    asyncio.run(main())
