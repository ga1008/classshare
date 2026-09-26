"""课表编辑模式：本地草稿校验/装饰 + 教务草稿保存适配器（只保存、不提交）。"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx

from classroom_app.db import schema_schedule_availability, schema_schedule_editor
from classroom_app.services import academic_schedule_draft_push_service as push
from classroom_app.services import schedule_editor_service as editor


def lesson(key, *, week, weekday, sections, room="（知新楼B310）金融科技综合实验室", course="计算机网络原理",
           jxb="JXB-0003", extra=None):
    return {
        "event_key": key, "teaching_class_id": jxb, "teaching_class_name": f"{course}-0003", "course_name": course,
        "class_label": "计算机科学2606班", "weekday": weekday, "sections": list(sections), "section_label": "",
        "classroom": room, "actual_date": f"2026-09-{10 + week:02d}", "week_index": week, "counts_towards_total": True,
        **(extra or {}),
    }


def overview(lessons_by_week, *, max_week=19, editable=True):
    weeks = []
    for index in range(1, max_week + 1):
        rows = lessons_by_week.get(index, [])
        weeks.append({"week_index": index, "label": f"第{index}周", "is_current": index == 5, "lessons": rows,
                      "lesson_count": len(rows), "total_hours": sum(len(r["sections"]) for r in rows), "date_range_label": ""})
    return {
        "schedule_source": "academic" if editable else "smart", "has_data": True,
        "selected_term": {"year": "2026-2027", "term": "1", "max_week": max_week, "week1_monday": "2026-03-02", "focus_week": 5},
        "section_range": {"min": 1, "max": 11}, "weeks": weeks, "terms": [],
    }


class ScheduleEditorServiceTests(unittest.TestCase):
    def setUp(self):
        schema_schedule_editor.reset_schema_ready_for_tests()
        schema_schedule_availability.reset_schema_ready_for_tests()
        clock = patch.object(editor, "china_today", return_value=date(2026, 3, 1))
        clock.start()
        self.addCleanup(clock.stop)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        schema_schedule_editor.ensure_schedule_editor_schema(self.conn)
        self.a = lesson("ev-a", week=5, weekday=4, sections=(4, 5))
        self.b = lesson("ev-b", week=5, weekday=5, sections=(2, 3), course="Python程序设计", jxb="JXB-0004")
        self.c = lesson("ev-c", week=6, weekday=4, sections=(4, 5))
        self.overview = overview({5: [self.a, self.b], 6: [self.c]})

    def save(self, **payload):
        return editor.save_draft(self.conn, 1, self.overview, {"event_key": "ev-a", **payload})

    def test_move_within_week_records_original_and_proposed_with_dates(self):
        draft = self.save(week=5, weekday=2, start_section=6, reason="调休")
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["change_kind"], "move")
        self.assertEqual(draft["original"], {"week": 5, "weekday": 4, "sections": [4, 5], "date": "2026-09-15",
                                             "room": self.a["classroom"], "room_id": ""})
        self.assertEqual(draft["proposed"]["sections"], [6, 7])
        self.assertEqual(draft["proposed"]["date"], "2026-03-31")  # 第5周周二
        self.assertEqual(draft["proposed"]["room"], self.a["classroom"])
        self.assertEqual(draft["proposed_label"], "第5周 周二 第6-7节 · （知新楼B310）金融科技综合实验室")
        self.assertEqual(draft["reason"], "调休")

    def test_first_section_and_overflow_and_span_mismatch_are_rejected(self):
        with self.assertRaisesRegex(editor.ScheduleEditError, "早读"):
            self.save(week=5, weekday=2, start_section=1)
        with self.assertRaisesRegex(editor.ScheduleEditError, "超出"):
            self.save(week=5, weekday=2, start_section=11)
        with self.assertRaisesRegex(editor.ScheduleEditError, "数量必须一致"):
            self.save(week=5, weekday=2, sections=[6, 7, 8])
        with self.assertRaisesRegex(editor.ScheduleEditError, "周次须在"):
            self.save(week=20, weekday=2, start_section=6)
        with self.assertRaisesRegex(editor.ScheduleEditError, "没有变化"):
            self.save(week=5, weekday=4, start_section=4)
        # 两小节为单位：起始节只能是 2/4/6/8/10
        with self.assertRaisesRegex(editor.ScheduleEditError, "两小节"):
            self.save(week=5, weekday=2, start_section=3)
        # 已过去的日期不可放置（时钟固定在 2026-03-01，第 1 周周一 = 2026-03-02，往前一周即过去）
        with self.assertRaisesRegex(editor.ScheduleEditError, "已经过去"):
            with patch.object(editor, "china_today", return_value=date(2026, 4, 1)):
                self.save(week=5, weekday=2, start_section=6)  # 2026-03-31 < 04-01
        # 节假日不可放置：第 6 周周六 = 2026-04-11？不是；第 5 周周六 2026-04-04 清明节
        with self.assertRaisesRegex(editor.ScheduleEditError, "节假日"):
            self.save(week=5, weekday=6, start_section=6)
        # 已上过的课次不能调整
        with patch.object(editor, "china_today", return_value=date(2026, 12, 1)):
            with self.assertRaisesRegex(editor.ScheduleEditError, "已经上过"):
                self.save(week=5, weekday=2, start_section=6)

    def test_overlap_with_own_lesson_or_other_draft_is_rejected_but_vacated_slot_is_free(self):
        with self.assertRaisesRegex(editor.ScheduleEditError, "重叠.*Python"):
            self.save(week=5, weekday=5, start_section=2)
        # b moves away first; its old slot becomes available for a
        editor.save_draft(self.conn, 1, self.overview, {"event_key": "ev-b", "week": 5, "weekday": 1, "start_section": 8})
        self.save(week=5, weekday=5, start_section=2)
        # but a cannot land on b's new position
        with self.assertRaisesRegex(editor.ScheduleEditError, "已计划调至此处"):
            self.save(week=5, weekday=1, start_section=8)

    def test_cross_week_move_replaces_existing_draft_and_room_only_change_is_kind_room(self):
        first = self.save(week=5, weekday=2, start_section=6)
        second = self.save(week=7, weekday=1, start_section=2)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(editor.list_drafts(self.conn, 1, "2026-2027", "1")), 1)
        room_only = self.save(week=5, weekday=4, start_section=4, room="（大成楼C108）AI数智财务创新中心", room_id="130C108")
        self.assertEqual(room_only["change_kind"], "room")
        self.assertEqual(room_only["proposed"]["room_id"], "130C108")

    def test_decoration_marks_source_and_adds_ghost_in_target_week(self):
        self.save(week=7, weekday=1, start_section=2)
        payload = editor.build_editor_payload(self.conn, 1, self.overview)
        week5 = next(w for w in payload["overview"]["weeks"] if w["week_index"] == 5)
        week7 = next(w for w in payload["overview"]["weeks"] if w["week_index"] == 7)
        source = next(l for l in week5["lessons"] if l["event_key"] == "ev-a")
        self.assertEqual(source["edit_draft"]["status"], "draft")
        ghost = next(l for l in week7["lessons"] if l.get("edit_ghost"))
        self.assertEqual((ghost["weekday"], ghost["sections"], ghost["source_event_key"]), (1, [2, 3], "ev-a"))
        self.assertFalse(ghost["counts_towards_total"])
        self.assertEqual(week7["draft_count"], 1)
        self.assertTrue(payload["editable"])
        self.assertEqual(payload["rules"], {"min_section": 2, "max_section": 11, "max_week": 19, "pair_starts": [2, 4, 6, 8, 10], "pair_unit": 2})
        self.assertEqual(payload["today"], "2026-03-01")
        self.assertTrue(any(d["date"] == "2026-04-04" and d["kind"] == "holiday" for d in payload["calendar"]["days"]))
        self.assertIn("ttksq_cxTtksqIndex", payload["zf_entry_url"])

    def test_pushed_draft_is_locked_until_withdrawn(self):
        draft = self.save(week=5, weekday=2, start_section=6)
        editor.update_draft_remote_state(self.conn, draft["id"], status="pushed", remote_ttk_id="T", remote_detail_id="D", pushed=True)
        with self.assertRaisesRegex(editor.ScheduleEditError, "先「从教务撤回」"):
            self.save(week=5, weekday=3, start_section=6)
        with self.assertRaisesRegex(editor.ScheduleEditError, "先「从教务撤回」"):
            editor.delete_draft(self.conn, 1, draft["id"])
        self.assertTrue(editor.get_draft(self.conn, 1, draft["id"])["pushed_at"])

    def test_non_academic_source_and_pending_proposal_cannot_be_edited(self):
        with self.assertRaisesRegex(editor.ScheduleEditError, "同步教务课表"):
            editor.save_draft(self.conn, 1, overview({5: [self.a]}, editable=False), {"event_key": "ev-a", "week": 5, "weekday": 2, "start_section": 6})
        pending = lesson("ev-p", week=5, weekday=3, sections=(6, 7), extra={"counts_towards_total": False, "adjustment": {"phase": "pending"}})
        with self.assertRaisesRegex(editor.ScheduleEditError, "待审核"):
            editor.save_draft(self.conn, 1, overview({5: [pending]}), {"event_key": "ev-p", "week": 5, "weekday": 2, "start_section": 6})


FORM_PAGE = """
<html><body>
<form id="subForm">
<input type="hidden" id="jxb_id" name="jxb_id" value="JXB-0003"/>
<input type="hidden" id="xqh_id" name="xqh_id" value="1"/>
<input type="hidden" id="xnm" name="xnm" value="2026"/><input type="hidden" id="xqm" name="xqm" value="3"/>
<input type="hidden" id="kkbm_id" name="kkbm_id" value="0403"/><input type="hidden" id="xsxy2" name="xsxy2" value="04"/>
<input type="hidden" id="ttk_id" name="ttk_id" value="TTK-DRAFT"/><input type="hidden" id="skzdzc" name="skzdzc" value="17"/>
<input type="hidden" id="dqjc" name="dqjc" value="11"/><input type="hidden" id="sfzf" name="sfzf" value=""/>
<select id="tdlb" name="tdlb"><option value="01" spl_id="TKGL_TK">调课</option><option value="03" spl_id="TKGL_ZTK">停课</option></select>
</form>
<script>
 modelList = eval([{"select_id":"1","xqj":"4","zc":"65511","jcarr":"4,5","cd_id":"136B310","jxdd":"（知新楼B310）金融科技综合实验室","jgh_id":"JGH-1","jsxm":"张老师"},
   {"select_id":"3","xqj":"4","zc":"65536","jcarr":"8,9","cd_id":"130C108","jxdd":"（大成楼C108）AI数智财务创新中心","jgh_id":"JGH-1","jsxm":"张老师"},
   {"select_id":"4","xqj":"5","zc":"65535","jcarr":"2,3","cd_id":"136B310","jxdd":"（知新楼B310）金融科技综合实验室","jgh_id":"JGH-1","jsxm":"张老师"}]);
 tjModelList=eval();
 tkxxList = eval([{"select_id":1,"xqj":5,"zcarr":"5","jcarr":"2,3","cd_id":"136B310","ttkxx_id":"DETAIL-OLD"}]);
</script></body></html>
"""


class DraftPushAdapterTests(unittest.TestCase):
    def test_module_never_references_the_submit_endpoint(self):
        source = Path(push.__file__).read_text(encoding="utf-8")
        self.assertNotIn("tj" + "Ttksq", source)
        self.assertNotIn("cxUpdate" + "Tkyy", source)

    def test_bitmasks_follow_zf_week_and_section_encoding(self):
        self.assertEqual(push.week_bitmask(5), 16)
        self.assertEqual(push.week_bitmask([1, 2, 3]), 7)
        self.assertEqual(push.section_bitmask([4, 5]), 24)
        self.assertEqual(push.describe_conflict(6), "教师冲突，场地冲突")

    def test_parse_form_page_reads_ids_slots_and_existing_details(self):
        page = push.parse_form_page(FORM_PAGE)
        self.assertEqual((page["ttk_id"], page["jxb_id"], page["xnm"], page["xqm"], page["kkbm_id"]), ("TTK-DRAFT", "JXB-0003", "2026", "3", "0403"))
        self.assertEqual(len(page["slots"]), 3)
        self.assertEqual(page["existing_details"][0]["ttkxx_id"], "DETAIL-OLD")
        original = {"week": 6, "weekday": 4, "sections": [4, 5]}
        self.assertEqual(push.find_original_slot(page["slots"], original)["cd_id"], "136B310")
        self.assertIsNone(push.find_original_slot(page["slots"], {"week": 5, "weekday": 4, "sections": [4, 5]}))  # week 5 not in 65511
        self.assertIsNone(push.find_original_slot(page["slots"], {"week": 5, "weekday": 4, "sections": [8, 9]}))  # week 5 bit not in 65536
        self.assertIsNotNone(push.find_existing_detail(page["existing_details"], {"week": 5, "weekday": 5, "sections": [2, 3]}))
        with self.assertRaisesRegex(push.DraftPushError, "缺少字段"):
            push.parse_form_page("<html><input id='xnm' value='2026'></html>")

    def test_detail_form_mirrors_browser_payload(self):
        page = push.parse_form_page(FORM_PAGE)
        slot = page["slots"][0]
        draft = {"original": {"week": 5, "weekday": 4, "sections": [4, 5], "room": "x", "room_id": ""},
                 "proposed": {"week": 6, "weekday": 2, "sections": [6, 7], "room": "（大成楼C108）AI数智财务创新中心", "room_id": "130C108"},
                 "reason": "调休", "note": ""}
        fields = dict(push.build_detail_form(page, slot, draft))
        self.assertEqual(fields["tklxdm"], "01")
        self.assertEqual(fields["spl_id"], "TKGL_TK")
        self.assertEqual((fields["yzcd"], fields["yxqj"], fields["yjc"]), ("16", "4", "24"))
        self.assertEqual((fields["xzcd"], fields["xxqj"], fields["xjc"]), ("32", "2", "96"))
        self.assertEqual((fields["zcd"], fields["xqj"], fields["jc"]), (fields["xzcd"], fields["xxqj"], fields["xjc"]))
        self.assertEqual((fields["ycd_id"], fields["xcd_id"], fields["yjgh_id"], fields["xjgh_id"]), ("136B310", "130C108", "JGH-1", "JGH-1"))
        self.assertEqual(fields["tkyy"], "调休")
        self.assertEqual(fields["sfyxsgt"], "1")


class DraftPushFlowTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end push against a fake 教务 transport: view → conflict check → save."""

    def setUp(self):
        schema_schedule_editor.reset_schema_ready_for_tests()
        schema_schedule_availability.reset_schema_ready_for_tests()
        clock = patch.object(editor, "china_today", return_value=date(2026, 3, 1))
        clock.start()
        self.addCleanup(clock.stop)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        schema_schedule_editor.ensure_schedule_editor_schema(self.conn)
        base = overview({6: [lesson("ev-a", week=6, weekday=4, sections=(4, 5))]})
        self.draft = editor.save_draft(self.conn, 1, base, {"event_key": "ev-a", "week": 7, "weekday": 2, "start_section": 6, "reason": "调休"})
        self.conn.commit()
        self.calls: list[tuple[str, str, dict]] = []
        conn = self.conn

        @contextmanager
        def db():
            # The service closes the connection when the context ends; keep the
            # in-memory database alive across calls by only committing here.
            yield conn

        for target in (patch.object(push, "get_db_connection", db),
                       patch.object(push, "load_teacher_academic_access_method", return_value={"username": "u", "password": "p", "school_code": "gxufl"})):
            target.start()
            self.addCleanup(target.stop)
        self.addCleanup(self.conn.close)

    def fake_client(self, *, conflict_num=0, save_response=None):
        calls = self.calls

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            form = {}
            body = request.content.decode("utf-8", "ignore")
            for name in ("yzcd", "xzcd", "xjc", "ttk_id", "xcd_id", "sfctttk", "tkyy"):
                marker = f'name="{name}"'
                if marker in body:
                    tail = body.split(marker, 1)[1].split("\r\n\r\n", 1)[1]
                    form[name] = tail.split("\r\n", 1)[0]
            calls.append((request.method, path, form))
            if path.endswith("ttksq_cxTtksqIndex.html"):
                return httpx.Response(200, text="<div id='searchForm'></div>")
            if path.endswith("ttksq_cxTtksqView.html"):
                return httpx.Response(200, text=FORM_PAGE)
            if path.endswith("ttksq_cxConflictCtzt.html"):
                return httpx.Response(200, json={"conflictNum": conflict_num, "ctxxList": [{"x": 1}] if conflict_num else []})
            if path.endswith("ttksq_cxSaveTtksj.html"):
                return httpx.Response(200, json=save_response or {"bcName": "星期四第4-5节{5周}→星期二第6-7节{6周}", "ttkxx_id": "DETAIL-NEW"})
            if path.endswith("ttksq_scTtksqsj.html"):
                return httpx.Response(200, text="1")
            raise AssertionError(f"unexpected 教务 call {path}")

        @asynccontextmanager
        async def opener(_credential):
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(base_url="https://jwxt.gxufl.com", transport=transport) as client:
                yield client, type("Profile", (), {"school_code": "gxufl"})(), {"status": "verified"}

        return patch.object(push, "open_authenticated_academic_client", opener)

    async def test_push_saves_draft_detail_and_records_remote_ids(self):
        with self.fake_client():
            result = await push.push_drafts_to_academic_system(1, year="2026-2027", term="1")
        self.assertEqual(result["status"], "success", result)
        paths = [path for _method, path, _form in self.calls]
        self.assertEqual(paths, ["/tkgl/ttksq_cxTtksqIndex.html", "/tkgl/ttksq_cxTtksqView.html",
                                 "/tkgl/ttksq_cxConflictCtzt.html", "/tkgl/ttksq_cxSaveTtksj.html"])
        save_form = self.calls[-1][2]
        self.assertEqual((save_form["yzcd"], save_form["xzcd"], save_form["xjc"], save_form["ttk_id"], save_form["tkyy"]), ("32", "64", "96", "TTK-DRAFT", "调休"))
        self.assertNotIn("sfctttk", save_form)
        stored = editor.get_draft(self.conn, 1, self.draft["id"])
        self.assertEqual((stored["status"], stored["remote_ttk_id"], stored["remote_detail_id"]), ("pushed", "TTK-DRAFT", "DETAIL-NEW"))
        self.assertIn("提交申请", result["message"])

    async def test_conflict_stops_without_saving_unless_forced(self):
        with self.fake_client(conflict_num=4):
            result = await push.push_drafts_to_academic_system(1, year="2026-2027", term="1")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("/tkgl/ttksq_cxSaveTtksj.html", [p for _m, p, _f in self.calls])
        stored = editor.get_draft(self.conn, 1, self.draft["id"])
        self.assertEqual(stored["status"], "conflict")
        self.assertEqual(stored["remote_conflict"]["conflict_num"], 4)
        self.assertIn("场地冲突", stored["remote_message"])
        self.calls.clear()
        with self.fake_client(conflict_num=4):
            forced = await push.push_drafts_to_academic_system(1, year="2026-2027", term="1", force=True, force_note="已协调")
        self.assertEqual(forced["status"], "success", forced)
        self.assertEqual(self.calls[-1][2]["sfctttk"], "1")
        self.assertEqual(editor.get_draft(self.conn, 1, self.draft["id"])["status"], "pushed")

    async def test_hard_conflict_is_never_forced(self):
        with self.fake_client(conflict_num=8):
            result = await push.push_drafts_to_academic_system(1, year="2026-2027", term="1", force=True)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("/tkgl/ttksq_cxSaveTtksj.html", [p for _m, p, _f in self.calls])
        self.assertTrue(editor.get_draft(self.conn, 1, self.draft["id"])["remote_conflict"]["hard"])

    async def test_existing_remote_detail_is_linked_instead_of_duplicated(self):
        base = overview({5: [lesson("ev-b", week=5, weekday=5, sections=(2, 3))]})
        editor.save_draft(self.conn, 1, base, {"event_key": "ev-b", "week": 9, "weekday": 1, "start_section": 4})
        self.conn.commit()
        with self.fake_client():
            result = await push.push_drafts_to_academic_system(1, year="2026-2027", term="1")
        linked = next(r for r in result["results"] if r["original_label"].startswith("第5周 周五"))
        self.assertEqual((linked["status"], linked["detail_id"]), ("pushed", "DETAIL-OLD"))
        self.assertEqual(sum(1 for _m, p, _f in self.calls if p.endswith("ttksq_cxSaveTtksj.html")), 1)

    async def test_withdraw_deletes_remote_detail_and_unlocks_local_draft(self):
        editor.update_draft_remote_state(self.conn, self.draft["id"], status="pushed", remote_ttk_id="TTK-DRAFT", remote_detail_id="DETAIL-GONE", pushed=True)
        self.conn.commit()
        with self.fake_client():
            result = await push.withdraw_draft_from_academic_system(1, self.draft["id"])
        self.assertEqual(result["status"], "success", result)
        self.assertIn("/tkgl/ttksq_scTtksqsj.html", [p for _m, p, _f in self.calls])
        self.assertEqual(editor.get_draft(self.conn, 1, self.draft["id"])["status"], "draft")


if __name__ == "__main__":
    unittest.main()
