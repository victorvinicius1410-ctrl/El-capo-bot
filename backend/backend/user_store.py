import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from time import monotonic
from typing import Any
from urllib.parse import quote

import httpx


logger = logging.getLogger("backend-gateway")

# Intervalo entre upserts de garantia da linha em /users para o mesmo usuário.
USER_ROW_ENSURE_TTL_SECONDS = 600.0

BULLEX_CONNECTION_TABLE = "bullex_connections"
BULLEX_CONNECTION_FIELDS = (
    "user_id",
    "bullex_email",
    "connected",
    "requires_2fa",
    "account_mode",
    "currency",
    "last_balance",
    "last_connected_at",
    "encrypted_password",
    "credentials_saved_at",
)
# Campos opcionais: se a migration ainda não rodou, o upsert faz retry sem eles.
BULLEX_CONNECTION_OPTIONAL_FIELDS = {
    "last_connected_at",
    "encrypted_password",
    "credentials_saved_at",
}
BULLEX_PUBLIC_SELECT = (
    "user_id,bullex_email,connected,requires_2fa,account_mode,currency,"
    "last_balance,last_connected_at,credentials_saved_at"
)
BULLEX_CREDENTIALS_SELECT = "user_id,bullex_email,encrypted_password,credentials_saved_at"


@dataclass
class BullExUserRecord:
    user_id: str
    bullex_email: str | None = None
    connected: bool | None = None
    requires_2fa: bool | None = None
    account_mode: str | None = None
    currency: str | None = None
    last_balance: float | None = None
    last_connected_at: str | None = None
    credentials_saved_at: str | None = None
    has_saved_credentials: bool = False


class UserStore(ABC):
    @abstractmethod
    def get_user(self, user_id: str) -> BullExUserRecord | None:
        raise NotImplementedError

    @abstractmethod
    def save_connection(self, user_id: str, payload: dict[str, Any]) -> BullExUserRecord:
        raise NotImplementedError

    @abstractmethod
    def update_connection(self, user_id: str, payload: dict[str, Any]) -> BullExUserRecord:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self, user_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_saved_credentials(self, user_id: str) -> dict[str, Any] | None:
        """Retorna email + encrypted_password (apenas para o gateway)."""
        raise NotImplementedError

    @abstractmethod
    def clear_encrypted_credentials(self, user_id: str) -> None:
        """Remove senha criptografada (esquecimento explícito)."""
        raise NotImplementedError

    @abstractmethod
    def save_market_assets_snapshot(self, user_id: str, assets: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def save_market_asset_payout(self, user_id: str, symbol: str, payout: int | float | None) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_market_assets_snapshot(self, user_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def connection_upsert_diagnostic(
        self,
        user_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = {"user_id": user_id, **(payload or {})}
        filtered = {key: value for key, value in body.items() if key in BULLEX_CONNECTION_FIELDS}
        # Defesa: nunca enviar bullex_email null/vazio no merge (apagaria o salvo).
        email = filtered.get("bullex_email")
        if email is None or (isinstance(email, str) and not email.strip()):
            filtered.pop("bullex_email", None)
        return {
            "table": BULLEX_CONNECTION_TABLE,
            "fields": list(BULLEX_CONNECTION_FIELDS),
            "payload": filtered,
        }


class InMemoryUserStore(UserStore):
    def __init__(self) -> None:
        self.users: dict[str, BullExUserRecord] = {}
        self.market_assets: dict[str, dict[str, dict[str, Any]]] = {}
        self._encrypted_passwords: dict[str, str] = {}

    def get_user(self, user_id: str) -> BullExUserRecord | None:
        record = self.users.get(user_id)
        if record is None:
            return None
        record.has_saved_credentials = bool(
            self._encrypted_passwords.get(user_id)
            and str(record.bullex_email or "").strip()
        )
        return record

    def save_connection(self, user_id: str, payload: dict[str, Any]) -> BullExUserRecord:
        return self._upsert(user_id, payload)

    def update_connection(self, user_id: str, payload: dict[str, Any]) -> BullExUserRecord:
        return self._upsert(user_id, payload)

    def disconnect(self, user_id: str) -> None:
        # Mantém encrypted_password para auto-reconexão do robô.
        record = self.users.get(user_id) or BullExUserRecord(user_id=user_id)
        record.connected = False
        record.requires_2fa = False
        self.users[user_id] = record

    def get_saved_credentials(self, user_id: str) -> dict[str, Any] | None:
        record = self.users.get(user_id)
        encrypted = self._encrypted_passwords.get(user_id)
        if record is None or not encrypted:
            return None
        return {
            "user_id": user_id,
            "bullex_email": record.bullex_email,
            "encrypted_password": encrypted,
            "credentials_saved_at": record.credentials_saved_at,
        }

    def clear_encrypted_credentials(self, user_id: str) -> None:
        self._encrypted_passwords.pop(user_id, None)
        record = self.users.get(user_id)
        if record is not None:
            record.credentials_saved_at = None
            record.has_saved_credentials = False

    def save_market_assets_snapshot(self, user_id: str, assets: list[dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing_assets = self.market_assets.setdefault(user_id, {})
        for asset in assets:
            symbol = asset.get("symbol")
            if not symbol:
                continue
            previous = existing_assets.get(symbol, {})
            incoming_payout = asset.get("payout")
            existing_assets[symbol] = {
                "user_id": user_id,
                "active_id": asset.get("active_id"),
                "symbol": symbol,
                "name": asset.get("name") or symbol,
                "enabled": asset.get("enabled", True),
                "payout": incoming_payout if incoming_payout is not None else previous.get("payout"),
                "last_seen_at": now,
                "updated_at": now,
            }

    def save_market_asset_payout(self, user_id: str, symbol: str, payout: int | float | None) -> None:
        if payout is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        existing_assets = self.market_assets.setdefault(user_id, {})
        previous = existing_assets.get(symbol, {})
        existing_assets[symbol] = {
            "user_id": user_id,
            "active_id": previous.get("active_id"),
            "symbol": symbol,
            "name": previous.get("name") or symbol,
            "enabled": previous.get("enabled", True),
            "payout": payout,
            "last_seen_at": now,
            "updated_at": now,
        }
        logger.info("MARKET_ASSET_PAYOUT_UPDATED %s %s %s", user_id, symbol, payout)

    def get_market_assets_snapshot(self, user_id: str) -> list[dict[str, Any]]:
        assets = self.market_assets.get(user_id, {})
        return [dict(asset) for _, asset in sorted(assets.items())]

    def _upsert(self, user_id: str, payload: dict[str, Any]) -> BullExUserRecord:
        record = self.users.get(user_id) or BullExUserRecord(user_id=user_id)
        for key, value in payload.items():
            if key == "encrypted_password":
                if value:
                    self._encrypted_passwords[user_id] = str(value)
                else:
                    self._encrypted_passwords.pop(user_id, None)
                continue
            if hasattr(record, key):
                setattr(record, key, value)
        record.has_saved_credentials = bool(
            self._encrypted_passwords.get(user_id)
            and str(record.bullex_email or "").strip()
        )
        self.users[user_id] = record
        return record


class SupabaseUserStore(UserStore):
    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        self.base_url = supabase_url.rstrip("/")
        self.service_role_key = service_role_key
        self.rest_url = f"{self.base_url}/rest/v1"
        self.headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
        }

    def get_user(self, user_id: str) -> BullExUserRecord | None:
        self._ensure_user_row(user_id)
        rows = self._request(
            "GET",
            f"/bullex_connections?user_id=eq.{quote(user_id, safe='')}&select={BULLEX_PUBLIC_SELECT}",
        )
        if not rows:
            return None
        return self._to_record(rows[0])

    def save_connection(self, user_id: str, payload: dict[str, Any]) -> BullExUserRecord:
        self._ensure_user_row(user_id)
        return self._upsert_connection(user_id, payload)

    def update_connection(self, user_id: str, payload: dict[str, Any]) -> BullExUserRecord:
        self._ensure_user_row(user_id)
        return self._upsert_connection(user_id, payload)

    def disconnect(self, user_id: str) -> None:
        # NÃO limpa encrypted_password — o robô precisa reconectar com tela fechada.
        self._ensure_user_row(user_id)
        body = {"user_id": user_id, "connected": False, "requires_2fa": False}
        self._request(
            "POST",
            "/bullex_connections?on_conflict=user_id",
            json=body,
            extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def get_saved_credentials(self, user_id: str) -> dict[str, Any] | None:
        self._ensure_user_row(user_id)
        try:
            rows = self._request(
                "GET",
                (
                    f"/bullex_connections?user_id=eq.{quote(user_id, safe='')}"
                    f"&select={BULLEX_CREDENTIALS_SELECT}"
                ),
            )
        except httpx.HTTPStatusError as exc:
            # Migration ainda não aplicada: coluna encrypted_password inexistente.
            if exc.response.status_code in {400, 404}:
                logger.warning(
                    "[BULLEX_CREDENTIALS_SCHEMA_MISSING] user_id=%s status=%s",
                    user_id,
                    exc.response.status_code,
                )
                return None
            raise
        if not rows:
            return None
        row = rows[0]
        if not row.get("encrypted_password"):
            return None
        return row

    def clear_encrypted_credentials(self, user_id: str) -> None:
        self._ensure_user_row(user_id)
        body = {
            "user_id": user_id,
            "encrypted_password": None,
            "credentials_saved_at": None,
        }
        try:
            self._request(
                "POST",
                "/bullex_connections?on_conflict=user_id",
                json=body,
                extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {400, 404}:
                logger.warning(
                    "[BULLEX_CREDENTIALS_CLEAR_SKIPPED] user_id=%s status=%s",
                    user_id,
                    exc.response.status_code,
                )
                return
            raise

    def save_market_assets_snapshot(self, user_id: str, assets: list[dict[str, Any]]) -> None:
        self._ensure_user_row(user_id)
        rows = []
        now = datetime.now(timezone.utc).isoformat()
        existing_payouts = self._get_existing_market_asset_payouts(user_id)
        for asset in assets:
            symbol = asset.get("symbol")
            if not symbol:
                continue
            incoming_payout = asset.get("payout")
            rows.append(
                {
                    "user_id": user_id,
                    "active_id": asset.get("active_id"),
                    "symbol": symbol,
                    "name": asset.get("name") or symbol,
                    "enabled": asset.get("enabled", True),
                    "payout": incoming_payout if incoming_payout is not None else existing_payouts.get(symbol),
                    "last_seen_at": now,
                }
            )
        if not rows:
            return
        self._request(
            "POST",
            "/market_assets?on_conflict=user_id,symbol",
            json=rows,
            extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def save_market_asset_payout(self, user_id: str, symbol: str, payout: int | float | None) -> None:
        self._ensure_user_row(user_id)
        if payout is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._request(
            "POST",
            "/market_assets?on_conflict=user_id,symbol",
            json={
                "user_id": user_id,
                "symbol": symbol,
                "payout": payout,
                "last_seen_at": now,
            },
            extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        logger.info("MARKET_ASSET_PAYOUT_UPDATED %s %s %s", user_id, symbol, payout)

    def get_market_assets_snapshot(self, user_id: str) -> list[dict[str, Any]]:
        self._ensure_user_row(user_id)
        rows = self._request(
            "GET",
            (
                f"/market_assets?user_id=eq.{quote(user_id, safe='')}"
                "&select=user_id,active_id,symbol,name,enabled,payout,last_seen_at,updated_at"
                "&order=symbol.asc"
            ),
        )
        return [row for row in rows if isinstance(row, dict)]

    def _ensure_user_row(self, user_id: str) -> None:
        # O upsert em /users rodava antes de TODA leitura e gravação — inclusive
        # no `get_user` que o robot-runtime chama no event loop. É idempotente,
        # então basta uma vez por usuário a cada USER_ROW_ENSURE_TTL_SECONDS:
        # corta metade das idas síncronas ao Supabase sem mudar o contrato
        # (a linha continua garantida antes de gravar em bullex_connections).
        ensured = getattr(self, "_ensured_user_rows", None)
        if ensured is None:
            ensured = self._ensured_user_rows = {}
        key = str(user_id)
        last = ensured.get(key)
        if last is not None and monotonic() - last < USER_ROW_ENSURE_TTL_SECONDS:
            return
        body = {"id": user_id}
        self._request(
            "POST",
            "/users?on_conflict=id",
            json=body,
            extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        ensured[key] = monotonic()

    def _upsert_connection(self, user_id: str, payload: dict[str, Any]) -> BullExUserRecord:
        diagnostic = self.connection_upsert_diagnostic(user_id, payload)
        body = diagnostic["payload"]
        path = f"/{BULLEX_CONNECTION_TABLE}?on_conflict=user_id"
        headers = {"Prefer": "resolution=merge-duplicates,return=representation"}
        try:
            rows = self._request("POST", path, json=body, extra_headers=headers)
        except httpx.HTTPStatusError as exc:
            if not self._is_optional_field_schema_error(exc, body):
                raise
            fallback_body = {
                key: value for key, value in body.items() if key not in BULLEX_CONNECTION_OPTIONAL_FIELDS
            }
            logger.warning(
                "[SUPABASE UPSERT RETRY] table=%s removed_fields=%s",
                BULLEX_CONNECTION_TABLE,
                sorted(BULLEX_CONNECTION_OPTIONAL_FIELDS.intersection(body)),
            )
            rows = self._request("POST", path, json=fallback_body, extra_headers=headers)
            body = fallback_body
        return self._to_record(rows[0] if rows else body)

    def _is_optional_field_schema_error(
        self,
        exc: httpx.HTTPStatusError,
        body: dict[str, Any],
    ) -> bool:
        if exc.response.status_code != 400:
            return False
        response_text = exc.response.text.lower()
        return any(
            field in body and field.lower() in response_text
            for field in BULLEX_CONNECTION_OPTIONAL_FIELDS
        )

    def _get_existing_market_asset_payouts(self, user_id: str) -> dict[str, Any]:
        rows = self._request(
            "GET",
            f"/market_assets?user_id=eq.{quote(user_id, safe='')}&select=symbol,payout",
        )
        payouts: dict[str, Any] = {}
        for row in rows:
            symbol = row.get("symbol")
            if symbol:
                payouts[symbol] = row.get("payout")
        return payouts

    def _to_record(self, row: dict[str, Any]) -> BullExUserRecord:
        return BullExUserRecord(
            user_id=row["user_id"],
            bullex_email=row.get("bullex_email"),
            connected=row.get("connected"),
            requires_2fa=row.get("requires_2fa"),
            account_mode=row.get("account_mode"),
            currency=row.get("currency"),
            last_balance=row.get("last_balance"),
            last_connected_at=row.get("last_connected_at"),
            credentials_saved_at=row.get("credentials_saved_at"),
            # PUBLIC_SELECT não traz encrypted_password; exige email + saved_at.
            has_saved_credentials=bool(
                row.get("credentials_saved_at")
                and str(row.get("bullex_email") or "").strip()
            ),
        )

    def _request(
        self,
        method: str,
        path: str,
        json: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        headers = dict(self.headers)
        if extra_headers:
            headers.update(extra_headers)

        with httpx.Client(timeout=20.0) as client:
            response = client.request(
                method=method,
                url=f"{self.rest_url}{path}",
                headers=headers,
                json=json,
            )

        if response.status_code >= 400:
            logger.warning(
                "[SUPABASE HTTP ERROR] status=%s url=%s payload=%s response=%s",
                response.status_code,
                response.request.url,
                json,
                response.text,
            )
        response.raise_for_status()
        if not response.content:
            return []
        return response.json()


def create_user_store() -> UserStore:
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if supabase_url and service_role_key:
        return SupabaseUserStore(supabase_url, service_role_key)
    return InMemoryUserStore()
