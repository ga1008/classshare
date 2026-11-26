import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.database import get_db_connection
from classroom_app.dependencies import get_current_user
from classroom_app.routers.ui_parts.dashboard import router
from classroom_app.services import academic_course_exam_sync_service as courses
from classroom_app.services import academic_invigilation_sync_service as duties
from classroom_app.services import academic_exam_jobs as jobs
from classroom_app.services import dashboard_academic_exams as dashboard
from classroom_app.services import scheduled_task_service as scheduler
from classroom_app.services.academic_service import CHINA_TZ
from tests.sqlite_database_fixture import isolated_sqlite_database


class AcademicExamDashboardJobTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_sqlite_database())
        self.clock = datetime(2099, 11, 10, 13, 0, tzinfo=CHINA_TZ)
        for module in (jobs, courses, duties, dashboard):
            self.enterContext(patch.object(module, "china_now", return_value=self.clock))
        with get_db_connection() as db:
            db.execute("""INSERT INTO teachers(id,name,email,hashed_password) VALUES
                       (801,'Teacher','teacher@example.test','test'),(802,'Other','other@example.test','test')""")
            db.execute("""INSERT INTO academic_semesters(id,teacher_id,name,start_date,end_date) VALUES
                       (811,801,'2099-2100第一学期','2099-09-01','2100-01-31'),
                       (812,802,'2098-2099第一学期','2098-09-01','2099-01-31')""")
            db.execute("""INSERT INTO classes(id,name,created_by_teacher_id) VALUES
                       (820,'Primary',801),(821,'Linked',801),(822,'Unrelated',802)""")
            db.execute("INSERT INTO courses(id,name,academic_course_code,created_by_teacher_id) VALUES(831,'网络','NET',801)")
            db.execute("""INSERT INTO class_offerings(id,class_id,course_id,teacher_id,semester_id,academic_teaching_class_name)
                       VALUES(841,820,831,801,811,'NET-0001'),(842,821,831,802,812,'OLD')""")
            db.execute("INSERT INTO class_offering_class_links(offering_id,class_id,teacher_id) VALUES(841,821,801)")
            db.execute("""INSERT INTO students(id,name,student_id_number,class_id,email,enrollment_status) VALUES
                       (851,'Student','S851',821,'student@example.test','active'),
                       (852,'Other','S852',822,'other-s@example.test','active'),
                       (853,'Inactive','S853',821,'inactive@example.test','inactive')""")
            db.execute("""INSERT INTO teacher_email_configs(teacher_id,smtp_host,from_email,is_default)
                       VALUES(801,'smtp.example.test','sender@example.test',1)""")
            for teacher_id in (801,802):
                db.execute("""INSERT INTO teacher_academic_system_credentials
                           (teacher_id,school_code,school_name,adapter_key,base_url,login_url,username,password_encrypted)
                           VALUES(?,'gxufl','Example','test','https://example.test','/login','user','fake')""", (teacher_id,))
            db.commit()

    def panel(self, role, pk, *, refresh=False):
        with get_db_connection() as db:
            result = dashboard.load_exam_dashboard(db, {"role": role, "id": pk}, refresh=refresh)
            db.commit()
            return result

    def test_refresh_through_worker_to_personal_panels_and_channels(self):
        self.assertEqual(self.panel("student", 851, refresh=True)["sync"]["status"], "pending")
        for pk in (852, 853):
            self.assertFalse(self.panel("student", pk, refresh=True)["can_refresh"])
        self.panel("teacher", 801, refresh=True)
        with get_db_connection() as db:
            owners = db.execute("SELECT owner_user_pk FROM scheduled_tasks WHERE task_kind=?", (jobs.SYNC,)).fetchall()
            self.assertEqual([r[0] for r in owners], [801])

        queries = []
        def respond(request):
            if "doType=query" not in request.url.query.decode():
                return httpx.Response(200, json=[])
            queries.append(request.url.path)
            row = {"pkvalue": "school-row", "xnm": "2099", "xqm": "3", "kcmc": "NET/网络",
                   "kssj": "2099-12-05 9:00-11:00", "cdmc": "教学楼301", "jxbmc": "NET-0001"}
            return httpx.Response(200, json={"items": [row], "totalPage": 1, "totalResult": 1})
        @asynccontextmanager
        async def school(_credentials):
            async with httpx.AsyncClient(base_url="https://school.invalid", transport=httpx.MockTransport(respond)) as client:
                yield client, SimpleNamespace(school_name="Example"), {}

        for mod in (courses, duties):
            self.enterContext(patch.object(mod, "open_authenticated_academic_client", school))
            self.enterContext(patch.object(mod, "load_teacher_academic_access_method", return_value={"id": 1}))
        jobs.register_handlers()
        result = asyncio.run(scheduler.process_due_scheduled_tasks_once(limit=1))
        self.assertEqual(result["done"], 1)
        self.assertEqual(len(set(queries)), 2)
        student = self.panel("student", 851, refresh=True)
        self.assertEqual(student["sync"]["status"], "success")
        self.assertEqual([(r["kind"],r["date"],r["location"]) for r in student["items"]],
                         [("exam","2099-12-05","教学楼301")])
        self.assertCountEqual([r["kind"] for r in self.panel("teacher",801)["items"]], ["exam","invigilation"])
        for role, pk in (("student",852),("student",853),("teacher",802)):
            self.assertEqual(self.panel(role,pk)["items"], [])
        with get_db_connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM email_outbox").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM message_center_notifications").fetchone()[0], 3)
            db.execute("UPDATE teacher_academic_course_exam_items SET sync_status='stale'")
            db.execute("UPDATE teacher_academic_invigilation_items SET semester_id=812")
            db.commit()
        self.assertEqual(self.panel("teacher",801)["items"], [])

    def test_daily_catchup_lease_coalescing_and_retry_visibility(self):
        with get_db_connection() as db:
            jobs.ensure_daily(db)
            db.execute("UPDATE scheduled_tasks SET status='running',locked_by='existing' WHERE task_kind=?", (jobs.DAILY,))
            jobs.ensure_daily(db)
            timer = db.execute("SELECT * FROM scheduled_tasks WHERE task_kind=?", (jobs.DAILY,)).fetchone()
            self.assertEqual(timer["locked_by"], "existing")
            expected = self.clock.replace(hour=2).astimezone().replace(tzinfo=None)
            self.assertEqual(datetime.fromisoformat(timer["run_at"]), expected)
            db.commit()
        result = jobs.handle_daily({})
        self.assertEqual(result.next_run_at, expected + timedelta(days=1))
        with get_db_connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scheduled_tasks WHERE task_kind=?", (jobs.SYNC,)).fetchone()[0], 2)
            db.execute("UPDATE scheduled_tasks SET status='running',locked_by='owner' WHERE owner_user_pk=801")
            self.assertEqual(jobs.sync_state(db, [801], "student")["status"], "running")
            jobs.enqueue(db, [801])
            self.assertEqual(db.execute("SELECT locked_by FROM scheduled_tasks WHERE owner_user_pk=801").fetchone()[0], "owner")
            db.execute("UPDATE scheduled_tasks SET status='failed',last_error=? WHERE owner_user_pk=801", (jobs.PARTIAL,))
            self.assertEqual(jobs.sync_state(db,[801],"student")["status"], "partial_success")
            db.execute("UPDATE scheduled_tasks SET status='pending',attempt_count=1 WHERE owner_user_pk=801")
            self.assertIn("重试",jobs.sync_state(db,[801],"student")["message"])

    def test_split_rooms_respect_administrative_class_on_personal_panel(self):
        with get_db_connection() as db:
            db.executemany("""INSERT INTO teacher_academic_course_exam_items
                (teacher_id,semester_id,class_offering_id,exam_key,course_name,exam_date,
                 starts_at,ends_at,class_composition,location)
                VALUES(801,811,841,?,'网络','2099-12-05','2099-12-05T09:00','2099-12-05T11:00',?,?)""",
                [("one","Primary","301"),("two","Linked","302"),("three","Linked","303")])
            db.commit()
        rows = self.panel("student", 851)["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(next(r["value"] for r in rows[0]["detail_rows"] if r["label"] == "候选考场"), "302、303")
        self.assertNotIn("301", str(rows))
        self.assertEqual(len(self.panel("teacher", 801)["items"]), 3)

    def test_http_refresh_is_scoped_to_the_authenticated_user(self):
        app = FastAPI()
        app.include_router(router)
        actor = {"role": "student", "id": 851}
        app.dependency_overrides[get_current_user] = lambda: actor
        with TestClient(app) as client:
            response = client.post("/api/dashboard/academic-exams/refresh?teacher_id=802",
                                   json={"id": 802, "role": "teacher"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["sync"]["status"], "pending")
            self.assertIn("no-store", response.headers["cache-control"])
            actor.update(id=852)
            self.assertFalse(client.get("/api/dashboard/academic-exams").json()["can_refresh"])
            actor.update(role="guest")
            self.assertEqual(client.post("/api/dashboard/academic-exams/refresh").status_code, 403)
        with get_db_connection() as db:
            ids = [r[0] for r in db.execute("SELECT owner_user_pk FROM scheduled_tasks WHERE task_kind=?", (jobs.SYNC,))]
            self.assertEqual(ids, [801])
