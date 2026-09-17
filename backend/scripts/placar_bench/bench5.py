"""Bancada 5: cenários exigidos pela correção do congelamento (Fase 0.2).

A guarda `stop_reset_at + placar em branco` saiu do reconcile e da
reidratação; quem cobre a janela do "Reiniciar placar" agora é a marca de baixa
intencional (TTL 120s). Estes cenários provam que a troca não reabre o defeito
antigo — o placar zerado NÃO pode voltar quando a marca expira.
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
SEQ = [900]


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


async def opera(u, resultados):
    for res, lucro in resultados:
        SEQ[0] += 1
        oid = str(14300000000 + SEQ[0])
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


def reiniciar_placar(u):
    """Clique do cliente + efeito do comando no dono do placar."""
    gw("POST", "/robot/reset-score", u)
    M.auto_trader.reset_score(u)      # robot_runtime_main faz isso ao receber o cmd
    publish(u)
    time.sleep(0.6)


def check(nome, caminho, esperado, obtido, ok):
    RESULTS.append((nome, ok))
    print(f"[{'OK  ' if ok else 'FALHA'}] {nome}\n        caminho : {caminho}\n"
          f"        esperado: {esperado}\n        obtido  : {obtido}")


async def s60_marca_expirada_nao_ressuscita():
    """A marca dura 120s. Depois dela, o placar antigo não pode voltar."""
    u = "55555555-0000-4000-8000-000000000060"
    gw("GET", "/robot/state", u)
    await opera(u, [("WIN", 8.7), ("WIN", 8.7), ("LOSS", -10.0)])
    antes_do_reset = smem(u)
    reiniciar_placar(u)
    # Simula a marca caducando (TTL 120s) sem esperar de verdade.
    M.clear_session_score_authority(u)
    gw("GET", "/robot/state", u)
    gw("POST", "/robot/config", u, {"entry_value": 15.0})
    time.sleep(0.8)
    _, p = gw("GET", "/robot/state", u)
    ok = spayload(p) == (0, 0, 0.0) and sdb(u) == (0, 0, 0.0) and sredis(u) == (0, 0, 0.0)
    check("S60 placar zerado não volta quando a marca expira",
          "reset-score -> marca caduca -> reconcile/rehydrate sem a guarda de stop_reset_at",
          "0x0 na tela, no banco e no Redis",
          f"antes do reset={antes_do_reset} | tela={spayload(p)} banco={sdb(u)} redis={sredis(u)}", ok)


async def s61_primeiro_resultado_depois_do_reset():
    u = "55555555-0000-4000-8000-000000000061"
    gw("GET", "/robot/state", u)
    await opera(u, [("WIN", 8.7), ("WIN", 8.7)])
    reiniciar_placar(u)
    await opera(u, [("WIN", 8.7)])
    _, p = gw("GET", "/robot/state", u)
    ok = spayload(p) == (1, 0, 8.7) and sdb(u) == (1, 0, 8.7)
    check("S61 primeiro resultado depois do reset conta 1x0",
          "reset-score -> finish_monitored_trade limpa a marca -> placar sobe",
          "1x0 (nem 0x0 travado, nem 3x0 somado ao antigo)",
          f"tela={spayload(p)} banco={sdb(u)} redis={sredis(u)}", ok)


async def s62_reset_seguido_de_parar():
    """A combinação que apagava o placar em produção."""
    u = "55555555-0000-4000-8000-000000000062"
    gw("GET", "/robot/state", u)
    await opera(u, [("WIN", 8.7)])
    reiniciar_placar(u)
    await opera(u, [("WIN", 8.7), ("WIN", 8.7), ("LOSS", -10.0)])
    gw("POST", "/robot/stop", u)
    time.sleep(0.8)
    banco = sdb(u)
    M.robot_bus._get_client().delete(f"robot:snapshot:{u}")   # 10 min depois
    _, p = gw("GET", "/robot/state", u)
    ok = banco == (2, 1, 7.4) and spayload(p) == (2, 1, 7.4)
    check("S62 reiniciar placar, operar e parar mantém o placar do dia",
          "reset-score -> operações -> POST /robot/stop -> snapshot expirado",
          "2x1 lucro 7.4 no banco e na tela 10 min depois",
          f"banco={banco} tela_depois_do_snapshot_expirar={spayload(p)}", ok)


async def s63_descarte_agora_tem_log():
    """F5: o descarte continua acontecendo, mas agora deixa rastro."""
    u = "55555555-0000-4000-8000-000000000063"
    await opera(u, [("WIN", 8.7)])
    M.reset_cycle_after_finish(u)
    await opera(u, [("LOSS", -10.0)])
    import logging
    registros = []

    class Coletor(logging.Handler):
        def emit(self, record):
            registros.append(record.getMessage())

    coletor = Coletor()
    logging.getLogger("backend-gateway").addHandler(coletor)
    try:
        # Resultado de uma ordem que não é mais a ordem em memória.
        await M.finish_monitored_trade(u, "14300000999", "WIN", 8.7)
    finally:
        logging.getLogger("backend-gateway").removeHandler(coletor)
    descartes = [r for r in registros if "TRADE_RESULT_DISCARDED" in r]
    ok = bool(descartes)
    check("S63 resultado descartado deixa log com o motivo",
          "finish_trade -> [TRADE_RESULT_DISCARDED]",
          "pelo menos uma linha de log com o motivo do descarte",
          f"linhas={len(descartes)} exemplo={descartes[0][:120] if descartes else 'NENHUMA'}", ok)


async def main():
    await s60_marca_expirada_nao_ressuscita()
    await s61_primeiro_resultado_depois_do_reset()
    await s62_reset_seguido_de_parar()
    await s63_descarte_agora_tem_log()
    print("\n================ RESUMO ================")
    for nome, ok in RESULTS:
        print(f"{'OK   ' if ok else 'FALHA'} | {nome}")
    print(f"\n{len(RESULTS)} caminhos testados, {sum(1 for _, ok in RESULTS if not ok)} com falha")


if __name__ == "__main__":
    asyncio.run(main())
