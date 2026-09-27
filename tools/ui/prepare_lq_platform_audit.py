"""Seed missing page scenarios only into an owned isolated LQ S3 fixture.

No application import occurs before environment/DB guards. Re-running preserves
owned examples. Attendance PDF fetching remains a separate domain contract.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

STUDENTS = [
    ("00001", "合成学生甲", "合成一班", ["CHECKED", "CHECKED", "UNCHECKED", "SICK_LEAVE"], ["verified"] * 4),
    ("00002", "合成学生乙", "合成一班", ["CHECKED", "UNKNOWN", "PERSONAL_LEAVE", "LATE_OR_EARLY"], ["verified", "unknown", "verified", "verified"]),
    ("00003", "合成学生丙", "合成二班", ["NOT_APPLICABLE"] * 4, ["verified"] * 4),
]
SESSIONS = [
    ("第1次 03-09 19:30", "2026-03-09 19:30:00"),
    ("第2次 03-12 09:30", "2026-03-12 09:30:00"),
    ("第3次 03-16 19:30", "2026-03-16 19:30:00"),
    ("第4次 03-19 09:30", "2026-03-19 09:30:00"),
]


def insert(conn, table, values):
    columns = ",".join(values)
    marks = ",".join("?" for _ in values)
    cur = conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))
    return int(cur.lastrowid)


def binding(conn, teacher_id, index, course_code, course_name, class_name):
    return insert(conn, "smart_attendance_source_bindings", {
        "owner_teacher_id": teacher_id, "school_code": "QA-SCHOOL", "platform_code": "smart_classroom",
        "external_account_key": "qa-s6-a", "remote_schedule_id": f"qa-s6-a-{index}",
        "academic_year": "2025-2026", "academic_term": 2,
        "remote_course_id": course_code, "remote_course_name": course_name,
        "remote_class_id": f"qa-class-{index}", "remote_class_name": class_name,
        "binding_state": "confirmed", "revision": 1, "confirmed_by": teacher_id,
        "confirmed_at": "2026-03-20T08:00:00", "created_at": "2026-03-20T08:00:00", "updated_at": "2026-03-20T08:00:00",
    })


def report(conn, binding_id, updated_at):
    return insert(conn, "attendance_reports", {
        "binding_id": binding_id, "scope_kind": "all_schedule", "revision": 1,
        "created_at": "2026-03-20T08:00:00", "updated_at": updated_at,
    })


def version(conn, report_id, teacher_id, state):
    return insert(conn, "attendance_report_versions", {
        "report_id": report_id, "version_no": 1, "request_key": f"qa-version-{report_id}",
        "source_file_hash": "a" * 64 if state == "cached" else None,
        "source_byte_size": 20480, "source_page_count": 2, "source_filename": "qa-attendance.pdf",
        "fetched_at": "2026-03-20T08:30:00" if state == "cached" else None,
        "source_state": state, "safe_message": "合成导出失败样本" if state == "failed" else "",
        "created_by": teacher_id, "created_at": "2026-03-20T08:05:00",
    })


def seed_attendance(conn, teacher_id):
    # Report 1: cached original + a parse run awaiting review (warning tone).
    b1 = binding(conn, teacher_id, 1, "ATT-A", "签到合成课程甲", "合成教学班一")
    r1 = report(conn, b1, "2026-03-20T12:00:00")
    v1 = version(conn, r1, teacher_id, "cached")
    validation = {"can_confirm": False, "student_count": len(STUDENTS), "session_count": len(SESSIONS),
                  "cell_count": len(STUDENTS) * len(SESSIONS), "unknown_count": 1, "conflict_count": 0,
                  "unmapped_student_count": len(STUDENTS), "unmapped_session_count": len(SESSIONS),
                  "blockers": [{"code": "unknown_cells", "message": "有 1 条待核实记录"}], "warnings": []}
    run = insert(conn, "attendance_parse_runs", {
        "source_version_id": v1, "run_no": 1, "request_key": f"qa-run-{r1}", "parser_version": "v1",
        "prompt_version": "v1", "model_id": "qa-synthetic", "schema_version": "v1", "ai_used": 1,
        "ai_coverage_json": json.dumps({"processed_blocks": 2, "total_blocks": 2}), "state": "needs_review",
        "validation_json": json.dumps(validation, ensure_ascii=False),
        "coverage_json": json.dumps({"processed_pages": 2, "total_pages": 2}), "revision": 1,
        "started_at": "2026-03-20T09:00:00", "finished_at": "2026-03-20T09:05:00",
    })
    conn.execute("UPDATE attendance_reports SET latest_source_version_id=? WHERE id=?", (v1, r1))
    session_ids = [insert(conn, "attendance_report_sessions", {
        "parse_run_id": run, "column_index": index, "source_header": header, "source_datetime": moment,
        "time_precision": "minute", "mapping_state": "unmapped", "evidence_json": json.dumps({"page": 1}),
    }) for index, (header, moment) in enumerate(SESSIONS, start=1)]
    for row_index, (number, name, class_name, statuses, qualities) in enumerate(STUDENTS, start=1):
        student = insert(conn, "attendance_report_students", {
            "parse_run_id": run, "row_index": row_index, "student_number": number, "source_name": name,
            "source_class_name": class_name, "identity_state": "unmapped", "source_page": 1,
        })
        for column, (status, quality) in enumerate(zip(statuses, qualities)):
            insert(conn, "attendance_report_cells", {
                "parse_run_id": run, "student_row_id": student, "session_column_id": session_ids[column],
                "raw_text": status, "raw_status": status, "normalized_status": status, "quality_state": quality,
                "interpretation_method": "synthetic", "evidence_page": 1, "revision": 1,
            })

    # Report 2: original cached, never parsed (neutral tone).
    b2 = binding(conn, teacher_id, 2, "ATT-B", "签到合成课程乙", "合成教学班二")
    r2 = report(conn, b2, "2026-03-19T12:00:00")
    v2 = version(conn, r2, teacher_id, "cached")
    conn.execute("UPDATE attendance_reports SET latest_source_version_id=? WHERE id=?", (v2, r2))

    # Report 3: export failed (danger tone). 'failed' is deliberately not an
    # active state, so the list does not start a 5s poll during the specs.
    b3 = binding(conn, teacher_id, 3, "ATT-C", "签到合成课程丙", "合成教学班三")
    r3 = report(conn, b3, "2026-03-18T12:00:00")
    v3 = version(conn, r3, teacher_id, "failed")
    conn.execute("UPDATE attendance_reports SET latest_source_version_id=? WHERE id=?", (v3, r3))
    return {"attendanceReportIds": [r1, r2, r3], "attendanceRunId": run}

def prepare(runtime: Path):
    runtime = runtime.resolve()
    root = (ROOT / '.codex-temp').resolve()
    if runtime == root or not runtime.is_relative_to(root):
        raise ValueError('Use an owned .codex-temp child')
    fixture_path = runtime / 'fixture.json'
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    database = runtime / 'db/classroom.db'
    if fixture.get('uiV3Synthetic') is not True or fixture.get('lqS3Synthetic') is not True or Path(fixture['databasePath']).resolve() != database:
        raise ValueError('Only an explicitly synthetic S3 SQLite fixture may be seeded')
    if (runtime / '.lq-s3-e2e.lock').exists():
        raise RuntimeError('Do not seed while a browser runner owns the fixture')
    sys.path.insert(0, str(ROOT))
    from tools.isolated_environment import isolate_sqlite_environment, guard_dotenv_loading, guard_postgres_connections
    isolate_sqlite_environment(runtime)
    guard_dotenv_loading()
    guard_postgres_connections()
    from classroom_app.services.lessondoc import pack_service
    result = dict(fixture.get('platformAudit') or {})
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        teacher_id = int(fixture['teacher']['id'])
        if not result.get('attendanceReportIds'):
            if conn.execute('SELECT COUNT(*) FROM attendance_reports').fetchone()[0]:
                raise ValueError('Attendance data exists without this seeder ownership')
            result.update(seed_attendance(conn, teacher_id))
        else:
            ids = result['attendanceReportIds']
            actual = conn.execute('SELECT COUNT(*) FROM attendance_reports r JOIN smart_attendance_source_bindings b ON b.id=r.binding_id WHERE r.id IN (%s) AND b.owner_teacher_id=? AND b.external_account_key=?' % ','.join('?' for _ in ids), [*ids, teacher_id, 'qa-s6-a']).fetchone()[0]
            if actual != len(ids): raise ValueError('Owned attendance records changed')
        if not result.get('lessonDocPackId'):
            manifest = {'spec': 'lessondoc/2.0', 'kind': 'home', 'course': {'name': 'LQ 合成课件', 'totalHours': 2, 'sessionCount': 1}, 'theme': 'sky', 'stages': [{'label': '合成阶段', 'lessons': [1]}], 'lessons': [{'n': 1, 'title': '合成课次', 'status': 'ready', 'topics': ['浏览器验收']}]}
            created = pack_service.create_pack_skeleton(conn, teacher_id=teacher_id, course_id=fixture['courseId'], manifest=manifest, pack_name='LQ 平台验收合成课件')
            pack = created['pack']
            deck = {'spec': 'lessondoc/2.0', 'kind': 'lesson', 'lesson': 1, 'course': 'LQ 合成课件', 'title': '合成课次', 'slides': [{'layout': 'title'}, {'layout': 'content', 'section': '验证', 'title': '合成内容', 'blocks': [{'type': 'text', 'md': '仅供隔离浏览器验收。'}]}]}
            pack_service.write_lesson_files(conn, pack, 1, deck)
            result.update(lessonDocPackId=int(pack['id']), htmlMaterialId=int(created['root_material_id']))
            conn.execute('INSERT INTO course_material_assignments (class_offering_id, material_id, assigned_by_teacher_id) VALUES (?, ?, ?)', (fixture['classOfferingId'], created['root_material_id'], teacher_id))
        else:
            pack = conn.execute('SELECT teacher_id, root_material_id FROM course_doc_packs WHERE id=?', (result['lessonDocPackId'],)).fetchone()
            if not pack or pack['teacher_id'] != teacher_id or pack['root_material_id'] != result['htmlMaterialId']: raise ValueError('Owned LessonDoc pack changed')
    fixture['platformAudit'] = result
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=True, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    prepare(parser.parse_args().runtime)
