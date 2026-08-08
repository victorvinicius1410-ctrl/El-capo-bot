"""Testes da API de login/sessão via cookies httpOnly (sem Supabase no frontend)."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth_session_service import (
    AuthSessionError,
    AuthSessionTokens,
    AuthUserView,
    InvalidCredentialsError,
    PasswordPolicyError,
    SessionCookieNames,
)
from backend.auth_router import create_auth_router


class FakeAuthSessionService:
    """Duplo de teste para o serviço de sessão."""

    def __init__(self) -> None:
        self.tokens = AuthSessionTokens(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_in=3600,
        )
        self.user = AuthUserView(id="user-1", email="demo@elcapo.local")
        self.login_calls: list[tuple[str, str]] = []
        self.logout_calls = 0
        self.refresh_calls: list[str] = []
        self.update_password_calls: list[str] = []
        self.update_email_calls: list[str] = []
        self.fail_login = False
        self.fail_password_policy = False
        self.fail_refresh = False

    async def login(self, email: str, password: str) -> tuple[AuthSessionTokens, AuthUserView]:
        self.login_calls.append((email, password))
        if self.fail_login:
            raise InvalidCredentialsError("Credenciais inválidas")
        return self.tokens, self.user

    async def resolve_user(self, access_token: str) -> AuthUserView:
        if access_token != self.tokens.access_token:
            raise AuthSessionError("Sessão inválida")
        return self.user

    async def refresh(self, refresh_token: str) -> AuthSessionTokens:
        self.refresh_calls.append(refresh_token)
        if self.fail_refresh or refresh_token != self.tokens.refresh_token:
            raise AuthSessionError("Sessão expirada")
        self.tokens = AuthSessionTokens(
            access_token="access-token-renewed",
            refresh_token="refresh-token-rotated",
            expires_in=3600,
        )
        return self.tokens

    async def update_password(self, access_token: str, password: str) -> None:
        if self.fail_password_policy:
            raise PasswordPolicyError("Senha fraca")
        self.update_password_calls.append(password)

    async def update_email(self, access_token: str, email: str) -> AuthUserView:
        self.update_email_calls.append(email)
        self.user = AuthUserView(id=self.user.id, email=email)
        return self.user

    async def establish_session(
        self,
        access_token: str,
        refresh_token: str | None = None,
    ) -> tuple[AuthSessionTokens, AuthUserView]:
        self.tokens = AuthSessionTokens(
            access_token=access_token,
            refresh_token=refresh_token or self.tokens.refresh_token,
            expires_in=3600,
        )
        return self.tokens, await self.resolve_user(access_token)

    def cookie_names(self, *, secure: bool) -> SessionCookieNames:
        if secure:
            return SessionCookieNames(
                access="__Host-elcapo-access",
                refresh="__Host-elcapo-refresh",
            )
        return SessionCookieNames(access="elcapo-access", refresh="elcapo-refresh")


def _build_app(service: FakeAuthSessionService, *, secure: bool = False) -> TestClient:
    app = FastAPI()
    app.include_router(create_auth_router(service, secure_cookies=secure))
    return TestClient(app)


class AuthSessionApiTests(unittest.TestCase):
    def test_login_sets_http_only_cookies_and_returns_user(self) -> None:
        service = FakeAuthSessionService()
        client = _build_app(service)

        response = client.post(
            "/auth/login",
            json={"email": "demo@elcapo.local", "password": "DemoElCapo2026!"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["user"]["email"], "demo@elcapo.local")
        self.assertNotIn("access_token", body["data"])
        self.assertIn("elcapo-access", response.cookies)
        cookie_header = ";".join(response.headers.get_list("set-cookie"))
        self.assertIn("HttpOnly", cookie_header)
        self.assertEqual(service.login_calls, [("demo@elcapo.local", "DemoElCapo2026!")])

    def test_login_invalid_credentials_returns_401(self) -> None:
        service = FakeAuthSessionService()
        service.fail_login = True
        client = _build_app(service)

        response = client.post(
            "/auth/login",
            json={"email": "x@y.com", "password": "wrong"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_session_reads_access_cookie(self) -> None:
        service = FakeAuthSessionService()
        client = _build_app(service)
        client.cookies.set("elcapo-access", "access-token")

        response = client.get("/auth/session")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["authenticated"])
        self.assertEqual(response.json()["data"]["user"]["id"], "user-1")

    def test_session_without_cookie_returns_unauthenticated(self) -> None:
        service = FakeAuthSessionService()
        client = _build_app(service)

        response = client.get("/auth/session")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["data"]["authenticated"])
        self.assertIsNone(response.json()["data"]["user"])

    def test_session_silently_refreshes_expired_access_cookie(self) -> None:
        """Access expirado + refresh válido → renova cookies sem logout."""
        service = FakeAuthSessionService()
        client = _build_app(service)
        client.cookies.set("elcapo-access", "access-token-expired")
        client.cookies.set("elcapo-refresh", "refresh-token")

        response = client.get("/auth/session")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["data"]["authenticated"])
        self.assertTrue(body["data"]["refreshed"])
        self.assertEqual(body["data"]["user"]["id"], "user-1")
        self.assertEqual(service.refresh_calls, ["refresh-token"])
        self.assertEqual(response.cookies["elcapo-access"], "access-token-renewed")
        self.assertEqual(response.cookies["elcapo-refresh"], "refresh-token-rotated")

    def test_session_with_only_refresh_cookie_renews(self) -> None:
        """Browser apagou access (Max-Age) mas refresh de 30d ainda existe."""
        service = FakeAuthSessionService()
        client = _build_app(service)
        client.cookies.set("elcapo-refresh", "refresh-token")

        response = client.get("/auth/session")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["authenticated"])
        self.assertEqual(service.refresh_calls, ["refresh-token"])

    def test_session_refresh_failure_returns_unauthenticated(self) -> None:
        service = FakeAuthSessionService()
        service.fail_refresh = True
        client = _build_app(service)
        client.cookies.set("elcapo-refresh", "refresh-token")

        response = client.get("/auth/session")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["data"]["authenticated"])
        self.assertEqual(service.refresh_calls, ["refresh-token"])

    def test_post_refresh_renews_cookies(self) -> None:
        service = FakeAuthSessionService()
        client = _build_app(service)
        client.cookies.set("elcapo-refresh", "refresh-token")

        response = client.post("/auth/refresh")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["user"]["id"], "user-1")
        self.assertEqual(response.cookies["elcapo-access"], "access-token-renewed")

    def test_post_refresh_without_cookie_returns_401(self) -> None:
        service = FakeAuthSessionService()
        client = _build_app(service)

        response = client.post("/auth/refresh")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "SESSION_EXPIRED")

    def test_logout_clears_cookies(self) -> None:
        service = FakeAuthSessionService()
        client = _build_app(service)
        client.cookies.set("elcapo-access", "access-token")
        client.cookies.set("elcapo-refresh", "refresh-token")

        response = client.post("/auth/logout")

        self.assertEqual(response.status_code, 204)
        set_cookie_headers = response.headers.get_list("set-cookie")
        joined = "\n".join(set_cookie_headers).lower()
        self.assertTrue(
            set_cookie_headers,
            "logout deve emitir Set-Cookie para apagar a sessão no browser",
        )
        self.assertIn("elcapo-access=", joined)
        self.assertIn("elcapo-refresh=", joined)
        self.assertTrue(
            any("max-age=0" in header.lower() for header in set_cookie_headers),
            "cookies de sessão devem ser expirados (Max-Age=0)",
        )
        # O jar do TestClient pode não aplicar Max-Age=0 em cookies setados
        # via client.cookies.set(); browsers reais honram o Set-Cookie.
        # Simula o efeito do browser limpando o storage de cookies.
        client.cookies.clear()
        session = client.get("/auth/session")
        self.assertFalse(session.json()["data"]["authenticated"])

    def test_update_password_requires_session_cookie(self) -> None:
        service = FakeAuthSessionService()
        client = _build_app(service)

        response = client.patch(
            "/auth/password",
            json={"password": "NovaSenha123"},
        )
        self.assertEqual(response.status_code, 401)

        client.cookies.set("elcapo-access", "access-token")
        response = client.patch(
            "/auth/password",
            json={"password": "NovaSenha123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.update_password_calls, ["NovaSenha123"])

    def test_weak_password_rejected(self) -> None:
        service = FakeAuthSessionService()
        service.fail_password_policy = True
        client = _build_app(service)
        client.cookies.set("elcapo-access", "access-token")

        response = client.patch("/auth/password", json={"password": "fraca"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "PASSWORD_POLICY")

    def test_establish_session_from_recovery_tokens(self) -> None:
        service = FakeAuthSessionService()
        client = _build_app(service)

        response = client.post(
            "/auth/session/from-tokens",
            json={
                "access_token": "recovery-access",
                "refresh_token": "recovery-refresh",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("elcapo-access", response.cookies)
        self.assertEqual(response.cookies["elcapo-access"], "recovery-access")


class AuthSessionServiceUnitTests(unittest.IsolatedAsyncioTestCase):
    def test_password_policy_rejects_weak_passwords(self) -> None:
        from backend.auth_session_service import PasswordPolicy

        ok, err = PasswordPolicy.validate("Abcd1234")
        self.assertTrue(ok)
        self.assertIsNone(err)

        ok, err = PasswordPolicy.validate("short1A")
        self.assertFalse(ok)
        self.assertIsNotNone(err)

        ok, err = PasswordPolicy.validate("alllowercase1")
        self.assertFalse(ok)

    async def test_login_strips_email_and_password_edges(self) -> None:
        """Espaços de copy/paste nas bordas não devem invalidar o login."""
        from unittest.mock import AsyncMock, MagicMock

        from backend.auth_session_service import AuthSessionService

        service = AuthSessionService("https://example.supabase.co", "service-role")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_in": 3600,
            "user": {"id": "u1", "email": "admin@elcapobot.online"},
        }
        service._client.post = AsyncMock(return_value=mock_response)
        try:
            tokens, user = await service.login(
                "  Admin@ElCapoBot.online  ",
                "  SenhaForte1  ",
            )
        finally:
            await service.aclose()

        self.assertEqual(user.email, "admin@elcapobot.online")
        self.assertEqual(tokens.access_token, "tok")
        sent = service._client.post.call_args.kwargs["json"]
        self.assertEqual(sent["email"], "admin@elcapobot.online")
        self.assertEqual(sent["password"], "SenhaForte1")

    async def test_login_rate_limit_returns_friendly_message(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from backend.auth_session_service import AuthSessionService, InvalidCredentialsError

        service = AuthSessionService("https://example.supabase.co", "service-role")
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "error_code": "over_request_rate_limit",
            "msg": "Request rate limit reached",
        }
        service._client.post = AsyncMock(return_value=mock_response)
        try:
            with self.assertRaises(InvalidCredentialsError) as ctx:
                await service.login("admin@elcapobot.online", "SenhaForte1")
        finally:
            await service.aclose()
        self.assertIn("Muitas tentativas", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
