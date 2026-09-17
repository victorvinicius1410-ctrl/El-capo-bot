"""Criação de contas e links de uso único via Supabase Auth Admin."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        Inicializa o cliente server-only.

        Args:
            supabase_url: URL do projeto Supabase.
            service_role_key: Credencial restrita ao backend.
            redirect_url: URL HTTPS permitida após validar o link.
            transport: Transporte HTTP alternativo (testes exercitam a
                requisição real em vez de trocar a classe do cliente).
        """
        self.base_url = supabase_url.rstrip("/")
        self.redirect_url = redirect_url
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        """Cria o cliente HTTP honrando o transporte injetado em testes."""
        return httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=False,
            transport=self._transport,
        )

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
        async with self._client() as client:
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
                logger.error(
                    "account_link.create_failed status=%s",
                    created.status_code,
                )
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
        except AccountLinkError as exc:
            # A rota pública responde 202 mesmo aqui (anti-enumeração); sem
            # este log uma falha de parsing fica invisível indefinidamente.
            logger.warning("account_link.recovery_unavailable reason=%s", exc)
            return None

    async def _generate_link(self, link_type: str, email: str) -> AccountLink:
        """Solicita link e extrai o tenant somente do app_metadata confiável."""
        async with self._client() as client:
            response = await client.post(
                f"{self.base_url}/auth/v1/admin/generate_link",
                headers=self.headers,
                # `redirect_to` vai na RAIZ: a API HTTP da Auth Admin ignora
                # `options` (formato do client JS) e cai no Site URL do projeto.
                json={
                    "type": link_type,
                    "email": email,
                    "redirect_to": self.redirect_url,
                },
            )
        if response.status_code >= 400:
            logger.warning(
                "account_link.generate_link_rejected status=%s",
                response.status_code,
            )
            raise AccountLinkError("ACCOUNT_LINK_FAILED")
        body: dict[str, Any] = response.json()
        # A Auth Admin devolve os campos do usuário na RAIZ da resposta;
        # "user"/"properties" só aparecem em respostas derivadas de client SDK.
        nested = body.get("user") if isinstance(body.get("user"), dict) else {}
        properties = (
            body.get("properties")
            if isinstance(body.get("properties"), dict)
            else {}
        )
        app_metadata = next(
            (
                candidate
                for candidate in (body.get("app_metadata"), nested.get("app_metadata"))
                if isinstance(candidate, dict)
            ),
            {},
        )
        user_id = str(body.get("id") or nested.get("id") or "")
        action_link = str(
            body.get("action_link")
            or properties.get("action_link")
            or nested.get("action_link")
            or ""
        )
        company_id = str(app_metadata.get("company_id") or "")
        if not company_id and user_id:
            company_id = await self._company_from_access_profile(user_id)
        if not action_link:
            logger.error(
                "account_link.no_action_link status=%s keys=%s",
                response.status_code,
                sorted(body.keys()),
            )
            raise AccountLinkError("ACCOUNT_LINK_NO_ACTION_LINK")
        if not user_id or not company_id:
            logger.error(
                "account_link.no_identity status=%s has_user_id=%s has_company=%s",
                response.status_code,
                bool(user_id),
                bool(company_id),
            )
            raise AccountLinkError("ACCOUNT_LINK_NO_COMPANY")
        return AccountLink(
            user_id=user_id,
            company_id=company_id,
            action_link=action_link,
        )

    async def _company_from_access_profile(self, user_id: str) -> str:
        """
        Resolve o tenant pelo perfil persistido quando falta no app_metadata.

        Contas criadas fora do fluxo de compra podem não ter ``company_id`` no
        ``app_metadata``. ``user_access_profiles.user_id`` é chave primária, então
        a consulta é determinística — nunca escolhe um tenant por conveniência.
        Perfis com ``deleted_at`` ficam de fora: conta removida não deve render
        link de recuperação nem ser adotada por uma compra nova.

        Args:
            user_id: Identidade já validada pela Auth Admin.

        Returns:
            Tenant do perfil, ou string vazia quando não houver perfil.
        """
        async with self._client() as client:
            response = await client.get(
                f"{self.base_url}/rest/v1/user_access_profiles",
                headers=self.headers,
                params={
                    "user_id": f"eq.{user_id}",
                    "deleted_at": "is.null",
                    "select": "company_id",
                    "limit": "1",
                },
            )
        if response.status_code >= 400:
            logger.warning(
                "account_link.profile_lookup_failed status=%s",
                response.status_code,
            )
            return ""
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            return ""
        return str(rows[0].get("company_id") or "")
