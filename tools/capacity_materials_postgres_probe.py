"""Verify material batching and permissions in an owned, isolated PG schema."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(args):
    from tools.isolated_environment import guard_dotenv_loading, isolate_sqlite_environment
    import psycopg
    from psycopg import sql

    cluster = args.cluster_root.resolve(strict=True)
    if cluster != (ROOT / ".codex-temp/platform-capacity-20261105-pg").resolve() or args.port == 5432:
        raise ValueError("Explicit task-owned non-default-port PostgreSQL cluster required")
    dsn = f"postgresql://rehearsal_admin@127.0.0.1:{args.port}/lanshare_assessment_rehearsal_capacity"
    schema = "capacity_materials_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as admin:
        directory, listener, port = admin.execute(
            "SELECT current_setting('data_directory'),current_setting('listen_addresses'),current_setting('port')"
        ).fetchone()
        if Path(directory).resolve() != cluster or listener != "127.0.0.1" or int(port) != args.port:
            raise ValueError("PostgreSQL server identity mismatch")
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    report = {"schema": schema, "production_data_accessed": False}
    try:
        with tempfile.TemporaryDirectory(prefix="capacity-materials-") as temporary, ExitStack() as stack:
            isolate_sqlite_environment(Path(temporary))
            guard_dotenv_loading(stack)
            for name in list(os.environ):
                if name.endswith(("_API_KEY", "_ACCESS_TOKEN")) or name.lower().endswith("_proxy"):
                    os.environ.pop(name, None)
            os.environ.update(DB_ENGINE="postgres", DATABASE_URL=dsn, POSTGRES_BACKEND_READY="true", POSTGRES_POOL_ENABLED="false")
            original_connect, original_dns = socket.socket.connect, socket.getaddrinfo
            def local_connect(sock, address):
                if str(address[0]).lower() not in {"localhost", "127.0.0.1", "::1"}:
                    raise RuntimeError("Material probe forbids external sockets")
                return original_connect(sock, address)
            def local_dns(host, *values, **kwargs):
                if host is not None and str(host).lower() not in {"localhost", "127.0.0.1", "::1"}:
                    raise RuntimeError("Material probe forbids external DNS")
                return original_dns(host, *values, **kwargs)
            stack.enter_context(patch.object(socket.socket, "connect", local_connect))
            stack.enter_context(patch.object(socket, "getaddrinfo", local_dns))

            from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
            from classroom_app.services.material_render_service import resolve_render_target, resolve_render_targets, resolve_render_file
            from classroom_app.services.material_publication_service import get_classroom_publication_context, record_classroom_material_publication
            from classroom_app.services.materials_service import ensure_user_material_access, get_nearest_assignment_anchor
            from fastapi import HTTPException
            from tests.test_session_learning_materials_service import _make_conn

            def connect():
                return LanSharePostgresConnection(psycopg.connect(dsn, connect_timeout=5,
                    row_factory=sqlite_compatible_dict_row, application_name=schema,
                    options=f"-c search_path={schema} -c statement_timeout=15000 -c lock_timeout=3000"))
            # This canonical test fixture contains synthetic students and
            # material rows only. No application DB or .env is opened.
            source = _make_conn()
            with connect() as conn:
                for row in source.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                    conn.execute(row["sql"])
                    records = source.execute(f'SELECT * FROM "{row["name"]}"').fetchall()
                    for record in records:
                        placeholders = ",".join("?" for _ in record)
                        conn.execute(f'INSERT INTO "{row["name"]}" VALUES ({placeholders})', tuple(record))
                conn.execute("INSERT INTO students(id,class_id) VALUES(8,2)")
                for index in range(1, 201):
                    folder, entry = 1000+index*2, 1001+index*2
                    path = f"site/lesson_{index}"
                    conn.execute("INSERT INTO course_materials(id,teacher_id,parent_id,root_id,name,material_path,node_type) VALUES(?,1,200,200,?,?,'folder')",
                                 (folder, f"lesson_{index}", path))
                    conn.execute("INSERT INTO course_materials(id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type,file_ext,file_hash) "
                                 "VALUES(?,1,?,200,?,?,'file','text','html',?)",
                                 (entry, folder, f"lesson_{index}.html", f"{path}/lesson_{index}.html", f"h-{index}"))
                    conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,learning_material_id) VALUES(?,5,?)", (1000+index, entry))
                conn.execute("INSERT INTO course_materials(id,teacher_id,parent_id,root_id,name,material_path,node_type,file_ext) VALUES(3000,1,200,200,'style.css','site/style.css','file','css')")
            source.close()
            statements = []
            execute = LanSharePostgresConnection.execute
            def counted(conn, statement, params=None):
                statements.append(statement)
                return execute(conn, statement, params)
            with connect() as conn:
                conn.execute("SET TRANSACTION READ ONLY")
                folders = conn.execute("SELECT * FROM course_materials WHERE id BETWEEN 1000 AND 2000 AND node_type='folder' ORDER BY id").fetchall()
                root = conn.execute("SELECT * FROM course_materials WHERE id=200").fetchone()
                with patch.object(LanSharePostgresConnection, "execute", counted):
                    started = time.perf_counter()
                    old = {int(row["id"]): resolve_render_target(conn, row) for row in folders}
                    baseline = {"sql": len(statements), "seconds": round(time.perf_counter()-started, 4)}
                    statements.clear()
                    started = time.perf_counter()
                    current = resolve_render_targets(conn, folders)
                    batched = {"sql": len(statements), "seconds": round(time.perf_counter()-started, 4)}
                    assert old == current and len(current) == 200 and batched["sql"] <= 3
                    statements.clear()
                    for _ in range(20):
                        assert resolve_render_file(conn, root, "style.css")["id"] == 3000
                    assert len(statements) == 20
                    statements.clear()
                    nodes, restrictions = get_classroom_publication_context(conn, 5)
                    publication_sql = len(statements)
                    assert [row["id"] for row in nodes] == [200] and restrictions == {}
                    assert not any("CREATE " in statement.upper() for statement in statements)
                report.update(folder_baseline=baseline, folder_batched=batched, explicit_assets_sql=20,
                              publication_bindings=200, publication_sql=publication_sql, readonly_projection=True)
            student = {"role": "student", "id": 9}
            with connect() as conn:
                assert ensure_user_material_access(conn, 1003, student)["id"] == 1003
                try:
                    ensure_user_material_access(conn, 1003, {"role": "student", "id": 8})
                    raise AssertionError("Cross-class material access unexpectedly accepted")
                except HTTPException as exc:
                    assert exc.status_code == 403
                conn.execute("INSERT INTO material_ai_import_records(id,document_group,package_material_id) VALUES(1,'final_material',1002)")
                try:
                    ensure_user_material_access(conn, 1003, student)
                    raise AssertionError("Teacher archive material unexpectedly accepted")
                except HTTPException as exc:
                    assert exc.status_code == 403
                assert ensure_user_material_access(conn, 1003, {"role": "teacher", "id": 1})["id"] == 1003
                record_classroom_material_publication(conn, 1004, 5, 1)
                nodes, restrictions = get_classroom_publication_context(conn, 5)
                child = conn.execute("SELECT * FROM course_materials WHERE id=1005").fetchone()
                assert get_nearest_assignment_anchor(conn, 5, child, publication_nodes=nodes, restricted_paths=restrictions)["id"] == 1004
            with connect() as conn:
                conn.execute("UPDATE class_offering_sessions SET learning_material_id=NULL")
                conn.execute("DELETE FROM course_material_assignments")
            with connect() as conn:
                try:
                    ensure_user_material_access(conn, 1005, student)
                    raise AssertionError("Withdrawn publication unexpectedly accepted")
                except HTTPException as exc:
                    assert exc.status_code == 403
            report.update(cross_class_denied=True, teacher_archive_denied=True, teacher_owner_preserved=True,
                          nearest_anchor_preserved=True, publication_revocation_rechecked=True)
    finally:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        report["schema_removed"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
