"""Persistência segura de credenciais Bullex para auto-reconexão."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from backend.services.encryption_service import EncryptionService
from backend.user_store import UserStore


logger = logging.getLogger("backend-gateway")


@dataclass(frozen=True)
class SavedBullexCredentials:
    """Credenciais Bullex descriptografadas apenas em memória no gateway."""

    email: str
    password: str


class BullexCredentialsService:
    """
    Salva e recupera email/senha da corretora com criptografia em repouso.

    A senha nunca é devolvida ao frontend. O robô usa as credenciais salvas
    para reconectar automaticamente quando o SSID da sessão Bullex cai
    (incluindo com a tela do cliente fechada).
    """

    def __init__(self, user_store: UserStore, encryption: EncryptionService) -> None:
        """
        Args:
            user_store: Persistência multi-tenant das conexões Bullex.
            encryption: AES-256-GCM (EncryptionService) com chave do ambiente.
        """
        self._store = user_store
        self._encryption = encryption

    def save(self, user_id: str, email: str, password: str) -> None:
        """
        Criptografa e persiste as credenciais do usuário autenticado.

        Args:
            user_id: ID da sessão autenticada (nunca vindo do body livre).
            email: Email da conta Bullex.
            password: Senha em texto (só existe neste momento em memória).

        Raises:
            ValueError: Quando email/senha estiverem vazios.
        """
        clean_email = str(email or "").strip()
        clean_password = str(password or "")
        if not clean_email or not clean_password:
            raise ValueError("Email e senha Bullex são obrigatórios para salvar")
        encrypted = self._encryption.encrypt(clean_password)
        now = datetime.now(timezone.utc).isoformat()
        self._store.save_connection(
            user_id,
            {
                "bullex_email": clean_email,
                "encrypted_password": encrypted,
                "credentials_saved_at": now,
            },
        )
        logger.info("[BULLEX_CREDENTIALS_SAVED] user_id=%s", user_id)

    def load(self, user_id: str) -> SavedBullexCredentials | None:
        """
        Descriptografa as credenciais salvas do usuário.

        Args:
            user_id: ID da sessão autenticada.

        Returns:
            Credenciais em memória ou ``None`` se não houver senha salva.
        """
        record = self._store.get_saved_credentials(user_id)
        if record is None:
            return None
        email = str(record.get("bullex_email") or "").strip()
        encrypted = str(record.get("encrypted_password") or "").strip()
        if not email or not encrypted:
            return None
        try:
            password = self._encryption.decrypt(encrypted)
        except ValueError:
            logger.warning("[BULLEX_CREDENTIALS_DECRYPT_FAILED] user_id=%s", user_id)
            return None
        if not password:
            return None
        return SavedBullexCredentials(email=email, password=password)

    def has_saved(self, user_id: str) -> bool:
        """Indica se há par email+senha utilizável para auto-reconexão.

        Senha órfã (``encrypted_password`` sem ``bullex_email``) não conta:
        ``load()`` falharia e o painel fingiria login salvo.
        """
        record = self._store.get_saved_credentials(user_id)
        if record is None:
            return False
        email = str(record.get("bullex_email") or "").strip()
        encrypted = str(record.get("encrypted_password") or "").strip()
        if encrypted and not email:
            logger.warning(
                "[BULLEX_CREDENTIALS_INCOMPLETE] user_id=%s reason=missing_email",
                user_id,
            )
            return False
        return bool(email and encrypted)

    def clear(self, user_id: str) -> None:
        """
        Remove as credenciais salvas (esquecimento explícito do cliente).

        Args:
            user_id: ID da sessão autenticada.
        """
        self._store.clear_encrypted_credentials(user_id)
        logger.info("[BULLEX_CREDENTIALS_CLEARED] user_id=%s", user_id)
