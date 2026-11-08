"""Create a second classroom only inside the controller's owned synthetic PG.

No DSN, host, credentials, arbitrary SQL, or production fixture can be supplied.
The second offering shares the synthetic class so each of its 200 students has
real classroom authorization. It adds no learning records or fabricated ACKs.
"""
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.platform_capacity_linux_lab import validate_container_boundary, _checked_pg_connection


def main():
    identity = validate_container_boundary()
    output = Path(identity["artifact_root"]) / "recorder-second-offering.json"
    if output.exists():
        raise ValueError("Preserve an existing companion fixture")
    fixture = identity["fixture"]
    conn, _ = _checked_pg_connection(identity["marker"])
    try:
        with conn.transaction():
            class_id, teacher_id = conn.execute(
                "SELECT class_id,teacher_id FROM class_offerings WHERE id=%s",
                (fixture["classOfferingId"],)).fetchone()
            if class_id != fixture["classId"] or teacher_id != fixture["teacher"]["id"]:
                raise ValueError("Companion ownership differs from the guarded synthetic fixture")
            course_id = conn.execute("""INSERT INTO courses
                (name,sect_name,created_by_teacher_id,school_code,school_name)
                VALUES('Synthetic companion Python','Synthetic',%s,'capacity.synthetic','Synthetic capacity school')
                RETURNING id""", (teacher_id,)).fetchone()[0]
            offering_id = conn.execute("""INSERT INTO class_offerings(class_id,course_id,teacher_id,semester)
                VALUES(%s,%s,%s,'capacity-synthetic') RETURNING id""",
                (class_id, course_id, teacher_id)).fetchone()[0]
        output.write_text(json.dumps({"synthetic": True, "lab_nonce": identity["lab_nonce"],
            "primary_offering_id": fixture["classOfferingId"], "second_offering_id": offering_id,
            "class_id": class_id, "source": "controller-created synthetic companion classroom"}, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
