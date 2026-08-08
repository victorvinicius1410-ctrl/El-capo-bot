"""Cadastro público de leads com aprovação administrativa.

Leads nascem sem acesso operacional (`grant_access=false`) e com
`approval_status=pending` até um admin liberar dias ou uma compra aprovar.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.admin_models import (
    AccountType,
    ApprovalStatus,
    ClientCreate,
    ClientRecord,
    PaymentStatus,
)
from backend.admin_repository import AdminRepository
from backend.auth_session_service import PasswordPolicy, PasswordPolicyError

logger = logging.getLogger("backend-registration")

DEFAULT_COMPANY_ID = "00000000-0000-0000-0000-000000000001"
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegistrationError(Exception):
    """Falha de validação ou regra de negócio no cadastro público."""


class EmailAlreadyRegisteredError(RegistrationError):
    """O e-mail já possui conta no provedor de autenticação."""


@dataclass(frozen=True)
class RegisterLeadPayload:
    """Dados aceitos no cadastro self-service de lead."""

    name: str
    email: str
    password: str
    phone: str | None = None


class RegistrationService:
    """Regras de cadastro público sem conceder acesso operacional."""

    def __init__(
        self,
        repository: AdminRepository,
        *,
        company_id: str = DEFAULT_COMPANY_ID,
    ) -> None:
        """
        Inicializa o serviço de registro.

        Args:
            repository: Persistência administrativa (Auth + perfil).
            company_id: Tenant fixo no servidor (nunca vem do frontend).
        """
        self.repository = repository
        self.company_id = company_id

    async def register_lead(self, payload: RegisterLeadPayload) -> ClientRecord:
        """
        Cria um lead pendente de aprovação sem acesso operacional.

        Args:
            payload: Nome, e-mail, senha e telefone opcional.

        Returns:
            Perfil persistido com ``approval_status=pending``.

        Raises:
            RegistrationError: Nome/e-mail inválidos.
            PasswordPolicyError: Senha fora da política.
            EmailAlreadyRegisteredError: E-mail já cadastrado.
        """
        name = payload.name.strip()
        email = payload.email.strip().lower()
        phone = payload.phone.strip() if payload.phone and payload.phone.strip() else None

        if len(name) < 2:
            raise RegistrationError("Nome deve ter pelo menos 2 caracteres")
        if not _EMAIL_PATTERN.match(email):
            raise RegistrationError("Email inválido")

        valid, error = PasswordPolicy.validate(payload.password)
        if not valid:
            raise PasswordPolicyError(error or "Senha inválida")

        trader_id = f"LEAD-{secrets.token_hex(5).upper()}"
        try:
            record = await self.repository.create_client(
                self.company_id,
                ClientCreate(
                    name=name,
                    email=email,
                    phone=phone,
                    trader_id=trader_id,
                    password=payload.password,
                    account_type=AccountType.CLIENT,
                    trial_days=None,
                    payment_status=PaymentStatus.PENDING,
                ),
            )
        except Exception as exc:
            if _is_duplicate_email_error(exc):
                raise EmailAlreadyRegisteredError(
                    "Este e-mail já está cadastrado"
                ) from exc
            logger.error(
                "registration.create_failed company_id=%s email_domain=%s",
                self.company_id,
                email.split("@")[-1] if "@" in email else "unknown",
                exc_info=True,
            )
            raise RegistrationError("Não foi possível concluir o cadastro") from exc

        now = datetime.now(timezone.utc)
        record.approval_status = ApprovalStatus.PENDING
        record.grant_access = False
        record.plan_name = "Aguardando aprovação"
        record.payment_status = PaymentStatus.PENDING
        record.account_type = AccountType.CLIENT
        record.expires_at = None
        record.updated_at = now
        return await self.repository.save_client(record)


def _is_duplicate_email_error(error: Exception) -> bool:
    """Detecta conflito de e-mail no Auth Admin / PostgREST."""
    message = str(error).lower()
    markers = (
        "already been registered",
        "already registered",
        "user already exists",
        "email_exists",
        "duplicate key",
        "unique constraint",
        "23505",
    )
    return any(marker in message for marker in markers)
