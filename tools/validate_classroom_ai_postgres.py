"""Validate classroom AI saves in a disposable local PostgreSQL schema.

Run: venv/Scripts/python.exe tools/validate_classroom_ai_postgres.py --run-local
The public schema and application data are never modified.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import uuid
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local", action="store_true", required=True)
    parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from classroom_app import config
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from fastapi.testclient import TestClient
    from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
    from classroom_app.db import postgres_schema as schema_service
    from classroom_app.db.errors import DatabaseProgrammingError
    from classroom_app.services.psych_profile_service import load_ai_class_config
    from tests.test_classroom_ai_config import seed_fixture, build_app, routes

    if conninfo_to_dict(config.DATABASE_URL).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Only a local PostgreSQL instance is allowed")
    schema = "classroom_ai_qa_" + uuid.uuid4().hex
    admin = psycopg.connect(config.DATABASE_URL, autocommit=True, connect_timeout=10)
    created = False
    checks = []

    def connection():
        return LanSharePostgresConnection(psycopg.connect(
            config.DATABASE_URL, connect_timeout=10, row_factory=sqlite_compatible_dict_row,
            options=f"-c search_path={schema} -c lock_timeout=5000 -c statement_timeout=10000",
        ))

    def index_exists(conn, index_name):
        return bool(conn.execute("SELECT 1 FROM pg_indexes WHERE schemaname=? AND indexname=?", (schema, index_name)).fetchone())

    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        created = True
        with connection() as conn:
            seed_fixture(conn, engine="postgres")
            conn.execute("ALTER TABLE ai_class_configs DROP CONSTRAINT ai_class_configs_class_offering_id_key")

        with patch.object(routes, "get_db_connection", connection), TestClient(build_app()) as client:
            payload = {"class_offering_id": 11, "textbook_id": "2", "system_prompt": "initial", "syllabus": "initial syllabus"}
            with patch.object(routes.logger, "exception") as logged:
                response = client.post("/api/manage/ai/configure", data=payload)
                assert response.status_code == 500, response.text
                assert logged.called
            with connection() as conn:
                assert conn.execute("SELECT textbook_id FROM class_offerings WHERE id=11").fetchone()[0] == 1
                assert conn.execute("SELECT COUNT(*) FROM ai_class_configs").fetchone()[0] == 0
                try:
                    conn.execute("EXPLAIN INSERT INTO ai_class_configs (class_offering_id) VALUES (11) ON CONFLICT (class_offering_id) DO NOTHING")
                except psycopg.errors.InvalidColumnReference as exc:
                    assert exc.sqlstate == "42P10"
                    checks.append("reproduced production missing conflict target (42P10), save rollback verified")
                    conn.rollback()
                else:
                    raise AssertionError("Missing constraint was not reproduced")

            # Scope the existing startup migration to this isolated schema.
            with patch.object(schema_service, "_public_tables", return_value={"ai_class_configs"}), \
                 patch.object(schema_service, "_index_exists", side_effect=index_exists):
                with connection() as conn:
                    conn.execute("INSERT INTO ai_class_configs (class_offering_id) VALUES (11), (11)")
                    try:
                        schema_service.ensure_postgres_runtime_constraints(conn)
                    except DatabaseProgrammingError:
                        assert conn.execute("SELECT COUNT(*) FROM ai_class_configs").fetchone()[0] == 2
                        conn.rollback()
                    else:
                        raise AssertionError("Migration must not discard duplicate configuration data")
                checks.append("duplicate data causes explicit migration refusal without deleting records")
                with connection() as conn:
                    report = schema_service.ensure_postgres_runtime_constraints(conn)
                    assert report["created_indexes"] == ["idx_ai_class_configs_unique_offering"]
                with connection() as conn:
                    assert not schema_service.ensure_postgres_runtime_constraints(conn)["schema_writes_executed"]
                checks.append("existing startup migration repairs missing unique index idempotently")

            assert client.post("/api/manage/ai/configure", data=payload).status_code == 200
            payload["system_prompt"] = "updated"
            assert client.post("/api/manage/ai/configure", data=payload).status_code == 200
            saved = client.get("/api/manage/ai/config/11").json()
            assert saved["system_prompt"] == "updated" and saved["textbook_id"] == 2
            with connection() as conn:
                assert load_ai_class_config(conn, 11)["system_prompt"] == "updated"
                assert load_ai_class_config(conn, 12)["system_prompt"] == ""
                assert conn.execute("SELECT COUNT(*) FROM ai_class_configs").fetchone()[0] == 1
            checks.append("create, update, reload and live configuration reader agree; classrooms remain isolated")

            def save_concurrently(book):
                data = {**payload, "textbook_id": str(book), "system_prompt": f"book-{book}", "syllabus": f"syllabus-{book}"}
                with TestClient(build_app()) as peer:
                    return peer.post("/api/manage/ai/configure", data=data).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                assert list(pool.map(save_concurrently, [1, 2])) == [200, 200]
            saved = client.get("/api/manage/ai/config/11").json()
            assert saved["system_prompt"] == f"book-{saved['textbook_id']}"
            assert saved["syllabus"] == f"syllabus-{saved['textbook_id']}"
            checks.append("parallel saves keep textbook, prompt and syllabus in one atomic transaction")

            response = client.post("/api/manage/ai/configure", data={**payload, "textbook_id": ""})
            assert response.status_code == 200 and response.json()["textbook_id"] is None
            assert client.get("/api/manage/ai/config/11").json()["textbook_id"] is None
            assert client.post("/api/manage/ai/configure", data={**payload, "class_offering_id": 13}).status_code == 404
            assert client.post("/api/manage/ai/configure", data={**payload, "textbook_id": "3"}).status_code == 404
            checks.append("explicit textbook removal and cross-teacher permission boundaries verified")
    finally:
        if created:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            assert not admin.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)).fetchone()
        admin.close()
    print(json.dumps({"status": "passed", "checks": checks, "isolated_schema_removed": True}, indent=2))


if __name__ == "__main__":
    main()
