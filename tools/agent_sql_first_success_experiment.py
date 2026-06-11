from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.db import schema_gongwen
from classroom_app.db.schema_assignments import ensure_assignment_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db.schema_foundation import ensure_foundation_schema
from classroom_app.db.schema_materials_integrations import ensure_materials_integrations_schema
from classroom_app.services.agent_bridge_service import run_readonly_query


TARGET_SUCCESS_RATE = 0.85


@dataclass(frozen=True)
class SqlFirstSuccessCase:
    id: str
    teacher_question: str
    sql: str
    params: dict[str, Any]
    expected_columns: tuple[str, ...]
    expected_min_rows: int = 1
    expected_any_row: dict[str, Any] | None = None


def _row_matches(row: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(row.get(column) == value for column, value in expected.items())


def _insert_fixture_data(conn: sqlite3.Connection) -> None:
    now = datetime.now()
    future_exam = (now + timedelta(days=2)).isoformat(timespec="seconds")
    session_day = (now + timedelta(days=3)).date().isoformat()
    other_session_day = (now + timedelta(days=4)).date().isoformat()
    conn.execute(
        """
        INSERT INTO teachers (id, name, email, hashed_password, school_code, school_name, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (7, "Teacher Seven", "teacher7@example.test", "hashed", "gxufl", "GXUFL", 1),
    )
    conn.execute(
        """
        INSERT INTO teachers (id, name, email, hashed_password, school_code, school_name, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (8, "Other Teacher", "teacher8@example.test", "hashed", "gxufl", "GXUFL", 1),
    )
    conn.executemany(
        "INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)",
        [(1, "Class 1", 7), (2, "Other Class", 8)],
    )
    conn.executemany(
        "INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)",
        [(1, "Integrated English", 7), (2, "Other Course", 8)],
    )
    conn.executemany(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
        [(1, 1, 1, 7), (2, 2, 2, 8)],
    )
    conn.executemany(
        "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
        [
            (1, "S001", "Alice", 1),
            (2, "S002", "Bob", 1),
            (3, "S003", "Carol", 1),
            (4, "S004", "Mallory", 2),
        ],
    )
    conn.executemany(
        """
        INSERT INTO assignments (id, course_id, class_offering_id, title, requirements_md, status, due_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1,
                1,
                1,
                "Policy reflection homework",
                "Write about policy summary.",
                "published",
                "2026-01-08T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
            (
                2,
                1,
                1,
                "Speaking lab preparation",
                "Prepare a two-minute speech.",
                "published",
                "2026-01-12T00:00:00+00:00",
                "2026-01-03T00:00:00+00:00",
            ),
            (
                3,
                2,
                2,
                "Other teacher policy homework",
                "This row must not be needed by teacher 7 cases.",
                "published",
                "2026-01-08T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        ],
    )
    conn.executemany(
        """
        INSERT INTO submissions (id, assignment_id, student_pk_id, student_name, submitted_at, score)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "1", 1, "Alice", "2026-01-02T00:00:00+00:00", 95),
            (2, "1", 2, "Bob", "2026-01-02T00:00:00+00:00", 58),
            (3, "2", 1, "Alice", "2026-01-04T00:00:00+00:00", 82),
            (4, "3", 4, "Mallory", "2026-01-02T00:00:00+00:00", 99),
        ],
    )
    conn.executemany(
        """
        INSERT INTO course_materials (id, teacher_id, material_path, name, node_type, preview_type, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1,
                7,
                "/materials/course-1/policy-intro.md",
                "Policy intro",
                "file",
                "markdown",
                "2026-01-03T00:00:00+00:00",
            ),
            (
                2,
                8,
                "/materials/other/policy.md",
                "Other policy material",
                "file",
                "markdown",
                "2026-01-03T00:00:00+00:00",
            ),
        ],
    )
    conn.executemany(
        """
        INSERT INTO class_offering_sessions (
            id, class_offering_id, order_index, title, session_date, weekday, learning_material_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 1, 1, "Policy reading", session_day, 1, 1),
            (2, 1, 2, "Speaking lab", other_session_day, 2, None),
            (3, 2, 1, "Other session", other_session_day, 2, 2),
        ],
    )
    conn.execute(
        """
        INSERT INTO teacher_calendar_events (
            teacher_id, source_type, source_key, title, starts_at, location, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (7, "exam", "exam:policy-oral", "Policy oral exam", future_exam, "A101", "active"),
    )
    conn.execute(
        """
        INSERT INTO gongwen_documents (
            id, remote_id, attr_school_code, attr_level, openness,
            title, sn, author, parsed_summary, parsed_text, publish_time, parsed_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "remote-1",
            "gxufl",
            "school",
            "school",
            "Policy Notice",
            "GW-1",
            "Academic Office",
            "policy summary",
            "policy body",
            "2026-01-05",
            "done",
        ),
    )
    conn.commit()


def open_experiment_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema_gongwen._SCHEMA_READY = False
    ensure_foundation_schema(conn)
    ensure_assignment_schema(conn)
    ensure_classroom_activity_schema(conn)
    ensure_materials_integrations_schema(conn)
    with _patched_gongwen_engine():
        schema_gongwen.ensure_gongwen_schema(conn)
    _insert_fixture_data(conn)
    return conn


class _patched_gongwen_engine:
    def __enter__(self):
        from unittest.mock import patch

        self._patch = patch.object(schema_gongwen, "get_configured_db_engine", return_value="sqlite")
        return self._patch.__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        return self._patch.__exit__(exc_type, exc_value, traceback)


def experiment_cases() -> tuple[SqlFirstSuccessCase, ...]:
    now = datetime.now()
    return (
        SqlFirstSuccessCase(
            id="teacher_classrooms",
            teacher_question="List my current classes.",
            sql=(
                "SELECT co.id AS class_offering_id, c.name AS course_name, cl.name AS class_name "
                "FROM class_offerings co JOIN courses c ON c.id = co.course_id "
                "JOIN classes cl ON cl.id = co.class_id "
                "WHERE co.teacher_id = :teacher_id ORDER BY co.id DESC LIMIT 20"
            ),
            params={"teacher_id": 7},
            expected_columns=("class_offering_id", "course_name", "class_name"),
            expected_any_row={"course_name": "Integrated English", "class_name": "Class 1"},
        ),
        SqlFirstSuccessCase(
            id="class_roster",
            teacher_question="Who is in Class 1?",
            sql=(
                "SELECT st.id, st.name, st.student_id_number FROM students st "
                "JOIN class_offerings co ON co.class_id = st.class_id "
                "WHERE co.id = :class_offering_id ORDER BY st.name LIMIT 100"
            ),
            params={"class_offering_id": 1},
            expected_columns=("id", "name", "student_id_number"),
            expected_min_rows=3,
            expected_any_row={"name": "Carol"},
        ),
        SqlFirstSuccessCase(
            id="assignment_submission_counts",
            teacher_question="How many submissions does each recent assignment have?",
            sql=(
                "SELECT a.id, a.title, a.status, COUNT(s.id) AS submission_count "
                "FROM assignments a LEFT JOIN submissions s ON s.assignment_id = a.id "
                "WHERE a.class_offering_id = :class_offering_id "
                "GROUP BY a.id ORDER BY a.created_at DESC LIMIT 10"
            ),
            params={"class_offering_id": 1},
            expected_columns=("id", "title", "status", "submission_count"),
            expected_min_rows=2,
            expected_any_row={"title": "Policy reflection homework", "submission_count": 2},
        ),
        SqlFirstSuccessCase(
            id="missing_assignment_students",
            teacher_question="Who has not submitted the policy homework?",
            sql=(
                "SELECT st.id, st.name FROM students st "
                "JOIN class_offerings co ON co.class_id = st.class_id "
                "WHERE co.id = :class_offering_id "
                "AND st.id NOT IN (SELECT s.student_pk_id FROM submissions s WHERE s.assignment_id = :assignment_id) "
                "ORDER BY st.name LIMIT 100"
            ),
            params={"class_offering_id": 1, "assignment_id": 1},
            expected_columns=("id", "name"),
            expected_any_row={"name": "Carol"},
        ),
        SqlFirstSuccessCase(
            id="low_score_students",
            teacher_question="Which students scored below 60?",
            sql=(
                "SELECT a.title AS assignment_title, s.student_name, s.score "
                "FROM submissions s JOIN assignments a ON CAST(a.id AS TEXT) = CAST(s.assignment_id AS TEXT) "
                "LEFT JOIN class_offerings co ON co.id = a.class_offering_id "
                "JOIN courses c ON c.id = a.course_id "
                "WHERE (co.teacher_id = :teacher_id OR c.created_by_teacher_id = :teacher_id) "
                "AND s.score IS NOT NULL AND s.score < :threshold "
                "ORDER BY a.created_at DESC, s.score ASC LIMIT 50"
            ),
            params={"teacher_id": 7, "threshold": 60},
            expected_columns=("assignment_title", "student_name", "score"),
            expected_any_row={"student_name": "Bob", "score": 58},
        ),
        SqlFirstSuccessCase(
            id="upcoming_teacher_schedule",
            teacher_question="What is on my upcoming schedule?",
            sql=(
                "SELECT title, starts_at, location, source_type FROM teacher_calendar_events "
                "WHERE teacher_id = :teacher_id AND status = 'active' AND deleted_at IS NULL "
                "AND starts_at >= :start_at ORDER BY starts_at ASC LIMIT 20"
            ),
            params={"teacher_id": 7, "start_at": now.isoformat(timespec="seconds")},
            expected_columns=("title", "starts_at", "location", "source_type"),
            expected_any_row={"title": "Policy oral exam", "location": "A101"},
        ),
        SqlFirstSuccessCase(
            id="class_session_plan",
            teacher_question="Show the teaching sessions and linked material.",
            sql=(
                "SELECT s.order_index, s.title, s.session_date, lm.name AS learning_material_name "
                "FROM class_offering_sessions s LEFT JOIN course_materials lm ON lm.id = s.learning_material_id "
                "WHERE s.class_offering_id = :class_offering_id ORDER BY s.order_index LIMIT 60"
            ),
            params={"class_offering_id": 1},
            expected_columns=("order_index", "title", "session_date", "learning_material_name"),
            expected_min_rows=2,
            expected_any_row={"title": "Policy reading", "learning_material_name": "Policy intro"},
        ),
        SqlFirstSuccessCase(
            id="recent_materials",
            teacher_question="What recent materials have I uploaded?",
            sql=(
                "SELECT id, name, material_path, preview_type, updated_at FROM course_materials "
                "WHERE teacher_id = :teacher_id AND node_type = 'file' ORDER BY updated_at DESC LIMIT 20"
            ),
            params={"teacher_id": 7},
            expected_columns=("id", "name", "material_path", "preview_type", "updated_at"),
            expected_any_row={"name": "Policy intro"},
        ),
        SqlFirstSuccessCase(
            id="gongwen_keyword",
            teacher_question="Find policy-related school notices.",
            sql=(
                "SELECT id, title, sn, author, publish_time FROM gongwen_documents "
                "WHERE lower(COALESCE(title,'') || COALESCE(parsed_summary,'') || COALESCE(parsed_text,'')) "
                "LIKE :pattern ORDER BY publish_time DESC LIMIT 20"
            ),
            params={"pattern": "%policy%"},
            expected_columns=("id", "title", "sn", "author", "publish_time"),
            expected_any_row={"title": "Policy Notice"},
        ),
        SqlFirstSuccessCase(
            id="assignment_keyword_search",
            teacher_question="Find my assignments related to policy.",
            sql=(
                "SELECT a.id, a.title, c.name AS course_name FROM assignments a "
                "JOIN courses c ON c.id = a.course_id "
                "LEFT JOIN class_offerings co ON co.id = a.class_offering_id "
                "WHERE (co.teacher_id = :teacher_id OR c.created_by_teacher_id = :teacher_id) "
                "AND (lower(a.title) LIKE :pattern OR lower(COALESCE(a.requirements_md, '')) LIKE :pattern) "
                "ORDER BY a.created_at DESC, a.id DESC LIMIT 20"
            ),
            params={"teacher_id": 7, "pattern": "%policy%"},
            expected_columns=("id", "title", "course_name"),
            expected_any_row={"title": "Policy reflection homework", "course_name": "Integrated English"},
        ),
    )


def run_sql_first_success_experiment(
    conn: sqlite3.Connection | None = None,
    cases: Sequence[SqlFirstSuccessCase] | None = None,
) -> dict[str, Any]:
    own_conn = conn is None
    conn = conn or open_experiment_conn()
    cases = tuple(cases or experiment_cases())
    results: list[dict[str, Any]] = []
    try:
        for case in cases:
            case_result: dict[str, Any] = {
                "id": case.id,
                "teacher_question": case.teacher_question,
                "success": False,
                "row_count": 0,
                "error": "",
                "case": asdict(case),
            }
            try:
                query_result = run_readonly_query(conn, case.sql, limit=50, params=case.params)
                rows = query_result.get("rows") or []
                columns = set(query_result.get("columns") or [])
                missing_columns = [column for column in case.expected_columns if column not in columns]
                if missing_columns:
                    raise AssertionError(f"missing columns: {', '.join(missing_columns)}")
                if int(query_result.get("row_count") or 0) < int(case.expected_min_rows):
                    raise AssertionError(
                        f"expected at least {case.expected_min_rows} rows, got {query_result.get('row_count')}"
                    )
                if case.expected_any_row and not any(_row_matches(row, case.expected_any_row) for row in rows):
                    raise AssertionError(f"no row matched {case.expected_any_row}")
                case_result["success"] = True
                case_result["row_count"] = int(query_result.get("row_count") or 0)
            except Exception as exc:  # noqa: BLE001 - experiment reports every first-attempt failure.
                case_result["error"] = str(exc)
            results.append(case_result)
    finally:
        if own_conn:
            conn.close()
            schema_gongwen._SCHEMA_READY = False

    success_count = sum(1 for result in results if result["success"])
    case_count = len(results)
    success_rate = (success_count / case_count) if case_count else 0.0
    return {
        "name": "agent_sql_first_success_experiment",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_success_rate": TARGET_SUCCESS_RATE,
        "case_count": case_count,
        "success_count": success_count,
        "success_rate": success_rate,
        "passed": success_rate >= TARGET_SUCCESS_RATE,
        "results": results,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agent bridge SQL first-success experiment.")
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    parser.add_argument("--min-success-rate", type=float, default=TARGET_SUCCESS_RATE)
    args = parser.parse_args(argv)

    report = run_sql_first_success_experiment()
    report["target_success_rate"] = float(args.min_success_rate)
    report["passed"] = float(report["success_rate"]) >= float(args.min_success_rate)
    if args.output:
        _write_report(args.output, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            "Agent SQL first-success: "
            f"{report['success_count']}/{report['case_count']} "
            f"({report['success_rate']:.0%}); target {float(args.min_success_rate):.0%}; "
            f"passed={report['passed']}"
        )
        if args.output:
            print(f"Report: {args.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
