"""Rotas REST de autenticação/sessão (login, logout, session, conta)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.auth_session_service import (
    AuthSessionError,
    AuthSessionTokens,
    AuthUserView,
    InvalidCredentialsError,
    PasswordPolicyError,
    SessionCookieNames,
    extract_access_token_from_request,
    extract_refresh_token_from_request,
)

if TYPE_CHECKING:
    from backend.registration_service import RegistrationService


class AuthSessionServiceProtocol(Protocol):
    """Contrato mínimo usado pelo router (permite doubles de teste)."""

    async def login(self, email: str, password: str) -> tuple[AuthSessionTokens, AuthUserView]: ...

    async def resolve_user(self, access_token: str) -> AuthUserView: ...

    async def refresh(self, refresh_token: str) -> AuthSessionTokens: ...

    async def update_password(self, access_token: str, password: str) -> None: ...

    async def update_email(self, access_token: str, email: str) -> AuthUserView: ...

    async def establish_session(
        self,
        access_token: str,
        refresh_token: str | None = None,
    ) -> tuple[AuthSessionTokens, AuthUserView]: ...

    def cookie_names(self, *, secure: bool) -> SessionCookieNames: ...


class LoginPayload(BaseModel):
    """Credenciais de acesso ao painel."""

    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class RegisterPayload(BaseModel):
    """Cadastro público de lead (sem acesso operacional até aprovação)."""

    name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)
    phone: str | None = None


class PasswordPayload(BaseModel):
    """Nova senha (política validada no serviço)."""

    password: str = Field(min_length=1)


class EmailPayload(BaseModel):
    """Novo email da conta."""

    email: str = Field(min_length=3)


class RecoveryTokensPayload(BaseModel):
    """Tokens vindos do link de recuperação (hash da URL)."""

    access_token: str = Field(min_length=10)
    refresh_token: str | None = None


def create_auth_router(
    service: AuthSessionServiceProtocol,
    *,
    secure_cookies: bool,
    registration_service: RegistrationService | None = None,
    on_access_token_invalidated: Callable[[str], None] | None = None,
) -> APIRouter:
    """
    Cria o router de autenticação.

    Args:
        service: Serviço de sessão Supabase.
        secure_cookies: True em produção (Secure + __Host-).
        registration_service: Cadastro público opcional de leads.
        on_access_token_invalidated: Opcional — limpa cache de auth do gateway
            no logout (hash do access token).

    Returns:
        APIRouter montado em /auth/*.
    """
    router = APIRouter(tags=["auth"])

    @router.post("/auth/login")
    async def login(payload: LoginPayload, response: Response) -> dict[str, Any]:
        """Autentica e grava tokens somente em cookies httpOnly."""
        try:
            tokens, user = await service.login(str(payload.email), payload.password)
        except InvalidCredentialsError as exc:
            return _error(401, "INVALID_CREDENTIALS", str(exc))
        except AuthSessionError as exc:
            return _error(502, "AUTH_PROVIDER_ERROR", str(exc))
        _set_session_cookies(response, service, tokens, secure=secure_cookies)
        return {"ok": True, "data": {"user": {"id": user.id, "email": user.email}}}

    if registration_service is not None:
        from backend.registration_service import (
            EmailAlreadyRegisteredError,
            RegisterLeadPayload,
            RegistrationError,
        )

        @router.post("/auth/register", status_code=201)
        async def register(payload: RegisterPayload, response: Response) -> dict[str, Any]:
            """Cadastra lead pendente e tenta iniciar sessão com cookies."""
            try:
                record = await registration_service.register_lead(
                    RegisterLeadPayload(
                        name=payload.name,
                        email=str(payload.email),
                        password=payload.password,
                        phone=payload.phone,
                    )
                )
            except PasswordPolicyError as exc:
                return _error(400, "PASSWORD_POLICY", str(exc), field="password")
            except EmailAlreadyRegisteredError as exc:
                return _error(409, "EMAIL_ALREADY_REGISTERED", str(exc), field="email")
            except RegistrationError as exc:
                return _error(400, "REGISTRATION_ERROR", str(exc))

            try:
                tokens, _user = await service.login(record.email, payload.password)
                _set_session_cookies(response, service, tokens, secure=secure_cookies)
            except AuthSessionError:
                pass

            return {
                "ok": True,
                "data": {
                    "user": {
                        "id": record.user_id,
                        "email": record.email,
                        "name": record.name,
                    },
                    "approval_status": record.approval_status.value,
                    "message": (
                        "Cadastro realizado. Aguarde a aprovação do administrador "
                        "ou escolha um plano."
                    ),
                },
            }

    @router.post("/auth/logout", status_code=204)
    async def logout(request: Request, response: Response) -> None:
        """Encerra a sessão limpando cookies httpOnly no Response injetado.

        Não retornar um Response novo: isso descarta os Set-Cookie de limpeza
        e o browser mantém a sessão (logout aparente no UI, mas cookie vivo).
        """
        access = extract_access_token_from_request(
            request.headers.get("authorization"),
            dict(request.cookies),
        )
        if access and on_access_token_invalidated is not None:
            on_access_token_invalidated(access)
        _clear_session_cookies(response, service, secure=secure_cookies)

    @router.get("/auth/session")
    async def session(request: Request, response: Response) -> dict[str, Any]:
        """Retorna o usuário da sessão ou authenticated=false.

        Se o access cookie expirou mas o refresh ainda é válido, renova os
        cookies em silêncio (evita “página morta” após ~1h com a aba aberta).
        """
        cookies = dict(request.cookies)
        access = extract_access_token_from_request(
            request.headers.get("authorization"),
            cookies,
        )
        if access:
            try:
                user = await service.resolve_user(access)
                return {
                    "ok": True,
                    "data": {
                        "authenticated": True,
                        "user": {"id": user.id, "email": user.email},
                    },
                }
            except AuthSessionError:
                pass

        renewed = await _try_refresh_session(
            service,
            response,
            cookies=cookies,
            secure=secure_cookies,
        )
        if renewed is None:
            return {"ok": True, "data": {"authenticated": False, "user": None}}
        tokens, user = renewed
        return {
            "ok": True,
            "data": {
                "authenticated": True,
                "user": {"id": user.id, "email": user.email},
                "refreshed": True,
                "expires_in": tokens.expires_in,
            },
        }

    @router.post("/auth/refresh")
    async def refresh_session(request: Request, response: Response) -> dict[str, Any]:
        """Renova access/refresh cookies a partir do refresh cookie httpOnly."""
        renewed = await _try_refresh_session(
            service,
            response,
            cookies=dict(request.cookies),
            secure=secure_cookies,
        )
        if renewed is None:
            return _error(401, "SESSION_EXPIRED", "Sessão expirada. Faça login novamente.")
        tokens, user = renewed
        return {
            "ok": True,
            "data": {
                "user": {"id": user.id, "email": user.email},
                "expires_in": tokens.expires_in,
            },
        }

    @router.post("/auth/session/from-tokens")
    async def session_from_tokens(
        payload: RecoveryTokensPayload,
        response: Response,
    ) -> dict[str, Any]:
        """Estabelece cookies a partir de tokens do link de recuperação."""
        try:
            tokens, user = await service.establish_session(
                payload.access_token,
                payload.refresh_token,
            )
        except AuthSessionError as exc:
            return _error(401, "INVALID_SESSION", str(exc))
        _set_session_cookies(response, service, tokens, secure=secure_cookies)
        return {"ok": True, "data": {"user": {"id": user.id, "email": user.email}}}

    @router.patch("/auth/password")
    async def update_password(payload: PasswordPayload, request: Request) -> dict[str, Any]:
        """Altera a senha do usuário autenticado via cookie/Bearer."""
        access = _require_access(request)
        if isinstance(access, JSONResponse):
            return access
        try:
            await service.update_password(access, payload.password)
        except PasswordPolicyError as exc:
            return _error(400, "PASSWORD_POLICY", str(exc), field="password")
        except AuthSessionError as exc:
            return _error(400, "PASSWORD_UPDATE_FAILED", str(exc))
        return {"ok": True, "data": {"updated": True}}

    @router.patch("/auth/email")
    async def update_email(payload: EmailPayload, request: Request) -> dict[str, Any]:
        """Solicita alteração de email do usuário autenticado."""
        access = _require_access(request)
        if isinstance(access, JSONResponse):
            return access
        try:
            user = await service.update_email(access, str(payload.email))
        except AuthSessionError as exc:
            return _error(400, "EMAIL_UPDATE_FAILED", str(exc), field="email")
        return {"ok": True, "data": {"user": {"id": user.id, "email": user.email}}}

    return router


def _require_access(request: Request) -> str | JSONResponse:
    access = extract_access_token_from_request(
        request.headers.get("authorization"),
        dict(request.cookies),
    )
    if not access:
        return _error(401, "NO_AUTH", "Não autenticado")
    return access


async def _try_refresh_session(
    service: AuthSessionServiceProtocol,
    response: Response,
    *,
    cookies: dict[str, str],
    secure: bool,
) -> tuple[AuthSessionTokens, AuthUserView] | None:
    """
    Tenta renovar a sessão com o refresh cookie e grava novos cookies.

    Args:
        service: Serviço de autenticação.
        response: Response FastAPI onde os Set-Cookie serão aplicados.
        cookies: Cookies da requisição atual.
        secure: Se True, usa cookies ``__Host-`` + Secure.

    Returns:
        Par (tokens, user) se a renovação funcionar; ``None`` caso contrário.
    """
    refresh = extract_refresh_token_from_request(cookies)
    if not refresh:
        return None
    try:
        tokens = await service.refresh(refresh)
        user = await service.resolve_user(tokens.access_token)
    except AuthSessionError:
        _clear_session_cookies(response, service, secure=secure)
        return None
    _set_session_cookies(response, service, tokens, secure=secure)
    return tokens, user


def _set_session_cookies(
    response: Response,
    service: AuthSessionServiceProtocol,
    tokens: AuthSessionTokens,
    *,
    secure: bool,
) -> None:
    names = service.cookie_names(secure=secure)
    common = {
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "path": "/",
    }
    response.set_cookie(
        names.access,
        tokens.access_token,
        max_age=tokens.expires_in,
        **common,
    )
    if tokens.refresh_token:
        response.set_cookie(
            names.refresh,
            tokens.refresh_token,
            max_age=60 * 60 * 24 * 30,
            **common,
        )


def _clear_session_cookies(
    response: Response,
    service: AuthSessionServiceProtocol,
    *,
    secure: bool,
) -> None:
    names = service.cookie_names(secure=secure)
    for name in (names.access, names.refresh):
        response.delete_cookie(name, path="/", secure=secure, httponly=True, samesite="lax")
    # Limpa também o par alternativo (migração secure↔local)
    alt = service.cookie_names(secure=not secure)
    for name in (alt.access, alt.refresh):
        response.delete_cookie(name, path="/", secure=not secure, httponly=True, samesite="lax")


def _error(
    status: int,
    code: str,
    message: str,
    field: str | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
    if field:
        body["error"]["field"] = field
    return JSONResponse(status_code=status, content=body)
