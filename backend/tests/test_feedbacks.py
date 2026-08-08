"""Testes do fluxo de feedbacks com moderação admin."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend import main
from backend.feedback_store import (
    FeedbackStore,
    FeedbackValidationError,
    create_feedback_store,
)


class FeedbackStoreUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = str(Path(self._tmpdir.name) / "feedbacks.db")
        self.store = FeedbackStore(db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_create_feedback_starts_as_pending(self) -> None:
        item = self.store.create(
            user_id="user-1",
            author_name="Trader Demo",
            content="O robô melhorou bastante minhas entradas.",
            rating=5,
        )
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["user_id"], "user-1")
        self.assertEqual(item["rating"], 5)
        self.assertNotIn(item, self.store.list_approved())

    def test_approved_feedback_appears_in_public_list(self) -> None:
        created = self.store.create(
            user_id="user-1",
            author_name="Trader Demo",
            content="Feedback aprovado deve aparecer para todos.",
            rating=4,
        )
        reviewed = self.store.review(
            feedback_id=created["id"],
            status="approved",
            reviewer_user_id="admin-1",
        )
        assert reviewed is not None
        approved = self.store.list_approved()
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["id"], created["id"])
        self.assertEqual(approved[0]["status"], "approved")

    def test_rejected_feedback_does_not_appear_publicly(self) -> None:
        created = self.store.create(
            user_id="user-1",
            author_name="Trader Demo",
            content="Este feedback será rejeitado pelo admin.",
        )
        self.store.review(created["id"], "rejected", "admin-1")
        self.assertEqual(self.store.list_approved(), [])
        mine = self.store.list_by_user("user-1")
        self.assertEqual(mine[0]["status"], "rejected")

    def test_rejected_feedback_is_deleted_after_seven_days(self) -> None:
        created = self.store.create(
            user_id="user-1",
            author_name="Trader Demo",
            content="Este feedback rejeitado deve expirar automaticamente.",
        )
        self.store.review(created["id"], "rejected", "admin-1")
        expired_at = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        self._set_feedback_dates(created["id"], reviewed_at=expired_at)

        self.assertEqual(self.store.purge_expired_rejected(), 1)
        self.assertIsNone(self.store.get_by_id(created["id"]))

    def test_rejected_feedback_can_be_approved_during_review_window(self) -> None:
        created = self.store.create(
            user_id="user-1",
            author_name="Trader Demo",
            content="Este feedback será reavaliado antes de completar sete dias.",
        )
        self.store.review(created["id"], "rejected", "admin-1")
        reviewed = self.store.review(created["id"], "approved", "admin-2")

        assert reviewed is not None
        self.assertEqual(reviewed["status"], "approved")

    def test_approved_feedback_can_be_archived_and_deleted(self) -> None:
        created = self.store.create(
            user_id="user-1",
            author_name="Trader Demo",
            content="Este feedback aprovado será arquivado pelo administrador.",
        )
        self.store.review(created["id"], "approved", "admin-1")

        archived = self.store.archive(created["id"], "admin-1")

        assert archived is not None
        self.assertEqual(archived["status"], "archived")
        self.assertEqual(self.store.list_approved(), [])
        self.assertTrue(self.store.delete(created["id"]))
        self.assertIsNone(self.store.get_by_id(created["id"]))

    def test_feedback_lists_support_ten_item_pages(self) -> None:
        for index in range(12):
            created = self.store.create(
                user_id="user-1",
                author_name=f"Trader {index:02d}",
                content=f"Feedback número {index:02d} com conteúdo suficiente.",
            )
            self.store.review(created["id"], "approved", "admin-1")

        first_page = self.store.list_approved(limit=10, offset=0)
        second_page = self.store.list_approved(limit=10, offset=10)

        self.assertEqual(len(first_page), 10)
        self.assertEqual(len(second_page), 2)

    def test_admin_status_ordering_matches_moderation_priority(self) -> None:
        oldest_pending = self.store.create(
            user_id="user-1",
            author_name="Primeiro",
            content="Primeira solicitação enviada para a moderação.",
        )
        newest_pending = self.store.create(
            user_id="user-2",
            author_name="Segundo",
            content="Segunda solicitação enviada para a moderação.",
        )
        self._set_feedback_dates(oldest_pending["id"], created_at="2026-01-01T00:00:00+00:00")
        self._set_feedback_dates(newest_pending["id"], created_at="2026-01-02T00:00:00+00:00")

        pending = self.store.list_for_admin("pending")

        self.assertEqual([item["id"] for item in pending], [oldest_pending["id"], newest_pending["id"]])

    def _set_feedback_dates(
        self,
        feedback_id: str,
        *,
        created_at: str | None = None,
        reviewed_at: str | None = None,
    ) -> None:
        """Ajusta datas persistidas para validar ordenação e expiração."""
        assignments: list[str] = []
        values: list[str] = []
        if created_at is not None:
            assignments.append("created_at = ?")
            values.append(created_at)
        if reviewed_at is not None:
            assignments.append("reviewed_at = ?")
            values.append(reviewed_at)
        values.append(feedback_id)
        connection = sqlite3.connect(self.store.database_path)
        try:
            connection.execute(
                f"update feedbacks set {', '.join(assignments)} where id = ?",
                values,
            )
            connection.commit()
        finally:
            connection.close()

    def test_empty_content_raises_validation_error(self) -> None:
        with self.assertRaises(FeedbackValidationError):
            self.store.create(user_id="user-1", author_name="Demo", content="")

    def test_invalid_rating_raises_validation_error(self) -> None:
        with self.assertRaises(FeedbackValidationError):
            self.store.create(
                user_id="user-1",
                author_name="Demo",
                content="Conteúdo válido com rating inválido.",
                rating=9,
            )

    def test_list_by_user_isolates_tenants(self) -> None:
        self.store.create(
            user_id="user-a",
            author_name="Usuario A",
            content="Feedback do usuário A com texto suficiente.",
        )
        self.store.create(
            user_id="user-b",
            author_name="Usuario B",
            content="Feedback do usuário B com texto suficiente.",
        )
        self.assertEqual(len(self.store.list_by_user("user-a")), 1)
        self.assertEqual(self.store.list_by_user("user-a")[0]["user_id"], "user-a")

    def test_create_feedback_with_video_and_result(self) -> None:
        item = self.store.create(
            user_id="user-1",
            author_name="Trader Demo",
            content="Descrição completa da operação com o AutoBot.",
            result="+R$ 420,00 / WIN",
            video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            rating=5,
        )
        self.assertEqual(item["result"], "+R$ 420,00 / WIN")
        self.assertEqual(item["video_url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(item["content"], "Descrição completa da operação com o AutoBot.")

    def test_invalid_video_url_raises_validation_error(self) -> None:
        with self.assertRaises(FeedbackValidationError):
            self.store.create(
                user_id="user-1",
                author_name="Demo",
                content="Descrição válida com URL de vídeo inválida.",
                video_url="javascript:alert(1)",
            )

    def test_empty_result_is_stored_as_null(self) -> None:
        item = self.store.create(
            user_id="user-1",
            author_name="Demo",
            content="Descrição válida sem resultado informado.",
            result="   ",
        )
        self.assertIsNone(item["result"])
        self.assertIsNone(item["video_url"])


class FeedbackApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = str(Path(self._tmpdir.name) / "feedbacks-api.db")
        self.old_api_key = main.config.panel_api_key
        self.old_admin_emails = main.config.admin_emails
        self.old_store = main.feedback_store
        main.config.panel_api_key = "test-key"
        main.config.admin_emails = {"admin@elcapobot.local"}
        main.feedback_store = create_feedback_store(db_path)
        self.client = TestClient(main.app)
        self.user_headers = {
            "x-api-key": "test-key",
            "x-user-id": "trader-1",
            "x-user-email": "demo@elcapobot.local",
        }
        self.admin_headers = {
            "x-api-key": "test-key",
            "x-user-id": "admin-1",
            "x-user-email": "admin@elcapobot.local",
        }

    def tearDown(self) -> None:
        main.config.panel_api_key = self.old_api_key
        main.config.admin_emails = self.old_admin_emails
        main.feedback_store = self.old_store
        self._tmpdir.cleanup()

    def test_create_feedback_returns_pending_and_hidden_from_public(self) -> None:
        create = self.client.post(
            "/feedbacks",
            headers=self.user_headers,
            json={
                "author_name": "Demo",
                "content": "Gostei muito do AutoBot nas operações OTC.",
                "rating": 5,
            },
        )
        self.assertEqual(create.status_code, 201)
        body = create.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["status"], "pending")

        public = self.client.get("/feedbacks", headers=self.user_headers)
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()["data"]["items"], [])

        mine = self.client.get("/feedbacks/mine", headers=self.user_headers)
        self.assertEqual(len(mine.json()["data"]["items"]), 1)

    def test_admin_can_approve_feedback_for_public_list(self) -> None:
        created = self.client.post(
            "/feedbacks",
            headers=self.user_headers,
            json={
                "author_name": "Demo",
                "content": "Feedback que o admin vai aprovar agora.",
                "rating": 4,
            },
        ).json()["data"]

        approve = self.client.post(
            f"/admin/feedbacks/{created['id']}/approve",
            headers=self.admin_headers,
        )
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json()["data"]["status"], "approved")

        public = self.client.get("/feedbacks", headers=self.user_headers)
        items = public.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], created["id"])

    def test_non_admin_cannot_approve_feedback(self) -> None:
        created = self.client.post(
            "/feedbacks",
            headers=self.user_headers,
            json={
                "author_name": "Demo",
                "content": "Tentativa de auto-aprovação deve falhar.",
            },
        ).json()["data"]

        response = self.client.post(
            f"/admin/feedbacks/{created['id']}/approve",
            headers=self.user_headers,
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["ok"])

    def test_admin_can_list_pending_feedbacks(self) -> None:
        self.client.post(
            "/feedbacks",
            headers=self.user_headers,
            json={
                "author_name": "Demo",
                "content": "Item pendente para a fila do admin.",
            },
        )
        pending = self.client.get(
            "/admin/feedbacks",
            headers=self.admin_headers,
            params={"status": "pending"},
        )
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(len(pending.json()["data"]["items"]), 1)

    def test_reject_keeps_feedback_off_public_list(self) -> None:
        created = self.client.post(
            "/feedbacks",
            headers=self.user_headers,
            json={
                "author_name": "Demo",
                "content": "Feedback que será rejeitado pelo admin.",
            },
        ).json()["data"]

        reject = self.client.post(
            f"/admin/feedbacks/{created['id']}/reject",
            headers=self.admin_headers,
        )
        self.assertEqual(reject.status_code, 200)
        self.assertEqual(reject.json()["data"]["status"], "rejected")

        public = self.client.get("/feedbacks", headers=self.user_headers)
        self.assertEqual(public.json()["data"]["items"], [])

    def test_create_feedback_with_video_result_appears_when_approved(self) -> None:
        created = self.client.post(
            "/feedbacks",
            headers=self.user_headers,
            json={
                "author_name": "Demo",
                "description": "Descrição com vídeo e resultado para a comunidade.",
                "result": "WIN +R$ 180",
                "video_url": "https://youtu.be/dQw4w9WgXcQ",
                "rating": 5,
            },
        ).json()["data"]
        self.assertEqual(created["result"], "WIN +R$ 180")
        self.assertEqual(created["video_url"], "https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(created["description"], created["content"])

        self.client.post(
            f"/admin/feedbacks/{created['id']}/approve",
            headers=self.admin_headers,
        )
        public = self.client.get("/feedbacks", headers=self.user_headers).json()["data"]["items"]
        self.assertEqual(public[0]["result"], "WIN +R$ 180")
        self.assertEqual(public[0]["video_url"], "https://youtu.be/dQw4w9WgXcQ")
        self.assertIn("Descrição com vídeo", public[0]["description"])

    def test_feedback_endpoints_paginate_ten_at_a_time(self) -> None:
        for index in range(12):
            created = self.client.post(
                "/feedbacks",
                headers=self.user_headers,
                json={
                    "author_name": f"Demo {index:02d}",
                    "content": f"Feedback paginado número {index:02d} para aprovação.",
                },
            ).json()["data"]
            self.client.post(
                f"/admin/feedbacks/{created['id']}/approve",
                headers=self.admin_headers,
            )

        first = self.client.get("/feedbacks", headers=self.user_headers).json()["data"]
        second = self.client.get(
            "/feedbacks",
            headers=self.user_headers,
            params={"offset": 10},
        ).json()["data"]

        self.assertEqual(len(first["items"]), 10)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 10)
        self.assertEqual(len(second["items"]), 2)
        self.assertFalse(second["has_more"])

    def test_admin_can_archive_and_delete_approved_feedback(self) -> None:
        created = self.client.post(
            "/feedbacks",
            headers=self.user_headers,
            json={
                "author_name": "Demo",
                "content": "Feedback aprovado que será arquivado e excluído.",
            },
        ).json()["data"]
        self.client.post(
            f"/admin/feedbacks/{created['id']}/approve",
            headers=self.admin_headers,
        )

        archive = self.client.patch(
            f"/admin/feedbacks/{created['id']}",
            headers=self.admin_headers,
            json={"status": "archived"},
        )
        self.assertEqual(archive.status_code, 200)
        self.assertEqual(archive.json()["data"]["status"], "archived")
        public = self.client.get("/feedbacks", headers=self.user_headers)
        self.assertEqual(public.json()["data"]["items"], [])

        delete = self.client.delete(
            f"/admin/feedbacks/{created['id']}",
            headers=self.admin_headers,
        )
        self.assertEqual(delete.status_code, 204)

    def test_admin_can_reapprove_rejected_feedback(self) -> None:
        created = self.client.post(
            "/feedbacks",
            headers=self.user_headers,
            json={
                "author_name": "Demo",
                "content": "Feedback rejeitado que será reavaliado pelo admin.",
            },
        ).json()["data"]
        self.client.post(
            f"/admin/feedbacks/{created['id']}/reject",
            headers=self.admin_headers,
        )

        approve = self.client.post(
            f"/admin/feedbacks/{created['id']}/approve",
            headers=self.admin_headers,
        )

        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json()["data"]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
