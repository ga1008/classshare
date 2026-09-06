"""Privacy, sampling scope and no-database health reads for queue metrics."""
import json
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.services import career_job_metrics_service as metrics
from tests.test_career_lifecycle import fixture


class CareerJobMetricsTests(unittest.TestCase):
    def setUp(self):
        self.conn=fixture(); self.serial=0
        self.now=datetime(2026,9,6,12,tzinfo=timezone.utc)
        with metrics._snapshot_lock:
            metrics._snapshot={"available":False,"last_success_at":None,"last_refresh_error":"not_initialized"}
            metrics._last_success_monotonic=None

    def tearDown(self):
        self.conn.close()

    def job(self,kind="career_test",status="queued",attempt=0,error="",created="2026-09-06T11:00:00+00:00",started=None,finished=None):
        self.serial+=1
        cursor=self.conn.execute("""INSERT INTO ai_jobs(task_type,status,dedupe_key,attempt_count,last_error_code,
            created_at,started_at,finished_at,owner_user_pk,payload_json,last_error) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (kind,status,f"private-job-{self.serial}",attempt,error,created,started,finished,987654321,
             '{"body":"private-student-body"}',"private-provider-error"))
        return cursor.lastrowid

    def attempt(self,job_id,*,number=1,stage="execute",error="",started="2026-09-06T11:10:00+00:00",finished="2026-09-06T11:11:00+00:00"):
        self.conn.execute("""INSERT INTO ai_job_attempts(job_id,attempt_no,stage,status,error_code,error_message,started_at,finished_at)
            VALUES(?,?,?,'error',?,'private-attempt-body',?,?)""",(job_id,number,stage,error,started,finished))

    def collect(self,**kwargs):
        return metrics.collect_career_job_metrics(self.conn,task_types=("career_test",),now=self.now,**kwargs)

    def test_counts_errors_and_actual_attempt_durations_are_private_and_scoped(self):
        self.job(status="queued"); self.job(status="retry_wait",attempt=2)
        finished=self.job(status="succeeded",attempt=2,started="2026-09-06T11:05:00+00:00",finished="2026-09-06T11:20:00+00:00")
        self.attempt(finished,error="TimeoutError")
        self.attempt(finished,number=2,error="Student_987654321",finished="2026-09-06T11:12:00+00:00")
        self.attempt(finished,number=2,stage="apply",finished="2026-09-06T11:20:00+00:00")
        self.job(kind="teaching_only",status="running")
        state=self.collect(); serialized=json.dumps(state)
        for private in ("987654321","private-student-body","private-provider-error","private-attempt-body","private-job-","teaching_only"):
            self.assertNotIn(private,serialized)
        self.assertEqual(state["active"]["sample_count"],2)
        self.assertEqual(state["active"]["oldest_waiting_age_seconds_in_sample"],3600)
        self.assertEqual(state["recent_attempts"]["error_codes_in_sample"],{"TimeoutError":1,"other_error":1})
        self.assertEqual(state["recent_attempts"]["execute_duration"]["sample_count"],2)
        self.assertEqual(state["recent_attempts"]["execute_duration"]["maximum_ms"],120000)
        self.assertEqual(state["recent_jobs"]["first_start_queue_delay"]["p50_ms"],300000)

    def test_global_ledger_tail_empty_of_career_is_no_sample_not_success(self):
        job=self.job(status="succeeded");self.attempt(job)
        for _ in range(4):
            job=self.job(kind="teaching_only",status="succeeded");self.attempt(job)
        state=self.collect(recent_limit=3,attempt_limit=3)
        self.assertEqual(state["recent_jobs"]["career_rows_in_sample"],0)
        self.assertTrue(state["recent_jobs"]["ledger_sample_truncated"])
        self.assertEqual(state["recent_jobs"]["counts_by_status"],{})
        self.assertIsNone(state["recent_jobs"]["first_start_queue_delay"]["p95_ms"])
        self.assertEqual(state["recent_attempts"]["execute_duration"]["sample_count"],0)
        self.assertIsNone(state["recent_attempts"]["execute_duration"]["maximum_ms"])
        self.assertNotIn("success_rate",json.dumps(state))

    def test_active_truncation_reports_lower_bound_and_limits_read_rows(self):
        for _ in range(5):self.job()
        state=self.collect(active_limit=2)
        self.assertEqual(state["active"]["sample_count"],2)
        self.assertTrue(state["active"]["truncated"])
        self.assertTrue(state["active"]["counts_are_lower_bounds"])
        self.assertEqual(state["refresh"]["sql_statements"],3)

    def test_invalid_timestamps_and_unfinished_attempts_do_not_become_zero_duration(self):
        job=self.job(status="running",started="unknown");self.attempt(job,finished=None)
        state=self.collect()
        self.assertIsNone(state["recent_jobs"]["first_start_queue_delay"]["p50_ms"])
        self.assertIsNone(state["recent_attempts"]["execute_duration"]["maximum_ms"])

    def test_cached_health_never_opens_db_and_failed_refresh_keeps_last_good_data(self):
        self.job();self.conn.commit()
        @contextmanager
        def connect():yield self.conn
        with patch.object(metrics,"registered_student_career_handlers",return_value={"career_test":None}), \
             patch.object(metrics,"get_configured_db_engine",return_value="sqlite"):
            initial=metrics.refresh_career_job_metrics(connection_factory=connect)
        self.assertTrue(initial["available"]);self.assertFalse(initial["stale"])
        initial["active"]["sample_count"]=999
        with patch.object(metrics,"get_db_connection",side_effect=AssertionError("no DB on health")):
            for _ in range(1000):self.assertEqual(metrics.career_job_metrics_snapshot()["active"]["sample_count"],1)
            failed=metrics.refresh_career_job_metrics()
        self.assertTrue(failed["available"]);self.assertTrue(failed["stale"])
        self.assertEqual(failed["active"]["sample_count"],1)
        self.assertEqual(failed["last_refresh_error"],"metrics_refresh_failed")
        self.assertNotIn("no DB on health",json.dumps(failed))

    def test_collection_is_pure_select_and_does_not_register_or_initialize_schema(self):
        self.job();self.conn.commit();sql=[]
        self.conn.set_trace_callback(sql.append)
        with patch.object(metrics,"registered_student_career_handlers",return_value={"career_test":None}):
            metrics.collect_career_job_metrics(self.conn)
        self.assertTrue(sql)
        self.assertTrue(all(statement.lstrip().startswith(("SELECT","WITH")) for statement in sql))
        self.assertEqual(len(sql),6)


if __name__=="__main__":unittest.main()
