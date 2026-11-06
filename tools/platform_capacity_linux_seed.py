"""Fresh synthetic classroom fixtures inside an explicitly verified Linux lab."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


def order_fresh_constraints(source_db, output):
    """Preserve exporter SQL, creating all referenced unique keys before FKs."""
    from tools import db_postgres_export as exporter
    with sqlite3.connect(Path(source_db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = exporter._table_names(conn)
        unique = [statement for table in tables for statement in exporter._unique_constraints(conn, table)]
        indexes = [exporter._index_sql(index) for index in exporter._indexes(conn)]
        foreign = [statement for table in tables for statement in exporter._foreign_key_constraints(conn, table)]
    # The unmodified exporter contains the same constraints in per-table order.
    # Refuse a drift instead of silently omitting or weakening any constraint.
    original = Path(output).read_text(encoding="utf-8")
    if any(statement not in original for statement in [*unique, *indexes, *foreign]):
        raise ValueError("Fresh exporter constraint set changed unexpectedly")
    Path(output).write_text("\n".join(["-- Fresh synthetic schema: all referenced keys before foreign keys", "BEGIN;",
                                        *unique, *indexes, *foreign, "COMMIT;", ""]), encoding="utf-8")
    return {"unique_constraints":len(unique),"indexes":len(indexes),"foreign_keys":len(foreign),
            "order":"all unique keys and indexes before foreign keys"}


def adapt_fresh_migration_metadata(conn):
    """Only the lab export's SQLite integer success flag needs PG boolean."""
    from classroom_app.db.migration_registry import schema_migrations_table_sql
    if "success boolean NOT NULL" not in schema_migrations_table_sql("postgres"):
        raise ValueError("Authoritative migration metadata contract changed")
    column = conn.execute("SELECT data_type FROM information_schema.columns WHERE table_schema='public' "
                          "AND table_name='schema_migrations' AND column_name='success'").fetchone()
    if column and column[0] == "boolean":
        return {"success_type":"boolean","changed":False}
    if not column or column[0] not in {"smallint","integer","bigint"}:
        raise ValueError("Unexpected fresh migration success type")
    select = "SELECT version,name,checksum,applied_at,duration_ms,db_engine,success,error FROM public.schema_migrations ORDER BY version,db_engine"
    constraints = "SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.schema_migrations'::regclass ORDER BY conname"
    with conn.transaction():
        before, keys = conn.execute(select).fetchall(), conn.execute(constraints).fetchall()
        if any(row[6] not in (0,1) or row[6] is None for row in before):
            raise ValueError("Fresh migration success contains a nonboolean integer")
        conn.execute("ALTER TABLE public.schema_migrations ALTER COLUMN success TYPE boolean USING (success <> 0)")
        expected = [(*row[:6],bool(row[6]),*row[7:]) for row in before]
        if conn.execute(select).fetchall() != expected or conn.execute(constraints).fetchall() != keys:
            raise ValueError("Migration metadata conversion changed rows or constraints")
    return {"success_type":"boolean","changed":True,"rows_preserved":len(before),"constraints_preserved":len(keys),
            "other_columns":"unchanged"}


def adapt_fresh_native_signature_timestamp(conn):
    """Match the owned native PG's single timestamp exception, on empty data.

    Product signature_workflow declares this column TIMESTAMP on PostgreSQL.
    Metadata-only evidence: capacity-native-schema-types.json, SHA256 below;
    its 4,607 columns contain only this timestamp and schema_migrations.success
    boolean. All other exported TEXT dates intentionally remain TEXT.
    """
    query = "SELECT data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='signature_access_requests' AND column_name='cancelled_at'"
    row = conn.execute(query).fetchone()
    if row and row[0] == "timestamp without time zone":
        return {"changed":False,"type":"timestamp without time zone"}
    if not row or row[0] != "text" or conn.execute("SELECT COUNT(*) FROM public.signature_access_requests").fetchone()[0] != 0:
        raise ValueError("Signature timestamp bridge requires the exact empty fresh TEXT column")
    constraints = "SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.signature_access_requests'::regclass ORDER BY conname"
    with conn.transaction():
        before = conn.execute(constraints).fetchall()
        conn.execute("ALTER TABLE public.signature_access_requests ALTER COLUMN cancelled_at TYPE timestamp without time zone USING NULLIF(cancelled_at,'')::timestamp without time zone")
        if conn.execute(query).fetchone() != ("timestamp without time zone",) or conn.execute(constraints).fetchall() != before:
            raise ValueError("Signature timestamp bridge changed type or constraints unexpectedly")
    return {"changed":True,"type":"timestamp without time zone","rows":0,"constraints_preserved":len(before),
            "native_metadata_sha256":"c84e64ddbd58f1ae032ddaad142e1626cf9e8900bd748fc9a5455f6fe9e9f98d"}


def schema_source_child(identity):
    """Build a genuine fresh SQLite schema in a separate no-PG child process.

    PostgreSQL startup validates/migrates an existing foundation. Use the
    repository's real SQLite initializer and existing exporter to create that
    foundation, never an old business database or a pretend client DB marker.
    """
    runtime = Path(identity["artifact_root"])
    source_root = runtime / "fresh-schema-source"
    export_root = Path("/app/.codex-temp") / ("capacity-schema-" + identity["lab_nonce"])
    if source_root.exists() or export_root.exists():
        raise ValueError("Schema source/export must be new owned lab paths")
    source_root.mkdir(parents=True)
    os.environ.update({"DB_ENGINE":"sqlite", "DATABASE_URL":"", "POSTGRES_BACKEND_READY":"false",
                       "POSTGRES_POOL_ENABLED":"false", "LANSHARE_DATA_ROOT":str(source_root), "MAIN_DATA_DIR":str(source_root)})
    from tools.isolated_environment import guard_postgres_connections
    guard_postgres_connections()
    from classroom_app.database import init_database
    from classroom_app.config import ensure_runtime_directories
    ensure_runtime_directories()
    init_database()
    source_db = source_root / "db/classroom.db"
    if not source_db.is_file():
        raise ValueError("Fresh SQLite initializer did not create its declared source")
    from tools.db_postgres_export import build_postgres_export_package
    report = build_postgres_export_package(export_root, source_db=source_db)
    if report["status"] != "ok" or report["foreign_key_violations"] != 0:
        raise ValueError("Fresh schema source/export integrity check failed")
    destination = runtime / "schema-export"; destination.mkdir()
    for name in ("01-schema.sql", "02-data.sql", "03-constraints-indexes.sql"):
        shutil.copyfile(export_root / "package" / name, destination / name)
    constraints = order_fresh_constraints(source_db, destination / "03-constraints-indexes.sql")
    from tools.platform_capacity_linux_lab import write_json
    write_json(runtime / "schema-export-report.json", {"lab_nonce":identity["lab_nonce"], "source":"fresh synthetic SQLite initializer",
        "source_database_sha256":hashlib.sha256(source_db.read_bytes()).hexdigest(), "tables":report["table_count"],
        "total_seed_rows":report["total_rows"], "foreign_key_violations":0,
        "constraints":constraints,
        "sql_sha256":{name:hashlib.sha256((destination/name).read_bytes()).hexdigest() for name in ("01-schema.sql","02-data.sql","03-constraints-indexes.sql")}})
    return {"lab_nonce":identity["lab_nonce"],"state":"fresh_schema_exported","tables":report["table_count"]}


def _initialize_postgres_foundation(identity):
    runtime = Path(identity["artifact_root"])
    from tools.platform_capacity_linux_lab import _checked_pg_connection, read_json, write_json
    # Never initialize application/config globals in this parent before the
    # fresh SQLite child finishes; the parent remains PostgreSQL throughout.
    subprocess.run([sys.executable,"tools/platform_capacity_linux_lab.py","container-schema-source"],cwd="/app",check=True)
    report = read_json(runtime / "schema-export-report.json")
    if report.get("lab_nonce") != identity["lab_nonce"] or report.get("foreign_key_violations") != 0:
        raise ValueError("Fresh schema export belongs to another lab")
    conn, _ = _checked_pg_connection(identity["marker"])
    try:
        names = conn.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
        if names != [("capacity_lab_identity",)]:
            raise ValueError("Foundation load refuses any pre-existing application tables")
        for name in ("01-schema.sql","02-data.sql","03-constraints-indexes.sql"):
            path = runtime / "schema-export" / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != report["sql_sha256"][name]:
                raise ValueError("Fresh foundation export changed")
            conn.execute(path.read_text(encoding="utf-8"),prepare=False)
        metadata = adapt_fresh_migration_metadata(conn)
        signature_timestamp = adapt_fresh_native_signature_timestamp(conn)
        write_json(runtime / "foundation-import-report.json", {"lab_nonce":identity["lab_nonce"],"migration_metadata":metadata,
            "signature_timestamp":signature_timestamp,
            "scope":"fresh lab export only; production adapter/exporter unchanged"})
    finally:
        conn.close()


def seed_lab(identity):
    """No app import may precede the controller's physical DB/nonce checks."""
    if identity.get("fixture") is not None or not identity.get("pg_system_identifier"):
        raise ValueError("Fresh controller bootstrap identity is required")
    marker, runtime = identity["marker"], Path(identity["artifact_root"])
    if (runtime / "server-fixture.json").exists():
        raise ValueError("The owned lab has already been seeded")
    _initialize_postgres_foundation(identity)
    from classroom_app.database import init_database, get_db_connection
    from classroom_app.db.connection import execute_insert_returning_id
    from classroom_app.dependencies import get_password_hash
    from classroom_app.services.file_service import global_file_write_path
    from classroom_app.services.material_publication_service import record_classroom_material_publication
    init_database()
    encoded = get_password_hash(marker["synthetic_password"])
    stamp = datetime.now().isoformat()
    content = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>合成容量学习材料</title>"
               "<h1>合成容量学习材料</h1>" + "<p>观察地址、网关、协议与课堂协作，记录可验证的实验过程。</p>" * 120 + "</html>").encode()
    file_hash = hashlib.sha256(content).hexdigest()
    path = global_file_write_path(file_hash); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
    with get_db_connection() as conn:
        if conn.execute("SELECT COUNT(*) AS n FROM students").fetchone()["n"] != 0:
            raise ValueError("Fresh lab must contain no existing students")
        teacher = execute_insert_returning_id(conn, """INSERT INTO teachers(name,email,hashed_password,password_updated_at,
            is_active,school_code,school_name) VALUES(?,?,?,?,1,'capacity.synthetic','Synthetic capacity school')""",
            ("合成容量教师", "capacity-teacher@example.invalid", encoded, stamp))
        class_id = execute_insert_returning_id(conn, """INSERT INTO classes(name,created_by_teacher_id,school_code,school_name,description)
            VALUES(?,?,'capacity.synthetic','Synthetic capacity school','owned synthetic capacity fixture')""",
            ("Capacity synthetic class " + marker["lab_nonce"], teacher))
        course = execute_insert_returning_id(conn, """INSERT INTO courses(name,sect_name,created_by_teacher_id,school_code,school_name)
            VALUES('合成网络原理','网络宗门',?,'capacity.synthetic','Synthetic capacity school')""", (teacher,))
        offering = execute_insert_returning_id(conn, "INSERT INTO class_offerings(class_id,course_id,teacher_id,semester) VALUES(?,?,?,'capacity-synthetic')",
                                              (class_id, course, teacher))
        users = []
        for index in range(200):
            identifier = f"CAPACITY-SYNTHETIC-{index:04d}"
            student = execute_insert_returning_id(conn, """INSERT INTO students(student_id_number,name,class_id,gender,email,
                hashed_password,password_reset_required,password_updated_at,description,school_code,school_name)
                VALUES(?,?,?,'unknown',?,?,0,?,'owned synthetic capacity fixture','capacity.synthetic','Synthetic capacity school')""",
                (identifier, f"容量合成学生{index:04d}", class_id, f"capacity-{index}@example.invalid", encoded, stamp))
            users.append({"identifier": identifier, "student_id": student})
        root = execute_insert_returning_id(conn, """INSERT INTO course_materials(teacher_id,material_path,name,node_type,preview_type)
            VALUES(?,'capacity-lab','合成容量材料','folder','folder')""", (teacher,))
        conn.execute("UPDATE course_materials SET root_id=? WHERE id=?", (root, root))
        material = execute_insert_returning_id(conn, """INSERT INTO course_materials(teacher_id,parent_id,root_id,material_path,name,node_type,
            mime_type,preview_type,file_ext,file_hash,file_size) VALUES(?,?,?,'capacity-lab/index.html','合成容量学习材料.html','file',
            'text/html','html','.html',?,?)""", (teacher, root, root, file_hash, len(content)))
        record_classroom_material_publication(conn, root, offering, teacher, mode="explicit")
        conn.commit()
    fixture = {"kind": "capacity-linux-server-fixture", "synthetic": True, "uiV3Synthetic": True,
               "lab_nonce": marker["lab_nonce"], "classOfferingId": offering, "classId": class_id,
               "teacher": {"id": teacher, "email": "capacity-teacher@example.invalid"}, "student": {"id": users[0]["student_id"]},
               "visualMaterialIds": [material], "password": marker["synthetic_password"], "users": users}
    from tools.platform_capacity_social import _seed_validated_social
    from tools.platform_capacity_reading import seed_reading
    _seed_validated_social(runtime, fixture, users=200)
    seed_reading(runtime, fixture)
    from classroom_app.services.learning_progress_service import refresh_pending_learning_snapshots
    for _ in range(20):
        result = refresh_pending_learning_snapshots(limit=100)
        if not result.get("refreshed"):
            break
    from tools.platform_capacity_linux_lab import write_json
    write_json(runtime / "server-fixture.json", fixture, private=True)
    return {"lab_nonce": marker["lab_nonce"], "users": len(users), "class_offering_id": offering, "state": "seeded"}


def final_durable_facts(identity, report_path):
    """Raw psycopg read-only transaction; never init/import application DB code."""
    from tools.platform_capacity_linux_lab import _checked_pg_connection, read_json, write_json
    report_path = Path(report_path).resolve(strict=True)
    runtime = Path(identity["artifact_root"])
    if not report_path.is_relative_to(runtime) or report_path == runtime:
        raise ValueError("Final client report must be an explicit owned lab artifact")
    report = read_json(report_path)
    evidence = report.get("lab", {})
    expected_identity = read_json(runtime / "capacity-lab-identity.json")
    if (report.get("status") not in {"passed", "failed", "aborted"} or report.get("users") != 200
            or evidence.get("lab_nonce") != identity["lab_nonce"] or evidence.get("mode") != "linux_lab_attach_only"
            or evidence.get("expected_identity") != expected_identity or evidence.get("release_id") != identity["marker"]["release_id"]):
        raise ValueError("Final report does not belong to this terminal lab run")
    fixture = identity["fixture"]
    expected = {"capacity": sum(row["count"] for row in report.get("statuses", []) if row["flow"] == "behavior" and row["status"] in {200, 202}),
                "capacity_ambient": sum(row["count"] for row in report.get("ambient", {}).get("statuses", [])
                                        if row["flow"] == "ambient-heartbeat" and row["status"] in {200, 202})}
    start = datetime.fromtimestamp(report["run_started_epoch"], timezone.utc).astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat()
    end = str(report.get("health", {}).get("server_local_time", ""))
    if not end:
        raise ValueError("Final report must retain its server wall-clock boundary")
    end_value = datetime.fromisoformat(end)
    if end_value.tzinfo is not None:
        end_value = end_value.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    # Health uses seconds; receipts retain microseconds. The exclusive next
    # second includes the last accepted fraction instead of truncating it.
    end_exclusive = (end_value.replace(microsecond=0) + timedelta(seconds=1)).isoformat(timespec="microseconds")
    if datetime.fromisoformat(end_exclusive) < datetime.fromisoformat(start):
        raise ValueError("Final report must retain its server wall-clock boundary")
    ids = [item["student_id"] for item in fixture["users"]]
    conn, _ = _checked_pg_connection(identity["marker"])
    try:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            receipts = conn.execute("""SELECT page_key,state,COUNT(*) FROM behavior_write_inbox
                WHERE received_at>=%s AND received_at<%s AND user_role='student' AND user_pk=ANY(%s)
                AND class_offering_id=%s AND page_key=ANY(%s) GROUP BY page_key,state ORDER BY page_key,state""",
                (start, end_exclusive, ids, fixture["classOfferingId"], list(expected))).fetchall()
            projections = conn.execute("""SELECT student_id,dirty,dirty_version,calculated_version FROM learning_progress_snapshots
                WHERE student_id=ANY(%s) AND class_offering_id=%s ORDER BY student_id""", (ids, fixture["classOfferingId"])).fetchall()
            unresolved = conn.execute("SELECT COUNT(*) FROM behavior_write_inbox WHERE state IN ('pending','quarantined')").fetchone()[0]
    finally:
        conn.close()
    receipts_ok = ({page: sum(n for key,state,n in receipts if key == page and state == "applied") for page in expected} == expected
                   and all(state == "applied" for _,state,_ in receipts))
    projections_ok = len(projections) == 200 and all(not dirty and calculated == version for _,dirty,version,calculated in projections)
    result = {"lab_nonce": identity["lab_nonce"], "run_id": report["run_id"], "status": "passed" if receipts_ok and projections_ok and unresolved == 0 else "failed",
              "performance_run_status_separate": report["status"], "expected_receipts": expected, "receipt_states": receipts,
              "all_accepted_measured_batches_applied": receipts_ok, "projection_generations": projections,
              "all_projection_generations_clean_and_current": projections_ok, "inbox_pending_or_quarantined": unresolved,
              "scope": "read-only Linux controller facts; client report alone does not certify this state", "production_data_modified": False}
    write_json(runtime / "final-durable-facts.json", result)
    return {key:value for key,value in result.items() if key != "projection_generations"}
