"""Add deterministic editor examples only to an existing synthetic UI fixture."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def prepare_exam(conn, fixture, teacher):
    paper = "lq-synthetic-exam-paper"
    questions = {"title": "网络基础练习", "pages": [{"id": "p1", "name": "简答题", "questions": [
        {"id": "q1", "type": "textarea", "text": "说明协议分层的作用。", "score": 100},
    ]}]}
    existing = conn.execute("SELECT teacher_id FROM exam_papers WHERE id=?", (paper,)).fetchone()
    if existing and existing["teacher_id"] != teacher["id"]:
        raise ValueError("Synthetic examination belongs to a different fixture teacher")
    conn.execute("INSERT INTO exam_papers (id,teacher_id,title,questions_json,exam_config_json) VALUES (?,?,?,?,?) "
                 "ON CONFLICT(id) DO UPDATE SET questions_json=excluded.questions_json",
                 (paper, teacher["id"], "LQ 合成试卷", json.dumps(questions, ensure_ascii=False), "{}"))
    route = next((url for name, url in fixture.get("lqCaptureRoutes", {}).get("student", []) if name == "exam-take"), None)
    if route:
        assignment = int(route.rsplit("/", 1)[-1])
        changed = conn.execute("UPDATE assignments SET status='published',due_at=NULL,availability_mode='permanent',auto_close=0 "
                               "WHERE id=? AND exam_paper_id=?", (assignment, paper))
        if changed.rowcount != 1:
            raise ValueError("Recorded synthetic examination no longer matches its owned paper")
    else:
        source = dict(conn.execute("SELECT * FROM assignments WHERE id=?", (fixture["studentSubmissionAssignmentId"],)).fetchone())
        source.pop("id")
        source.update(title="LQ 合成考试", exam_paper_id=paper, due_at=None, status="published", availability_mode="permanent", auto_close=0)
        columns = list(source)
        assignment = conn.execute(f"INSERT INTO assignments ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [source[k] for k in columns]).lastrowid
    return assignment


def prepare(runtime: Path):
    runtime = runtime.resolve()
    if not runtime.is_relative_to((ROOT / ".codex-temp").resolve()) or runtime == (ROOT / ".codex-temp").resolve():
        raise ValueError("Use a task-owned synthetic fixture under .codex-temp")
    fixture_path = runtime / "fixture.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    database = runtime / "db/classroom.db"
    if fixture.get("uiV3Synthetic") is not True or Path(fixture["databasePath"]).resolve() != database:
        raise ValueError("The fixture must be explicitly synthetic")
    if fixture.get("lqCaptureRoutes"):
        # Upgrade early S0 fixtures that used an unknown status and the wrong
        # paper shape; photographing their 200 status page was not exam coverage.
        with sqlite3.connect(database) as conn:
            conn.row_factory = sqlite3.Row
            prepare_exam(conn, fixture, fixture["teacher"])
        print("LQ examples retained; published examination shape verified")
        return
    os.environ.update({"PYTHON_DOTENV_DISABLED": "1", "DB_ENGINE": "sqlite", "POSTGRES_BACKEND_READY": "false", "LANSHARE_DATA_ROOT": str(runtime), "MAIN_DATA_DIR": str(runtime), "MAIN_DB_PATH": str(database)})
    sys.path.insert(0, str(ROOT))
    from tools.isolated_environment import (
        guard_dotenv_loading, guard_postgres_connections, isolate_sqlite_environment,
    )
    isolate_sqlite_environment(runtime)
    guard_dotenv_loading()
    guard_postgres_connections()
    from classroom_app.services import lesson_plan_service as lp, assessment_plan_service as ap, teacher_evaluation_service as te
    teacher = fixture["teacher"]
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        lesson = lp.create_lesson_plan(conn, teacher=teacher, title="LQ 合成教案", cover={"course_name": "网络基础"}, sessions=[{"index": 1}], status="ready")
        assessment = ap.create_assessment_plan(conn, teacher=teacher, title="LQ 合成考核方案", fields={"course_name": "网络基础"}, items=[], status="ready")
        evaluation = te.create_evaluation(conn, teacher=teacher, title="LQ 合成评学表", fields={"course_name": "网络基础"}, items=[], status="ready")
        assignment = prepare_exam(conn, fixture, teacher)
        conn.commit()
    fixture["lqCaptureRoutes"] = {
        "teacher": [["lesson-plan-editor", f"/lesson-plan/{lesson}/edit"], ["assessment-plan-editor", f"/assessment-plan/{assessment}/edit"], ["evaluation-editor", f"/teacher-evaluation/{evaluation}/edit"], ["wrong-summary", f"/assignment/{fixture['teacherReviewAssignmentId']}/wrong-summary"], ["materials", "/manage/library"]],
        "student": [["exam-take", f"/exam/take/{assignment}"]],
    }
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=True, indent=2), encoding="utf-8")
    print("Prepared 3 independent editors, exam-taking, wrong-summary and material library")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    prepare(parser.parse_args().runtime)
