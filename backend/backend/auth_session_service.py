"""Sessão de autenticação via Supabase Auth — apenas no backend.

O frontend nunca chama o Supabase Auth diretamente. Login, logout, sessão,
troca de senha/email e recuperação passam por esta camada, que persiste
tokens em cookies httpOnly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger("backend-gateway")


class AuthSessionError(Exception):
    """Falha genérica de sessão autenticada."""


class InvalidCredentialsError(AuthSessionError):
    """Email ou senha inválidos."""


class PasswordPolicyError(AuthSessionError):
    """Senha fora da política."""


@dataclass(frozen=True)
class AuthSessionTokens:
    """Tokens emitidos pelo Supabase Auth."""

    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class AuthUserView:
    """Usuário seguro para resposta ao frontend (sem tokens)."""

    id: str
    email: str


@dataclass(frozen=True)
class SessionCookieNames:
    """Nomes dos cookies de sessão (dev vs produção)."""

    access: str
    refresh: str


class PasswordPolicy:
    """Política de senha: 8+ chars, maiúscula, minúscula e número."""

    MIN_LENGTH = 8
    PATTERN = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).+$")

    @classmethod
    def validate(cls, password: str) -> tuple[bool, str | None]:
        """
        Valida complexidade da senha.

        Args:
            password: Senha em texto puro.

        Returns:
            (True, None) se válida; (False, mensagem) caso contrário.
        """
        if len(password) < cls.MIN_LENGTH:
            return False, f"Mínimo {cls.MIN_LENGTH} caracteres"
        if not cls.PATTERN.match(password):
            return False, "Deve conter maiúscula, minúscula e número"
        return True, None


class AuthSessionService:
    """Proxy server-side do Supabase Auth (password grant + user update)."""

    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        self.base_url = supabase_url.rstrip("/")
        self.service_role_key = service_role_key
        # Keep-alive compartilhado: login/refresh/session deixam de abrir
        # um TCP/TLS novo a cada chamada (ver PERFORMANCE_SISTEMA.md).
        self._client = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        )

    async def aclose(self) -> None:
        """Fecha o client HTTP compartilhado (shutdown do processo)."""
        await self._client.aclose()

    def cookie_names(self, *, secure: bool) -> SessionCookieNames:
        """Retorna nomes de cookie endurecidos em produção (__Host-)."""
        if secure:
            return SessionCookieNames(
                access="__Host-elcapo-access",
                refresh="__Host-elcapo-refresh",
            )
        return SessionCookieNames(access="elcapo-access", refresh="elcapo-refresh")

    async def login(self, email: str, password: str) -> tuple[AuthSessionTokens, AuthUserView]:
        """
        Autentica com email/senha via grant password do Supabase.

        Raises:
            InvalidCredentialsError: Credenciais inválidas.
            AuthSessionError: Falha inesperada do provedor.
        """
        normalized_email = email.strip().casefold()
        # Trim só nas bordas: espaços de copy/paste/autofill não devem invalidar a senha.
        # Espaços internos deliberados na senha são preservados.
        clean_password = password.strip()
        response = await self._client.post(
            f"{self.base_url}/auth/v1/token?grant_type=password",
            headers={
                "apikey": self.service_role_key,
                "Content-Type": "application/json",
            },
            json={"email": normalized_email, "password": clean_password},
        )
        if response.status_code in {400, 401}:
            provider_code, provider_msg = _safe_provider_error(response)
            logger.warning(
                "auth.login.rejected status=%s provider_code=%s provider_msg=%s email=%s",
                response.status_code,
                provider_code,
                provider_msg,
                normalized_email,
            )
            if provider_code in {"over_request_rate_limit", "over_email_send_rate_limit"}:
                raise InvalidCredentialsError(
                    "Muitas tentativas. Aguarde alguns minutos e tente novamente."
                )
            raise InvalidCredentialsError("Email ou senha inválidos")
        if response.status_code >= 400:
            provider_code, provider_msg = _safe_provider_error(response)
            logger.error(
                "auth.login.provider_failed status=%s provider_code=%s provider_msg=%s",
                response.status_code,
                provider_code,
                provider_msg,
                exc_info=False,
            )
            raise AuthSessionError("Falha ao autenticar")
        payload = response.json()
        tokens = _tokens_from_payload(payload)
        user = _user_from_payload(payload.get("user") or {})
        if not user.email:
            user = AuthUserView(id=user.id, email=normalized_email)
        return tokens, user

    async def resolve_user(self, access_token: str) -> AuthUserView:
        """Valida o access token e devolve id/email."""
        if not access_token.strip():
            raise AuthSessionError("Sessão ausente")
        response = await self._client.get(
            f"{self.base_url}/auth/v1/user",
            headers={
                "apikey": self.service_role_key,
                "Authorization": f"Bearer {access_token}",
            },
            timeout=10.0,
        )
        if response.status_code != 200:
            raise AuthSessionError("Sessão inválida ou expirada")
        return _user_from_payload(response.json())

    async def refresh(self, refresh_token: str) -> AuthSessionTokens:
        """Renova o par de tokens com o refresh token."""
        response = await self._client.post(
            f"{self.base_url}/auth/v1/token?grant_type=refresh_token",
            headers={
                "apikey": self.service_role_key,
                "Content-Type": "application/json",
            },
            json={"refresh_token": refresh_token},
        )
        if response.status_code >= 400:
            raise AuthSessionError("Sessão expirada")
        return _tokens_from_payload(response.json())

    async def establish_session(
        self,
        access_token: str,
        refresh_token: str | None = None,
    ) -> tuple[AuthSessionTokens, AuthUserView]:
        """
        Estabelece sessão a partir de tokens (ex.: link de recuperação).

        Valida o access_token no provedor antes de gravar cookies.
        """
        user = await self.resolve_user(access_token)
        tokens = AuthSessionTokens(
            access_token=access_token,
            refresh_token=(refresh_token or "").strip(),
            expires_in=3600,
        )
        return tokens, user

    async def update_password(self, access_token: str, password: str) -> None:
        """Atualiza a senha do usuário autenticado."""
        valid, error = PasswordPolicy.validate(password)
        if not valid:
            raise PasswordPolicyError(error or "Senha inválida")
        await self._update_user(access_token, {"password": password})

    async def update_email(self, access_token: str, email: str) -> AuthUserView:
        """Solicita alteração de email (confirmação pelo provedor)."""
        normalized = email.strip().casefold()
        if not normalized or "@" not in normalized:
            raise AuthSessionError("Email inválido")
        payload = await self._update_user(access_token, {"email": normalized})
        return _user_from_payload(payload.get("user") or payload)

    async def _update_user(self, access_token: str, body: dict) -> dict:
        response = await self._client.put(
            f"{self.base_url}/auth/v1/user",
            headers={
                "apikey": self.service_role_key,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        if response.status_code >= 400:
            logger.error(
                "auth.update_user.failed status=%s",
                response.status_code,
                exc_info=False,
            )
            raise AuthSessionError("Não foi possível atualizar a conta")
        return response.json() if response.content else {}


def _safe_provider_error(response: httpx.Response) -> tuple[str, str]:
    """Extrai código/mensagem do provedor sem vazar payloads sensíveis."""
    try:
        payload = response.json()
    except Exception:
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    code = str(payload.get("error_code") or payload.get("error") or "").strip()
    message = str(payload.get("msg") or payload.get("error_description") or "").strip()
    # Nunca propaga corpo bruto (pode conter hints sensíveis); só metadados curtos.
    return code[:80], message[:120]


def _tokens_from_payload(payload: dict) -> AuthSessionTokens:
    access = str(payload.get("access_token") or "").strip()
    refresh = str(payload.get("refresh_token") or "").strip()
    expires_in = int(payload.get("expires_in") or 3600)
    if not access:
        raise AuthSessionError("Provedor não retornou access_token")
    return AuthSessionTokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=max(60, expires_in),
    )


def _user_from_payload(payload: dict) -> AuthUserView:
    user_id = str(payload.get("id") or "").strip()
    email = str(payload.get("email") or "").strip().casefold()
    if not user_id:
        raise AuthSessionError("Provedor não retornou usuário")
    return AuthUserView(id=user_id, email=email)


def extract_access_token_from_request(
    authorization: str | None,
    cookies: dict[str, str],
) -> str | None:
    """
    Extrai access token do Bearer ou dos cookies de sessão.

    Preferência: Authorization Bearer > cookie __Host- > cookie local.
    """
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    for name in ("__Host-elcapo-access", "elcapo-access"):
        value = (cookies.get(name) or "").strip()
        if value:
            return value
    return None


def extract_refresh_token_from_request(cookies: dict[str, str]) -> str | None:
    """
    Extrai o refresh token dos cookies de sessão.

    Preferência: cookie ``__Host-`` (produção) > cookie local (dev).

    Args:
        cookies: Mapa nome→valor dos cookies da requisição.

    Returns:
        Refresh token limpo, ou ``None`` se ausente.
    """
    for name in ("__Host-elcapo-refresh", "elcapo-refresh"):
        value = (cookies.get(name) or "").strip()
        if value:
            return value
    return None
