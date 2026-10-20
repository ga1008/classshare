"""Employee identity invariants on synthetic SQLite databases only."""
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from classroom_app.db.schema_foundation import ensure_foundation_schema
from classroom_app.db.migrations import _ensure_organization_catalog_schema, _ensure_teacher_organization_memberships_schema
from classroom_app.db.schema_teacher_identity import ensure_teacher_identity_schema
from classroom_app.services import teacher_account_service as accounts
from classroom_app.services.teacher_identity_service import (
    normalize_employee_number, teacher_login_candidates, update_primary_employee_number, TeacherIdentityConflict,
)
from classroom_app.services.profile_service import update_basic_profile


class TeacherIdentityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        ensure_foundation_schema(self.conn)
        _ensure_organization_catalog_schema(self.conn)
        _ensure_teacher_organization_memberships_schema(self.conn)
        ensure_teacher_identity_schema(self.conn)
        self.conn.execute("ALTER TABLE teachers ADD COLUMN identity_category TEXT DEFAULT ''")
        for number in (1, 2, 3):
            self.conn.execute(
                """INSERT INTO teachers(id, name, email, hashed_password, school_code, school_name, college, department)
                   VALUES (?, ?, ?, 'synthetic', 'a', 'School A', 'College A', 'Department A')""",
                (number, f"Teacher {number}", f"{number}@example.test"),
            )
        self.conn.commit()

    def membership(self, teacher_id, number, **kwargs):
        return accounts.upsert_teacher_membership(
            self.conn, teacher_id=teacher_id, employee_number=number, **kwargs,
        )

    def test_normalization_preserves_zeros_and_rejects_invalid_numbers(self):
        self.assertEqual("0001-AB", normalize_employee_number(" ０００１-ab "))
        for value in ("a b", "abc@example.test", "a" * 65, "\x00abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_employee_number(value)
        with self.assertRaises(ValueError):
            normalize_employee_number("", required=True)

    def test_school_identity_uniqueness_in_service_and_database(self):
        self.membership(1, "0001")
        with self.assertRaisesRegex(ValueError, "其他教师"):
            self.membership(2, "0001")
        self.membership(2, "0002")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE teacher_organization_memberships SET employee_number='0001' WHERE teacher_id=2")
        # A different school's identical employee number is a different identity.
        self.membership(2, "0001", school_code="b", school_name="School B")
        self.assertEqual(2, len(teacher_login_candidates(self.conn, "0001")))
        self.assertEqual([1], [r["id"] for r in teacher_login_candidates(self.conn, "0001", school_name="School A")])
        self.assertEqual([2], [r["id"] for r in teacher_login_candidates(self.conn, "0001", school_code="b")])
        self.assertEqual([], teacher_login_candidates(self.conn, "1"))

    def test_default_school_switch_preserves_independent_employee_numbers(self):
        first = self.membership(1, "0001", is_primary=True)
        second = self.membership(1, "B-009", school_code="b", school_name="School B")
        self.assertEqual("0001", accounts.get_teacher_account(self.conn, 1)["employee_number"])
        accounts.set_teacher_primary_membership(self.conn, teacher_id=1, membership_id=second["id"])
        self.assertEqual("B-009", accounts.get_teacher_account(self.conn, 1)["employee_number"])
        update_primary_employee_number(self.conn, teacher_id=1, employee_number="B-010")
        rows = {r["school_code"]: r["employee_number"] for r in accounts.list_teacher_memberships(self.conn, 1)}
        self.assertEqual({"a": "0001", "b": "B-010"}, rows)
        accounts.deactivate_teacher_membership(self.conn, teacher_id=1, membership_id=second["id"])
        self.assertEqual("0001", accounts.get_teacher_account(self.conn, 1)["employee_number"])
        self.assertEqual([], teacher_login_candidates(self.conn, "B-010"))
        with self.assertRaisesRegex(ValueError, "其他教师"):
            self.membership(2, "B-010", school_code="b", school_name="School B")

    def test_membership_edit_without_number_preserves_it_and_never_copies_to_other_school(self):
        self.membership(1, "0007")
        accounts.upsert_teacher_membership(self.conn, teacher_id=1, department="Changed department")
        self.assertEqual("0007", accounts.get_teacher_account(self.conn, 1)["employee_number"])
        new = accounts.upsert_teacher_membership(self.conn, teacher_id=1, school_code="b", school_name="School B")
        self.assertEqual("", new["employee_number"])

    def test_email_login_remains_available_for_legacy_accounts(self):
        self.assertEqual([1], [r["id"] for r in teacher_login_candidates(self.conn, " 1@EXAMPLE.TEST ")])
        self.conn.execute("UPDATE teachers SET is_active=0 WHERE id=1")
        self.assertEqual([], teacher_login_candidates(self.conn, "1@example.test"))

    def test_profile_requires_employee_number_and_cannot_move_school(self):
        self.membership(1, "0001")
        with self.assertRaisesRegex(ValueError, "职工号"):
            update_basic_profile(self.conn, {"role": "teacher", "id": 1}, {"email": "1@example.test"})
        profile = update_basic_profile(
            self.conn, {"role": "teacher", "id": 1},
            {"email": "1@example.test", "employee_number": "0002", "expected_school_code": "a",
             "school_code": "b", "school_name": "School B"},
        )
        self.assertEqual(("0002", "a", "School A"), (profile["employee_number"], profile["school_code"], profile["school_name"]))

    def test_stale_profile_does_not_assign_previous_school_employee_number_to_new_default(self):
        self.membership(1, "0001", is_primary=True)
        self.membership(1, "B-001", school_code="b", school_name="School B", is_primary=True)
        with self.assertRaises(TeacherIdentityConflict):
            update_basic_profile(
                self.conn, {"role": "teacher", "id": 1},
                {"email": "1@example.test", "employee_number": "0002", "expected_school_code": "a"},
            )
        self.assertEqual("B-001", accounts.get_teacher_account(self.conn, 1)["employee_number"])

    def test_profile_rejects_other_teachers_email_before_changing_employee_identity(self):
        self.membership(1, "0001")
        self.conn.execute("UPDATE teachers SET email='Other@Example.TEST' WHERE id=2")
        with self.assertRaisesRegex(ValueError, "邮箱"):
            update_basic_profile(
                self.conn, {"role": "teacher", "id": 1},
                {"email": "other@example.test", "employee_number": "0002", "expected_school_code": "a"},
            )
        self.assertEqual("0001", accounts.get_teacher_account(self.conn, 1)["employee_number"])

    def test_creation_requires_number_and_propagates_to_membership(self):
        with self.assertRaisesRegex(ValueError, "职工号"):
            accounts.create_teacher_account(
                self.conn, actor_teacher_id=1, name="New", email="new@example.test", password="password123",
            )
        with patch.object(accounts, "get_password_hash", return_value="synthetic"):
            teacher = accounts.create_teacher_account(
                self.conn, actor_teacher_id=1, name="New", email="new@example.test", password="password123",
                school_code="a", school_name="School A", department="Department A", employee_number="0008",
            )
        self.assertEqual("0008", teacher["employee_number"])
        self.assertEqual("0008", teacher["active_memberships"][0]["employee_number"])

    def test_additive_migration_is_idempotent_and_keeps_legacy_values(self):
        self.membership(1, "0001")
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM teachers ORDER BY id")]
        ensure_teacher_identity_schema(self.conn)
        ensure_teacher_identity_schema(self.conn)
        self.assertEqual(before, [tuple(r) for r in self.conn.execute("SELECT * FROM teachers ORDER BY id")])

    def test_concurrent_school_number_claims_have_one_winner(self):
        self.conn.commit()
        with tempfile.TemporaryDirectory(prefix="teacher-identities-") as folder:
            path = Path(folder) / "synthetic.sqlite"
            destination = sqlite3.connect(path)
            self.conn.backup(destination)
            destination.close()

            def claim(teacher_id):
                conn = sqlite3.connect(path, timeout=10)
                conn.row_factory = sqlite3.Row
                try:
                    accounts.upsert_teacher_membership(conn, teacher_id=teacher_id, employee_number="0009")
                    conn.commit()
                    return "saved"
                except ValueError:
                    conn.rollback()
                    return "conflict"
                finally:
                    conn.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(claim, (1, 2)))
            self.assertEqual(["conflict", "saved"], sorted(outcomes))
