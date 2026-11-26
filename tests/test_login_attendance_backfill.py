from unittest import TestCase
from unittest.mock import AsyncMock, patch

from classroom_app.services import login_attendance_service as local
from classroom_app.services.login_attendance_access_service import record_access
import test_login_attendance_calendar as support


class BackfillTests(TestCase):
    database = support.TeacherCalendarTests.database
    get = support.TeacherCalendarTests.get

    def setUp(self):
        support.TeacherCalendarTests.setUp(self)
        self.url = self.path + "/sessions/21/backfill"

    def access(self, sid, offset, ip="192.0.2.4"):
        record_access(self.conn, student_id=sid, observed_at_ms=self.start_ms + offset,
                      ip_address=ip, source="authenticated_request")
        self.conn.commit()

    def preview(self):
        response = self.client.get(self.url)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        return response.json()

    def post(self, revision, **args):
        body = {"revision": revision, "idempotency_key": "teacher-backfill-1", "sync_school": False, **args}
        return self.client.post(self.url, headers=self.headers, json=body)

    def test_boundaries_positive_only_network_receipts_and_no_write_on_preview(self):
        self.access(1, 0)
        self.access(2, -1000)
        self.access(3, 90 * 60000)
        self.access(6, 45000)
        self.conn.executemany("INSERT INTO behavior_write_inbox VALUES(?,?,'student',?,?)",
            [(10, 4, "applied", "2026-11-08 09:00:00"), (11, 2, "applied", "2026-11-08 09:00:00"),
             (10, 5, "pending", "2026-11-08T09:00:00")])
        self.conn.commit()
        view = self.preview()
        rows = {r["student_id"]: r for r in view["records"]}
        self.assertEqual([1, 4], sorted(s for s,r in rows.items() if r["suggested"]))
        self.assertTrue(rows[2]["evidence"]["before_ms"])
        self.assertTrue(rows[3]["evidence"]["after_ms"])
        self.assertFalse(rows[6]["eligible"])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM login_attendance_rounds").fetchone())
        local.save_policy(self.conn,10,self.user,{"revision":0, "networks":["203.0.113.0/24"], "enforce_network":True})
        self.conn.commit()
        refreshed = self.preview()
        self.assertFalse(any(r["suggested"] for r in refreshed["records"]))
        self.assertEqual(409, self.post(view["revision"]).status_code)

    def test_replay_and_partial_register_preserve_decisions_and_evidence(self):
        self.access(1, 1000)
        rev = self.preview()["revision"]
        args = {"overrides": [{"student_id": 2, "status": "SICK_LEAVE"}], "reason": "课堂请假单"}
        self.assertEqual(422, self.post(rev, overrides=args["overrides"]).status_code)
        save = self.post(rev, **args)
        self.assertEqual(200, save.status_code, save.text)
        result = save.json()
        self.assertEqual((1,1,3),(result["automatic"],result["manual"],result["pending"]))
        self.assertEqual(result, self.post(rev, **args).json())
        self.assertEqual(409, self.post(rev).status_code)
        records = {r["student_id"]: r for r in local._records(self.conn,result["round_id"])}
        self.assertEqual(("PRESENT",0),(records[1]["status"],records[1]["manual_confirmed"]))
        self.assertEqual(self.start_ms+1000, records[1]["first_seen_ms"])
        self.assertEqual(("SICK_LEAVE",1),(records[2]["status"],records[2]["manual_confirmed"]))
        follow = self.preview()
        self.assertEqual({3,4,5}, {r["student_id"] for r in follow["records"] if r["eligible"]})
        self.assertEqual(422,self.post(follow["revision"],idempotency_key="different-key",**args).status_code)
        self.access(3, 1100)
        self.assertEqual(409,self.post(follow["revision"],idempotency_key="different-key").status_code)
        follow = self.post(self.preview()["revision"],idempotency_key="different-key").json()
        self.assertEqual(result["round_id"],follow["round_id"])

    def test_scope_policy_official_status_and_school_dispatch(self):
        self.access(1, 1000)
        support.TeacherCalendarTests.smart(self, students=((1,"SICK_LEAVE"),))
        self.access(2, 1000)
        view = self.preview()
        self.assertFalse(next(r for r in view["records"] if r["student_id"]==1)["eligible"])
        with patch("classroom_app.services.smart_classroom_reconciliation_service.reconcile", new_callable=AsyncMock,
                   return_value={"state":"location_required"}) as sync:
            result = self.post(view["revision"], sync_school=True).json()
            self.assertEqual("location_required", result["school"]["state"])
            sync.assert_awaited_once_with(self.user,10,21,None,result["school_key"])
        self.assertEqual(403, self.client.post(self.url, headers={"Origin":"https://different.test"},
            json={"revision":view["revision"], "idempotency_key":"other-key", "sync_school":False}).status_code)
        self.assertEqual(409,self.client.get(self.url.replace("/21/","/25/")).status_code)
        self.assertEqual(409,self.client.get(self.url.replace("/21/","/26/")).status_code)
        self.assertEqual(404,self.client.get(self.url.replace("/21/","/24/")).status_code)
        self.user["id"] = 8
        self.assertEqual(403,self.client.get(self.url).status_code)

    def test_second_confirmation_preserves_existing_normal_round_and_stale_roster(self):
        self.access(1, 1000)
        rev=self.preview()["revision"]
        self.conn.execute("UPDATE class_offering_sessions SET academic_section_text='4,5' WHERE id=21")
        self.conn.commit()
        self.assertEqual(409,self.post(rev).status_code)
        support.TeacherCalendarTests.create(self)
        self.assertEqual(409,self.client.get(self.url).status_code)

    def test_bounded_roster_query_and_preview_for_large_class(self):
        self.conn.executemany("INSERT INTO students(id,name,student_id_number,class_id,enrollment_status) VALUES(?,?,?,1,'active')",
                             [(i, f"Student {i}", f"T{i}",) for i in range(100,300)])
        self.conn.commit()
        queries=[]
        self.conn.set_trace_callback(queries.append)
        try:
            rows=self.preview()["records"]
        finally:
            self.conn.set_trace_callback(None)
        self.assertGreaterEqual(len(rows),200)
        self.assertEqual(1,sum("FROM login_attendance_access WHERE" in s for s in queries))
        self.assertEqual(1,sum("FROM behavior_write_inbox WHERE" in s for s in queries))

    def test_original_school_authorization_requires_the_same_register_version(self):
        from fastapi import HTTPException
        from classroom_app.services.smart_classroom_reconciliation_service import _check_backfill_version
        self.access(1, 1000)
        with patch("classroom_app.services.smart_classroom_reconciliation_service.reconcile", new_callable=AsyncMock,
                   return_value={"state":"location_required"}):
            result = self.post(self.preview()["revision"], sync_school=True).json()
        key = result["school_key"]
        _check_backfill_version(self.conn, self.user, 10, 21, key, None)
        with self.assertRaises(HTTPException):
            _check_backfill_version(self.conn, self.user, 10, 21, key, [1])
        follow = self.post(self.preview()["revision"], idempotency_key="second-decision",
                           overrides=[{"student_id":2,"status":"LATE"}],reason="课堂确认")
        self.assertEqual(200, follow.status_code, follow.text)
        with self.assertRaises(HTTPException):
            _check_backfill_version(self.conn, self.user, 10, 21, key, None)

    def test_fresh_school_leave_wins_over_backfilled_access(self):
        from classroom_app.services.login_attendance_school_register_service import reconcile_snapshot
        self.access(1, 1000)
        result = self.post(self.preview()["revision"]).json()
        support.TeacherCalendarTests.smart(self, students=((1,"SICK_LEAVE"),))
        snapshot = {"record":{"id":"synthetic-21-2026-11-08T08:30:00","isStop":True,
                              "updateTime":"2026-11-08T09:00:00"}, "students":{"S1":"SICK_LEAVE"}}
        imported = reconcile_snapshot(self.conn,self.user,10,21,snapshot)
        self.assertEqual([1],imported["imported_ids"])
        row = next(r for r in local._records(self.conn,result["round_id"]) if r["student_id"] == 1)
        self.assertEqual("SICK_LEAVE",local.record_status(row))
