"""Criação de contas e links de uso único via Supabase Auth Admin."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

import httpx


class AccountLinkError(Exception):
    """Erro seguro ao gerar um link de acesso."""


@dataclass(frozen=True)
class AccountLink:
    """Link temporário associado a uma identidade verificada."""

    user_id: str
    company_id: str
    action_link: str


class SupabaseAccountLinkService:
    """Usa Auth Admin somente no backend para criar links de uso único."""

    def __init__(
        self,
        supabase_url: str,
        service_role_key: str,
        *,
        redirect_url: str,
    ) -> None:
        """
        Inicializa o cliente server-only.

        Args:
            supabase_url: URL do projeto Supabase.
            service_role_key: Credencial restrita ao backend.
            redirect_url: URL HTTPS permitida após validar o link.
        """
        self.base_url = supabase_url.rstrip("/")
        self.redirect_url = redirect_url
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }

    async def create_purchase_account(
        self,
        *,
        company_id: str,
        email: str,
        name: str,
    ) -> AccountLink:
        """
        Cria usuário sem senha conhecida e gera link de primeiro acesso.

        Args:
            company_id: Tenant resolvido pelo plano Cakto.
            email: Email recebido na compra.
            name: Nome do comprador.

        Returns:
            Identidade criada e link de uso único.

        Raises:
            AccountLinkError: Quando a Auth Admin não concluir a operação.
        """
        generated_password = secrets.token_urlsafe(32)
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            created = await client.post(
                f"{self.base_url}/auth/v1/admin/users",
                headers=self.headers,
                json={
                    "email": email,
                    "password": generated_password,
                    "email_confirm": True,
                    "user_metadata": {"name": name},
                    "app_metadata": {
                        "company_id": company_id,
                        "is_admin": False,
                    },
                },
            )
            if created.status_code in {409, 422}:
                existing_link = await self._generate_link("recovery", email)
                if existing_link.company_id != company_id:
                    raise AccountLinkError("ACCOUNT_TENANT_MISMATCH")
                return existing_link
            if created.status_code >= 400:
                raise AccountLinkError("ACCOUNT_CREATE_FAILED")
            user = created.json()
        link = await self._generate_link("recovery", email)
        if link.company_id != company_id or link.user_id != str(user.get("id") or ""):
            raise AccountLinkError("ACCOUNT_TENANT_MISMATCH")
        return link

    async def create_recovery_link(self, email: str) -> AccountLink | None:
        """
        Gera recuperação sem revelar se o email existe.

        Args:
            email: Identificador informado na tela pública.

        Returns:
            Link e identidade quando a conta existe; caso contrário, ``None``.
        """
        try:
            return await self._generate_link("recovery", email)
        except AccountLinkError:
            return None

    async def _generate_link(self, link_type: str, email: str) -> AccountLink:
        """Solicita link e extrai o tenant somente do app_metadata confiável."""
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            response = await client.post(
                f"{self.base_url}/auth/v1/admin/generate_link",
                headers=self.headers,
                json={
                    "type": link_type,
                    "email": email,
                    "options": {"redirect_to": self.redirect_url},
                },
            )
        if response.status_code >= 400:
            raise AccountLinkError("ACCOUNT_LINK_FAILED")
        body: dict[str, Any] = response.json()
        user = body.get("user") if isinstance(body.get("user"), dict) else {}
        app_metadata = (
            user.get("app_metadata")
            if isinstance(user.get("app_metadata"), dict)
            else {}
        )
        properties = (
            body.get("properties")
            if isinstance(body.get("properties"), dict)
            else {}
        )
        company_id = str(app_metadata.get("company_id") or "")
        user_id = str(user.get("id") or "")
        action_link = str(body.get("action_link") or properties.get("action_link") or "")
        if not company_id or not user_id or not action_link:
            raise AccountLinkError("ACCOUNT_LINK_INVALID")
        return AccountLink(
            user_id=user_id,
            company_id=company_id,
            action_link=action_link,
        )
