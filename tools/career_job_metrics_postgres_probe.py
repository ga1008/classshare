"""Synthetic local PostgreSQL validation of bounded career metrics and cached health.

Uses the existing localhost-only isolated schema fixture. Does not call an AI
provider or register a fake source/job in application tables.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

import psycopg
from classroom_app import config
from classroom_app.services import career_job_metrics_service as metrics
from tools.career_postgres_workflow_probe import isolated_career_postgres


def run():
    kind="career_metrics_probe"
    with isolated_career_postgres(students=2) as fixture:
        schema=fixture["schema"];connect=fixture["connect"]
        with connect() as conn:
            conn.executemany("""INSERT INTO ai_jobs(task_type,status,dedupe_key,attempt_count,created_at,owner_user_pk,payload_json,last_error)
                VALUES(?,?,?,?,?,?,?,?)""",[(kind,status,"private-career-"+status,attempt,"2026-09-06T11:00:00+00:00",7654321,
                '{"private":"student biography"}',"private provider body") for status,attempt in (("queued",0),("running",1),("retry_wait",2),("result_ready",1))])
            # Newer unrelated jobs dominate the bounded global tail. This must
            # produce no career latency sample, not an apparent perfect rate.
            conn.executemany("INSERT INTO ai_jobs(task_type,status,dedupe_key) VALUES('grading_probe','queued',?)",
                             [(f"private-grading-{i}",) for i in range(1100)])
            conn.execute("ANALYZE ai_jobs")
            conn.commit()
        with connect() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            empty=metrics.collect_career_job_metrics(conn,task_types=(kind,))
            assert empty["active"]["sample_count"]==4
            assert empty["recent_jobs"]["career_rows_in_sample"]==0
            assert empty["recent_jobs"]["first_start_queue_delay"]["p95_ms"] is None
            assert empty["recent_jobs"]["ledger_sample_truncated"]
        with connect() as conn:
            terminal=conn.execute("""INSERT INTO ai_jobs(task_type,status,dedupe_key,attempt_count,created_at,started_at,finished_at)
                VALUES(?,'succeeded','private-completed-career',2,?,?,?) RETURNING id""",
                (kind,"2026-09-06T11:00:00+00:00","2026-09-06T11:05:00+00:00","2026-09-06T11:10:00+00:00")).fetchone()["id"]
            conn.executemany("""INSERT INTO ai_job_attempts(job_id,attempt_no,stage,status,error_code,error_message,started_at,finished_at)
                VALUES(?,?,'execute',?,?,'private attempt body',?,?)""",
                [(terminal,1,"error","TimeoutError","2026-09-06T11:05:00+00:00","2026-09-06T11:06:00+00:00"),
                 (terminal,2,"succeeded","","2026-09-06T11:08:00+00:00","2026-09-06T11:10:00+00:00")])
            conn.commit()
        with patch.object(metrics,"registered_student_career_handlers",return_value={kind:None}), \
             patch.object(metrics,"get_configured_db_engine",return_value="postgres"):
            t=time.perf_counter();state=metrics.refresh_career_job_metrics(connection_factory=connect);refresh_ms=(time.perf_counter()-t)*1000
        assert state["available"] and not state["stale"],state
        assert state["active"]["sample_count"]==4
        assert state["recent_jobs"]["first_start_queue_delay"]["p50_ms"]==300000
        assert state["recent_attempts"]["execute_duration"]["maximum_ms"]==120000
        assert state["recent_attempts"]["error_codes_in_sample"]=={"TimeoutError":1}
        encoded=json.dumps(state)
        for private in ("7654321","private-","biography","provider body","attempt body"):
            assert private not in encoded
        with connect() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            truncated=metrics.collect_career_job_metrics(conn,task_types=(kind,),active_limit=2)
            assert truncated["active"]["counts_are_lower_bounds"] and truncated["active"]["sample_count"]==2
            plan=conn.execute("""EXPLAIN (FORMAT JSON) SELECT task_type,status,attempt_count,created_at,available_at,started_at
                FROM ai_jobs WHERE task_type=? AND status='queued' ORDER BY created_at,id LIMIT 2501""",(kind,)).fetchone()[0]
        with patch.object(metrics,"get_db_connection",side_effect=AssertionError("health must not access PostgreSQL")):
            t=time.perf_counter()
            for _ in range(1000):assert metrics.career_job_metrics_snapshot()["active"]["sample_count"]==4
            health_ms=(time.perf_counter()-t)*1000
            failed=metrics.refresh_career_job_metrics()
            assert failed["available"] and failed["stale"] and failed["last_refresh_error"]=="metrics_refresh_failed"
        assert "idx_ai_jobs_type_status_created" in json.dumps(plan),plan
    with psycopg.connect(config.DATABASE_URL,connect_timeout=5) as admin:
        assert admin.execute("SELECT COUNT(*) FROM pg_namespace WHERE nspname=%s",(schema,)).fetchone()[0]==0
    return {"ok":True,"engine":"local isolated PostgreSQL","synthetic_only":True,"isolated_schema_removed":True,
            "checks":["readonly_repeatable_read_refresh","indexed_bounded_active_reads","all_domain_recent_tail_does_not_scan_history",
                      "no_career_sample_is_not_success","attempt_and_queue_timing_scope","active_truncation_lower_bound",
                      "no_private_content_or_student_ids","1000_health_reads_without_db","failed_refresh_preserves_stale_aggregate"],
            "measurement":{"refresh_including_connection_ms":round(refresh_ms,2),"collection":state["refresh"],
                           "1000_memory_reads_total_ms":round(health_ms,2),"snapshot_bytes":len(encoded.encode()),
                           "scope":"local synthetic fixture, one registered metric task type; not production latency SLA"}}


if __name__=="__main__":print(json.dumps(run(),ensure_ascii=False,indent=2))
