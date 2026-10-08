"""可调时段：学生课表/教室占用缓存 → 课次可放置判定；教务班级课表适配器探测。"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from unittest.mock import patch

import httpx

from classroom_app.db import schema_schedule_availability, schema_schedule_editor
from classroom_app.services import academic_availability_sync_service as sync
from classroom_app.services import schedule_availability_service as avail
from classroom_app.services import schedule_editor_service as editor
from tests.test_schedule_editor import lesson, overview

ROSTER_DDL = """
CREATE TABLE teacher_academic_roster_memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT, teacher_id INTEGER, teaching_class_id TEXT, teaching_class_name TEXT,
    admin_class_code TEXT, admin_class_name TEXT, student_number TEXT
);
CREATE TABLE teacher_academic_teaching_places (
    id INTEGER PRIMARY KEY AUTOINCREMENT, teacher_id INTEGER, school_code TEXT DEFAULT 'gxufl', place_id TEXT,
    room_code TEXT, room_name TEXT, room_full_name TEXT, building_name TEXT, campus_name TEXT, seat_count INTEGER,
    is_schedulable INTEGER, room_type_name TEXT
);
INSERT INTO teacher_academic_teaching_places (teacher_id, place_id, room_code, room_name, room_full_name, building_name, campus_name, seat_count, is_schedulable, room_type_name)
VALUES (1, '136B310', 'B310', '（知新楼B310）金融科技综合实验室', '（知新楼B310）金融科技综合实验室', '知新楼', '五合校区', 62, 1, '实验室');
"""


def make_conn():
    schema_schedule_editor.reset_schema_ready_for_tests()
    schema_schedule_availability.reset_schema_ready_for_tests()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(ROSTER_DDL)
    schema_schedule_editor.ensure_schedule_editor_schema(conn)
    for student in range(3):
        conn.execute("INSERT INTO teacher_academic_roster_memberships (teacher_id, teaching_class_id, teaching_class_name, admin_class_code, admin_class_name, student_number) VALUES (1, 'JXB-0003', '计算机网络原理-0003', 'BJ-2606', '计算机科学2606班', ?)", (f"s{student}",))
    return conn


class AvailabilityServiceTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(editor, "china_today", return_value=date(2026, 3, 1))
        clock.start()
        self.addCleanup(clock.stop)
        self.conn = make_conn()
        self.addCleanup(self.conn.close)
        self.a = lesson("ev-a", week=5, weekday=4, sections=(4, 5))
        self.overview = overview({5: [self.a, lesson("ev-b", week=5, weekday=2, sections=(6, 7), course="Python程序设计", jxb="JXB-0004")]})
        # students of 2606班 have 高数 on Mon 2-3 in weeks 1-8 and the lesson itself (must be ignored)
        avail.replace_class_slots(self.conn, year="2026-2027", term="1", scope_kind="admin_class", scope_key="BJ-2606", scope_name="计算机科学2606班",
                                  slots=[{"weekday": 1, "sections": [2, 3], "weeks": list(range(1, 9)), "course_name": "高等数学", "teaching_class_name": "高等数学-0001"},
                                         {"weekday": 4, "sections": [4, 5], "weeks": [5], "course_name": "计算机网络原理", "teaching_class_name": "计算机网络原理-0003"}],
                                  source_path="/kbcx/test")
        avail.record_room_slot_check(self.conn, year="2026-2027", term="1", room_id="136B310", room_name="（知新楼B310）金融科技综合实验室",
                                     week=5, weekday=3, sections=[6, 7], status="busy", detail="实时查空：教室未在空闲列表")
        avail.record_room_slot_check(self.conn, year="2026-2027", term="1", room_id="136B310", room_name="（知新楼B310）金融科技综合实验室",
                                     week=5, weekday=5, sections=[6, 7], status="free")

    def test_matrix_marks_students_teacher_and_room_and_ignores_own_lesson(self):
        data = avail.build_lesson_availability(self.conn, 1, self.overview, "ev-a")
        self.assertTrue(data["found"])
        self.assertEqual(data["coverage"]["students"], "synced")
        self.assertEqual(data["room"]["id"], "136B310")  # resolved from the room name
        self.assertEqual(data["students"]["5"]["1"]["2"], "计算机科学2606班 高等数学")
        self.assertNotIn("4", data["students"]["5"])  # the lesson's own slot is not a clash
        self.assertEqual(data["teacher"]["5"]["2"]["6"], "本人 Python程序设计")
        self.assertEqual(data["room_busy_blocks"][0]["sections"], [6, 7])
        self.assertNotIn("3", data["room_busy"].get("5", {}))
        self.assertEqual(data["room_checked"]["5"]["5"]["6"], "free")

    def test_slot_verdicts(self):
        data = avail.build_lesson_availability(self.conn, 1, self.overview, "ev-a")
        self.assertEqual(avail.check_slot(data, week=5, weekday=1, sections=[2, 3])["level"], "block")
        self.assertEqual(avail.check_slot(data, week=5, weekday=2, sections=[6, 7])["level"], "block")
        self.assertEqual(avail.check_slot(data, week=5, weekday=3, sections=[6, 7])["level"], "room")
        self.assertEqual(avail.check_slot(data, week=5, weekday=5, sections=[6, 7])["level"], "ok")
        self.assertEqual(avail.check_slot(data, week=6, weekday=5, sections=[6, 7])["level"], "unknown")
        self.assertEqual(avail.check_slot(data, week=9, weekday=1, sections=[2, 3])["level"], "unknown")  # 高数 ends week 8

    def test_combined_busy_is_not_a_claim_about_each_section(self):
        data = avail.build_lesson_availability(self.conn, 1, self.overview, "ev-a")
        for sections in ([6], [7], [5, 6], [7, 8]):
            self.assertEqual(avail.check_slot(data, week=5, weekday=3, sections=sections)["level"], "unknown")
        self.assertEqual(avail.check_slot(data, week=5, weekday=3, sections=[5, 6, 7])["level"], "room")
        self.assertEqual(avail.check_slot(data, week=5, weekday=5, sections=[6])["level"], "ok")

    def test_rechecking_same_combination_updates_one_cache_row(self):
        avail.record_room_slot_check(self.conn, year="2026-2027", term="1", room_id="136B310", room_name="B310",
                                     week=5, weekday=3, sections=[7, 6, 6], status="free")
        count = self.conn.execute("SELECT COUNT(*) FROM academic_room_slot_checks WHERE week=5 AND weekday=3").fetchone()[0]
        self.assertEqual(count, 1)
        data = avail.build_lesson_availability(self.conn, 1, self.overview, "ev-a")
        self.assertEqual(data["room_busy_blocks"], [])
        self.assertEqual(avail.check_slot(data, week=5, weekday=3, sections=[6, 7])["level"], "ok")

    def test_save_draft_blocks_student_clash_and_flags_busy_room(self):
        with self.assertRaisesRegex(editor.ScheduleEditError, "学生有其他课程"):
            editor.save_draft(self.conn, 1, self.overview, {"event_key": "ev-a", "week": 5, "weekday": 1, "start_section": 2})
        busy = editor.save_draft(self.conn, 1, self.overview, {"event_key": "ev-a", "week": 5, "weekday": 3, "start_section": 6})
        self.assertEqual(busy["room_status"], "busy")
        self.assertEqual(busy["availability"]["level"], "room")
        free = editor.save_draft(self.conn, 1, self.overview, {"event_key": "ev-a", "week": 5, "weekday": 5, "start_section": 6})
        self.assertEqual(free["room_status"], "free")
        payload = editor.build_editor_payload(self.conn, 1, self.overview)
        self.assertEqual(payload["availability_sync"]["status"], "never")
        ghost = next(l for w in payload["overview"]["weeks"] for l in w["lessons"] if l.get("edit_ghost"))
        self.assertEqual(ghost["edit_room_status"], "free")

    def test_sync_state_roundtrip(self):
        avail.save_sync_state(self.conn, 1, year="2026-2027", term="1", status="success", message="ok", class_scope_count=2, synced=True)
        state = avail.load_sync_state(self.conn, 1, "2026-2027", "1")
        self.assertEqual((state["status"], state["class_scope_count"]), ("success", 2))
        self.assertTrue(state["synced_at"])


class AvailabilitySyncAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clock = patch.object(editor, "china_today", return_value=date(2026, 3, 1))
        clock.start()
        self.addCleanup(clock.stop)
        self.conn = make_conn()
        self.calls: list[str] = []
        conn = self.conn

        @contextmanager
        def db():
            yield conn

        for target in (patch.object(sync, "get_db_connection", db),
                       patch.object(sync, "load_teacher_academic_access_method", return_value={"username": "u", "password": "p", "school_code": "gxufl"}),
                       patch.object(sync, "_fetch_timetable_field_keys", return_value=[])):
            target.start()
            self.addCleanup(target.stop)
        self.addCleanup(self.conn.close)

    def fake_client(self, *, class_rows=True, room_ok=False):
        """Mimics the live 2026-09-27 contract: class rows via bjkbdy_cxBjkbdyTjkbList, timetable via
        bjkbdy_cxBjKb only when the whole row (xqh_id/njdm_id/zyh_id/tjkbzdm…) is posted, rooms via cdkbdy_cxCdKb."""
        calls = self.calls
        row = {"bh_id": "BJ-2606", "bj": "计算机科学2606班", "xqh_id": "2", "njdm_id": "2026", "zyh_id": "0422", "tjkbzdm": "T1",
               "tjkbzxsdm": "X1", "xnm": "2026", "xqm": "3", "xnmc": "2026-2027", "xqmmc": "1", "zymc": "计算机科学", "jgmc": "信息学院",
               "njmc": "2026", "xkrs": "40", "jsxm": "", "lxdh": "", "bh": "2026050106"}

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            body = request.content.decode()
            if request.url.path == "/kbdy/bjkbdy_cxBjkbdyTjkbList.html":
                items = [row] if class_rows and ("bh_id=BJ-2606" in body or "bh_id=&" in body or body.endswith("bh_id=")) else []
                return httpx.Response(200, json={"items": items, "totalResult": len(items)}, headers={"content-type": "application/json"})
            if request.url.path == "/kbdy/bjkbdy_cxBjKb.html":
                complete = all(f"{key}={value}" in body for key, value in (("bh_id", "BJ-2606"), ("xqh_id", "2"), ("njdm_id", "2026"), ("tjkbzdm", "T1")))
                items = [
                    {"kcmc": "高等数学", "jxbmc": "高等数学-0001", "xqj": "1", "jcs": "2-3", "zcd": "1-8周", "cdmc": "A101", "xm": "王老师", "kch": "M1"},
                    {"kcmc": "计算机网络原理", "jxbmc": "计算机网络原理-0003", "xqj": "4", "jcs": "4-5", "zcd": "1-3周,6-16周", "cdmc": "B310", "kch": "E040016B1"},
                ] if complete else []
                return httpx.Response(200, json={"kbList": items, "xsbjList": []}, headers={"content-type": "application/json"})
            if request.url.path == "/kbdy/cdkbdy_cxCdKb.html" and room_ok and "cd_id=136B310" in body:
                return httpx.Response(200, json={"kbList": [{"kcmc": "线性代数", "jxbmc": "线代-0002", "xqj": "3", "jcs": "6-7", "zcd": "1-16周", "cdmc": "B310", "kch": "M2"}]}, headers={"content-type": "application/json"})
            if request.url.path.endswith("Index.html"):
                return httpx.Response(200, text="<html></html>")
            return httpx.Response(404, text="not found")

        @asynccontextmanager
        async def opener(_credential):
            async with httpx.AsyncClient(base_url="https://jwxt.gxufl.com", transport=httpx.MockTransport(handler)) as client:
                yield client, type("Profile", (), {"school_code": "gxufl"})(), {"status": "verified"}

        return patch.object(sync, "open_authenticated_academic_client", opener)

    async def test_class_rows_then_whole_row_query_and_room_query_store_slots(self):
        base = overview({5: [lesson("ev-a", week=5, weekday=4, sections=(4, 5))]})
        with self.fake_client(room_ok=True):
            result = await sync.sync_availability_for_term(1, year="2026-2027", term="1", overview=base)
        self.assertEqual(result["status"], "success", result)
        self.assertEqual((result["class_scope_count"], result["class_slot_count"], result["room_count"], result["room_slot_count"]), (1, 2, 1, 1))
        self.assertEqual(self.calls.count("/kbdy/bjkbdy_cxBjkbdyTjkbList.html"), 1)  # located by bh_id, no full-list fallback
        self.assertIn("/kbdy/cdkbdy_cxCdKb.html", self.calls)
        rows = self.conn.execute("SELECT weekday, sections_json, weeks_json, course_name FROM academic_class_timetable_slots ORDER BY weekday").fetchall()
        self.assertEqual([tuple(r) for r in rows][0], (1, "[2, 3]", "[1, 2, 3, 4, 5, 6, 7, 8]", "高等数学"))
        data = avail.build_lesson_availability(self.conn, 1, base, "ev-a")
        self.assertEqual(data["coverage"]["room"], "timetable")
        self.assertEqual(avail.check_slot(data, week=5, weekday=3, sections=[6, 7])["level"], "room")
        self.assertEqual(avail.check_slot(data, week=5, weekday=1, sections=[2, 3])["level"], "block")
        self.assertEqual(avail.check_slot(data, week=5, weekday=5, sections=[6, 7])["level"], "ok")
        self.assertEqual(avail.load_sync_state(self.conn, 1, "2026-2027", "1")["status"], "success")

    async def test_missing_class_row_leaves_a_clear_status_and_tries_name_fallback(self):
        base = overview({5: [lesson("ev-a", week=5, weekday=4, sections=(4, 5))]})
        with self.fake_client(class_rows=False):
            result = await sync.sync_availability_for_term(1, year="2026-2027", term="1", overview=base)
        self.assertEqual(result["status"], "class_unavailable")
        self.assertIn("班级课表打印列表中未找到", result["message"])
        self.assertEqual(self.calls.count("/kbdy/bjkbdy_cxBjkbdyTjkbList.html"), 2)  # by bh_id, then full list by name
        self.assertNotIn("/kbdy/bjkbdy_cxBjKb.html", self.calls)
        self.assertTrue(all(src["status"] == "rejected" for src in result["sources"] if src.get("label", "").startswith("班级课表")))

    async def test_free_room_search_records_room_verdict(self):
        async def fake_query(_teacher_id, filters):
            if filters.get("cd_id"):
                return {"status": "success", "items": [], "total_count": 0, "school_code": "gxufl"}
            return {"status": "success", "items": [{"place_id": "130C108", "display_name": "（大成楼C108）AI数智财务创新中心", "seat_count": 80}], "total_count": 1, "school_code": "gxufl"}
        with patch.object(sync, "query_free_classrooms_from_academic_system", fake_query), patch.object(sync, "_free_room_target", return_value={
            "place_id": "136B310", "campus_id": "1", "school_code": "gxufl", "room_name": "B310",
        }):
            result = await sync.search_free_rooms(1, year="2026-2027", term="1", week=5, weekday=3, sections=[6, 7], room_id="136B310", room_name="（知新楼B310）金融科技综合实验室")
        self.assertEqual(result["room_status"], "busy")
        row = self.conn.execute("SELECT status FROM academic_room_slot_checks WHERE room_id='136B310' AND week=5 AND weekday=3").fetchone()
        self.assertEqual(row["status"], "busy")


if __name__ == "__main__":
    unittest.main()
