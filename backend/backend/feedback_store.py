"""Persistência e regras de negócio dos feedbacks de usuários."""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Literal
from urllib.parse import urlparse

FeedbackStatus = Literal["pending", "approved", "rejected", "archived"]

VALID_STATUSES: set[str] = {"pending", "approved", "rejected", "archived"}
REJECTED_RETENTION_DAYS = 7
MIN_CONTENT_LENGTH = 10
MAX_CONTENT_LENGTH = 2000
MIN_AUTHOR_LENGTH = 2
MAX_AUTHOR_LENGTH = 80
MAX_RESULT_LENGTH = 120
MAX_VIDEO_URL_LENGTH = 500

_WHITESPACE_RE = re.compile(r"\s+")


class FeedbackValidationError(ValueError):
    """Erro de validação de input de feedback."""


class FeedbackStore:
    """Armazena feedbacks em SQLite com status de moderação."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        parent = os.path.dirname(database_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._ensure_schema()

    def create(
        self,
        *,
        user_id: str,
        author_name: str,
        content: str,
        rating: int | None = None,
        result: str | None = None,
        video_url: str | None = None,
    ) -> dict[str, Any]:
        """
        Cria um feedback com status pending.

        Args:
            user_id: Identificador do autor (da sessão autenticada).
            author_name: Nome público exibido no feedback.
            content: Descrição do feedback.
            rating: Nota opcional de 1 a 5.
            result: Resultado da operação (ex.: WIN, +R$ 150).
            video_url: URL de vídeo (YouTube, Vimeo ou arquivo direto).

        Returns:
            Feedback criado serializado.

        Raises:
            FeedbackValidationError: Se algum campo for inválido.
        """
        normalized_user_id = _require_non_empty(user_id, "user_id")
        normalized_author = _normalize_author_name(author_name)
        normalized_content = _normalize_content(content)
        normalized_rating = _normalize_rating(rating)
        normalized_result = _normalize_result(result)
        normalized_video = _normalize_video_url(video_url)
        now = _utc_now_iso()
        feedback_id = str(uuid.uuid4())

        with self._connect() as connection:
            connection.execute(
                """
                insert into feedbacks (
                    id, user_id, author_name, content, rating, result, video_url, status,
                    created_at, updated_at, reviewed_at, reviewed_by
                ) values (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, null, null)
                """,
                (
                    feedback_id,
                    normalized_user_id,
                    normalized_author,
                    normalized_content,
                    normalized_rating,
                    normalized_result,
                    normalized_video,
                    now,
                    now,
                ),
            )

        item = self.get_by_id(feedback_id)
        if item is None:
            raise RuntimeError("Falha ao persistir feedback")
        return item

    def get_by_id(self, feedback_id: str) -> dict[str, Any] | None:
        """Retorna um feedback pelo id ou None."""
        self.purge_expired_rejected()
        with self._connect() as connection:
            row = connection.execute(
                "select * from feedbacks where id = ?",
                (feedback_id,),
            ).fetchone()
        return _row_to_dict(row) if row else None

    def list_approved(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Lista feedbacks aprovados, do envio mais recente ao mais antigo.

        Args:
            limit: Quantidade máxima de itens; None retorna todos.
            offset: Quantidade de itens a ignorar no início.
        """
        self.purge_expired_rejected()
        pagination_sql, pagination_params = _pagination_clause(limit, offset)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                select * from feedbacks
                where status = 'approved'
                order by created_at desc, id desc
                {pagination_sql}
                """,
                pagination_params,
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_by_user(
        self,
        user_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Lista feedbacks do próprio usuário, mais recentes primeiro.

        Args:
            user_id: Identificador obtido da sessão autenticada.
            limit: Quantidade máxima de itens; None retorna todos.
            offset: Quantidade de itens a ignorar no início.
        """
        self.purge_expired_rejected()
        normalized_user_id = _require_non_empty(user_id, "user_id")
        pagination_sql, pagination_params = _pagination_clause(limit, offset)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                select * from feedbacks
                where user_id = ?
                order by created_at desc, id desc
                {pagination_sql}
                """,
                (normalized_user_id, *pagination_params),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_for_admin(
        self,
        status: str | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Lista feedbacks para moderação.

        Args:
            status: Filtro opcional de status.
            limit: Quantidade máxima de itens; None retorna todos.
            offset: Quantidade de itens a ignorar no início.
        """
        if status is not None and status not in VALID_STATUSES:
            raise FeedbackValidationError("Status de filtro inválido")

        self.purge_expired_rejected()
        pagination_sql, pagination_params = _pagination_clause(limit, offset)
        with self._connect() as connection:
            if status:
                order_by = {
                    "pending": "created_at asc, id asc",
                    "approved": "created_at desc, id desc",
                    "rejected": "reviewed_at asc, created_at asc, id asc",
                    "archived": "updated_at desc, created_at desc, id desc",
                }[status]
                rows = connection.execute(
                    f"""
                    select * from feedbacks
                    where status = ?
                    order by {order_by}
                    {pagination_sql}
                    """,
                    (status, *pagination_params),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    select * from feedbacks
                    order by
                        case status
                            when 'pending' then 0
                            when 'approved' then 1
                            when 'rejected' then 2
                            else 3
                        end,
                        created_at desc
                    {pagination_sql}
                    """,
                    pagination_params,
                ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def review(
        self,
        feedback_id: str,
        status: FeedbackStatus,
        reviewer_user_id: str,
    ) -> dict[str, Any] | None:
        """
        Aprova ou rejeita um feedback.

        Args:
            feedback_id: Id do feedback.
            status: `approved` ou `rejected`.
            reviewer_user_id: Id do admin que revisou.

        Returns:
            Feedback atualizado, ou None se não existir.
        """
        if status not in {"approved", "rejected"}:
            raise FeedbackValidationError("Status de revisão inválido")
        reviewer = _require_non_empty(reviewer_user_id, "reviewer_user_id")
        self.purge_expired_rejected()
        now = _utc_now_iso()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                update feedbacks
                set status = ?, reviewed_at = ?, reviewed_by = ?, updated_at = ?
                where id = ?
                """,
                (status, now, reviewer, now, feedback_id),
            )
            if cursor.rowcount == 0:
                return None

        return self.get_by_id(feedback_id)

    def archive(
        self,
        feedback_id: str,
        reviewer_user_id: str,
    ) -> dict[str, Any] | None:
        """
        Arquiva um feedback aprovado para removê-lo da exibição pública.

        Args:
            feedback_id: Identificador do feedback aprovado.
            reviewer_user_id: Identificador do admin autenticado.

        Returns:
            Feedback arquivado, ou None se não existir ou não estiver aprovado.
        """
        reviewer = _require_non_empty(reviewer_user_id, "reviewer_user_id")
        now = _utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                update feedbacks
                set status = 'archived', reviewed_at = ?, reviewed_by = ?, updated_at = ?
                where id = ? and status = 'approved'
                """,
                (now, reviewer, now, feedback_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_by_id(feedback_id)

    def delete(self, feedback_id: str) -> bool:
        """
        Exclui definitivamente um feedback.

        Args:
            feedback_id: Identificador do feedback.

        Returns:
            True quando um registro foi removido.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                "delete from feedbacks where id = ?",
                (feedback_id,),
            )
        return cursor.rowcount > 0

    def purge_expired_rejected(self, now: datetime | None = None) -> int:
        """
        Exclui feedbacks rejeitados cujo prazo de reavaliação terminou.

        Args:
            now: Horário UTC opcional, útil para execução determinística em testes.

        Returns:
            Quantidade de feedbacks excluídos.
        """
        current = now or datetime.now(timezone.utc)
        cutoff = (current - timedelta(days=REJECTED_RETENTION_DAYS)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                delete from feedbacks
                where status = 'rejected'
                  and reviewed_at is not null
                  and reviewed_at <= ?
                """,
                (cutoff,),
            )
        return cursor.rowcount

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                create table if not exists feedbacks (
                    id text primary key,
                    user_id text not null,
                    author_name text not null,
                    content text not null,
                    rating integer,
                    result text,
                    video_url text,
                    status text not null check (
                        status in ('pending', 'approved', 'rejected', 'archived')
                    ),
                    created_at text not null,
                    updated_at text not null,
                    reviewed_at text,
                    reviewed_by text
                )
                """
            )
            self._ensure_column(connection, "feedbacks", "result", "text")
            self._ensure_column(connection, "feedbacks", "video_url", "text")
            self._ensure_archived_status(connection)
            connection.execute(
                "create index if not exists feedbacks_status_idx on feedbacks (status)"
            )
            connection.execute(
                "create index if not exists feedbacks_user_id_idx on feedbacks (user_id)"
            )

    def _ensure_archived_status(self, connection: sqlite3.Connection) -> None:
        """Migra a constraint de status antiga sem perder feedbacks existentes."""
        row = connection.execute(
            "select sql from sqlite_master where type = 'table' and name = 'feedbacks'"
        ).fetchone()
        create_sql = str(row["sql"] if row else "")
        if "'archived'" in create_sql:
            return

        connection.execute("alter table feedbacks rename to feedbacks_legacy")
        connection.execute(
            """
            create table feedbacks (
                id text primary key,
                user_id text not null,
                author_name text not null,
                content text not null,
                rating integer,
                result text,
                video_url text,
                status text not null check (
                    status in ('pending', 'approved', 'rejected', 'archived')
                ),
                created_at text not null,
                updated_at text not null,
                reviewed_at text,
                reviewed_by text
            )
            """
        )
        connection.execute(
            """
            insert into feedbacks (
                id, user_id, author_name, content, rating, result, video_url, status,
                created_at, updated_at, reviewed_at, reviewed_by
            )
            select
                id, user_id, author_name, content, rating, result, video_url, status,
                created_at, updated_at, reviewed_at, reviewed_by
            from feedbacks_legacy
            """
        )
        connection.execute("drop table feedbacks_legacy")

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        existing = {
            row["name"]
            for row in connection.execute(f"pragma table_info({table})").fetchall()
        }
        if column not in existing:
            connection.execute(f"alter table {table} add column {column} {definition}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


def create_feedback_store(database_path: str | None = None) -> FeedbackStore:
    """
    Factory do store de feedbacks.

    Args:
        database_path: Caminho opcional do SQLite. Se omitido, usa
            `FEEDBACK_DB_PATH` ou deriva de `ROBOT_DB_PATH`.
    """
    if database_path:
        return FeedbackStore(database_path)

    env_path = os.getenv("FEEDBACK_DB_PATH", "").strip()
    if env_path:
        return FeedbackStore(env_path)

    robot_db = os.getenv("ROBOT_DB_PATH", "").strip()
    if robot_db:
        parent = os.path.dirname(robot_db) or "."
        return FeedbackStore(os.path.join(parent, "feedbacks.db"))

    return FeedbackStore(os.path.join("data", "feedbacks.db"))


def public_feedback_view(item: dict[str, Any]) -> dict[str, Any]:
    """Remove campos internos antes de expor ao cliente."""
    rejection_expires_at = None
    if item["status"] == "rejected" and item.get("reviewed_at"):
        reviewed_at = datetime.fromisoformat(item["reviewed_at"])
        rejection_expires_at = (
            reviewed_at + timedelta(days=REJECTED_RETENTION_DAYS)
        ).isoformat()
    return {
        "id": item["id"],
        "author_name": item["author_name"],
        "content": item["content"],
        "description": item["content"],
        "result": item.get("result"),
        "video_url": item.get("video_url"),
        "rating": item["rating"],
        "status": item["status"],
        "created_at": item["created_at"],
        "reviewed_at": item.get("reviewed_at"),
        "rejection_expires_at": rejection_expires_at,
    }


def owner_feedback_view(item: dict[str, Any]) -> dict[str, Any]:
    """View para o autor ver o próprio feedback (inclui status)."""
    return public_feedback_view(item)


def admin_feedback_view(item: dict[str, Any]) -> dict[str, Any]:
    """View administrativa com metadados de moderação."""
    return {
        **public_feedback_view(item),
        "user_id": item["user_id"],
        "reviewed_by": item.get("reviewed_by"),
        "updated_at": item.get("updated_at"),
    }


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "author_name": row["author_name"],
        "content": row["content"],
        "rating": row["rating"],
        "result": row["result"] if "result" in keys else None,
        "video_url": row["video_url"] if "video_url" in keys else None,
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "reviewed_at": row["reviewed_at"],
        "reviewed_by": row["reviewed_by"],
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pagination_clause(limit: int | None, offset: int) -> tuple[str, tuple[int, ...]]:
    """
    Valida e monta a paginação SQLite.

    Args:
        limit: Quantidade máxima de itens; None desativa o limite.
        offset: Quantidade de itens ignorados.

    Returns:
        Fragmento SQL e parâmetros posicionais.

    Raises:
        FeedbackValidationError: Se limite ou offset forem inválidos.
    """
    if offset < 0:
        raise FeedbackValidationError("Offset deve ser maior ou igual a zero")
    if limit is None:
        if offset:
            raise FeedbackValidationError("Offset exige um limite")
        return "", ()
    if limit < 1 or limit > 100:
        raise FeedbackValidationError("Limite deve estar entre 1 e 100")
    return "limit ? offset ?", (limit, offset)


def _require_non_empty(value: str, field: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise FeedbackValidationError(f"{field} é obrigatório")
    return normalized


def _normalize_author_name(author_name: str) -> str:
    cleaned = _WHITESPACE_RE.sub(" ", (author_name or "").strip())
    if len(cleaned) < MIN_AUTHOR_LENGTH:
        raise FeedbackValidationError(
            f"Nome deve ter pelo menos {MIN_AUTHOR_LENGTH} caracteres"
        )
    if len(cleaned) > MAX_AUTHOR_LENGTH:
        raise FeedbackValidationError(
            f"Nome deve ter no máximo {MAX_AUTHOR_LENGTH} caracteres"
        )
    return cleaned


def _normalize_content(content: str) -> str:
    cleaned = _WHITESPACE_RE.sub(" ", (content or "").strip())
    if len(cleaned) < MIN_CONTENT_LENGTH:
        raise FeedbackValidationError(
            f"Descrição deve ter pelo menos {MIN_CONTENT_LENGTH} caracteres"
        )
    if len(cleaned) > MAX_CONTENT_LENGTH:
        raise FeedbackValidationError(
            f"Descrição deve ter no máximo {MAX_CONTENT_LENGTH} caracteres"
        )
    return cleaned


def _normalize_result(result: str | None) -> str | None:
    if result is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", str(result).strip())
    if not cleaned:
        return None
    if len(cleaned) > MAX_RESULT_LENGTH:
        raise FeedbackValidationError(
            f"Resultado deve ter no máximo {MAX_RESULT_LENGTH} caracteres"
        )
    return cleaned


def _normalize_video_url(video_url: str | None) -> str | None:
    if video_url is None:
        return None
    cleaned = str(video_url).strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_VIDEO_URL_LENGTH:
        raise FeedbackValidationError(
            f"URL do vídeo deve ter no máximo {MAX_VIDEO_URL_LENGTH} caracteres"
        )
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FeedbackValidationError("URL do vídeo deve começar com http:// ou https://")
    return cleaned


def _normalize_rating(rating: int | None) -> int | None:
    if rating is None:
        return None
    if not isinstance(rating, int) or isinstance(rating, bool):
        raise FeedbackValidationError("Rating deve ser um inteiro de 1 a 5")
    if rating < 1 or rating > 5:
        raise FeedbackValidationError("Rating deve ser um inteiro de 1 a 5")
    return rating
