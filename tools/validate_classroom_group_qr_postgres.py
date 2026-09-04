"""Validate group QR service SQL using session-local PostgreSQL tables.

Run inside the deployed app container:
    python tools/validate_classroom_group_qr_postgres.py

One connection and one transaction are used; it is always rolled back. Public
tables are excluded from search_path. Images are stored in a temporary directory.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class CheckFailure(RuntimeError):
    """A safe, fixed check label; never contains a connection string or user data."""


def require(condition, name):
    if not condition:
        raise CheckFailure(name)


def validate():
    from classroom_app import config
    from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
    from classroom_app.db.postgres_schema import POSTGRES_RUNTIME_COLUMN_DEFINITIONS
    from classroom_app.services import classroom_group_qr_service as service, file_service
    from fastapi import HTTPException, UploadFile
    from PIL import Image
    import psycopg

    require(config.DB_ENGINE == "postgres", "postgres_engine_required")
    require(bool(config.DATABASE_URL), "database_configuration_required")
    checks = []

    def passed(name, condition=True):
        require(condition, name)
        checks.append({"name": name, "ok": True})

    def expect_status(name, expected, operation):
        try:
            operation()
        except HTTPException as exc:
            passed(name, exc.status_code == expected)
        else:
            raise CheckFailure(name)

    teacher = {"role": "teacher", "id": 101}
    conn = None
    raw = None
    rolled_back = False
    with tempfile.TemporaryDirectory(prefix="classroom-group-qr-postgres-") as directory:
        with patch.object(file_service, "GLOBAL_FILES_DIR", Path(directory) / "files"), \
             patch.object(file_service, "GLOBAL_FILES_LEGACY_DIRS", ()):
            try:
                # Bypass the application pool so exactly one physical connection
                # is created. Do not use the connection's committing context manager.
                raw = psycopg.connect(config.DATABASE_URL, autocommit=False,
                    connect_timeout=10, row_factory=sqlite_compatible_dict_row)
                conn = LanSharePostgresConnection(raw)
                conn.execute("SET LOCAL search_path = pg_temp, pg_catalog")
                conn.execute("SET LOCAL statement_timeout = '10s'")
                conn.execute("SET LOCAL lock_timeout = '3s'")
                version = conn.execute("SHOW server_version").fetchone()[0]
                qr_columns = ", ".join(
                    f"{name} {definition}"
                    for name, definition in POSTGRES_RUNTIME_COLUMN_DEFINITIONS["class_offerings"].items()
                    if name.startswith("group_qr_")
                )
                conn.execute(f"""CREATE TEMP TABLE class_offerings (
                    id BIGINT PRIMARY KEY, teacher_id BIGINT, class_id BIGINT, {qr_columns}
                ) ON COMMIT DROP""")
                conn.execute("""CREATE TEMP TABLE students (
                    id BIGINT PRIMARY KEY, class_id BIGINT, enrollment_status TEXT
                ) ON COMMIT DROP""")
                conn.execute("""CREATE TEMP TABLE class_offering_class_links (
                    offering_id BIGINT, class_id BIGINT
                ) ON COMMIT DROP""")
                # Verify every unqualified service table resolves to this session's
                # temporary namespace before any fixture write or service call.
                for table in ("class_offerings", "students", "class_offering_class_links"):
                    row = conn.execute("""SELECT relpersistence,
                        relnamespace = pg_my_temp_schema() AS session_local
                        FROM pg_catalog.pg_class WHERE oid = to_regclass(?)""", (table,)).fetchone()
                    require(row and row["relpersistence"] == "t" and row["session_local"],
                            "session_local_table_required")
                passed("temporary_table_isolation")
                conn.execute("INSERT INTO class_offerings (id, teacher_id, class_id) VALUES (11,101,1),(12,102,3)")
                conn.execute("""INSERT INTO students VALUES
                    (201,1,'active'),(202,2,'active'),(203,3,'active'),(204,1,'inactive'),(205,1,NULL)""")
                conn.execute("INSERT INTO class_offering_class_links VALUES (11,1),(11,2)")
                empty = service.serialize_group_qr(service.load_group_qr_offering(conn, 11, teacher))
                passed("initial_empty_state", empty == {"image_url": "", "description": "", "revision": ""})

                def upload(format, color, revision, description):
                    with io.BytesIO() as stream:
                        Image.new("RGB", (48, 64), color).save(stream, format=format)
                        original = stream.getvalue()
                        result = service.update_group_qr(conn, 11, teacher,
                            description=description, revision=revision,
                            file=UploadFile(file=stream, filename="fixture-image"))
                    row = service.load_group_qr_offering(conn, 11, teacher)
                    stored = file_service.resolve_global_file_path(row["group_qr_file_hash"])
                    require(stored is not None and stored.read_bytes() == original, "original_bytes_preserved")
                    return result

                first = upload("PNG", "white", "", "first line\r\nsecond line\rthird line")
                passed("upload_original_and_crlf_normalization",
                       bool(first["image_url"] and first["revision"])
                       and first["description"] == "first line\nsecond line\nthird line")
                reloaded = service.serialize_group_qr(service.load_group_qr_offering(conn, 11, teacher))
                passed("reload_matches_saved_settings", reloaded == first)
                description_only = service.update_group_qr(conn, 11, teacher,
                    description="updated\r\nintroduction", revision=first["revision"])
                passed("description_only_preserves_image", description_only["image_url"] == first["image_url"]
                       and description_only["description"] == "updated\nintroduction")
                expect_status("stale_revision_rejected", 409, lambda: service.update_group_qr(
                    conn, 11, teacher, description="stale", revision=first["revision"]))
                passed("conflict_preserves_record", service.serialize_group_qr(
                    service.load_group_qr_offering(conn, 11, teacher)) == description_only)
                for student_id in (201, 202, 205):
                    student = {"role": "student", "id": student_id}
                    passed(f"member_{student_id}_read", service.serialize_group_qr(
                        service.load_group_qr_offering(conn, 11, student)) == description_only)
                    expect_status(f"member_{student_id}_write_rejected", 403,
                        lambda: service.update_group_qr(conn, 11, student, description="forbidden",
                            revision=description_only["revision"], remove_image=True))
                for name, user in (("other_teacher", {"role": "teacher", "id": 102}),
                                   ("other_class_student", {"role": "student", "id": 203}),
                                   ("inactive_student", {"role": "student", "id": 204})):
                    expect_status(f"{name}_read_rejected", 404,
                        lambda: service.load_group_qr_offering(conn, 11, user))
                expect_status("other_teacher_write_rejected", 404,
                    lambda: service.update_group_qr(conn, 11, {"role": "teacher", "id": 102},
                        description="forbidden", revision=description_only["revision"]))
                removed = service.update_group_qr(conn, 11, teacher,
                    description=description_only["description"], revision=description_only["revision"],
                    remove_image=True)
                passed("remove_preserves_introduction", not removed["image_url"]
                       and removed["description"] == description_only["description"])
                second = upload("JPEG", "navy", removed["revision"], removed["description"])
                row = service.load_group_qr_offering(conn, 11, teacher)
                passed("upload_after_removal", bool(second["image_url"])
                       and row["group_qr_mime_type"] == "image/jpeg")
                other = service.serialize_group_qr(service.load_group_qr_offering(
                    conn, 12, {"role": "teacher", "id": 102}))
                passed("other_classroom_unchanged", other == empty)
            finally:
                if conn is not None:
                    try:
                        conn.rollback()
                        rolled_back = True
                    finally:
                        conn.close()
                elif raw is not None:
                    raw.close()
    require(rolled_back and raw is not None and raw.closed, "rollback_and_close_required")
    require(not Path(directory).exists(), "temporary_files_cleanup_required")
    return {"status": "passed", "postgres_version": version, "checks": checks,
            "transaction_rolled_back": True, "connection_closed": True,
            "temporary_files_removed": True, "public_tables_modified": False}


def main():
    try:
        result = validate()
    except Exception as exc:
        # Do not print driver exception messages: they can include connection data.
        result = {"status": "failed", "error_type": type(exc).__name__}
        if isinstance(exc, CheckFailure):
            result["check"] = str(exc)
        print(json.dumps(result))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
