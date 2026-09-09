"""Opt-in whiteboard concurrency checks in a fresh, local PostgreSQL schema.

Set LANSHARE_WHITEBOARD_TEST_DATABASE_URL explicitly. Every table and mutation
stays in a unique schema created by this suite and removed on completion.
"""

from __future__ import annotations

import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from urllib.parse import urlsplit

from classroom_app.db import schema_material_whiteboards as schema
from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.services import material_whiteboard_service as service


DATABASE_URL = os.environ.get("LANSHARE_WHITEBOARD_TEST_DATABASE_URL", "")
OWNER = {"role": "teacher", "id": 11}


def payload(name="白板"):
    return {"name": name, "elements": [{"type": "text", "text": name}], "viewport": {"scale": 1}}


@unittest.skipUnless(DATABASE_URL, "explicit local whiteboard PostgreSQL test URL required")
class MaterialWhiteboardPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        if urlsplit(DATABASE_URL).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("refusing non-local PostgreSQL whiteboard tests")
        cls.driver = psycopg
        cls.schema_name = f"whiteboard_test_{uuid.uuid4().hex}"
        with psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=5) as conn:
            address = conn.execute("SELECT host(inet_server_addr())").fetchone()[0]
            if address not in {"127.0.0.1", "::1"}:
                raise RuntimeError("refusing a non-loopback PostgreSQL server")
            conn.execute(f"CREATE SCHEMA {cls.schema_name}")
        cls.addClassCleanup(cls.drop_owned_schema)
        with cls.connection() as conn:
            conn.execute("CREATE TABLE course_materials (id INTEGER PRIMARY KEY, teacher_id INTEGER, material_path TEXT)")
            conn.execute("INSERT INTO course_materials VALUES (501, 11, 'whiteboard-test.md')")
            with patch.object(schema, "_SCHEMA_READY", False), patch.object(schema, "get_configured_db_engine", return_value="postgres"):
                schema.ensure_material_whiteboard_schema(conn)

    @classmethod
    def connection(cls):
        raw = cls.driver.connect(
            DATABASE_URL, row_factory=sqlite_compatible_dict_row, connect_timeout=5,
            options=f"-c search_path={cls.schema_name} -c statement_timeout=15000 -c lock_timeout=10000",
        )
        return LanSharePostgresConnection(raw)

    @classmethod
    def drop_owned_schema(cls):
        with cls.driver.connect(DATABASE_URL, autocommit=True, connect_timeout=5) as conn:
            conn.execute(f"DROP SCHEMA {cls.schema_name} CASCADE")

    def setUp(self):
        self.enterContext(patch.object(schema, "_SCHEMA_READY", True))
        with self.connection() as conn:
            conn.execute("TRUNCATE material_whiteboards")

    def seed(self):
        with self.connection() as conn:
            return service.upsert_board(conn, OWNER, 501, "same-key", payload(), 0)

    def race_saves(self, base_version):
        barrier = Barrier(6)

        def write(index):
            with self.connection() as conn:
                barrier.wait(timeout=10)
                try:
                    board = service.upsert_board(conn, OWNER, 501, "same-key", payload(f"版本 {index}"), base_version)
                    return "saved", board
                except service.WhiteboardConflict as exc:
                    # A unique-key collision must not poison the transaction.
                    self.assertEqual(conn.execute("SELECT 1 AS ok").fetchone()["ok"], 1)
                    return "conflict", exc.board

        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(write, range(6)))
        winners = [board for status, board in results if status == "saved"]
        conflicts = [board for status, board in results if status == "conflict"]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(len(conflicts), 5, results)
        for board in conflicts:
            self.assertEqual(board["name"], winners[0]["name"])
            self.assertEqual(board["version"], winners[0]["version"])
        return winners[0]

    def test_parallel_creation_has_one_winner_and_readable_conflicts(self):
        self.assertEqual(self.race_saves(0)["version"], 1)

    def test_parallel_updates_have_one_winner(self):
        self.seed()
        self.assertEqual(self.race_saves(1)["version"], 2)

    def test_rename_racing_save_preserves_the_rename(self):
        self.seed()
        barrier = Barrier(2)

        def rename():
            with self.connection() as conn:
                barrier.wait(timeout=10)
                return service.rename_board(conn, OWNER, 501, "same-key", "重命名后")

        def save():
            with self.connection() as conn:
                barrier.wait(timeout=10)
                try:
                    return service.upsert_board(conn, OWNER, 501, "same-key", payload("旧名称"), 1)
                except service.WhiteboardConflict:
                    return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            rename_task = executor.submit(rename)
            save_task = executor.submit(save)
            rename_task.result(timeout=15)
            save_task.result(timeout=15)
        with self.connection() as conn:
            self.assertEqual(service.get_board(conn, OWNER, 501, "same-key")["name"], "重命名后")

    def test_delete_racing_save_cannot_resurrect_board(self):
        self.seed()
        barrier = Barrier(2)

        def delete():
            with self.connection() as conn:
                barrier.wait(timeout=10)
                return service.delete_board(conn, OWNER, 501, "same-key")

        def save():
            with self.connection() as conn:
                barrier.wait(timeout=10)
                try:
                    return service.upsert_board(conn, OWNER, 501, "same-key", payload("过期自动保存"), 1)
                except service.WhiteboardConflict:
                    return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            delete_task = executor.submit(delete)
            save_task = executor.submit(save)
            delete_task.result(timeout=15)
            save_task.result(timeout=15)
        with self.connection() as conn:
            self.assertEqual(service.list_boards(conn, OWNER, 501), [])
            row = conn.execute("SELECT deleted_at FROM material_whiteboards").fetchone()
            self.assertIsNotNone(row["deleted_at"])

    def test_recreated_key_rejects_the_previous_incarnation(self):
        self.seed()
        with self.connection() as conn:
            service.delete_board(conn, OWNER, 501, "same-key")
            with self.assertRaises(service.WhiteboardConflict) as stale:
                service.upsert_board(conn, OWNER, 501, "same-key", payload(), 1)
            self.assertIsNone(stale.exception.board)
            recreated = service.upsert_board(conn, OWNER, 501, "same-key", payload("重新创建"), 0)
            self.assertEqual(recreated["version"], 3)
            with self.assertRaises(service.WhiteboardConflict) as stale:
                service.upsert_board(conn, OWNER, 501, "same-key", payload(), 1)
            self.assertEqual(stale.exception.board["name"], "重新创建")


if __name__ == "__main__":
    unittest.main()
