#!/usr/bin/env python3
"""Recupera operações que existem em ``robot_trades`` e sumiram do Histórico.

Duas causas conhecidas (ver backend/docs/PLACAR_DIAGNOSTICO_2026-09-15.md):

- **Empate (DRAW)**: o CHECK da tabela só admitia WIN/LOSS, o POST voltava 400 e
  o ``except`` genérico engolia. 45 de 45 desde 01/09. Só backfilla depois de
  rodar ``backend/migration_history_allow_draw.sql``.
- **Falha transitória** no POST ao Supabase: 14 LOSS reais (0,35%) sem linha,
  sem nada em comum entre elas.

O placar NÃO muda: empate não conta ponto, e WIN/LOSS já estavam contabilizados
na sessão. Isto conserta só o que o cliente vê no Histórico.

Uso::

    python scripts/backfill_history_from_trades.py --dry-run          # só relata
    python scripts/backfill_history_from_trades.py --apply            # grava
    python scripts/backfill_history_from_trades.py --apply --desde 2026-09-01

Precisa de ``SUPABASE_URL`` e ``SUPABASE_SERVICE_ROLE_KEY`` no ambiente
(``set -a && . ./.env && set +a`` a partir de ``/opt/elcapo/backend``).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backend.robot_persistence import build_trade_history_item  # noqa: E402

RESULTADOS_FINAIS = ("WIN", "LOSS", "DRAW")


def _requisicao(url: str, chave: str, metodo: str = "GET", corpo=None, extra=None):
    req = urllib.request.Request(url, method=metodo)
    req.add_header("apikey", chave)
    req.add_header("Authorization", f"Bearer {chave}")
    for nome, valor in (extra or {}).items():
        req.add_header(nome, valor)
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=dados, timeout=60) as resp:
        conteudo = resp.read()
        return resp.status, (json.loads(conteudo) if conteudo else [])


def _listar(base: str, chave: str, tabela: str, params: dict) -> list[dict]:
    """Lê uma tabela inteira, paginando de 1000 em 1000."""
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--dry-run", action="store_true", help="só relata, não grava")
    grupo.add_argument("--apply", action="store_true", help="grava as linhas faltantes")
    parser.add_argument("--desde", default="2026-09-01", help="data inicial (YYYY-MM-DD)")
    parser.add_argument(
        "--somente",
        default="",
        help="só estes resultados, separados por vírgula (ex.: WIN,LOSS). "
             "Use enquanto a migration do empate não rodou: DRAW ainda é "
             "recusado pelo CHECK da tabela e cada linha volta 400.",
    )
    args = parser.parse_args()
    permitidos = tuple(
        x.strip().upper() for x in args.somente.split(",") if x.strip()
    ) or RESULTADOS_FINAIS

    base = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    chave = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not chave:
        print("Faltam SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY no ambiente.")
        return 2

    corte = f"{args.desde}T00:00:00+00:00"
    print(f"Lendo operações desde {args.desde}...")
    espelhos = _listar(base, chave, "robot_trades", {
        "select": "user_id,order_id,result,executed_at,trade_json",
        "executed_at": f"gte.{corte}",
        "order": "executed_at.asc",
    })
    historico = _listar(base, chave, "robot_trade_history", {
        "select": "user_id,order_id",
        "finished_at": f"gte.{corte}",
        # `order` é obrigatório: sem ordenação estável o PostgREST repete e
        # pula linhas entre as páginas do Range, e o diff acusa como
        # "faltando" operação que está lá (1202 falsos positivos no 1º teste).
        "order": "id.asc",
    })
    ja_tem = {(linha["user_id"], str(linha["order_id"])) for linha in historico}
    print(f"  robot_trades: {len(espelhos)} | robot_trade_history: {len(historico)}")

    def _e_cliente_real(user_id: str) -> bool:
        """Todo cliente chega com UUID do Supabase Auth.

        `robot_trades` de produção tem lixo de teste (`user-real-finished`,
        `marketing-scoreboard`, ordens `ord-finish-1`): sem este filtro o
        backfill copiava essas linhas para o Histórico de produção. Mesma
        defesa do `_is_valid_account_user_id` do robot_runtime_main.
        """
        try:
            uuid.UUID(str(user_id))
            return True
        except (ValueError, AttributeError, TypeError):
            return False

    ignoradas = [
        linha for linha in espelhos
        if str(linha.get("result") or "").upper() in permitidos
        and (linha["user_id"], str(linha["order_id"])) not in ja_tem
        and not _e_cliente_real(linha["user_id"])
    ]
    if ignoradas:
        print(f"  ignorando {len(ignoradas)} linha(s) de teste (user_id não é UUID)")

    faltando = [
        linha for linha in espelhos
        if str(linha.get("result") or "").upper() in permitidos
        and (linha["user_id"], str(linha["order_id"])) not in ja_tem
        and _e_cliente_real(linha["user_id"])
    ]
    por_resultado = collections.Counter(str(x.get("result")).upper() for x in faltando)
    print(f"\nSem linha no Histórico: {len(faltando)}  {dict(por_resultado)}")
    if not faltando:
        return 0

    gravadas = 0
    problemas: list[tuple[str, str]] = []
    for linha in faltando:
        operacao = linha.get("trade_json") or {}
        rotulo = f"{linha['user_id'][:8]} {linha['order_id']} {linha.get('result')}"
        try:
            item = build_trade_history_item(linha["user_id"], operacao)
            item.pop("id", None)
        except Exception as erro:  # noqa: BLE001
            problemas.append((rotulo, f"não dá para montar: {erro}"))
            continue
        if args.dry_run:
            print(f"  [simulado] {rotulo} {item['active']} {item['finished_at'][:19]}")
            gravadas += 1
            continue
        try:
            status, _ = _requisicao(
                f"{base}/rest/v1/robot_trade_history?on_conflict=user_id,order_id",
                chave,
                metodo="POST",
                corpo=item,
                extra={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            print(f"  [gravado {status}] {rotulo}")
            gravadas += 1
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode()[:200]
            problemas.append((rotulo, f"HTTP {erro.code}: {detalhe}"))
        except Exception as erro:  # noqa: BLE001
            problemas.append((rotulo, f"{type(erro).__name__}: {erro}"))

    modo = "simuladas" if args.dry_run else "gravadas"
    print(f"\n{gravadas} linhas {modo}; {len(problemas)} com problema")
    for rotulo, motivo in problemas[:20]:
        print(f"  ! {rotulo}: {motivo}")
    if len(problemas) > 20:
        print(f"  ... e mais {len(problemas) - 20}")
    return 1 if problemas else 0


if __name__ == "__main__":
    raise SystemExit(main())
