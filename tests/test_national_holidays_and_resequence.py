"""全国节假日/调休自动获取 + 课次重排（调课后剩余课次按日期重排、材料随序号不变）。"""
from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch

from classroom_app.db import schema_national_holidays
from classroom_app.services import national_holiday_service as holidays
from classroom_app.services import offering_session_resequence_service as reseq
from classroom_app.services.academic_service import build_holiday_lookup


HOLIDAY_CN_2026 = {
    "year": 2026,
    "days": [
        {"name": "元旦", "date": "2026-01-01", "isOffDay": True},
        {"name": "元旦", "date": "2026-01-02", "isOffDay": True},
        {"name": "元旦", "date": "2026-01-03", "isOffDay": True},
        {"name": "元旦", "date": "2026-01-04", "isOffDay": False},
        {"name": "国庆节", "date": "2026-09-20", "isOffDay": False},
        {"name": "国庆节", "date": "2026-10-01", "isOffDay": True},
        {"name": "国庆节", "date": "2026-10-02", "isOffDay": True},
        {"name": "国庆节", "date": "2026-10-03", "isOffDay": True},
        {"name": "国庆节", "date": "2026-10-04", "isOffDay": True},
        {"name": "国庆节", "date": "2026-10-05", "isOffDay": True},
        {"name": "国庆节", "date": "2026-10-06", "isOffDay": True},
        {"name": "国庆节", "date": "2026-10-07", "isOffDay": True},
        {"name": "国庆节", "date": "2026-10-10", "isOffDay": False},
    ],
}


def make_conn():
    schema_national_holidays.reset_schema_ready_for_tests()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema_national_holidays.ensure_national_holiday_schema(conn)
    return conn


class NationalHolidayInferenceTests(unittest.TestCase):
    def test_workdays_map_to_last_weekday_holidays_of_the_block(self):
        rows = {r["date"]: r for r in holidays.infer_makeup_mappings(HOLIDAY_CN_2026["days"])}
        # 元旦：周日 1-4 补周五 1-2（1-1 周四、1-2 周五，1-3 周六不是工作日）
        self.assertEqual((rows["2026-01-04"]["kind"], rows["2026-01-04"]["makeup_for_date"], rows["2026-01-04"]["makeup_for_weekday"]),
                         ("workday", "2026-01-02", "周五"))
        self.assertIn("补 1 月 2 日（周五）", rows["2026-01-04"]["label"])
        self.assertEqual(rows["2026-01-04"]["inferred"], 1)
        # 国庆：两个调休日按顺序补最后两个工作日假期 10-6（周二）、10-7（周三）
        self.assertEqual(rows["2026-09-20"]["makeup_for_date"], "2026-10-06")
        self.assertEqual(rows["2026-10-10"]["makeup_for_date"], "2026-10-07")
        self.assertEqual(rows["2026-10-01"]["kind"], "holiday")
        self.assertEqual(rows["2026-10-01"]["makeup_for_date"], "")

    def test_store_and_lookup_roundtrip_and_status(self):
        conn = make_conn()
        self.addCleanup(conn.close)
        payload = {**HOLIDAY_CN_2026, "source_url": "https://example.test/2026.json"}
        self.assertEqual(holidays.store_national_holidays(conn, 2026, payload), 13)
        holidays.store_national_holidays(conn, 2026, payload)  # 同一年重复写入不重复
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM national_holiday_days").fetchone()[0], 13)
        lookup = holidays.national_lookup_from_rows(holidays.load_national_holiday_rows(conn, [2026]))
        self.assertEqual(lookup["2026-10-10"]["makeup_for_weekday"], "周三")
        self.assertTrue(lookup["2026-10-10"]["inferred"])
        self.assertEqual(lookup["2026-10-01"]["kind"], "holiday")
        status = holidays.load_national_holiday_status(conn)
        self.assertEqual((status["years"][0]["year"], status["years"][0]["workdays"]), (2026, 3))

    def test_calendar_swaps_are_bounded_and_coloured(self):
        lookup = holidays.national_lookup_from_rows([
            {"date": "2026-09-20", "kind": "workday", "name": "国庆节", "label": "x", "makeup_for_date": "2026-10-06", "makeup_for_weekday": "周二", "inferred": 1},
            {"date": "2026-10-10", "kind": "workday", "name": "国庆节", "label": "y", "makeup_for_date": "2026-10-07", "makeup_for_weekday": "周三", "inferred": 1},
            {"date": "2026-10-01", "kind": "holiday", "name": "国庆节", "label": "国庆节"},
        ])
        swaps = holidays.calendar_swaps(lookup, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual([s["workday_date"] for s in swaps], ["2026-09-20"])
        swaps = holidays.calendar_swaps(lookup, "2026-09-01", "2027-01-10")
        self.assertEqual([(s["workday_date"], s["color_index"]) for s in swaps], [("2026-09-20", 0), ("2026-10-10", 1)])

    def test_build_holiday_lookup_overlays_feed_but_keeps_curated_mappings(self):
        feed = {
            "2026-02-14": {"label": "推断", "kind": "workday", "scope": "national", "source": "holiday-cn",
                           "makeup_for_date": "2026-02-20", "makeup_for_weekday": "周五", "inferred": True,
                           "verification_note": "补课星期由放假通知推断"},
            "2026-09-20": {"label": "推断", "kind": "workday", "scope": "national", "source": "holiday-cn",
                           "makeup_for_date": "2026-10-07", "makeup_for_weekday": "周三", "inferred": True},
            "2027-02-07": {"label": "春节调休上班", "kind": "workday", "scope": "national", "source": "holiday-cn",
                           "makeup_for_date": "2027-02-12", "makeup_for_weekday": "周五", "inferred": True},
            "2027-02-11": {"label": "春节", "kind": "holiday", "scope": "national", "source": "holiday-cn"},
        }
        with patch.object(holidays, "cached_national_lookup", return_value=feed):
            lookup = build_holiday_lookup([2026, 2027])
        self.assertEqual(lookup["2027-02-11"]["kind"], "holiday")          # 2027 内置表没有 → 来自自动获取
        self.assertEqual(lookup["2027-02-07"]["makeup_for_weekday"], "周五")
        self.assertEqual(lookup["2026-02-14"]["makeup_for_date"], "2026-02-20")  # 内置只知调休上班，补课星期取推断
        self.assertTrue(lookup["2026-02-14"]["inferred"])
        self.assertIn("推断", lookup["2026-02-14"]["verification_note"])
        self.assertEqual(lookup["2026-09-20"]["makeup_for_date"], "2026-10-06")  # 校内核验优先于推断（feed 说 10-07）
        self.assertNotIn("inferred", lookup["2026-09-20"])
        plain = build_holiday_lookup([2026], include_national_feed=False)
        self.assertNotIn("makeup_for_date", plain["2026-02-14"])

    def test_refresh_uses_first_mirror_that_answers_and_never_raises(self):
        conn = make_conn()
        self.addCleanup(conn.close)

        @contextmanager
        def db():
            yield conn

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class FakeClient:
            calls: list[str] = []

            def get(self, url):
                FakeClient.calls.append(url)
                if "2026.json" in url:
                    return FakeResponse(HOLIDAY_CN_2026)
                raise holidays.httpx.ConnectError("offline")

        with patch.object(holidays, "get_db_connection", db):
            summary = holidays.refresh_national_holidays([2026, 2027], client=FakeClient())
        self.assertEqual(summary["stored"], {"2026": 13})
        self.assertIn("2027", summary["failed"])
        self.assertEqual(sum(1 for u in FakeClient.calls if "2027.json" in u), len(holidays.HOLIDAY_CN_MIRRORS))


SESSION_SCHEMA = """
CREATE TABLE class_offerings(id INTEGER PRIMARY KEY, teacher_id INTEGER, semester_id INTEGER);
INSERT INTO class_offerings VALUES(10, 1, 1);
CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY, class_offering_id INTEGER, order_index INTEGER, title TEXT,
 session_date TEXT, weekday INTEGER, week_index INTEGER, academic_section_text TEXT, academic_location TEXT,
 slot_section_count INTEGER, schedule_status TEXT DEFAULT 'scheduled', schedule_metadata_json TEXT DEFAULT '{}', updated_at TEXT, learning_material_id INTEGER, UNIQUE(class_offering_id, order_index));
CREATE TABLE academic_schedule_session_bindings(teacher_id INTEGER, semester_id INTEGER, session_id INTEGER, current_json TEXT,
 evidence TEXT, updated_at TEXT, PRIMARY KEY(teacher_id, semester_id, session_id));

"""


def seed_sessions(conn, count=6, week1_monday=date(2026, 8, 31)):
    for index in range(1, count + 1):
        day = week1_monday + timedelta(days=7 * (index - 1) + 1)  # 每周周二
        conn.execute(
            "INSERT INTO class_offering_sessions(id, class_offering_id, order_index, title, session_date, weekday, week_index,"
            " academic_section_text, academic_location, slot_section_count) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (100 + index, 10, index, f"第{index}课", day.isoformat(), 1, index, "4-5", "B416", 2),
        )
        conn.execute("UPDATE class_offering_sessions SET learning_material_id=? WHERE id=?", (900 + index, 100 + index))
        conn.execute("INSERT INTO academic_schedule_session_bindings VALUES(1, 1, ?, ?, 'official_exact', '')",
                     (100 + index, json.dumps({"date": day.isoformat(), "week": index, "weekday": 2, "sections": [4, 5],
                                               "room": "B416", "schedule_status": "scheduled"})))


class ResequenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SESSION_SCHEMA)
        seed_sessions(self.conn)
        self.addCleanup(self.conn.close)

    def dates(self):
        return [(r["order_index"], r["session_date"], r["id"]) for r in self.conn.execute(
            "SELECT id, order_index, session_date FROM class_offering_sessions ORDER BY order_index")]

    def test_preview_keeps_physical_slots_and_numbers_all_dates(self):
        moves = {103: {"date": "2026-10-13", "sections": [4, 5], "room": "B416"}}
        plan = reseq.plan_offering_resequence(self.conn, 10, moves=moves, today=date(2026, 12, 1), week1_monday=date(2026, 8, 31))
        self.assertEqual((plan["frozen_count"], plan["movable_count"]), (0, 6))
        self.assertEqual({item["session_id"]: item["order_index"] for item in plan["changes"]},
                         {103: 6, 104: 3, 105: 4, 106: 5})
        self.assertEqual("2026-10-13", next(item for item in plan["changes"] if item["session_id"] == 103)["new"]["date"])
        self.assertEqual("2026-09-22", next(item for item in plan["changes"] if item["session_id"] == 104)["new"]["date"])
        with self.assertRaisesRegex(ValueError, "待审批"):
            reseq.apply_offering_resequence(self.conn, plan)
        self.assertEqual("2026-09-15", self.conn.execute("SELECT session_date FROM class_offering_sessions WHERE id=103").fetchone()[0])

    def test_apply_updates_ordinals_materials_without_exchanging_physical_dates(self):
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-10-13',week_index=7 WHERE id=103")
        baseline = [tuple(row) for row in self.conn.execute("SELECT * FROM academic_schedule_session_bindings ORDER BY session_id")]
        plan = reseq.plan_offering_resequence(self.conn, 10, today=date(2026, 12, 1))
        report = reseq.apply_offering_resequence(self.conn, plan, teacher_id=1, semester_id=1)
        self.assertEqual(report["applied_count"], 4)
        self.assertEqual(self.dates(), [(1, "2026-09-01", 101), (2, "2026-09-08", 102), (3, "2026-09-22", 104),
                                        (4, "2026-09-29", 105), (5, "2026-10-06", 106), (6, "2026-10-13", 103)])
        self.assertEqual([(101, 901), (102, 902), (103, 906), (104, 903), (105, 904), (106, 905)],
                         [tuple(row) for row in self.conn.execute("SELECT id,learning_material_id FROM class_offering_sessions ORDER BY id")])
        self.assertEqual(baseline, [tuple(row) for row in self.conn.execute("SELECT * FROM academic_schedule_session_bindings ORDER BY session_id")])
        again = reseq.plan_offering_resequence(self.conn, 10)
        self.assertEqual(again["changes"], [])

    def test_cancelled_sessions_keep_materials_and_move_to_non_teaching_tail(self):
        self.conn.execute("UPDATE class_offering_sessions SET schedule_status='cancelled' WHERE id=104")
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-10-13',week_index=7 WHERE id=103")
        plan = reseq.plan_offering_resequence(self.conn, 10)
        self.assertEqual(plan["active_count"], 5)
        self.assertEqual([(item["session_id"], item["order_index"], item["active"]) for item in plan["assignments"]],
                         [(101, 1, True), (102, 2, True), (105, 3, True), (106, 4, True), (103, 5, True), (104, 6, False)])
        reseq.apply_offering_resequence(self.conn, plan)
        self.assertEqual((904, "2026-09-22", "cancelled"), tuple(self.conn.execute(
            "SELECT learning_material_id,session_date,schedule_status FROM class_offering_sessions WHERE id=104").fetchone()))


if __name__ == "__main__":
    unittest.main()
