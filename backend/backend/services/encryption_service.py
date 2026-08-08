"""Criptografia autenticada de segredos server-side."""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptionService:
    """Protege segredos em repouso usando AES-256-GCM."""

    VERSION = "v1"

    def __init__(self, encoded_key: str) -> None:
        """
        Inicializa o serviço com uma chave Base64 URL-safe de 32 bytes.

        Args:
            encoded_key: Chave AES-256 codificada em Base64 URL-safe.

        Raises:
            ValueError: Quando a chave estiver ausente ou não tiver 256 bits.
        """
        if not encoded_key:
            raise ValueError("ENCRYPTION_KEY não configurada")
        try:
            key = base64.urlsafe_b64decode(encoded_key.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("ENCRYPTION_KEY deve ser Base64 URL-safe") from exc
        if len(key) != 32:
            raise ValueError("ENCRYPTION_KEY deve representar exatamente 32 bytes")
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        """
        Criptografa um segredo com nonce aleatório e autenticação.

        Args:
            plaintext: Segredo em texto a proteger.

        Returns:
            Envelope versionado codificado em Base64 URL-safe.

        Raises:
            ValueError: Quando o texto estiver vazio.
        """
        if not plaintext:
            raise ValueError("Segredo não pode ser vazio")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode(), self.VERSION.encode())
        payload = base64.urlsafe_b64encode(nonce + ciphertext).decode()
        return f"{self.VERSION}.{payload}"

    def decrypt(self, envelope: str) -> str:
        """
        Descriptografa e autentica um envelope versionado.

        Args:
            envelope: Valor retornado por :meth:`encrypt`.

        Returns:
            Segredo original.

        Raises:
            ValueError: Quando a versão ou o envelope forem inválidos.
        """
        try:
            version, payload = envelope.split(".", 1)
            if version != self.VERSION:
                raise ValueError("Versão de criptografia não suportada")
            raw = base64.urlsafe_b64decode(payload.encode())
            nonce, ciphertext = raw[:12], raw[12:]
            return self._cipher.decrypt(nonce, ciphertext, version.encode()).decode()
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Segredo criptografado inválido") from exc
