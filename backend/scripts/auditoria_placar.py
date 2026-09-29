#!/usr/bin/env python3
"""Audita o placar de cada cliente contra o Histórico e o espelho de ordens.

Criado depois da revisão de 15/09/2026, para que os defeitos do placar não
voltem em silêncio — todos eles eram invisíveis no log
(backend/docs/PLACAR_DIAGNOSTICO_2026-09-15.md). Ampliado em 29/09/2026 com os
dois jeitos em que o espelho ``robot_trades`` mentia e levou um placar de 2x3
para 1x4 no "Iniciar Operação" (docs/PLACAR_OVERLAY.md §2026-09-29).

Confere quatro coisas, no dia civil de Brasília:

1. **Placar × Histórico** — ``robot_states.wins/losses`` tem de bater com os
   ciclos fechados do dia, posteriores ao "Reiniciar placar".
2. **Espelho pendente com resultado final** — linha ``PENDING_RESULT`` em
   ``robot_trades`` cuja ordem já está WIN/LOSS/DRAW no Histórico: alguém
   regravou uma cópia velha por cima do resultado.
3. **Ordens órfãs** — ``PENDING_RESULT`` sem Histórico há mais de uma hora: o
   monitor de resultado morreu e ninguém mais busca o resultado.
4. **Operação sem Histórico** — resultado final no espelho sem linha no
   Histórico (fora a perda oculta do modo LIVE). Criada bem depois do fim da
   operação é a assinatura de ordem apagada no Shift+O que ressuscitou.

Um ciclo com gale gera DUAS linhas no Histórico e UM ponto no placar; por isso a
contagem usa ``cycle_result`` (só a linha que fecha o ciclo tem), e não
``result``.

Uso::

    python scripts/auditoria_placar.py            # resumo
    python scripts/auditoria_placar.py --detalhe  # lista cliente por cliente
    python scripts/auditoria_placar.py --corrigir # conserta o espelho (2 e 4)

``--corrigir`` só mexe no espelho ``robot_trades``: põe o resultado do
Histórico na linha pendente e apaga a linha ressuscitada. Nunca toca o
Histórico nem o placar.

Sai com código 1 quando encontra divergência — serve para cron/alerta
(``scripts/alerta_auditoria_placar.py``).
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
# Operação recém-fechada: `persist_robot` grava em background.
CARENCIA_SEGUNDOS = 300
# Espelho criado tanto tempo depois do fim da operação não é o runtime gravando
# o resultado: é o `persist_robot` recriando uma ordem apagada.
RESSURREICAO_SEGUNDOS = 60


def _requisitar(base: str, chave: str, metodo: str, caminho: str, corpo=None) -> list[dict]:
    dados = None if corpo is None else json.dumps(corpo).encode()
    req = urllib.request.Request(f"{base}/rest/v1/{caminho}", data=dados, method=metodo)
    req.add_header("apikey", chave)
    req.add_header("Authorization", f"Bearer {chave}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=minimal")
    with urllib.request.urlopen(req, timeout=60) as resp:
        conteudo = resp.read()
    return json.loads(conteudo) if conteudo else []


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


def _idade(agora: datetime.datetime, valor) -> float:
    instante = _instante(valor)
    return 0.0 if instante is None else (agora - instante).total_seconds()


def auditar(
    base: str,
    chave: str,
    *,
    horas_orfa: int = 1,
    agora: datetime.datetime | None = None,
) -> dict:
    """Roda as quatro checagens e devolve o que achou.

    Cada item tem ``chave`` estável (mesmo problema → mesma chave entre
    rodadas), usada pelo alerta para só avisar o que persiste.

    Args:
        base: ``SUPABASE_URL``.
        chave: ``SUPABASE_SERVICE_ROLE_KEY``.
        horas_orfa: Idade mínima de uma ordem pendente sem Histórico.
        agora: Relógio (testes).

    Returns:
        ``{"corte", "clientes", "divergentes", "pendentes_com_final",
        "orfas", "sem_historico"}``.
    """
    agora = agora or datetime.datetime.now(datetime.timezone.utc)
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
        "select": "user_id,order_id,result,executed_at,created_at,trade_json",
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
        fecha_ciclo = str(
            linha.get("cycle_result") or linha.get("result") or ""
        ).strip().upper()
        if fecha_ciclo == "WIN":
            por_cliente[linha["user_id"]][0] += 1
        elif fecha_ciclo == "LOSS":
            por_cliente[linha["user_id"]][1] += 1

    finais = {
        (l["user_id"], str(l["order_id"])): l
        for l in historico
        if str(l.get("result") or "").upper() in {"WIN", "LOSS", "DRAW"}
    }
    ultima_no_historico: dict[str, float] = {}
    for linha in historico:
        idade = _idade(agora, linha.get("finished_at"))
        atual = ultima_no_historico.get(linha["user_id"])
        ultima_no_historico[linha["user_id"]] = idade if atual is None else min(atual, idade)

    pendentes_com_final = []
    orfas = []
    sem_historico = []
    for linha in espelhos:
        user_id = linha["user_id"]
        if not _e_cliente_real(user_id):
            continue
        resultado = str(linha.get("result") or "").upper()
        order_id = str(linha["order_id"])
        final = finais.get((user_id, order_id))
        trade = linha.get("trade_json") or {}
        if resultado == "PENDING_RESULT":
            if final is not None and _idade(agora, final.get("finished_at")) > CARENCIA_SEGUNDOS:
                pendentes_com_final.append({
                    **linha,
                    "final": final,
                    "chave": f"pendente:{user_id}:{order_id}",
                })
            elif final is None and _idade(agora, linha.get("executed_at")) > horas_orfa * 3600:
                orfas.append({**linha, "chave": f"orfa:{user_id}:{order_id}"})
        elif resultado in {"WIN", "LOSS", "DRAW"} and final is None:
            if trade.get("live_mode_active"):
                continue  # perda oculta do LIVE: fica fora do Histórico de propósito
            fim = trade.get("finished_at") or linha.get("executed_at")
            if _idade(agora, fim) <= CARENCIA_SEGUNDOS:
                continue
            criada = _instante(linha.get("created_at"))
            fim_dt = _instante(fim)
            ressuscitada = (
                criada is not None
                and fim_dt is not None
                and (criada - fim_dt).total_seconds() > RESSURREICAO_SEGUNDOS
            )
            sem_historico.append({
                **linha,
                "ressuscitada": ressuscitada,
                "chave": f"sem_historico:{user_id}:{order_id}",
            })

    divergentes = []
    for user_id, (wins, losses) in sorted(por_cliente.items()):
        # Operação recém-fechada: o placar do banco ainda não chegou.
        if ultima_no_historico.get(user_id, 1e9) <= CARENCIA_SEGUNDOS:
            continue
        estado = estados.get(user_id) or {}
        placar = (int(estado.get("wins") or 0), int(estado.get("losses") or 0))
        if placar != (wins, losses):
            divergentes.append({
                "user_id": user_id,
                "banco": placar,
                "historico": (wins, losses),
                "status": estado.get("status"),
                "chave": f"placar:{user_id}:{placar[0]}x{placar[1]}:{wins}x{losses}",
            })

    return {
        "corte": corte,
        "clientes": len(por_cliente),
        "divergentes": divergentes,
        "pendentes_com_final": pendentes_com_final,
        "orfas": orfas,
        "sem_historico": sem_historico,
    }


def descrever(achados: dict, *, detalhe: bool, horas_orfa: int) -> list[str]:
    """Relatório em texto (usado no terminal e no e-mail)."""
    linhas = [
        f"Auditoria do placar — dia de Brasília iniciado em {achados['corte'][:19]}Z",
        f"  clientes que operaram hoje        : {achados['clientes']}",
        f"  placar x Histórico                : {len(achados['divergentes'])} divergente(s)",
        f"  espelho pendente c/ resultado final: {len(achados['pendentes_com_final'])}",
        f"  ordens órfãs (> {horas_orfa}h)              : {len(achados['orfas'])}",
        f"  operação sem Histórico            : {len(achados['sem_historico'])}",
    ]
    if not detalhe:
        return linhas
    if achados["divergentes"]:
        linhas.append("\nDivergências (placar no banco x ciclos fechados no Histórico):")
        for item in achados["divergentes"]:
            banco, hist = item["banco"], item["historico"]
            linhas.append(
                f"  {item['user_id']}  banco={banco[0]}x{banco[1]}  "
                f"histórico={hist[0]}x{hist[1]}  status={item['status']}"
            )
    if achados["pendentes_com_final"]:
        linhas.append("\nEspelho PENDENTE com resultado final no Histórico (regravado por cima):")
        for item in achados["pendentes_com_final"][:30]:
            linhas.append(
                f"  {item['user_id']}  {item['order_id']}  histórico={item['final'].get('result')}"
            )
    if achados["orfas"]:
        linhas.append("\nOrdens órfãs:")
        for item in achados["orfas"][:30]:
            linhas.append(f"  {item['user_id']}  {item['order_id']}  {str(item['executed_at'])[:19]}")
    if achados["sem_historico"]:
        linhas.append("\nOperações sem Histórico:")
        for item in achados["sem_historico"][:30]:
            marca = "  (ressuscitada)" if item["ressuscitada"] else ""
            linhas.append(f"  {item['user_id']}  {item['order_id']}  {item.get('result')}{marca}")
    return linhas


def corrigir(base: str, chave: str, achados: dict) -> list[str]:
    """Conserta o espelho: pendente → resultado do Histórico; apaga ressuscitada."""
    feitos: list[str] = []
    for item in achados["pendentes_com_final"]:
        final = item["final"]
        trade = {
            **(item.get("trade_json") or {}),
            "result": final.get("result"),
            "profit": final.get("profit"),
            "finished_at": final.get("finished_at"),
            "cycle_result": final.get("cycle_result"),
            "final_result": final.get("final_result") or final.get("result"),
        }
        filtro = (
            f"robot_trades?user_id=eq.{urllib.parse.quote(item['user_id'])}"
            f"&order_id=eq.{urllib.parse.quote(str(item['order_id']))}"
        )
        _requisitar(base, chave, "PATCH", filtro, {
            "result": final.get("result"),
            "profit": final.get("profit"),
            "trade_json": trade,
        })
        feitos.append(f"pendente→{final.get('result')}  {item['user_id']}  {item['order_id']}")
    for item in achados["sem_historico"]:
        if not item["ressuscitada"]:
            continue  # pode ser gravação do Histórico que falhou: só avisa
        filtro = (
            f"robot_trades?user_id=eq.{urllib.parse.quote(item['user_id'])}"
            f"&order_id=eq.{urllib.parse.quote(str(item['order_id']))}"
        )
        _requisitar(base, chave, "DELETE", filtro)
        feitos.append(f"ressuscitada apagada  {item['user_id']}  {item['order_id']}")
    return feitos


def total(achados: dict) -> int:
    return sum(
        len(achados[k]) for k in ("divergentes", "pendentes_com_final", "orfas", "sem_historico")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detalhe", action="store_true", help="lista cliente por cliente")
    parser.add_argument(
        "--horas-orfa", type=int, default=1, help="idade mínima para acusar órfã"
    )
    parser.add_argument(
        "--corrigir", action="store_true", help="conserta o espelho robot_trades"
    )
    args = parser.parse_args()

    base = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    chave = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not chave:
        print("Faltam SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY no ambiente.")
        return 2

    achados = auditar(base, chave, horas_orfa=args.horas_orfa)
    print("\n".join(descrever(achados, detalhe=args.detalhe or args.corrigir, horas_orfa=args.horas_orfa)))
    if args.corrigir:
        feitos = corrigir(base, chave, achados)
        print(f"\nCORRIGIDO no espelho: {len(feitos)} linha(s)")
        for linha in feitos:
            print(f"  {linha}")
        return 0

    problemas = total(achados)
    if problemas:
        print(f"\nRESULTADO: {problemas} problema(s). Rode com --detalhe para a lista.")
        return 1
    print("\nRESULTADO: placar, Histórico e espelho de ordens batem.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
