import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db.schema_assignments import _ensure_optional_classroom_todo_scope
from classroom_app.services import todo_service


class TeacherPersonalTodoTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("CREATE TABLE class_offerings (id INTEGER PRIMARY KEY)")
        self.conn.execute("INSERT INTO class_offerings (id) VALUES (20)")
        self.conn.execute(
            """
            CREATE TABLE classroom_todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_offering_id INTEGER NOT NULL,
                owner_role TEXT NOT NULL,
                owner_user_pk INTEGER NOT NULL,
                title TEXT NOT NULL,
                notes TEXT DEFAULT '',
                start_at TEXT,
                due_at TEXT,
                completed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                deleted_at TEXT,
                metadata_json TEXT DEFAULT '{}',
                FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO classroom_todos (class_offering_id, owner_role, owner_user_pk, title)
            VALUES (20, 'teacher', 3, 'Existing classroom todo')
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_migration_preserves_data_and_detaches_when_classroom_is_deleted(self):
        _ensure_optional_classroom_todo_scope(self.conn)

        class_column = next(
            row for row in self.conn.execute("PRAGMA table_info(classroom_todos)")
            if row["name"] == "class_offering_id"
        )
        foreign_key = next(
            row for row in self.conn.execute("PRAGMA foreign_key_list(classroom_todos)")
            if row["from"] == "class_offering_id"
        )
        self.assertEqual(0, class_column["notnull"])
        self.assertEqual("SET NULL", foreign_key["on_delete"])
        self.assertEqual("Existing classroom todo", self.conn.execute(
            "SELECT title FROM classroom_todos WHERE id = 1"
        ).fetchone()["title"])

        self.conn.execute("DELETE FROM class_offerings WHERE id = 20")
        self.assertIsNone(self.conn.execute(
            "SELECT class_offering_id FROM classroom_todos WHERE id = 1"
        ).fetchone()["class_offering_id"])

    def test_teacher_can_create_move_and_detach_a_personal_todo(self):
        _ensure_optional_classroom_todo_scope(self.conn)
        teacher = {"id": 3, "role": "teacher", "name": "Teacher"}

        with patch.object(todo_service, "cancel_tasks_by_dedupe", return_value=0):
            created = todo_service.create_manual_todo(
                self.conn,
                class_offering_id=None,
                user=teacher,
                payload={"title": "Private planning"},
            )
            todo_id = created["id"]
            self.assertIsNone(self.conn.execute(
                "SELECT class_offering_id FROM classroom_todos WHERE id = ?", (todo_id,)
            ).fetchone()["class_offering_id"])

            self.conn.execute("INSERT INTO class_offerings (id) VALUES (21)")
            todo_service.update_manual_todo(
                self.conn,
                class_offering_id=None,
                todo_id=todo_id,
                user=teacher,
                payload={"class_offering_id": 21},
                enforce_classroom_scope=False,
            )
            self.assertEqual(21, self.conn.execute(
                "SELECT class_offering_id FROM classroom_todos WHERE id = ?", (todo_id,)
            ).fetchone()["class_offering_id"])

            todo_service.update_manual_todo(
                self.conn,
                class_offering_id=None,
                todo_id=todo_id,
                user=teacher,
                payload={"class_offering_id": None},
                enforce_classroom_scope=False,
            )
            self.assertIsNone(self.conn.execute(
                "SELECT class_offering_id FROM classroom_todos WHERE id = ?", (todo_id,)
            ).fetchone()["class_offering_id"])

    def test_account_listing_returns_private_and_accessible_classroom_todos(self):
        _ensure_optional_classroom_todo_scope(self.conn)
        self.conn.execute(
            """
            INSERT INTO classroom_todos (class_offering_id, owner_role, owner_user_pk, title)
            VALUES (NULL, 'teacher', 3, 'Private todo')
            """
        )
        self.conn.execute("INSERT INTO class_offerings (id) VALUES (21)")
        self.conn.execute(
            """
            INSERT INTO classroom_todos (class_offering_id, owner_role, owner_user_pk, title)
            VALUES (21, 'teacher', 3, 'Former classroom todo')
            """
        )

        items = todo_service.list_manual_todo_items(
            self.conn,
            class_offering_ids=[20],
            user={"id": 3, "role": "teacher"},
            include_unscoped=True,
            account_wide=True,
        )

        self.assertEqual(
            {"Existing classroom todo", "Private todo", "Former classroom todo"},
            {item["title"] for item in items},
        )
        personal = next(item for item in items if item["title"] == "Private todo")
        self.assertIsNone(personal["class_offering_id"])
        self.assertEqual("私人待办", personal["subtitle"])


if __name__ == "__main__":
    unittest.main()
