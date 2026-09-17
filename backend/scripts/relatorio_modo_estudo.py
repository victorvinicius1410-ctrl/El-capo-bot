#!/usr/bin/env python3
"""Relatório das operações feitas com o Modo Estudo ligado (17/09/2026).

O Modo Estudo esconde análise e loss no painel da conta marketing. Aqui o
estudo é feito de verdade: lê TODAS as operações marcadas
(``analysis_json.study_mode = true``), com win, loss e empate, e mostra onde o
acerto muda — estratégia, ativo, hora, confiança, gale, REV-Z e S/R.

Somente leitura. Conta ciclos pela linha que fecha o ciclo (``cycle_result``),
igual ao placar: um ciclo com gale tem duas linhas e só a última conta.

Uso::

    python scripts/relatorio_modo_estudo.py
    python scripts/relatorio_modo_estudo.py --user <uuid> --desde 2026-09-17

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

RESULTADOS = ("WIN", "LOSS", "DRAW")


def _listar(base: str, chave: str, params: dict) -> list[dict]:
    """Lê ``robot_trade_history`` paginando de 1000 em 1000."""
    linhas: list[dict] = []
    inicio = 0
    while True:
        query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        req = urllib.request.Request(f"{base}/rest/v1/robot_trade_history?{query}")
        req.add_header("apikey", chave)
        req.add_header("Authorization", f"Bearer {chave}")
        req.add_header("Range", f"{inicio}-{inicio + 999}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            pagina = json.loads(resp.read() or b"[]")
        linhas.extend(pagina)
        if len(pagina) < 1000:
            return linhas
        inicio += 1000


def _analise(linha: dict) -> dict:
    bruto = linha.get("analysis_json")
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto)
        except ValueError:
            bruto = {}
    return bruto if isinstance(bruto, dict) else {}


def _faixa_confianca(valor: object) -> str:
    try:
        numero = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    base = int(numero // 10) * 10
    return f"{base}-{base + 9}"


def _hora_utc(valor: object) -> str:
    try:
        quando = datetime.datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return "?"
    return f"{quando.astimezone(datetime.timezone.utc).hour:02d}h"


def _imprimir(titulo: str, grupos: dict[str, collections.Counter]) -> None:
    print(f"\n== {titulo}")
    print(f"{'':<28}{'WIN':>6}{'LOSS':>6}{'EMP':>6}{'acerto':>9}")
    ordem = sorted(grupos.items(), key=lambda item: -sum(item[1].values()))
    for nome, cont in ordem:
        decididos = cont["WIN"] + cont["LOSS"]
        acerto = f"{100 * cont['WIN'] / decididos:.1f}%" if decididos else "-"
        print(f"{nome[:27]:<28}{cont['WIN']:>6}{cont['LOSS']:>6}{cont['DRAW']:>6}{acerto:>9}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user", help="filtra por user_id")
    parser.add_argument("--desde", help="data ISO mínima de finished_at (UTC)")
    args = parser.parse_args()

    base = os.environ["SUPABASE_URL"].rstrip("/")
    chave = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    params = {
        "select": "*",
        "analysis_json->>study_mode": "eq.true",
        "order": "finished_at.asc,order_id.asc",
    }
    if args.user:
        params["user_id"] = f"eq.{args.user}"
    if args.desde:
        params["finished_at"] = f"gte.{args.desde}"

    linhas = _listar(base, chave, params)
    dimensoes: dict[str, dict[str, collections.Counter]] = collections.defaultdict(
        lambda: collections.defaultdict(collections.Counter)
    )
    total: collections.Counter = collections.Counter()
    for linha in linhas:
        resultado = str(linha.get("cycle_result") or "").upper()
        if resultado not in RESULTADOS:
            continue
        analise = _analise(linha)
        total[resultado] += 1
        chaves = {
            "estratégia": str(analise.get("strategy_key") or analise.get("strategy_name") or "?"),
            "ativo": str(linha.get("active") or "?"),
            "hora UTC": _hora_utc(linha.get("finished_at")),
            "confiança": _faixa_confianca(linha.get("confidence")),
            "direção": str(linha.get("direction") or "?"),
            "etapa de gale": str(linha.get("gale_step") or 0),
            "REV-Z": "com REV-Z" if analise.get("revz") else "sem REV-Z",
            "S/R na análise": str(analise.get("sr_respect_reason") or "?"),
            "pavio na análise": str(analise.get("wick_reason") or "?"),
        }
        for dimensao, valor in chaves.items():
            dimensoes[dimensao][valor][resultado] += 1

    print(f"Operações do Modo Estudo: {sum(total.values())} ciclos fechados")
    _imprimir("TOTAL", {"todas": total})
    for dimensao, grupos in dimensoes.items():
        _imprimir(dimensao, grupos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
