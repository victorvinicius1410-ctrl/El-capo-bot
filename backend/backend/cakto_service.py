"""Configuração server-only e cliente HTTP assíncrono da Cakto."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


class CaktoConfigurationError(ValueError):
    """Indica configuração inválida da integração Cakto."""


class CaktoAuthenticationError(ValueError):
    """Indica segredo inválido no payload público."""


@dataclass(frozen=True)
class CaktoOfferCreated:
    """Resultado da criação de oferta na Cakto, sem credenciais."""

    offer_id: str
    product_id: str
    checkout_url: str
    name: str
    price: float


@dataclass(frozen=True)
class CaktoConfig:
    """Credenciais Cakto mantidas exclusivamente no processo do servidor."""

    enabled: bool
    webhook_secret: str | None
    oauth_token: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    default_product_id: str | None = None
    api_base_url: str = "https://api.cakto.com.br"

    @property
    def webhook_configured(self) -> bool:
        """Informa disponibilidade do webhook sem expor o segredo."""
        return self.enabled and bool(self.webhook_secret)

    @property
    def api_configured(self) -> bool:
        """Informa disponibilidade da reconciliação sem expor o token."""
        return self.enabled and bool(
            self.oauth_token or (self.oauth_client_id and self.oauth_client_secret)
        )

    @property
    def offers_provisioning_configured(self) -> bool:
        """Informa se a API pode criar ofertas no produto padrão."""
        return self.api_configured and bool(self.default_product_id)

    @classmethod
    def from_environment(cls) -> CaktoConfig:
        """
        Carrega e valida variáveis server-only.

        A integração desabilitada não exige credenciais, permitindo startup local
        seguro. Quando habilitada, o segredo do webhook é obrigatório e forte.
        """
        enabled = _environment_value("CAKTO_ENABLED", "false").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        secret = _environment_value("CAKTO_WEBHOOK_SECRET", "").strip() or None
        token = _environment_value("CAKTO_OAUTH_TOKEN", "").strip() or None
        client_id = _environment_value("CAKTO_OAUTH_CLIENT_ID", "").strip() or None
        client_secret = _environment_value("CAKTO_OAUTH_CLIENT_SECRET", "").strip() or None
        default_product_id = (
            _environment_value("CAKTO_DEFAULT_PRODUCT_ID", "").strip() or None
        )
        base_url = _environment_value(
            "CAKTO_API_BASE_URL",
            "https://api.cakto.com.br",
        ).strip()
        if not enabled:
            return cls(
                enabled=False,
                webhook_secret=None,
                oauth_token=None,
                oauth_client_id=None,
                oauth_client_secret=None,
                default_product_id=None,
                api_base_url="https://api.cakto.com.br",
            )
        if secret is None or len(secret) < 16:
            raise CaktoConfigurationError(
                "CAKTO_WEBHOOK_SECRET deve ter pelo menos 16 caracteres"
            )
        if token is not None and len(token) < 16:
            raise CaktoConfigurationError(
                "CAKTO_OAUTH_TOKEN deve ter pelo menos 16 caracteres"
            )
        if bool(client_id) != bool(client_secret):
            raise CaktoConfigurationError(
                "CAKTO_OAUTH_CLIENT_ID e CAKTO_OAUTH_CLIENT_SECRET são obrigatórios em conjunto"
            )
        if client_id is not None and (len(client_id) < 8 or len(client_secret or "") < 16):
            raise CaktoConfigurationError("Credenciais OAuth Cakto inválidas")
        if default_product_id is not None and len(default_product_id) < 4:
            raise CaktoConfigurationError("CAKTO_DEFAULT_PRODUCT_ID inválido")
        parsed = urlparse(base_url)
        allowed_host = parsed.hostname == "cakto.com.br" or bool(
            parsed.hostname and parsed.hostname.endswith(".cakto.com.br")
        )
        if parsed.scheme != "https" or not allowed_host:
            raise CaktoConfigurationError("CAKTO_API_BASE_URL deve usar HTTPS da Cakto")
        return cls(
            enabled=True,
            webhook_secret=secret,
            oauth_token=token,
            oauth_client_id=client_id,
            oauth_client_secret=client_secret,
            default_product_id=default_product_id,
            api_base_url=base_url.rstrip("/"),
        )


class CaktoService:
    """Valida webhook e consulta o histórico público da Cakto."""

    def __init__(self, config: CaktoConfig) -> None:
        self.config = config

    def validate_webhook_payload(self, payload: dict[str, Any]) -> None:
        """
        Valida o segredo presente no corpo com comparação resistente a timing.

        Raises:
            CaktoConfigurationError: Se o webhook não está configurado.
            CaktoAuthenticationError: Se o segredo não coincide.
        """
        expected = self.config.webhook_secret
        if not self.config.enabled or not expected:
            raise CaktoConfigurationError("Webhook Cakto não configurado")
        received = str(payload.get("secret") or "")
        if not hmac.compare_digest(received.encode(), expected.encode()):
            raise CaktoAuthenticationError("Segredo Cakto inválido")

    async def fetch_event_history(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Consulta uma página limitada do histórico de eventos.

        Raises:
            CaktoConfigurationError: Se OAuth não está configurado.
            httpx.HTTPError: Se a API externa falhar.
        """
        if not self.config.api_configured:
            raise CaktoConfigurationError("API Cakto não configurada")
        bounded_page = max(1, page)
        bounded_per_page = min(max(1, per_page), 100)
        async with httpx.AsyncClient(timeout=20.0) as client:
            access_token = await self._access_token(client)
            response = await client.get(
                f"{self.config.api_base_url}/public_api/webhook/event_history/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
                params={"page": bounded_page, "limit": bounded_per_page},
            )
        response.raise_for_status()
        body = response.json()
        if isinstance(body, list):
            return _unwrap_history_payloads(body)
        if not isinstance(body, dict):
            return []
        data = body.get("data")
        if isinstance(data, list):
            return _unwrap_history_payloads(data)
        if isinstance(data, dict):
            items = data.get("items") or data.get("results")
            if isinstance(items, list):
                return _unwrap_history_payloads(items)
        items = body.get("items") or body.get("results")
        return _unwrap_history_payloads(items) if isinstance(items, list) else []

    async def create_offer(
        self,
        *,
        name: str,
        price: float,
        product_id: str | None = None,
        billing_interval_months: int = 1,
        status: str = "active",
    ) -> CaktoOfferCreated:
        """
        Cria uma oferta no produto padrão (ou no produto informado) via API pública.

        Args:
            name: Nome exibido no checkout da Cakto.
            price: Preço da oferta em reais.
            product_id: Produto Cakto; usa ``default_product_id`` quando omitido.
            billing_interval_months: Ciclo em meses (mapeado para interval/month).
            status: ``active`` ou ``disabled``.

        Returns:
            Identificadores e URL oficial ``https://pay.cakto.com.br/{offer_id}``.

        Raises:
            CaktoConfigurationError: Se a API ou o produto padrão não estão prontos.
            httpx.HTTPError: Se a API externa falhar.
            ValueError: Se name/price/interval forem inválidos.
        """
        if not self.config.api_configured:
            raise CaktoConfigurationError("API Cakto não configurada")
        resolved_product = (product_id or self.config.default_product_id or "").strip()
        if not resolved_product:
            raise CaktoConfigurationError("CAKTO_DEFAULT_PRODUCT_ID não configurado")
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Nome da oferta Cakto é obrigatório")
        if price < 5:
            raise ValueError("Preço mínimo da oferta Cakto é R$ 5,00")
        months = max(1, int(billing_interval_months))
        offer_status = "active" if status == "active" else "disabled"
        body = {
            "name": cleaned_name,
            "price": float(price),
            "product": resolved_product,
            "type": "subscription",
            "intervalType": "month",
            "interval": months,
            "status": offer_status,
            "quantity_recurrences": -1,
            "recurrence_period": months * 30,
            "units": 1,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            access_token = await self._access_token(client)
            response = await client.post(
                f"{self.config.api_base_url}/public_api/offers/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise CaktoConfigurationError("Resposta inválida ao criar oferta Cakto")
        offer_id = str(payload.get("id") or "").strip()
        if not offer_id:
            raise CaktoConfigurationError("Oferta Cakto sem identificador na resposta")
        return CaktoOfferCreated(
            offer_id=offer_id,
            product_id=str(payload.get("product") or resolved_product).strip(),
            checkout_url=f"https://pay.cakto.com.br/{offer_id}",
            name=str(payload.get("name") or cleaned_name),
            price=float(payload.get("price") if payload.get("price") is not None else price),
        )

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        """Obtém token estático ou temporário por credenciais OAuth server-only."""
        if self.config.oauth_token:
            return self.config.oauth_token
        if not self.config.oauth_client_id or not self.config.oauth_client_secret:
            raise CaktoConfigurationError("Credenciais OAuth Cakto não configuradas")
        response = await client.post(
            f"{self.config.api_base_url}/public_api/token/",
            data={
                "client_id": self.config.oauth_client_id,
                "client_secret": self.config.oauth_client_secret,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        body = response.json()
        access_token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(access_token, str) or not access_token:
            raise CaktoConfigurationError("Token OAuth Cakto ausente na resposta")
        return access_token


def _unwrap_history_payloads(items: list[Any]) -> list[dict[str, Any]]:
    """
    Extrai o envelope ``payload`` retornado pelo histórico oficial da Cakto.

    Args:
        items: Registros brutos da API de histórico.

    Returns:
        Payloads de webhook prontos para o processamento idempotente.
    """
    payloads: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        payload = item.get("payload")
        if isinstance(payload, dict):
            payloads.append(payload)
        else:
            payloads.append(item)
    return payloads


def _environment_value(name: str, default: str) -> str:
    """
    Lê primeiro a variável segregada por ambiente e mantém fallback legado.

    Args:
        name: Nome base da variável.
        default: Valor usado quando nenhuma configuração existe.

    Returns:
        Valor do prefixo DEV/STAGING/PROD ou da variável antiga.
    """
    environment = os.getenv("APP_ENV", "development").strip().lower()
    prefix = {
        "development": "DEV",
        "dev": "DEV",
        "staging": "STAGING",
        "production": "PROD",
        "prod": "PROD",
    }.get(environment, "DEV")
    return os.getenv(f"{prefix}_{name}", os.getenv(name, default))
