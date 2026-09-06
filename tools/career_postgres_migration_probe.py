"""Rehearse additive career/resume migration from the audited Git baseline.

Only local PostgreSQL is allowed. Synthetic rows live in a disposable schema;
no student data or application schema is copied or changed.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from types import ModuleType
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql
from classroom_app import config
from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.db import connection, schema_ai_jobs, schema_career_path, schema_resume
from classroom_app.services.resume import resume_document_service as documents


def run(baseline: str) -> dict:
    if not re.fullmatch(r"[a-fA-F0-9]{8,40}", baseline):
        raise ValueError("An audited Git commit hash is required")
    if urlsplit(config.DATABASE_URL).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Migration rehearsal only permits local PostgreSQL")
    schema = "career_migration_probe_" + uuid.uuid4().hex
    baseline_modules = []
    created = False
    def connect():
        return LanSharePostgresConnection(psycopg.connect(config.DATABASE_URL, connect_timeout=5,
            row_factory=sqlite_compatible_dict_row, options=f"-c search_path={schema}"))
    def reset_guards():
        schema_ai_jobs.reset_ai_job_schema_guard_for_tests()
        schema_career_path._SCHEMA_READY = schema_resume._SCHEMA_READY = False
    try:
        with psycopg.connect(config.DATABASE_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
        for name in ("schema_ai_jobs", "schema_career_path", "schema_resume"):
            content = subprocess.run(["git", "show", f"{baseline}:classroom_app/db/{name}.py"], cwd=ROOT,
                check=True, capture_output=True, encoding="utf-8").stdout
            module = ModuleType("classroom_app.db._migration_probe_" + name)
            module.__package__ = "classroom_app.db"
            exec(compile(content, f"{baseline}:{name}", "exec"), module.__dict__)
            module.get_configured_db_engine = lambda: "postgres"
            baseline_modules.append(module)
        with connect() as conn:
            conn.execute("CREATE TABLE submissions(id BIGINT PRIMARY KEY)")
            conn.execute("CREATE TABLE students(id BIGINT PRIMARY KEY,student_id TEXT,name TEXT,school_code TEXT,major_name TEXT)")
            baseline_modules[0].ensure_ai_job_schema(conn, engine="postgres")
            baseline_modules[1].ensure_career_path_schema(conn)
            baseline_modules[2].ensure_resume_schema(conn)
            for n in range(1, 6):
                conn.execute("INSERT INTO students VALUES (?,?,?,'probe','English')", (n, f"synthetic-{n}", f"Student {n}"))
                conn.execute("INSERT INTO resume_personal_info(student_id,name,email,seeded) VALUES (?,?,?,1)", (n,f"Student {n}",f"s{n}@example.test"))
                conn.execute("INSERT INTO resumes(student_id,title,template_key,layout_json,render_html,status,optimized_summary_md) VALUES (?,?,'classic',?,?,?,?)",
                    (n, f"Legacy {n}", '{"personal_fields":["name","email"],"blocks":[]}', f"<p>Original delivered version {n}</p>", "ready", f"Original summary {n}"))
            for n, state in enumerate(("ready", "failed", "generating"), 1):
                conn.execute("INSERT INTO career_major_networks(school_code,major_key,major_name,status,network_json) VALUES ('probe',?,?,?,?)",
                    (f"major-{n}", f"Major {n}", state, '{"legacy_marker":"unchanged"}'))
            for n in range(1, 16):
                conn.execute("INSERT INTO career_student_sessions(student_id,school_code,major_key,major_name,status,test_answers_json) VALUES (?,'probe','major-1','Major 1','ready',?)",
                    (n, json.dumps([{"q": "legacy", "a": n}])))
            conn.commit()
            originals = {name: [dict(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY id")]
                for name in ("resumes", "resume_personal_info", "career_major_networks", "career_student_sessions")}
        with ExitStack() as stack:
            for module in (schema_career_path, schema_resume, connection):
                stack.enter_context(patch.object(module, "get_configured_db_engine", return_value="postgres"))
            reset_guards()
            started = time.perf_counter()
            with connect() as conn:
                schema_ai_jobs.ensure_ai_job_schema(conn, engine="postgres")
                schema_career_path.ensure_career_path_schema(conn)
                schema_resume.ensure_resume_schema(conn)
                # Simulate a startup failure before the schema transaction
                # commits. PostgreSQL must leave the baseline usable.
                conn.rollback()
                rolled_back = conn.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='resume_versions'").fetchone()[0] == 0
                rolled_back = rolled_back and conn.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='ai_jobs' AND column_name='capacity_reserved_until'").fetchone()[0] == 0
            reset_guards()
            with connect() as conn:
                conn.execute("SET LOCAL lock_timeout = '5s'")
                conn.execute("SET LOCAL statement_timeout = '60s'")
                conn.execute("SELECT pg_advisory_xact_lock(?)", (742819036118,))
                schema_ai_jobs.ensure_ai_job_schema(conn, engine="postgres")
                schema_career_path.ensure_career_path_schema(conn)
                schema_resume.ensure_resume_schema(conn)
                conn.commit()
                current_rows = {name: list(conn.execute(f"SELECT * FROM {name} ORDER BY id")) for name in originals}
                preserved = all([
                    len(rows) == len(current_rows[name]) and all(all(dict(current)[key] == value for key, value in old.items())
                        for old, current in zip(rows, current_rows[name]))
                    for name, rows in originals.items()
                ])
                count_before = documents.backfill_resume_versions(conn, limit=100)
                conn.commit()
                count_after = documents.backfill_resume_versions(conn, limit=100)
                versions = [dict(r) for r in conn.execute("SELECT resume_id,revision,render_html,snapshot_json FROM resume_versions ORDER BY resume_id")]
                conn.commit()
            reset_guards()
            with connect() as conn:
                schema_ai_jobs.ensure_ai_job_schema(conn, engine="postgres")
                schema_career_path.ensure_career_path_schema(conn)
                schema_resume.ensure_resume_schema(conn)
                conn.commit()
                repeated_count = conn.execute("SELECT COUNT(*) FROM resume_versions").fetchone()[0]
            html_preserved = len(versions) == 5 and all(v["render_html"] == f"<p>Original delivered version {v['resume_id']}</p>" for v in versions)
            uncertain_marked = all(json.loads(v["snapshot_json"]).get("legacy_materials_reconstructed") is True for v in versions)
        return {"ok": rolled_back and preserved and count_before == 5 and count_after == 0 and repeated_count == 5 and html_preserved and uncertain_marked,
            "baseline_commit": baseline, "synthetic_legacy_rows": {name: len(rows) for name, rows in originals.items()},
            "original_columns_unchanged": preserved, "first_backfill": count_before, "repeat_backfill": count_after,
            "interrupted_schema_transaction_rolls_back": rolled_back,
            "repeat_migration_version_count": repeated_count, "delivered_html_preserved": html_preserved,
            "reconstructed_materials_explicitly_uncertain": uncertain_marked, "elapsed_seconds": round(time.perf_counter()-started,3)}
    finally:
        reset_guards()
        if created:
            if not re.fullmatch(r"career_migration_probe_[a-f0-9]{32}", schema):
                raise RuntimeError("Refusing to drop an unverified schema")
            with psycopg.connect(config.DATABASE_URL, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
                assert admin.execute("SELECT COUNT(*) FROM pg_namespace WHERE nspname=%s", (schema,)).fetchone()[0] == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="ea39ae93")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run(args.baseline)
    report["schema_cleanup"] = "verified"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(0 if report["ok"] else 1)
