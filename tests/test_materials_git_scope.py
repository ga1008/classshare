import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from classroom_app.db import connection
from classroom_app.services import materials_git_service as service


class MaterialRepositoryScopeTests(unittest.TestCase):
    SCOPE_FIELDS = (
        "scope_level", "owner_role", "owner_user_pk", "school_code",
        "school_name", "college", "department", "published_at",
    )

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.workspace = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="materials-git-scope-")))
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.stack.callback(self.conn.close)
        self.conn.executescript(
            """
            CREATE TABLE course_materials (
                id INTEGER PRIMARY KEY, teacher_id INTEGER, parent_id INTEGER,
                root_id INTEGER, material_path TEXT, name TEXT, node_type TEXT,
                mime_type TEXT, preview_type TEXT, ai_capability TEXT, file_ext TEXT,
                file_hash TEXT, file_size INTEGER, ai_parse_status TEXT, ai_parse_result_json TEXT,
                ai_optimize_status TEXT, ai_optimized_markdown TEXT,
                check_questions_json TEXT DEFAULT '', check_questions_status TEXT DEFAULT 'idle',
                check_questions_error TEXT DEFAULT '', check_questions_generated_at TEXT,
                scope_level TEXT DEFAULT 'private', owner_role TEXT DEFAULT '', owner_user_pk INTEGER,
                school_code TEXT DEFAULT '', school_name TEXT DEFAULT '', college TEXT DEFAULT '',
                department TEXT DEFAULT '', published_at TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE course_material_assignments (material_id INTEGER, class_offering_id INTEGER);
            INSERT INTO course_materials (
                id, teacher_id, root_id, material_path, name, node_type,
                scope_level, owner_role, owner_user_pk, school_code, school_name,
                college, department, published_at, created_at, updated_at
            ) VALUES (
                1, 9, 1, 'python-course', 'python-course', 'folder',
                'department', 'teacher', 9, 'gxufl', '广外',
                '数字科技学院', '网络工程系', '2026-09-01', '2026-09-01', '2026-09-01'
            );
            INSERT INTO course_material_assignments VALUES (1, 301);
            """
        )
        self.now = "2026-09-09T10:00:00"
        self.stack.enter_context(patch.object(connection, "get_configured_db_engine", return_value="sqlite"))
        self.stack.enter_context(patch.object(service, "_now_iso", side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(service, "_store_bytes_globally", side_effect=lambda data: (hashlib.sha256(data).hexdigest(), len(data))))
        self.stack.enter_context(patch.object(service, "_count_global_file_references", return_value=0))
        (self.workspace / "lesson_3").mkdir()
        self.lesson_path = self.workspace / "lesson_3/lesson_3.html"
        self.lesson_path.write_bytes(b"<html>lesson 3</html>")

    def root(self):
        return dict(self.conn.execute("SELECT * FROM course_materials WHERE id = 1").fetchone())

    def children(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM course_materials WHERE root_id = 1 AND id != 1 ORDER BY id")]

    def scope(self, row):
        return {field: row[field] for field in self.SCOPE_FIELDS}

    def sync(self, *, protected_paths=frozenset()):
        return service._sync_workspace_to_repository(self.conn, self.root(), self.workspace, protected_paths=protected_paths)

    def test_new_git_folder_and_file_inherit_root_metadata_without_new_assignments(self):
        root_scope = self.scope(self.root())

        summary, removable, changed = self.sync()

        self.assertEqual({"inserted": 2, "updated": 0, "deleted": 0, "unchanged": 0}, summary)
        self.assertEqual([], removable)
        self.assertEqual(2, len(changed))
        self.assertEqual({"folder", "file"}, {row["node_type"] for row in self.children()})
        self.assertTrue(all(self.scope(row) == root_scope for row in self.children()))
        self.assertEqual(root_scope, self.scope(self.root()))
        self.assertEqual([(1, 301)], [tuple(row) for row in self.conn.execute("SELECT * FROM course_material_assignments")])

    def test_unchanged_content_repairs_legacy_metadata_then_second_sync_is_idempotent(self):
        self.sync()
        hashes = {row["id"]: row["file_hash"] for row in self.children()}
        self.conn.execute(
            """
            UPDATE course_materials SET scope_level = 'private', owner_role = '', owner_user_pk = NULL,
                school_code = '', school_name = '', college = '', department = '', published_at = NULL
            WHERE root_id = 1 AND id != 1
            """
        )
        self.now = "2026-09-09T10:01:00"

        summary, _, changed = self.sync()

        self.assertEqual({"inserted": 0, "updated": 2, "deleted": 0, "unchanged": 0}, summary)
        self.assertEqual(2, len(changed))
        self.assertTrue(all(entry["status"] == "updated" for entry in changed))
        self.assertEqual(hashes, {row["id"]: row["file_hash"] for row in self.children()})
        self.assertTrue(all(self.scope(row) == self.scope(self.root()) for row in self.children()))
        before = [dict(row) for row in self.conn.execute("SELECT * FROM course_materials ORDER BY id")]
        changes = self.conn.total_changes
        self.now = "2026-09-09T10:02:00"

        repeated, _, repeated_entries = self.sync()

        self.assertEqual({"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 2}, repeated)
        self.assertEqual([], repeated_entries)
        self.assertEqual(changes, self.conn.total_changes)
        self.assertEqual(before, [dict(row) for row in self.conn.execute("SELECT * FROM course_materials ORDER BY id")])

    def test_content_and_metadata_change_count_once_per_existing_file(self):
        self.sync()
        file_row = next(row for row in self.children() if row["node_type"] == "file")
        self.conn.execute("UPDATE course_materials SET scope_level = 'private', department = '' WHERE id = ?", (file_row["id"],))
        self.lesson_path.write_bytes(b"<html>lesson 3 updated</html>")

        summary, _, changed = self.sync()

        self.assertEqual({"inserted": 0, "updated": 1, "deleted": 0, "unchanged": 1}, summary)
        self.assertEqual([file_row["id"]], [entry["id"] for entry in changed])
        updated = next(row for row in self.children() if row["id"] == file_row["id"])
        self.assertNotEqual(file_row["file_hash"], updated["file_hash"])
        self.assertEqual(self.scope(self.root()), self.scope(updated))

    def test_other_repository_with_identical_paths_is_untouched(self):
        self.conn.execute(
            """
            INSERT INTO course_materials (id, teacher_id, root_id, material_path, name, node_type,
                scope_level, college, department, updated_at)
            VALUES (90, 19, 90, 'python-course', 'python-course', 'folder',
                'private', '其他学院', '其他系', '2026-09-01')
            """
        )
        self.conn.execute(
            """
            INSERT INTO course_materials (id, teacher_id, parent_id, root_id, material_path, name,
                node_type, scope_level, department, file_hash, updated_at)
            VALUES (91, 19, 90, 90, 'python-course/lesson_3/lesson_3.html', 'lesson_3.html',
                'file', 'public', '外部目录', 'external-hash', '2026-09-02')
            """
        )
        before = [dict(row) for row in self.conn.execute("SELECT * FROM course_materials WHERE root_id = 90 ORDER BY id")]

        self.sync()

        self.assertEqual(before, [dict(row) for row in self.conn.execute("SELECT * FROM course_materials WHERE root_id = 90 ORDER BY id")])

    def test_null_root_values_replace_stale_values_and_remain_idempotent(self):
        self.sync()
        self.conn.execute("UPDATE course_materials SET owner_user_pk = NULL, published_at = NULL WHERE id = 1")

        summary, _, _ = self.sync()

        self.assertEqual(2, summary["updated"])
        self.assertTrue(all(row["owner_user_pk"] is None and row["published_at"] is None for row in self.children()))
        repeated, _, changed = self.sync()
        self.assertEqual(0, repeated["updated"])
        self.assertEqual([], changed)

    def test_protected_lessondoc_file_does_not_consume_another_unchanged_count(self):
        self.sync()
        file_row = next(row for row in self.children() if row["node_type"] == "file")
        self.conn.execute("UPDATE course_materials SET department = '' WHERE id = ?", (file_row["id"],))

        summary, _, changed = self.sync(protected_paths={"lesson_3/lesson_3.html"})

        self.assertEqual({"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 1}, summary)
        self.assertEqual([], changed)
        protected = next(row for row in self.children() if row["id"] == file_row["id"])
        self.assertEqual(self.scope(self.root()), self.scope(protected))


if __name__ == "__main__":
    unittest.main()
