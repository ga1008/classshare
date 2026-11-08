"""Teacher ownership, window and IP-disclosure gates with a synthetic DB."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.dependencies import get_current_user
from classroom_app.routers import behavior
from classroom_app.services import classroom_activity_evidence_service as evidence


class ClassroomActivityEvidenceRouteTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE class_offerings(id INTEGER PRIMARY KEY, teacher_id INTEGER);
            INSERT INTO class_offerings VALUES(10,101),(11,102);
        """)
        self.conn.execute("PRAGMA query_only=ON")
        self.actor = {"id": 101, "role": "teacher"}
        self.now = datetime(2026, 11, 8, 4, 0, tzinfo=timezone.utc)
        test = self

        class UTCClock(datetime):
            @classmethod
            def now(cls, tz=None):
                # Model a UTC production host. An explicit classroom timezone
                # must yield identical query semantics on every deployment.
                return test.now.astimezone(tz) if tz else test.now

        self.enterContext(patch.object(behavior, "get_db_connection", self.connection))
        self.builder = self.enterContext(patch.object(evidence, "build_classroom_activity_evidence", side_effect=self.result))
        app = FastAPI()
        app.include_router(behavior.router)
        self.enterContext(patch.object(behavior, "datetime", UTCClock))
        app.dependency_overrides[get_current_user] = lambda: self.actor.copy()
        self.client = self.enterContext(TestClient(app))

    @contextmanager
    def connection(self):
        yield self.conn

    def result(self, conn, offering, start, end, *, include_login_ips=False):
        login = {"successful_count": 1}
        if include_login_ips:
            login["ip_addresses"] = ["192.0.2.10"]
        return {"students": [{"login": login}], "window": {"start": start.isoformat(), "end": end.isoformat()}}

    def request(self, *, offering=10, start="2026-11-08T01:00:00Z", end="2026-11-08T03:00:00Z", ips=None):
        params = {"window_start": start, "window_end": end}
        if ips is not None:
            params["include_login_ips"] = ips
        return self.client.get(f"/api/classrooms/{offering}/activity-evidence", params=params)

    def test_teacher_owner_can_read_and_ip_requires_explicit_flag(self):
        result = self.request()
        self.assertEqual(200, result.status_code)
        self.assertEqual("no-store", result.headers["cache-control"])
        self.assertNotIn("192.0.2.10", result.text)
        self.assertFalse(self.builder.call_args.kwargs["include_login_ips"])
        result = self.request(ips="true")
        self.assertEqual(200, result.status_code)
        self.assertTrue(self.builder.call_args.kwargs["include_login_ips"])
        self.assertEqual(["192.0.2.10"], result.json()["students"][0]["login"]["ip_addresses"])

    def test_non_teacher_cannot_read_evidence_even_with_ip_flag(self):
        for role in ("student", "wanderer", "admin"):
            with self.subTest(role=role):
                self.actor["role"] = role
                response = self.request(ips="true")
                self.assertEqual(403, response.status_code)
                self.assertNotIn("192.0.2.10", response.text)
        self.builder.assert_not_called()

    def test_other_teacher_and_missing_offering_cannot_disclose_evidence(self):
        for offering in (11, 999):
            with self.subTest(offering=offering):
                response = self.request(offering=offering, ips="true")
                self.assertEqual(403, response.status_code)
                self.assertNotIn("192.0.2.10", response.text)
        self.builder.assert_not_called()

    def test_all_windows_use_shanghai_clock_including_utc_host_and_naive_query(self):
        aware = self.request()
        self.assertEqual(200, aware.status_code)
        self.assertEqual("2026-11-08T09:00:00+08:00", aware.json()["window"]["start"])
        self.assertEqual("2026-11-08T11:00:00+08:00", aware.json()["window"]["end"])
        naive = self.request(start="2026-11-08T09:00:00", end="2026-11-08T11:00:00")
        self.assertEqual(200, naive.status_code)
        self.assertEqual(aware.json()["window"], naive.json()["window"])

    def test_out_of_range_empty_reversed_and_future_windows_fail_before_statistics(self):
        now = self.now
        invalid = [
            (now - timedelta(days=7, seconds=1), now - timedelta(days=7) + timedelta(hours=1)),
            (now - timedelta(hours=25), now),
            (now, now), (now, now - timedelta(seconds=1)),
            (now - timedelta(hours=1), now + timedelta(seconds=6)),
        ]
        for start, end in invalid:
            with self.subTest(start=start, end=end):
                response = self.request(start=start.isoformat(), end=end.isoformat())
                self.assertEqual(422, response.status_code)
        self.builder.assert_not_called()

    def test_exact_seven_day_and_twenty_four_hour_boundary_are_allowed(self):
        start = self.now - timedelta(days=7)
        response = self.request(start=start.isoformat(), end=(start + timedelta(days=1)).isoformat())
        self.assertEqual(200, response.status_code)
        self.builder.assert_called_once()

    def test_malformed_time_and_ip_boolean_fail_validation(self):
        self.assertEqual(422, self.request(start="not-a-time").status_code)
        self.assertEqual(422, self.request(ips="reveal-everything").status_code)
        self.builder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
