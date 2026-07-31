import json
import sqlite3
import unittest
from datetime import date
from unittest.mock import patch

import httpx

from classroom_app.db.schema_academic_evaluations import ensure_academic_evaluation_schema
from classroom_app.services.academic_evaluation_sync_service import (
    _acquire_sync_lease,
    _fetch_evaluations,
    _finish_sync_lease,
    _upsert_evaluation,
    build_teacher_academic_evaluation_dashboard_context,
    get_teacher_classroom_academic_evaluation_detail,
)


class _RecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        return self


def _memory_database():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE academic_semesters (
            id INTEGER PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            name TEXT,
            start_date TEXT,
            end_date TEXT
        );
        CREATE TABLE teacher_academic_system_credentials (
            id INTEGER PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            school_code TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE class_offerings (
            id INTEGER PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            semester TEXT,
            class_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL
        );
        """
    )
    conn.executemany("INSERT INTO teachers(id, name) VALUES (?, ?)", [(1, "甲教师"), (2, "乙教师")])
    conn.execute(
        "INSERT INTO academic_semesters(id, teacher_id, name, start_date, end_date) VALUES (10, 1, '2025-2026-2', '2026-02-01', '2026-07-31')"
    )
    conn.execute("INSERT INTO teacher_academic_system_credentials VALUES (1, 1, 'gxufl', 1)")
    conn.execute("INSERT INTO courses VALUES (20, '计算机网络')")
    conn.execute("INSERT INTO classes VALUES (30, '网络工程 1 班')")
    conn.execute("INSERT INTO class_offerings VALUES (40, 1, 10, '2025-2026-2', 30, 20)")
    ensure_academic_evaluation_schema(conn)
    conn.commit()
    return conn


class AcademicEvaluationSchemaTests(unittest.TestCase):
    def test_postgres_schema_does_not_emit_sqlite_autoincrement(self):
        conn = _RecordingConnection()
        with patch(
            "classroom_app.db.schema_academic_evaluations.get_configured_db_engine",
            return_value="postgres",
        ):
            ensure_academic_evaluation_schema(conn)
        ddl = "\n".join(statement for statement, _params in conn.statements)
        self.assertIn("SERIAL PRIMARY KEY", ddl)
        self.assertNotIn("AUTOINCREMENT", ddl)


class AcademicEvaluationSourceContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_endpoint_shapes_are_parsed_serially(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            path = request.url.path
            if path.endswith("cxXspjjstjIndex.html"):
                return httpx.Response(200, text="<html>教学质量评价查询</html>")
            if path.endswith("cxXysfkf.html"):
                return httpx.Response(200, json=1)
            if path.endswith("cxKcxxList.html"):
                return httpx.Response(200, json=[{"kch_id": "COURSE-1", "kcmc": "计算机网络"}])
            if path.endswith("cxKcxsxxList.html"):
                return httpx.Response(200, json=[{"xsdm": "01", "xsmc": "理论"}])
            if path.endswith("cxXspjjsDjXmList.html"):
                return httpx.Response(200, json=[{"PFDJDMXMB_ID": "A-ID", "XMMC": "A"}])
            if path.endswith("cxXspjjsxxMap.html"):
                return httpx.Response(
                    200,
                    json={"kcpjf": "98.1759", "jqpjf": "98.1394", "cprs": "73", "jfrs": "59", "xqumc": "五合校区"},
                )
            if path.endswith("cxXspjjsxxList.html"):
                return httpx.Response(
                    200,
                    json={"items": [{"hh": 1, "zbxmmc": "课程目标清晰", "dxjz": "99.24", "myd": "99.237", "qzz": ".34", "xsmc": "理论", "rs_0": 56}]},
                )
            if path.endswith("cxXspy.html"):
                return httpx.Response(200, json={"items": [{"xh": 1, "py": "讲解清晰，课堂互动自然。", "row_id": "COMMENT-1"}]})
            return httpx.Response(404)

        async with httpx.AsyncClient(
            base_url="https://jwxt.gxufl.com",
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        ) as client:
            with patch(
                "classroom_app.services.academic_evaluation_sync_service.REQUEST_DELAY_MIN_SECONDS",
                0,
            ), patch(
                "classroom_app.services.academic_evaluation_sync_service.REQUEST_DELAY_MAX_SECONDS",
                0,
            ):
                evaluations, warnings, source_summary, course_count = await _fetch_evaluations(
                    client,
                    xnm="2025",
                    xqm="12",
                )

        self.assertEqual(warnings, [])
        self.assertEqual(course_count, 1)
        self.assertEqual(len(evaluations), 1)
        self.assertAlmostEqual(evaluations[0]["course_score"], 98.1759)
        self.assertEqual(evaluations[0]["response_count"], 73)
        self.assertEqual(evaluations[0]["enrolled_count"], 0)
        self.assertEqual(evaluations[0]["valid_response_count"], 59)
        self.assertEqual(evaluations[0]["metrics"][0]["grade_counts"], {"A": 56})
        self.assertEqual(evaluations[0]["comments"][0]["comment_text"], "讲解清晰，课堂互动自然。")
        self.assertEqual(len(source_summary), len(calls))
        self.assertEqual([path for _method, path in calls].count("/jxpjtj/jxpjtj_cxXspjjsxxMap.html"), 1)


class AcademicEvaluationLocalReadTests(unittest.TestCase):
    def setUp(self):
        self.conn = _memory_database()
        self.evaluation_id = _upsert_evaluation(
            self.conn,
            teacher_id=1,
            semester_id=10,
            academic_year="2025-2026",
            academic_term="2",
            item={
                "source_course_key": "COURSE-1",
                "course_name": "计算机网络",
                "hour_type_code": "01",
                "hour_type_name": "理论",
                "evaluation_target_code": "01",
                "campus_name": "五合校区",
                "course_score": 98.1759,
                "teacher_weighted_score": 98.1394,
                "enrolled_count": 73,
                "response_count": 73,
                "valid_response_count": 59,
                "institution_rank": 19,
                "metrics": [{
                    "source_metric_key": "METRIC-1",
                    "sequence_no": 1,
                    "metric_name": "课程目标清晰",
                    "mean_score": 99.24,
                    "satisfaction_score": 99.237,
                    "weight_value": .34,
                    "hour_type_name": "理论",
                    "grade_counts": {"A": 56, "B": 3},
                }],
                "comments": [{
                    "source_comment_key": "COMMENT-1",
                    "sequence_no": 1,
                    "comment_text": "讲解清晰，课堂互动自然。",
                    "comment_hash": "hash",
                }],
            },
            source_summary=[{"path": "/jxpjtj/example", "status_code": 200}],
            synced_at="2026-07-31T10:00:00+08:00",
        )
        self.conn.execute(
            """
            UPDATE teacher_academic_course_evaluations
            SET ai_summary = ?, ai_keywords_json = ?, ai_keyword_status = 'completed'
            WHERE id = ?
            """,
            (
                "学生普遍认可讲解清晰与课堂互动。",
                json.dumps([{"label": "讲解清晰", "sentiment": "positive", "count": 8, "confidence": .94}], ensure_ascii=False),
                self.evaluation_id,
            ),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_dashboard_matches_course_and_detail_is_teacher_scoped(self):
        overviews, sync = build_teacher_academic_evaluation_dashboard_context(
            self.conn,
            teacher_id=1,
            offerings=[{"id": 40, "semester_id": 10, "course_name": "计算机 网络"}],
        )
        self.assertAlmostEqual(overviews[40]["score"], 98.18)
        self.assertEqual(overviews[40]["keywords"][0]["label"], "讲解清晰")
        self.assertTrue(sync["has_credential"])

        detail = get_teacher_classroom_academic_evaluation_detail(
            self.conn,
            teacher_id=1,
            class_offering_id=40,
        )
        self.assertTrue(detail["available"])
        self.assertEqual(detail["sources"][0]["metrics"][0]["grade_counts"]["A"], 56)
        self.assertEqual(detail["sources"][0]["comments"][0]["text"], "讲解清晰，课堂互动自然。")
        self.assertIsNone(
            get_teacher_classroom_academic_evaluation_detail(
                self.conn,
                teacher_id=2,
                class_offering_id=40,
            )
        )

    def test_database_lease_blocks_concurrent_and_early_manual_refresh(self):
        status, state = _acquire_sync_lease(
            self.conn,
            teacher_id=1,
            semester_id=10,
            academic_year="2025-2026",
            academic_term="2",
            force=False,
        )
        self.conn.commit()
        self.assertEqual(status, "acquired")

        second_status, _second_state = _acquire_sync_lease(
            self.conn,
            teacher_id=1,
            semester_id=10,
            academic_year="2025-2026",
            academic_term="2",
            force=False,
        )
        self.assertEqual(second_status, "running")

        _finish_sync_lease(
            self.conn,
            teacher_id=1,
            academic_year="2025-2026",
            academic_term="2",
            lease_token=state["lease_token"],
            status="success",
            source_course_count=1,
            synced_evaluation_count=1,
        )
        self.conn.commit()
        forced_status, _forced_state = _acquire_sync_lease(
            self.conn,
            teacher_id=1,
            semester_id=10,
            academic_year="2025-2026",
            academic_term="2",
            force=True,
        )
        self.assertEqual(forced_status, "cooldown")


if __name__ == "__main__":
    unittest.main()
