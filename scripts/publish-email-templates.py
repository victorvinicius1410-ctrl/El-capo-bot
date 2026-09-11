#!/usr/bin/env python3
"""
Publica o conteúdo de fábrica dos e-mails no tenant, ativando o envio.

Escreve na persistência real (tabelas ``email_templates`` ou, enquanto a
migration SQL não roda, o bucket Storage ``email-templates``), preservando o
``id`` e o ``created_at`` da revisão atual. Sem ``--apply`` só simula.

Templates que o admin personalizou em Admin -> E-mails são sobrescritos: use
``--skip`` para preservá-los.

Uso::

    cd /opt/elcapo/backend
    set -a && . ./.env && set +a
    .venv/bin/python ../scripts/publish-email-templates.py --skip purchase.completed
    .venv/bin/python ../scripts/publish-email-templates.py --skip purchase.completed --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/elcapo/backend")

from backend.email_models import EmailTemplate
from backend.email_repository import SupabaseEmailRepository, new_template_id
from backend.email_templates_default import DEFAULT_BODIES, DEFAULT_SUBJECTS
from backend.webhook_models import DomainEventType

DEFAULT_COMPANY = "00000000-0000-0000-0000-000000000001"


def parse_args() -> argparse.Namespace:
    """Lê as opções de linha de comando."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", default=DEFAULT_COMPANY, help="ID do tenant")
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="EVENTO",
        help="Evento a preservar como está (repetível)",
    )
    parser.add_argument("--apply", action="store_true", help="Grava de verdade")
    return parser.parse_args()


async def main() -> int:
    """Publica os templates e devolve o código de saída do processo."""
    args = parse_args()
    skip = {item.strip() for item in args.skip}
    unknown = skip - {event.value for event in DomainEventType}
    if unknown:
        print(f"evento desconhecido em --skip: {sorted(unknown)}", file=sys.stderr)
        return 2
    try:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    except KeyError as exc:
        print(f"variável de ambiente ausente: {exc}", file=sys.stderr)
        return 2

    repository = SupabaseEmailRepository(url, key)
    now = datetime.now(timezone.utc)
    mode = "grava" if args.apply else "simula"
    print(f"[{mode}] tenant={args.company}")
    for event in DomainEventType:
        if event.value in skip:
            print(f"  --   {event.value:36s} preservado (--skip)")
            continue
        current = await repository.get_template(args.company, event)
        before = (
            f"enabled={str(current.is_enabled):5s} len={len(current.html_body)}"
            if current
            else "inexistente"
        )
        template = EmailTemplate(
            id=current.id if current else new_template_id(),
            company_id=args.company,
            event_type=event,
            subject=DEFAULT_SUBJECTS[event],
            html_body=DEFAULT_BODIES[event],
            is_enabled=True,
            created_at=current.created_at if current else now,
            updated_at=now,
        )
        if not args.apply:
            print(f"  ..   {event.value:36s} {before:28s} -> enabled=True  len={len(template.html_body)}")
            continue
        saved = await repository.upsert_template(template)
        print(f"  ok   {event.value:36s} {before:28s} -> enabled={saved.is_enabled} len={len(saved.html_body)}")
    if not args.apply:
        print("\nnada foi gravado — repita com --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
