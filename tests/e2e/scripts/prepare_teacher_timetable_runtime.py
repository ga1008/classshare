"""Create owned synthetic accounts and term data for the teacher lookup flow."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from prepare_ui_v3_runtime import prepare


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    runtime = parser.parse_args().runtime_root.resolve()
    assert runtime.is_relative_to(ROOT / ".codex-temp") and runtime != ROOT / ".codex-temp"
    prepare(runtime)
    fp = runtime / "fixture.json"
    fixture = json.loads(fp.read_text("utf-8"))
    assert fixture["uiV3Synthetic"] and Path(fixture["databasePath"]).resolve() == runtime / "db/classroom.db"
    with sqlite3.connect(fixture["databasePath"]) as conn:
        teacher = fixture["teacher"]["id"]
        other = fixture["otherTeacher"]["id"]
        conn.execute("UPDATE teachers SET school_code='gxufl',school_name='广西外国语学院' WHERE id IN (?,?)", (teacher, other))
        conn.execute("UPDATE teacher_organization_memberships SET school_code='gxufl',school_name='广西外国语学院',is_active=1 WHERE teacher_id IN (?,?)", (teacher, other))
        conn.execute("UPDATE academic_semesters SET school_code='gxufl',name='2026-2027第一学期',start_date='2026-09-07',end_date='2027-01-24',week_count=20 WHERE teacher_id=?", (teacher,))
        conn.execute("DELETE FROM teacher_academic_system_credentials WHERE teacher_id IN (?,?)", (teacher, other))
        credential_id = None
        for tid, username in ((teacher, "synthetic-query-user"), (other, "000123")):
            cursor = conn.execute("""INSERT INTO teacher_academic_system_credentials
                (teacher_id,school_code,school_name,adapter_key,base_url,login_url,username,password_encrypted,
                 last_status,last_verified_at)
                VALUES(?,'gxufl','合成学校','synthetic','https://academic.synthetic.invalid',
                       'https://academic.synthetic.invalid/login',?,'synthetic-no-secret','verified','2026-10-20')""", (tid, username))
            if tid == teacher:
                credential_id = cursor.lastrowid
    fixture["teacherTimetable"] = {"synthetic": True, "credentialId": credential_id, "year": "2026-2027", "term": "1"}
    fp.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), "utf-8")
    (runtime / "teacher-timetable-control.json").write_text('{"mode":"success"}', "utf-8")
    print(json.dumps({"runtime": str(runtime), "synthetic": True, "teacherId": teacher}))


if __name__ == "__main__":
    main()
