"""Attendance worker recovery and payment fences, using a disposable SQLite DB.

No network, production accounts, or model requests are made by this module.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

from classroom_app.db.connection import LanShareSQLiteConnection
from classroom_app.services import ai_durable_job_service as durable
from classroom_app.services import attendance_report_jobs as bridge
from classroom_app.services import attendance_report_parser_service as parser
from classroom_app.services import durable_process_job_worker as worker
from classroom_app.services.attendance_report_parser_service import AttendanceProcessingHalted
from tests import test_attendance_report_service as lifecycle_fixture


class AttendanceReportJobTests(unittest.TestCase):
    # Reuse the small, synthetic lifecycle fixture without inheriting its tests.
    _binding = lifecycle_fixture.AttendanceReportServiceTests._binding
    _export = lifecycle_fixture.AttendanceReportServiceTests._export
    _claim = lifecycle_fixture.AttendanceReportServiceTests._claim
    _cached = lifecycle_fixture.AttendanceReportServiceTests._cached
    _result = lifecycle_fixture.AttendanceReportServiceTests._result

    def setUp(self):
        lifecycle_fixture.AttendanceReportServiceTests.setUp(self)
        self.db_path = Path(self.directory.name) / "jobs.sqlite"
        with self._connect() as copy:
            self.conn.backup(copy)
        self.conn.close()
        self.conn = self._connect()
        self.addCleanup(self.conn.close)
        patch.object(bridge, "get_db_connection", side_effect=self._connect).start()
        patch.object(bridge, "get_configured_db_engine", return_value="sqlite").start()
        patch.object(durable, "get_db_connection", side_effect=self._connect).start()
        patch.object(worker, "get_db_connection", side_effect=self._connect).start()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, factory=LanShareSQLiteConnection, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _job(self, job_id):
        return dict(self.conn.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone())

    def _claimed_export(self):
        exported = self._export()
        self._claim(exported["job_id"])
        return self._job(exported["job_id"])

    def _set_job(self, job_id, **values):
        with self._connect() as conn:
            conn.execute("UPDATE ai_jobs SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?", (*values.values(), job_id))
            conn.commit()

    def _enqueue(self, key, owner=1, task="attendance_export", **values):
        with self._connect() as conn:
            row, _ = durable.create_ai_job(conn, task_type=task, dedupe_key=key, payload={"synthetic": True}, owner_role="teacher", owner_user_pk=owner, **values)
            conn.commit()
        return row

    def _lane(self, task="attendance_export", **kwargs):
        key = dict(worker.ATTENDANCE_LANES)[task]
        return durable.claim_due_ai_jobs(task_types=(task,), max_running=1, concurrency_lock_key=key, fair_owner=True, **kwargs)

    def test_expired_and_replaced_leases_never_send_ai(self):
        job = self._claimed_export()
        network = AsyncMock(return_value=({"answer": "synthetic"}, "test-model"))
        with patch.object(bridge, "_gateway_json", network):
            self._set_job(job["id"], lease_expires_at="2000-01-01T00:00:00")
            with self.assertRaises(AttendanceProcessingHalted):
                asyncio.run(bridge._durable_ai_gateway(job)("system", "prompt", teacher_id=1))
            self._claim(job["id"], "replacement")
            with self.assertRaises(AttendanceProcessingHalted):
                asyncio.run(bridge._durable_ai_gateway(job)("system", "prompt", teacher_id=1))
        network.assert_not_awaited()
        self.assertNotIn("attendance_ai_batches", json.loads(self._job(job["id"])["payload_json"]))

    def test_pending_call_survives_restart_without_repayment(self):
        job = self._claimed_export()
        network = AsyncMock(side_effect=TimeoutError("synthetic uncertain delivery"))
        with patch.object(bridge, "_gateway_json", network):
            with self.assertRaises(AttendanceProcessingHalted):
                asyncio.run(bridge._durable_ai_gateway(job)("system", "prompt", teacher_id=1))
            self._claim(job["id"], "replacement")
            with self.assertRaisesRegex(RuntimeError, "不确定"):
                asyncio.run(bridge._durable_ai_gateway(self._job(job["id"]))("system", "prompt", teacher_id=1))
        self.assertEqual(network.await_count, 1)
        batches = json.loads(self._job(job["id"])["payload_json"])["attendance_ai_batches"]
        self.assertEqual([item["state"] for item in batches.values()], ["pending"])

    def test_uncertain_transport_stops_remaining_paid_batches(self):
        job = self._claimed_export()
        network = AsyncMock(side_effect=TimeoutError("synthetic uncertain delivery"))
        with patch.object(bridge, "_gateway_json", network):
            with self.assertRaises(AttendanceProcessingHalted):
                asyncio.run(bridge._durable_ai_gateway(job)("system", "prompt", teacher_id=1))
        self.assertEqual(network.await_count, 1)

    def test_parser_propagates_halt_before_later_visual_pages(self):
        extracted = {"missing_pages": [1, 2], "page_meta": [{"width": 100, "height": 100}] * 2}
        call = AsyncMock(side_effect=AttendanceProcessingHalted("synthetic halt"))
        image = Mock(return_value="data:image/png;base64,c3ludGhldGlj")
        with patch.object(parser, "_extract_grid_pages", return_value=extracted), patch.object(parser, "_page_image", image):
            with self.assertRaises(AttendanceProcessingHalted):
                asyncio.run(parser.analyze_attendance_pdf("synthetic.pdf", teacher_id=1, ai_chat=call))
        self.assertEqual(call.await_count, 1)
        self.assertEqual(image.call_count, 1)

    def test_cancelled_ai_request_keeps_lane_until_upstream_finishes(self):
        _, parsed = self._cached()
        job = self._lane(task="attendance_parse")[0]
        self.assertEqual(job["id"], parsed["job_id"])
        self._enqueue("second-parse", owner=2, task="attendance_parse")
        claims_while_inflight = []

        async def in_flight(*args, **kwargs):
            with self._connect() as conn:
                durable.cancel_ai_job_by_id(conn, job["id"])
                conn.commit()
            claims_while_inflight.extend(self._lane(task="attendance_parse"))
            return {"answer": "synthetic"}, "test-model"

        async def dispatch(_job):
            await bridge._durable_ai_gateway(_job)("system", "prompt", teacher_id=1)
            return {"completed": True}

        with patch.object(bridge, "_gateway_json", AsyncMock(side_effect=in_flight)), patch.object(bridge, "dispatch_attendance_job", AsyncMock(side_effect=dispatch)):
            asyncio.run(worker._execute(job))
        self.assertEqual(claims_while_inflight, [])

    def test_cancelled_export_keeps_lane_until_source_request_finishes(self):
        job = self._claimed_export()
        self._enqueue("second-export", owner=2)
        original = Path(self.directory.name) / "download.pdf"
        original.write_bytes(b"%PDF-1.7\nsynthetic-cancel-test\n")
        claims_while_inflight = []

        async def in_flight(**kwargs):
            with self._connect() as conn:
                durable.cancel_ai_job_by_id(conn, job["id"])
                conn.commit()
            claims_while_inflight.extend(self._lane())
            return {"pdf_file": original, "page_count": 1, "filename": "synthetic.pdf", "request_manifest": {}, "checkin_manifest": {}}

        with patch.object(bridge, "fetch_attendance_source_snapshot", AsyncMock(side_effect=in_flight)), patch.object(bridge, "load_source_access", return_value={}):
            asyncio.run(worker._execute(job))
        self.assertEqual(claims_while_inflight, [])

    def test_completed_worker_releases_capacity_and_publishes_durable_result(self):
        job = self._claimed_export()
        with patch.object(bridge, "dispatch_attendance_job", AsyncMock(return_value={"completed": True, "report_id": 1})):
            asyncio.run(worker._execute(job))
        current = self._job(job["id"])
        self.assertEqual(current["status"], "succeeded")
        self.assertIsNotNone(current["result_id"])
        self.assertIsNone(current["capacity_reserved_until"])

    def test_source_stream_timeout_keeps_capacity_for_uncertain_upstream(self):
        job = self._claimed_export()
        with patch.object(bridge, "fetch_attendance_source_snapshot", AsyncMock(side_effect=httpx.ReadTimeout("synthetic stream timeout"))):
            asyncio.run(worker._execute(job))
        current = self._job(job["id"])
        self.assertEqual(current["status"], "retry_wait")
        self.assertIsNotNone(current["capacity_reserved_until"])

    def test_heartbeat_failure_stops_attendance_work(self):
        job = self._claimed_export()

        async def tick(awaitable, *, timeout):
            awaitable.close()
            raise asyncio.TimeoutError

        async def scenario():
            child = asyncio.create_task(asyncio.sleep(60))
            try:
                with patch.object(worker.asyncio, "wait_for", side_effect=tick), patch.object(worker, "renew_ai_job_lease", side_effect=sqlite3.OperationalError("synthetic connection failure")):
                    await worker._lease_heartbeat(job, asyncio.Event(), {"task": child}, {"until": "synthetic"})
                await asyncio.sleep(0)
                self.assertTrue(child.cancelled())
            finally:
                child.cancel()
                await asyncio.gather(child, return_exceptions=True)

        asyncio.run(scenario())

    def test_heartbeat_cancellation_does_not_cancel_worker_and_keeps_uncertain_capacity(self):
        job = self._claimed_export()

        async def scenario():
            started = asyncio.Event()

            async def dispatch(_job):
                started.set()
                await asyncio.Future()

            async def heartbeat(_job, stop, attendance_work, capacity):
                await started.wait()
                attendance_work["task"].cancel()

            with patch.object(bridge, "dispatch_attendance_job", AsyncMock(side_effect=dispatch)), patch.object(worker, "_lease_heartbeat", side_effect=heartbeat):
                await worker._execute(job)
            self.assertEqual(asyncio.current_task().cancelling(), 0)

        asyncio.run(scenario())
        current = self._job(job["id"])
        self.assertIsNotNone(current["capacity_reserved_until"])
        self.assertEqual(current["status"], "retry_wait")

    def test_completed_checkpoint_reuses_response_and_budget_is_durable(self):
        job = self._claimed_export()
        response = ({"cells": [{"row_index": 1, "column_index": 1}]}, "synthetic-model")
        network = AsyncMock(return_value=response)
        original_hash = job["payload_hash"]
        with patch.object(bridge, "_gateway_json", network), patch.object(bridge, "MAX_AI_CALLS", 2):
            self.assertEqual(asyncio.run(bridge._durable_ai_gateway(job)("system", "a", teacher_id=1)), response)
            self._claim(job["id"], "replacement")
            resumed = self._job(job["id"])
            self.assertEqual(asyncio.run(bridge._durable_ai_gateway(resumed)("system", "a", teacher_id=1)), response)
            asyncio.run(bridge._durable_ai_gateway(resumed)("system", "b", teacher_id=1))
            with self.assertRaises(AttendanceProcessingHalted):
                asyncio.run(bridge._durable_ai_gateway(resumed)("system", "c", teacher_id=1))
        self.assertEqual(network.await_count, 2)
        self.assertEqual(self._job(job["id"])["payload_hash"], original_hash)

    def test_lease_lost_after_send_keeps_uncertain_reservation(self):
        job = self._claimed_export()

        async def lose_lease(*args, **kwargs):
            self._set_job(job["id"], lease_expires_at="2000-01-01T00:00:00")
            return {"answer": "synthetic"}, "test-model"

        network = AsyncMock(side_effect=lose_lease)
        with patch.object(bridge, "_gateway_json", network):
            with self.assertRaises(AttendanceProcessingHalted):
                asyncio.run(bridge._durable_ai_gateway(job)("system", "prompt", teacher_id=1))
            self._claim(job["id"], "replacement")
            with self.assertRaisesRegex(RuntimeError, "不确定"):
                asyncio.run(bridge._durable_ai_gateway(self._job(job["id"]))("system", "prompt", teacher_id=1))
        self.assertEqual(network.await_count, 1)

    def test_competing_identical_ai_calls_send_once(self):
        job = self._claimed_export()
        network = AsyncMock(return_value=({"answer": "synthetic"}, "test-model"))

        async def competing():
            call = bridge._durable_ai_gateway(job)
            return await asyncio.gather(call("system", "prompt", teacher_id=1), call("system", "prompt", teacher_id=1), return_exceptions=True)

        with patch.object(bridge, "_gateway_json", network):
            results = asyncio.run(competing())
        self.assertEqual(network.await_count, 1)
        # The second caller either sees pending or reuses the completed response.
        self.assertTrue(any(isinstance(item, tuple) for item in results))
        self.assertTrue(all(isinstance(item, (tuple, RuntimeError)) for item in results))

    def test_restart_after_pdf_cache_does_not_export_or_enqueue_again(self):
        job = self._claimed_export()
        original = Path(self.directory.name) / "download.pdf"
        original.write_bytes(b"%PDF-1.7\nsynthetic-checkpoint-test\n")
        snapshot = {"pdf_file": original, "page_count": 1, "filename": "synthetic.pdf", "request_manifest": {}, "checkin_manifest": {}}
        network = AsyncMock(return_value=snapshot)
        with patch.object(bridge, "fetch_attendance_source_snapshot", network), patch.object(bridge, "load_source_access", return_value={}):
            published = asyncio.run(bridge.dispatch_attendance_job(job))
            # Durable result publication never happened; another worker owns the retry.
            self._claim(job["id"], "replacement")
            recovered = asyncio.run(bridge.dispatch_attendance_job(self._job(job["id"])))
        self.assertFalse(original.exists())
        self.assertEqual(network.await_count, 1)
        self.assertEqual(recovered["parse_run_id"], published["parse_run_id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM attendance_parse_runs").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ai_jobs WHERE task_type='attendance_parse'").fetchone()[0], 1)

    def test_restart_after_candidate_publish_keeps_rows_without_parse_or_credentials(self):
        _, parsed = self._cached()
        self._claim(parsed["job_id"])
        job = self._job(parsed["job_id"])
        analyze = AsyncMock(return_value=self._result())
        with patch.object(bridge, "analyze_attendance_pdf", analyze), patch.object(bridge, "load_source_access", side_effect=AssertionError("cached parsing must not read credentials")):
            published = asyncio.run(bridge.dispatch_attendance_job(job))
            before = [tuple(row) for row in self.conn.execute("SELECT * FROM attendance_report_cells ORDER BY id")]
            self._claim(job["id"], "replacement")
            recovered = asyncio.run(bridge.dispatch_attendance_job(self._job(job["id"])))
        self.assertEqual(analyze.await_count, 1)
        self.assertEqual(published["state"], "validated")
        self.assertEqual(recovered["state"], "validated")
        self.assertEqual(before, [tuple(row) for row in self.conn.execute("SELECT * FROM attendance_report_cells ORDER BY id")])

    def test_two_workers_share_one_lane_and_parse_has_separate_capacity(self):
        self._enqueue("export-a")
        self._enqueue("export-b", owner=2)
        parse_job = self._enqueue("parse-a", task="attendance_parse")
        barrier = threading.Barrier(2)

        def claim(index):
            barrier.wait()
            return self._lane(worker_id=f"synthetic-{index}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim, (1, 2)))
        self.assertEqual(sum(len(items) for items in claims), 1)
        self.assertEqual(self._lane(task="attendance_parse")[0]["id"], parse_job["id"])
        self.assertEqual(self._lane(), [])
        running = next(item for items in claims for item in items)
        self._set_job(running["id"], status="succeeded")
        self.assertEqual(len(self._lane()), 1)

    def test_lane_respects_inflight_capacity_after_lease_expiration(self):
        first = self._enqueue("export-a")
        self._enqueue("export-b", owner=2)
        self._lane()
        self._set_job(first["id"], lease_expires_at="2000-01-01T00:00:00", capacity_reserved_until="2999-01-01T00:00:00")
        self.assertEqual(self._lane(), [])
        self._set_job(first["id"], capacity_reserved_until=None)
        self.assertEqual(len(self._lane()), 1)

    def test_fair_owner_rotation_and_aged_backlog_priority(self):
        first = self._enqueue("owner-one-a")
        second = self._enqueue("owner-one-b")
        other = self._enqueue("owner-two-a", owner=2)
        self.assertEqual(self._lane()[0]["id"], first["id"])
        self._set_job(first["id"], status="succeeded")
        self.assertEqual(self._lane()[0]["id"], other["id"])
        self._set_job(other["id"], status="succeeded")
        self._set_job(second["id"], created_at="2000-01-01T00:00:00")
        self._enqueue("never-served-owner", owner=3)
        self.assertEqual(self._lane()[0]["id"], second["id"])

    def test_worker_rotates_base_export_parse_lanes(self):
        calls, executed = [], []

        async def scenario():
            stop = asyncio.Event()

            def claim(**kwargs):
                calls.append(kwargs)
                return [{"id": len(calls), "task_type": kwargs["task_types"][0]}]

            async def execute(job):
                executed.append(job["task_type"])
                if len(executed) == 6:
                    stop.set()

            with patch.object(worker, "claim_result_ready_ai_jobs", return_value=[]), patch.object(worker, "claim_due_ai_jobs", side_effect=claim), patch.object(worker, "_execute", side_effect=execute):
                await worker._worker_loop(0, stop)

        asyncio.run(scenario())
        self.assertEqual(executed, [worker.BASE_TASK_TYPES[0], "attendance_export", "attendance_parse"] * 2)
        for kwargs in calls:
            if kwargs["task_types"][0].startswith("attendance_"):
                self.assertEqual(kwargs["max_running"], 1)
                self.assertTrue(kwargs["fair_owner"])
                self.assertEqual(kwargs["concurrency_lock_key"], dict(worker.ATTENDANCE_LANES)[kwargs["task_types"][0]])

    def test_result_ready_attendance_finishes_without_business_redispatch(self):
        job = {"id": 1, "task_type": "attendance_parse", "lease_token": "synthetic", "payload_json": "{}"}
        mark = Mock()
        with patch.object(worker, "load_ai_job_result", return_value={"id": 9}), patch.object(worker, "mark_ai_job_succeeded", mark), patch.object(worker, "_ensure_target_completed", side_effect=AssertionError("not a document job")):
            asyncio.run(worker._finish_result_ready(job))
        mark.assert_called_once_with(1, 9, lease_token="synthetic")


if __name__ == "__main__":
    unittest.main()
