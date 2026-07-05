"""Tests for the idempotent 学年学期 unification migration.

Reproduces the production shape (two duplicate semester rows for one real term,
offerings whose text drifted from their semester_id, an orphan offering with a
non-canonical semester string and no semester_id) and asserts the migration
converges to the canonical, deduplicated, fully-linked state — and is a no-op on
a second run.
"""

import sqlite3
import unittest
from unittest import mock

from classroom_app.db import semester_unification_migration as mig


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE academic_semesters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER,
            school_code TEXT,
            school_name TEXT,
            name TEXT,
            start_date TEXT,
            end_date TEXT,
            week_count INTEGER DEFAULT 0,
            calendar_sync_status TEXT DEFAULT 'pending',
            updated_at TEXT
        );
        CREATE TABLE class_offerings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER,
            semester TEXT,
            semester_id INTEGER
        );
        CREATE TABLE academic_semester_calendar_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semester_id INTEGER,
            date TEXT,
            UNIQUE (semester_id, date)
        );
        CREATE TABLE teacher_calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semester_id INTEGER
        );
        """
    )
    return conn


def _seed_production_shape(conn: sqlite3.Connection) -> None:
    # 两条同一真实学期的重复行：id=1 有校历（保留），id=2 无校历（合并删除）。
    conn.execute(
        "INSERT INTO academic_semesters (id, teacher_id, school_code, school_name, name, start_date, end_date, week_count, calendar_sync_status) "
        "VALUES (1, 1, 'gxufl', '广外', '2025-2026学年第2学期', '2026-03-09', '2026-07-12', 18, 'synced')"
    )
    conn.execute(
        "INSERT INTO academic_semesters (id, teacher_id, school_code, school_name, name, start_date, end_date, week_count, calendar_sync_status) "
        "VALUES (2, 4, 'gxufl', '广外', '2025-2026第二学期', '2026-03-01', '2026-08-01', 23, 'pending')"
    )
    conn.execute("INSERT INTO academic_semester_calendar_days (semester_id, date) VALUES (1, '2026-04-05')")
    conn.execute("INSERT INTO academic_semester_calendar_days (semester_id, date) VALUES (2, '2026-04-05')")
    # 指向被删除行的日历事件，验证外键改指。
    conn.execute("INSERT INTO teacher_calendar_events (semester_id) VALUES (2)")
    # 课堂：文本漂移、指向重复行、以及孤儿。
    conn.execute("INSERT INTO class_offerings (id, teacher_id, semester, semester_id) VALUES (1, 1, '2025-2026学年第2学期', 1)")
    for oid in range(2, 8):
        conn.execute(
            "INSERT INTO class_offerings (id, teacher_id, semester, semester_id) VALUES (?, 1, '2025-2026第二学期', 1)",
            (oid,),
        )
    conn.execute("INSERT INTO class_offerings (id, teacher_id, semester, semester_id) VALUES (8, 1, '', NULL)")
    conn.execute("INSERT INTO class_offerings (id, teacher_id, semester, semester_id) VALUES (9, 1, '', NULL)")
    conn.execute("INSERT INTO class_offerings (id, teacher_id, semester, semester_id) VALUES (10, 1, '2025-2026-1', NULL)")
    conn.commit()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        _seed_production_shape(self.conn)
        self._patch = mock.patch.object(
            mig,
            "load_teacher_org_scope",
            return_value={"school_code": "gxufl", "school_name": "广外"},
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self.conn.close)

    def _semesters(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM academic_semesters ORDER BY id").fetchall()]

    def test_merges_duplicate_and_keeps_calendar_row(self):
        report = mig.unify_semesters(self.conn)
        self.conn.commit()
        rows = self._semesters()
        names = sorted(r["name"] for r in rows)
        # 第2学期只剩一条（保留 id=1，有校历），外加补建的第1学期。
        self.assertIn("2025-2026第二学期", names)
        self.assertIn("2025-2026第一学期", names)
        term2_rows = [r for r in rows if r["name"] == "2025-2026第二学期"]
        self.assertEqual(len(term2_rows), 1)
        self.assertEqual(term2_rows[0]["id"], 1)
        self.assertEqual(report["semesters_merged"], 1)
        self.assertEqual(report["names_normalized"], 1)  # id=1 "学年第2学期" → 规范

    def test_repoints_foreign_keys_and_drops_dup_calendar(self):
        mig.unify_semesters(self.conn)
        self.conn.commit()
        # teacher_calendar_events 从 2 改指 1
        event = self.conn.execute("SELECT semester_id FROM teacher_calendar_events").fetchone()
        self.assertEqual(int(event["semester_id"]), 1)
        # 重复行的日历日被删除（避免 UNIQUE 冲突），保留行日历仍在
        days = self.conn.execute("SELECT DISTINCT semester_id FROM academic_semester_calendar_days").fetchall()
        self.assertEqual([int(d["semester_id"]) for d in days], [1])

    def test_normalizes_offering_text_and_backfills_links(self):
        report = mig.unify_semesters(self.conn)
        self.conn.commit()
        offerings = {int(r["id"]): dict(r) for r in self.conn.execute("SELECT * FROM class_offerings").fetchall()}
        # 指向重复行的课堂文本全部规范
        for oid in range(1, 8):
            self.assertEqual(offerings[oid]["semester"], "2025-2026第二学期")
            self.assertEqual(int(offerings[oid]["semester_id"]), 1)
        # 未设学期课堂保持未设
        self.assertEqual(offerings[8]["semester"], "")
        self.assertIsNone(offerings[8]["semester_id"])
        # 孤儿课堂补建并绑定第1学期
        self.assertEqual(offerings[10]["semester"], "2025-2026第一学期")
        self.assertIsNotNone(offerings[10]["semester_id"])
        created = self.conn.execute(
            "SELECT name FROM academic_semesters WHERE id = ?", (offerings[10]["semester_id"],)
        ).fetchone()
        self.assertEqual(created["name"], "2025-2026第一学期")
        self.assertEqual(report["semesters_created"], 1)
        self.assertEqual(report["offering_links_backfilled"], 1)

    def test_idempotent_second_run_is_noop(self):
        mig.unify_semesters(self.conn)
        self.conn.commit()
        report2 = mig.unify_semesters(self.conn)
        self.conn.commit()
        self.assertEqual(
            report2,
            {
                "names_normalized": 0,
                "semesters_merged": 0,
                "offering_text_normalized": 0,
                "offering_links_backfilled": 0,
                "semesters_created": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
