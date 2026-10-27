"""Shared fixture for the social tests: an isolated SQLite DB, private media roots, synthetic people.

Never touches a real database, network or the real DATA_ROOT: the social store,
the global store, upload temp files and the retention summary live in a
temporary directory for the class.
"""
from __future__ import annotations

import hashlib
import inspect
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from classroom_app import database
from classroom_app.dependencies import get_current_user
from classroom_app.services import (
    file_service,
    presence_service,
    social_media_retention_service,
    social_media_service,
    social_media_store,
    social_push_service,
)
from tests.sqlite_database_fixture import isolated_sqlite_database

PEOPLE = ("甲同学", "乙同学", "丙同学", "丁同学", "戊同学")
SOCIAL_TABLES = (
    "social_friendships", "social_groups", "social_group_members", "social_messages", "social_message_media",
    "social_media_blobs", "social_media_tokens", "social_upload_sessions", "social_dm_reads",
    "social_upload_usage", "social_lan_transfers", "user_presence", "private_message_blocks",
    "message_center_notifications", "social_dm_participants", "social_friend_request_log", "social_blocks",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SocialDbTestCase(unittest.TestCase):
    """Five students in one class plus a super-admin teacher; helpers for users and clients."""

    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        cls.tmp = Path(cls.enterClassContext(tempfile.TemporaryDirectory(prefix="lanshare-social-")))
        stack = cls.enterClassContext(ExitStack())
        cls.social_root = cls.tmp / "social" / "sha256"
        cls.global_root = cls.tmp / "global"
        for target, attribute, value in (
            (social_media_store, "SOCIAL_STORE_ROOT", cls.social_root),
            (social_media_store, "UPLOAD_TEMP_ROOT", cls.tmp / "uploads"),
            (social_media_retention_service, "SUMMARY_PATH", cls.tmp / "runtime" / "retention.json"),
            (file_service, "GLOBAL_FILES_DIR", cls.global_root),
            (file_service, "GLOBAL_FILES_LEGACY_DIRS", ()),
        ):
            stack.enter_context(patch.object(target, attribute, value))
        with database.get_db_connection() as conn:
            conn.execute("INSERT INTO teachers (name, email, hashed_password, is_super_admin) VALUES (?, ?, ?, 1)",
                         ("合成教师", "social-teacher@example.test", "synthetic-hash"))
            cls.teacher_id = conn.execute("SELECT id FROM teachers WHERE email = ?",
                                          ("social-teacher@example.test",)).fetchone()[0]
            conn.execute("INSERT INTO classes (name, created_by_teacher_id) VALUES (?, ?)", ("社交测试班", cls.teacher_id))
            class_id = conn.execute("SELECT id FROM classes WHERE name = ?", ("社交测试班",)).fetchone()[0]
            cls.student_ids = []
            for index, name in enumerate(PEOPLE):
                conn.execute(
                    "INSERT INTO students (student_id_number, name, class_id, enrollment_status, created_at) "
                    "VALUES (?, ?, ?, 'active', '2026-10-27T08:00:00')", (f"S-SOCIAL-{index}", name, class_id))
                cls.student_ids.append(conn.execute("SELECT id FROM students WHERE student_id_number = ?",
                                                    (f"S-SOCIAL-{index}",)).fetchone()[0])
            conn.commit()
        cls.a, cls.b, cls.c, cls.d, cls.e = (f"student:{pk}" for pk in cls.student_ids)
        cls.teacher = f"teacher:{cls.teacher_id}"

    @classmethod
    def tearDownClass(cls):
        from classroom_app.services.social_common import drain_social_executor

        drain_social_executor()  # background last-seen writes must finish before the temp DB is removed
        super().tearDownClass()

    def setUp(self):
        self.addCleanup(self._drain)
        presence_service.reset_for_tests()
        social_push_service.reset_for_tests()
        social_media_service.upload_limiter = social_media_service.ByteRateLimiter()
        social_media_service._slot_registry.clear()
        with self.connect() as conn:
            from classroom_app.services.social_common import ensure_schema

            ensure_schema(conn)
            for table in SOCIAL_TABLES:
                conn.execute(f"DELETE FROM {table}")
            conn.commit()

    @staticmethod
    def _drain():
        from classroom_app.services.social_common import drain_social_executor

        drain_social_executor()

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def connect():
        return database.get_db_connection()

    @staticmethod
    def user(identity: str) -> dict:
        role, pk = identity.split(":")
        return {"id": int(pk), "role": role, "name": identity}

    def run_service(self, fn, *args, **kwargs):
        """Call ``fn(conn, ...)`` in its own committed transaction; returns ``(result, events)``."""
        events: list = []
        if "events" in inspect.signature(fn).parameters:
            kwargs["events"] = events
        with self.connect() as conn:
            result = fn(conn, *args, **kwargs)
            conn.commit()
        return result, events

    def befriend(self, first: str, second: str) -> None:
        from classroom_app.services import social_friend_service as friends

        self.run_service(friends.send_request, self.user(first), second, "hi")
        self.run_service(friends.send_request, self.user(second), first, "")

    def client_as(self, identity: str) -> TestClient:
        from classroom_app.app import app

        user = self.user(identity)
        app.dependency_overrides[get_current_user] = lambda: dict(user)
        self.addCleanup(app.dependency_overrides.pop, get_current_user, None)
        # app.py turns an API 403 into 401 when no session cookie is present; tests have none.
        hint = patch("classroom_app.app.get_active_user_from_request", return_value=dict(user))
        hint.start()
        self.addCleanup(hint.stop)
        return TestClient(app)

    def put_global_file(self, data: bytes) -> str:
        digest = sha(data)
        path = self.global_root / digest[:2] / digest[2:4] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return digest

    def count(self, sql: str, params: tuple = ()) -> int:
        with self.connect() as conn:
            return int(conn.execute(sql, params).fetchone()[0])
