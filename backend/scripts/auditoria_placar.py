#!/usr/bin/env python3
"""Audita o placar de cada cliente contra o Histórico e o espelho de ordens.

Criado depois da revisão de 15/09/2026, para que os defeitos do placar não
voltem em silêncio — todos eles eram invisíveis no log
(backend/docs/PLACAR_DIAGNOSTICO_2026-09-15.md).

Confere três coisas:

1. **Placar × Histórico** — ``robot_states.wins/losses`` tem de bater com os
   ciclos fechados do dia civil de Brasília, posteriores ao "Reiniciar placar".
2. **Ordens órfãs** — linhas em ``PENDING_RESULT`` com mais de uma hora: o
   monitor de resultado morreu e ninguém mais busca o resultado.
3. **Operação sem Histórico** — resultado final em ``robot_trades`` sem a linha
   correspondente em ``robot_trade_history``.

Um ciclo com gale gera DUAS linhas no Histórico e UM ponto no placar; por isso a
contagem usa ``cycle_result`` (só a linha que fecha o ciclo tem), e não
``result``.

Uso::

    python scripts/auditoria_placar.py            # resumo
    python scripts/auditoria_placar.py --detalhe  # lista cliente por cliente

Sai com código 1 quando encontra divergência — serve para cron/alerta.
Precisa de ``SUPABASE_URL`` e ``SUPABASE_SERVICE_ROLE_KEY`` no ambiente.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import urllib.parse
import urllib.request
import uuid

FUSO_BRASILIA = datetime.timezone(datetime.timedelta(hours=-3))


def _listar(base: str, chave: str, tabela: str, params: dict) -> list[dict]:
    """Lê uma tabela paginando de 1000 em 1000 (ordem estável obrigatória)."""
    linhas: list[dict] = []
    inicio = 0
    while True:
        query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        req = urllib.request.Request(f"{base}/rest/v1/{tabela}?{query}")
        req.add_header("apikey", chave)
        req.add_header("Authorization", f"Bearer {chave}")
        req.add_header("Range", f"{inicio}-{inicio + 999}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            pagina = json.loads(resp.read() or b"[]")
        linhas.extend(pagina)
        if len(pagina) < 1000:
            return linhas
        inicio += 1000


def _instante(valor) -> datetime.datetime | None:
    if not valor:
        return None
    try:
        return datetime.datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None


def _e_cliente_real(user_id: str) -> bool:
    try:
        uuid.UUID(str(user_id))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detalhe", action="store_true", help="lista cliente por cliente")
    parser.add_argument(
        "--horas-orfa", type=int, default=1, help="idade mínima para acusar órfã"
    )
    args = parser.parse_args()

    base = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    chave = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not chave:
        print("Faltam SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY no ambiente.")
        return 2

    agora = datetime.datetime.now(datetime.timezone.utc)
    inicio_do_dia = (
        agora.astimezone(FUSO_BRASILIA)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(datetime.timezone.utc)
    )
    corte = inicio_do_dia.isoformat()

    estados = {
        linha["user_id"]: (linha.get("state_json") or {})
        for linha in _listar(base, chave, "robot_states", {
            "select": "user_id,state_json", "order": "user_id.asc",
        })
    }
    historico = _listar(base, chave, "robot_trade_history", {
        "select": "user_id,order_id,parent_order_id,result,cycle_result,final_result,profit,finished_at",
        "finished_at": f"gte.{corte}",
        "order": "id.asc",
    })
    espelhos = _listar(base, chave, "robot_trades", {
        "select": "user_id,order_id,result,executed_at",
        "executed_at": f"gte.{corte}",
        "order": "executed_at.asc",
    })

    pernas_superadas = {
        str(linha.get("parent_order_id") or "").strip()
        for linha in historico
        if str(linha.get("parent_order_id") or "").strip()
    }

    por_cliente: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for linha in historico:
        estado = estados.get(linha["user_id"]) or {}
        reset = _instante(estado.get("stop_reset_at"))
        fim = _instante(linha.get("finished_at"))
        if reset is not None and fim is not None and fim < reset:
            continue
        # Perna de gale superada: a ordem que perdeu e passou o ciclo adiante
        # aparece como `parent_order_id` da etapa seguinte. Sem esta regra um
        # ciclo com gale contaria dois pontos.
        if (
            str(linha.get("order_id") or "") in pernas_superadas
            and not linha.get("cycle_result")
        ):
            continue
        # Operação recém-fechada: `persist_robot` grava o placar em background,
        # então uma linha de segundos atrás ainda não está no `robot_states`.
        if fim is not None and (agora - fim).total_seconds() < 120:
            continue
        fecha_ciclo = str(
            linha.get("cycle_result") or linha.get("result") or ""
        ).strip().upper()
        if fecha_ciclo == "WIN":
            por_cliente[linha["user_id"]][0] += 1
        elif fecha_ciclo == "LOSS":
            por_cliente[linha["user_id"]][1] += 1

    no_historico = {(l["user_id"], str(l["order_id"])) for l in historico}
    orfas = [
        l for l in espelhos
        if str(l.get("result") or "").upper() == "PENDING_RESULT"
        and _e_cliente_real(l["user_id"])
        and (agora - (_instante(l.get("executed_at")) or agora)).total_seconds()
        > args.horas_orfa * 3600
    ]
    sem_historico = [
        l for l in espelhos
        if str(l.get("result") or "").upper() in {"WIN", "LOSS", "DRAW"}
        and _e_cliente_real(l["user_id"])
        and (l["user_id"], str(l["order_id"])) not in no_historico
    ]

    divergentes = []
    for user_id, (wins, losses) in sorted(por_cliente.items()):
        estado = estados.get(user_id) or {}
        placar = (int(estado.get("wins") or 0), int(estado.get("losses") or 0))
        if placar != (wins, losses):
            divergentes.append((user_id, placar, (wins, losses), estado.get("status")))

    print(f"Auditoria do placar — dia de Brasília iniciado em {corte[:19]}Z")
    print(f"  clientes que operaram hoje : {len(por_cliente)}")
    print(f"  placar x Histórico         : {len(divergentes)} divergente(s)")
    print(f"  ordens órfãs (> {args.horas_orfa}h)       : {len(orfas)}")
    print(f"  operação sem Histórico     : {len(sem_historico)}")

    if divergentes and args.detalhe:
        print("\nDivergências (placar no banco x ciclos fechados no Histórico):")
        for user_id, placar, esperado, status in divergentes:
            print(
                f"  {user_id[:8]}  banco={placar[0]}x{placar[1]}  "
                f"histórico={esperado[0]}x{esperado[1]}  status={status}"
            )
    if orfas and args.detalhe:
        print("\nOrdens órfãs:")
        for linha in orfas[:20]:
            print(f"  {linha['user_id'][:8]}  {linha['order_id']}  {linha['executed_at'][:19]}")
    if sem_historico and args.detalhe:
        print("\nOperações sem Histórico:")
        for linha in sem_historico[:20]:
            print(f"  {linha['user_id'][:8]}  {linha['order_id']}  {linha.get('result')}")

    problemas = len(divergentes) + len(orfas) + len(sem_historico)
    if problemas:
        print(f"\nRESULTADO: {problemas} problema(s). Rode com --detalhe para a lista.")
        return 1
    print("\nRESULTADO: placar, Histórico e espelho de ordens batem.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
