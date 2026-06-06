import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app.services import (
    blog_news_crawler_service,
    chat_handler,
    materials_git_service,
    portfolio_service,
    signature_service,
    submission_file_alignment,
)


class FakeRow(dict):
    def keys(self):
        return super().keys()


class FakeCursor:
    def __init__(self, row=None, rows=None, rowcount=1, lastrowid=0):
        self._row = row
        self._rows = list(rows or [])
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class FakeContextConnection:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class RecordingConnection:
    def __init__(self):
        self.execute_calls = []
        self.commits = 0
        self.row_factory = None

    def cursor(self):
        raise AssertionError("write path must not use raw cursor()")

    def commit(self):
        self.commits += 1

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.execute_calls.append((normalized, tuple(params)))
        return FakeCursor()


class RemainingPostgresWritePathTests(unittest.TestCase):
    def test_chat_log_schema_postgres_validates_without_sqlite_ddl(self):
        class ChatSchemaConn(RecordingConnection):
            def execute(self, sql, params=()):
                normalized = " ".join(str(sql).split())
                self.execute_calls.append((normalized, tuple(params)))
                if "information_schema.columns" in normalized:
                    table_name = params[1]
                    required_columns = {
                        "chat_logs": chat_handler.CHAT_LOG_REQUIRED_COLUMNS,
                        "chat_log_migrations": chat_handler.CHAT_LOG_MIGRATION_REQUIRED_COLUMNS,
                    }[table_name]
                    return FakeCursor(rows=[FakeRow({"column_name": column}) for column in required_columns])
                raise AssertionError(f"unexpected SQL: {normalized}")

        conn = ChatSchemaConn()
        chat_handler._chat_log_schema_ready = False
        try:
            with patch.object(chat_handler, "get_configured_db_engine", return_value="postgres"), patch.object(
                chat_handler,
                "get_db_connection",
                return_value=FakeContextConnection(conn),
            ):
                chat_handler.ensure_chat_log_schema()
        finally:
            chat_handler._chat_log_schema_ready = False

        sql_text = "\n".join(sql for sql, _ in conn.execute_calls)
        self.assertIn("information_schema.columns", sql_text)
        self.assertNotIn("CREATE TABLE", sql_text)
        self.assertNotIn("ALTER TABLE", sql_text)
        self.assertNotIn("INSERT OR REPLACE", sql_text)

    def test_chat_log_migration_marker_uses_postgres_upsert(self):
        conn = RecordingConnection()
        with patch.object(chat_handler, "get_configured_db_engine", return_value="postgres"):
            chat_handler._mark_room_history_migrated(conn, 12)

        sql_text = "\n".join(sql for sql, _ in conn.execute_calls)
        self.assertIn("INSERT INTO chat_log_migrations", sql_text)
        self.assertIn("ON CONFLICT", sql_text)
        self.assertNotIn("INSERT OR REPLACE", sql_text)

    def test_chat_log_insert_uses_returning_helper(self):
        conn = RecordingConnection()
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            chat_handler,
            "get_db_connection",
            return_value=FakeContextConnection(conn),
        ), patch.object(chat_handler, "ensure_room_history_migrated", return_value=None), patch.object(
            chat_handler,
            "CHAT_LOG_DIR",
            Path(tmpdir),
        ), patch.object(
            chat_handler,
            "execute_insert_returning_id",
            return_value=501,
        ) as insert_helper:
            result = chat_handler._save_chat_message_sync(
                12,
                {
                    "user_id": 3,
                    "sender": "Alice",
                    "role": "student",
                    "message": "hello",
                    "timestamp": "2026-06-06T00:00:00",
                    "logged_at": "2026-06-06T00:00:00",
                },
            )

        self.assertEqual(501, result["id"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO chat_logs", insert_helper.call_args.args[1])
        self.assertEqual(1, conn.commits)

    def test_materials_git_workspace_sync_uses_returning_helper_for_new_nodes(self):
        class MaterialsConn(RecordingConnection):
            def execute(self, sql, params=()):
                normalized = " ".join(str(sql).split())
                self.execute_calls.append((normalized, tuple(params)))
                if "SELECT * FROM course_materials" in normalized:
                    return FakeCursor(rows=[])
                return FakeCursor()

        conn = MaterialsConn()
        root = {
            "id": 10,
            "teacher_id": 7,
            "material_path": "repo",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root_dir = Path(tmpdir)
            (root_dir / "chapter").mkdir()
            (root_dir / "chapter" / "readme.md").write_text("hello", encoding="utf-8")
            with patch.object(materials_git_service, "_store_bytes_globally", return_value=("hash-1", 5)), patch.object(
                materials_git_service,
                "execute_insert_returning_id",
                side_effect=[601, 602],
            ) as insert_helper:
                summary, _removable, changed = materials_git_service._sync_workspace_to_repository(conn, root, root_dir)

        self.assertEqual(2, summary["inserted"])
        self.assertEqual([601, 602], [item["id"] for item in changed])
        self.assertEqual(2, insert_helper.call_count)

    def test_portfolio_upsert_selects_persisted_row_without_lastrowid(self):
        class PortfolioConn(RecordingConnection):
            def execute(self, sql, params=()):
                normalized = " ".join(str(sql).split())
                self.execute_calls.append((normalized, tuple(params)))
                if normalized.startswith("SELECT id FROM student_portfolio_items"):
                    return FakeCursor(FakeRow({"id": 701}))
                return FakeCursor()

        conn = PortfolioConn()
        candidate = {
            "source_id": "sub-1",
            "title": "Work",
            "summary": "Summary",
            "artifact_type": "homework",
            "href": "/x",
            "created_at": "2026-06-06T00:00:00",
        }
        with patch.object(portfolio_service, "_load_source_candidate", return_value=candidate), patch.object(
            portfolio_service,
            "_record_growth_event",
            return_value=None,
        ) as record_growth, patch.object(
            portfolio_service,
            "get_portfolio_item",
            return_value={"id": 701},
        ):
            result = portfolio_service.add_portfolio_item(
                conn,
                9,
                source_type=portfolio_service.SOURCE_SUBMISSION,
                source_id="sub-1",
            )

        self.assertEqual(701, result["id"])
        record_growth.assert_called_once()
        self.assertTrue(any("ON CONFLICT" in sql for sql, _ in conn.execute_calls))

    def test_signature_access_request_uses_returning_helper(self):
        conn = RecordingConnection()
        actor = {"id": 5, "role": "teacher"}
        signature_row = FakeRow({"id": 44, "owner_role": "teacher", "owner_id": 9})
        with patch.object(signature_service, "build_signature_actor", return_value=actor), patch.object(
            signature_service,
            "_get_signature_row",
            return_value=signature_row,
        ), patch.object(signature_service, "can_view_signature", return_value=True), patch.object(
            signature_service,
            "can_use_signature",
            return_value=False,
        ), patch.object(signature_service, "can_request_signature_use", return_value=True), patch.object(
            signature_service,
            "execute_insert_returning_id",
            return_value=801,
        ) as insert_helper, patch.object(
            signature_service,
            "_serialize_signature_access_request",
            return_value={"id": 801},
        ):
            result = signature_service.create_signature_access_request(conn, {"id": 5, "role": "teacher"}, 44)

        self.assertEqual({"status": "success", "request": {"id": 801}}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO signature_access_requests", insert_helper.call_args.args[1])

    def test_submission_alignment_uses_returning_helper_for_recovered_submission(self):
        class AlignmentConn(RecordingConnection):
            def execute(self, sql, params=()):
                normalized = " ".join(str(sql).split())
                self.execute_calls.append((normalized, tuple(params)))
                if "FROM submissions s JOIN assignments" in normalized:
                    return FakeCursor(rows=[])
                if normalized.startswith("SELECT id, course_id FROM assignments"):
                    return FakeCursor(FakeRow({"id": params[0], "course_id": "101"}))
                if normalized.startswith("SELECT id, name, class_id FROM students"):
                    return FakeCursor(FakeRow({"id": params[0], "name": "Student", "class_id": 1}))
                if normalized.startswith("SELECT relative_path FROM submission_files"):
                    return FakeCursor(rows=[])
                return FakeCursor()

        conn = AlignmentConn()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            student_dir = root / "101" / "201" / "301"
            student_dir.mkdir(parents=True)
            (student_dir / "answer.txt").write_text("answer", encoding="utf-8")
            with patch.object(submission_file_alignment, "HOMEWORK_SUBMISSIONS_DIR", root), patch.object(
                submission_file_alignment,
                "execute_insert_returning_id",
                return_value=901,
            ) as insert_helper:
                report = submission_file_alignment.recover_orphan_files(conn)

        self.assertEqual(1, report.orphan_submissions_created)
        self.assertEqual(1, report.orphan_files_recovered)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO submissions", insert_helper.call_args.args[1])

    def test_blog_crawler_candidates_use_postgres_conflict_returning(self):
        class BlogConn(RecordingConnection):
            def execute(self, sql, params=()):
                normalized = " ".join(str(sql).split())
                self.execute_calls.append((normalized, tuple(params)))
                if "SELECT * FROM blog_news_crawler_items WHERE url_hash" in normalized:
                    return FakeCursor()
                if "ON CONFLICT DO NOTHING RETURNING *" in normalized:
                    return FakeCursor(
                        FakeRow(
                            {
                                "id": 1001,
                                "run_id": params[0],
                                "keyword": params[1],
                                "course_names_json": params[2],
                                "source_name": params[3],
                                "title": params[4],
                                "url": params[5],
                                "canonical_url": params[6],
                                "url_hash": params[7],
                                "content_hash": params[8],
                                "summary": params[9],
                                "published_at": params[10],
                                "fetched_at": params[11],
                                "media_json": params[12],
                                "score": params[13],
                                "raw_json": params[14],
                                "selected": 0,
                                "duplicate_of_item_id": None,
                                "duplicate_of_post_id": None,
                                "post_id": None,
                                "created_at": params[15],
                                "updated_at": params[16],
                            }
                        )
                    )
                return FakeCursor()

        conn = BlogConn()
        candidate = blog_news_crawler_service.NewsCandidate(
            keyword="python",
            course_names=["软件工程"],
            source_name="Example",
            title="Title",
            url="https://example.test/a",
            canonical_url="https://example.test/a",
            summary="Summary",
            published_at="2026-06-06T00:00:00",
            fetched_at="2026-06-06T00:01:00",
            score=12.5,
        )
        with patch.object(blog_news_crawler_service, "get_configured_db_engine", return_value="postgres"):
            stored, duplicate_count = blog_news_crawler_service._store_candidates(conn, 88, [candidate])

        self.assertEqual(0, duplicate_count)
        self.assertEqual(1, len(stored))
        self.assertTrue(any("ON CONFLICT DO NOTHING RETURNING *" in sql for sql, _ in conn.execute_calls))


if __name__ == "__main__":
    unittest.main()
