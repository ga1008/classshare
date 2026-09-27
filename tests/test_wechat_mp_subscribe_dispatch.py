"""批次 B：订阅消息持久化任务 + 发送 worker 的状态机与额度语义（隔离 SQLite）。"""

import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from classroom_app.services import wechat_mp_subscribe_dispatch_service as dispatch
from classroom_app.services import wechat_mp_subscribe_service as svc


class Clock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class DispatchTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lanshare-mp-dispatch-")
        self.path = Path(self.temp.name) / "synthetic.sqlite"
        with self.connection() as conn:
            conn.execute(
                "CREATE TABLE wechat_bindings (id INTEGER PRIMARY KEY, user_role TEXT, user_pk INTEGER,"
                " openid TEXT, status TEXT)"
            )
            conn.execute("INSERT INTO wechat_bindings VALUES (1, 'student', 7, 'openid-7', 'active')")
            conn.commit()
        self.clock = Clock(datetime(2026, 9, 27, 10, 0, 0))
        self.stack = ExitStack()
        svc.reset_schema_ready_for_tests()
        dispatch.reset_task_schema_ready_for_tests()
        for module in (svc, dispatch):
            self.stack.enter_context(patch.object(module, "get_configured_db_engine", return_value="sqlite"))
        self.stack.enter_context(patch.object(dispatch, "get_db_connection", self.connection))
        self.stack.enter_context(patch.object(dispatch, "_now", self.clock))
        self.sent: list[dict] = []

    def tearDown(self):
        self.stack.close()
        svc.reset_schema_ready_for_tests()
        dispatch.reset_task_schema_ready_for_tests()
        self.temp.cleanup()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def grant(self, remaining: int, key: str = "deadline", pk: int = 7) -> None:
        with self.connection() as conn:
            dispatch.ensure_mp_subscribe_task_schema(conn)
            conn.execute("DELETE FROM mp_subscribe_grants WHERE user_pk = ? AND template_key = ?", (pk, key))
            if remaining:
                conn.execute(
                    "INSERT INTO mp_subscribe_grants (user_role, user_pk, template_key, remaining, updated_at)"
                    " VALUES ('student', ?, ?, ?, '2026-09-27T09:00:00')",
                    (pk, key, remaining),
                )
            conn.commit()

    def remaining(self, key: str = "deadline", pk: int = 7) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT remaining FROM mp_subscribe_grants WHERE user_pk = ? AND template_key = ?", (pk, key)
            ).fetchone()
        return int(row["remaining"]) if row else 0

    def enqueue(self, event_key: str = "deadline:1:7:stage24", pk: int = 7, **kwargs) -> str:
        with self.connection() as conn:
            outcome = dispatch.enqueue_subscribe_message(
                conn, event_key=event_key, user_role="student", user_pk=pk, template_key="deadline",
                values={"thing10": "作业"}, page="pages/tasks/index", **kwargs,
            )
            conn.commit()
        return outcome

    def task(self, event_key: str = "deadline:1:7:stage24") -> dict:
        with self.connection() as conn:
            return dict(conn.execute("SELECT * FROM mp_subscribe_tasks WHERE event_key = ?", (event_key,)).fetchone())

    def transport(self, *responses):
        queue = list(responses)

        def send(token, body):
            self.sent.append({"token": token, "body": body})
            response = queue.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        return send

    def run_dispatch(self, transport, token=lambda force: "token-new" if force else "token"):
        return dispatch.dispatch_subscribe_tasks(transport=transport, token_provider=token, clock=self.clock)


class EnqueueTests(DispatchTestBase):
    def test_initial_state_reflects_binding_and_grant(self):
        self.assertEqual("no_binding", self.enqueue("deadline:1:99:stage24", pk=99))
        self.assertEqual("no_grant", self.enqueue())
        self.grant(1)
        self.assertEqual("queued", self.enqueue("deadline:2:7:stage24"))
        self.assertEqual("duplicate", self.enqueue("deadline:2:7:stage24"))

    def test_no_grant_event_is_rearmed_once_the_student_grants(self):
        self.assertEqual("no_grant", self.enqueue())
        # 同一事件在没有额度时重复入队不产生新行、也不唤醒。
        self.assertEqual("no_grant", self.enqueue())
        self.grant(1)
        self.assertEqual("rearmed", self.enqueue())
        self.assertEqual("pending", self.task()["state"])

    def test_enqueue_never_touches_the_network(self):
        self.grant(1)
        with patch.object(dispatch.httpx, "post", side_effect=AssertionError("network in business tx")):
            self.assertEqual("queued", self.enqueue())

    def test_graded_event_key_changes_only_with_result(self):
        first = dispatch.graded_event_key(5, 90, "好")
        self.assertEqual(first, dispatch.graded_event_key(5, 90, "好"))
        self.assertNotEqual(first, dispatch.graded_event_key(5, 91, "好"))
        self.assertNotEqual(first, dispatch.graded_event_key(5, 90, "改过"))


class DispatchTests(DispatchTestBase):
    def test_success_consumes_one_grant_and_sends_expected_payload(self):
        self.grant(2)
        self.enqueue()
        stats = self.run_dispatch(self.transport({"errcode": 0}))
        self.assertEqual({"succeeded": 1}, stats)
        self.assertEqual(1, self.remaining())
        self.assertEqual("openid-7", self.sent[0]["body"]["touser"])
        self.assertEqual({"thing10": {"value": "作业"}}, self.sent[0]["body"]["data"])
        self.assertEqual("succeeded", self.task()["state"])

    def test_two_tasks_cannot_share_the_last_grant(self):
        self.grant(1)
        self.enqueue("deadline:1:7:stage24")
        self.enqueue("deadline:2:7:stage24")
        stats = self.run_dispatch(self.transport({"errcode": 0}))
        self.assertEqual({"succeeded": 1, "no_grant": 1}, stats)
        self.assertEqual(1, len(self.sent))
        self.assertEqual(0, self.remaining())

    def test_user_rejection_clears_grants_and_is_rearmable(self):
        self.grant(3)
        self.enqueue()
        self.assertEqual({"rejected": 1}, self.run_dispatch(self.transport({"errcode": 43101})))
        self.assertEqual(0, self.remaining())
        self.grant(1)
        self.assertEqual("rearmed", self.enqueue())

    def test_temporary_error_refunds_and_backs_off(self):
        self.grant(1)
        self.enqueue()
        self.assertEqual({"pending": 1}, self.run_dispatch(self.transport({"errcode": 45009})))
        task = self.task()
        self.assertEqual(1, self.remaining())
        self.assertEqual(1, task["attempts"])
        self.assertEqual("2026-09-27T10:01:00", task["next_attempt_at"])
        # 退避期内不会被再次领取
        self.assertEqual({}, self.run_dispatch(self.transport()))

    def test_connection_failure_retries_until_attempts_exhausted(self):
        self.grant(1)
        self.enqueue()
        for _ in range(dispatch.MAX_ATTEMPTS):
            self.run_dispatch(self.transport(dispatch.SendNotAttempted("ConnectError")))
            self.clock.now += timedelta(hours=1)
        task = self.task()
        self.assertEqual("failed", task["state"])
        self.assertEqual(dispatch.MAX_ATTEMPTS, task["attempts"])
        self.assertEqual(1, self.remaining())

    def test_ambiguous_timeout_is_unknown_and_never_resent(self):
        self.grant(2)
        self.enqueue()
        self.assertEqual({"unknown": 1}, self.run_dispatch(self.transport(dispatch.SendOutcomeUnknown("ReadTimeout"))))
        self.assertEqual(1, self.remaining())
        self.clock.now += timedelta(hours=1)
        self.assertEqual({}, self.run_dispatch(self.transport()))
        self.assertEqual("duplicate", self.enqueue())

    def test_expired_token_is_refreshed_once(self):
        self.grant(1)
        self.enqueue()
        stats = self.run_dispatch(self.transport({"errcode": 40001}, {"errcode": 0}))
        self.assertEqual({"succeeded": 1}, stats)
        self.assertEqual(["token", "token-new"], [call["token"] for call in self.sent])

    def test_missing_credentials_refund_and_retry(self):
        self.grant(1)
        self.enqueue()
        self.assertEqual({"pending": 1}, self.run_dispatch(self.transport(), token=lambda force: ""))
        self.assertEqual(1, self.remaining())
        self.assertEqual("no_token", self.task()["last_error"])

    def test_permanent_error_refunds_and_stops(self):
        self.grant(1)
        self.enqueue()
        self.assertEqual({"failed": 1}, self.run_dispatch(self.transport({"errcode": 47003, "errmsg": "argument invalid"})))
        self.assertEqual(1, self.remaining())

    def test_deadline_reminder_expires_instead_of_arriving_late(self):
        self.grant(1)
        self.enqueue(expires_at="2026-09-27T09:59:00")
        self.assertEqual({"expired": 1}, self.run_dispatch(self.transport()))
        self.assertEqual([], self.sent)
        self.assertEqual(1, self.remaining())

    def test_unbound_after_enqueue_is_recorded_without_sending(self):
        self.grant(1)
        self.enqueue()
        with self.connection() as conn:
            conn.execute("UPDATE wechat_bindings SET status = 'revoked'")
            conn.commit()
        self.assertEqual({"no_binding": 1}, self.run_dispatch(self.transport()))
        self.assertEqual(1, self.remaining())

    def test_live_lease_blocks_a_second_worker_but_a_stale_one_is_reclaimed(self):
        self.grant(1)
        self.enqueue()
        claimed = dispatch._claim(10, "worker-a", self.clock())
        self.assertEqual(1, len(claimed))
        self.assertEqual([], dispatch._claim(10, "worker-b", self.clock()))
        self.clock.now += timedelta(seconds=dispatch.LEASE_SECONDS + 1)
        self.assertEqual(1, len(dispatch._claim(10, "worker-b", self.clock())))

    def test_group_summary_counts_by_outcome(self):
        self.grant(1)
        self.enqueue("nudge:1:7:2026-09-27", group_key="nudge:1:2026-09-27")
        self.enqueue("nudge:1:99:2026-09-27", pk=99, group_key="nudge:1:2026-09-27")
        self.run_dispatch(self.transport({"errcode": 0}))
        with self.connection() as conn:
            summary = dispatch.summarize_group(conn, "nudge:1:2026-09-27")
        self.assertEqual((2, 1, 1, 0), (summary["total"], summary["delivered"], summary["no_binding"], summary["in_flight"]))


class ReportIdempotencyTests(DispatchTestBase):
    def test_same_report_id_counts_once(self):
        with self.connection() as conn:
            self.assertTrue(dispatch.claim_subscribe_report(conn, report_id="s:7:r1", user_role="student", user_pk=7))
            self.assertFalse(dispatch.claim_subscribe_report(conn, report_id="s:7:r1", user_role="student", user_pk=7))
            svc.record_subscribe_grants(conn, user_role="student", user_pk=7, template_keys=["deadline"])
            self.assertEqual(
                {"deadline": 1, "nudge": 0, "graded": 0},
                dispatch.load_subscribe_balances(conn, user_role="student", user_pk=7),
            )


if __name__ == "__main__":
    unittest.main()
