#!/usr/bin/env python3
"""Roda a auditoria do placar e manda e-mail quando um problema persiste.

Chamado pelo cron (``/etc/cron.d/elcapo-auditoria-placar``) a cada 15 min.
Existe para o placar errado não depender de relato de cliente: em 28/09 o
defeito só apareceu quando o Sergio reclamou do 2x3 que virou 1x4
(docs/PLACAR_OVERLAY.md §2026-09-29).

Só avisa o que aparece em **duas rodadas seguidas** — uma operação que fecha no
meio da auditoria gera divergência passageira — e avisa cada problema uma vez
só; se ele sumir e voltar, avisa de novo.

Uso::

    python scripts/alerta_auditoria_placar.py --para dono@exemplo.com
    python scripts/alerta_auditoria_placar.py --para dono@exemplo.com --teste

Ambiente: ``SUPABASE_URL``, ``SUPABASE_SERVICE_ROLE_KEY`` e o SMTP de produção
(``PROD_SMTP_HOST``, ``PROD_SMTP_PORT``, ``PROD_SMTP_USERNAME``,
``PROD_SMTP_PASSWORD``, ``PROD_SMTP_USE_SSL``, ``PROD_EMAIL_FROM``).
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auditoria_placar  # noqa: E402

ESTADO_PADRAO = "/root/deploy-elcapo/logs/auditoria-placar-estado.json"


def _ler_estado(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text())
    except (OSError, ValueError):
        return {"vistos": [], "avisados": []}


def _enviar(para: str, assunto: str, corpo: str) -> None:
    host = os.environ["PROD_SMTP_HOST"]
    porta = int(os.getenv("PROD_SMTP_PORT") or 465)
    usuario = os.environ["PROD_SMTP_USERNAME"]
    senha = os.environ["PROD_SMTP_PASSWORD"]
    remetente = os.getenv("PROD_EMAIL_FROM") or usuario
    mensagem = EmailMessage()
    mensagem["Subject"] = assunto
    mensagem["From"] = remetente
    mensagem["To"] = para
    mensagem.set_content(corpo)
    if str(os.getenv("PROD_SMTP_USE_SSL") or "true").lower() in {"1", "true", "yes"}:
        with smtplib.SMTP_SSL(host, porta, timeout=30) as cliente:
            cliente.login(usuario, senha)
            cliente.send_message(mensagem)
    else:
        with smtplib.SMTP(host, porta, timeout=30) as cliente:
            cliente.ehlo()
            cliente.starttls()
            cliente.ehlo()
            cliente.login(usuario, senha)
            cliente.send_message(mensagem)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--para", required=True, help="e-mail que recebe o alerta")
    parser.add_argument("--estado", default=ESTADO_PADRAO, help="arquivo de estado entre rodadas")
    parser.add_argument("--teste", action="store_true", help="só manda um e-mail de teste")
    args = parser.parse_args()

    if args.teste:
        _enviar(
            args.para,
            "[El Capo] teste do alerta do placar",
            "Se você recebeu isto, o alerta da auditoria do placar consegue mandar e-mail.",
        )
        print("[ALERTA_PLACAR] e-mail de teste enviado")
        return 0

    base = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    chave = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not chave:
        print("[ALERTA_PLACAR] faltam SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY")
        return 2

    achados = auditoria_placar.auditar(base, chave)
    itens = {
        item["chave"]: item
        for grupo in ("divergentes", "pendentes_com_final", "orfas", "sem_historico")
        for item in achados[grupo]
    }
    caminho = Path(args.estado)
    estado = _ler_estado(caminho)
    confirmados = set(itens) & set(estado.get("vistos") or [])
    # Esquece o que sumiu: se voltar, é problema novo e merece aviso.
    avisados = set(estado.get("avisados") or []) & set(itens)
    novos = sorted(confirmados - avisados)

    resumo = auditoria_placar.descrever(achados, detalhe=True, horas_orfa=1)
    print("\n".join(resumo[:6]))
    if novos:
        corpo = "\n".join(
            [
                f"{len(novos)} problema(s) novo(s) no placar, confirmados em duas rodadas:",
                *[f"  - {k}" for k in novos],
                "",
                *resumo,
                "",
                "Diagnóstico: docs/PLACAR_OVERLAY.md §2026-09-29.",
                "Consertar o espelho: python scripts/auditoria_placar.py --corrigir",
            ]
        )
        try:
            _enviar(args.para, f"[El Capo] placar: {len(novos)} problema(s) novo(s)", corpo)
            avisados.update(novos)
            print(f"[ALERTA_PLACAR] e-mail enviado novos={len(novos)}")
        except Exception as erro:  # noqa: BLE001
            # Não marca como avisado: a próxima rodada tenta de novo.
            print(f"[ALERTA_PLACAR] falha no e-mail: {erro.__class__.__name__}: {erro}")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps({"vistos": sorted(itens), "avisados": sorted(avisados)}))
    return 1 if itens else 0


if __name__ == "__main__":
    raise SystemExit(main())
