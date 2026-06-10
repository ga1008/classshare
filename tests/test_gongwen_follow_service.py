"""公文关注 (gongwen follow) — settings CRUD, matching, scan worker, notifications."""

import contextlib
import os
import sqlite3
import unittest

# 必须在导入 schema 模块前固定引擎，否则宿主机的 DB_ENGINE=postgres 会让
# DDL 走 postgres 方言（GENERATED ... AS IDENTITY）而在 sqlite 上报错。
os.environ["DB_ENGINE"] = "sqlite"
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import classroom_app.db.schema_gongwen as schema_gongwen
import classroom_app.services.gongwen_follow_service as follow
from classroom_app.db.schema_gongwen import ensure_gongwen_schema
from classroom_app.services.gongwen_follow_service import (
    build_document_match_text,
    count_unseen_follow_hits,
    get_teacher_follow_settings,
    list_teacher_follow_hits,
    load_follow_hits_for_documents,
    mark_follow_hit_seen,
    match_document_for_followers,
    match_keywords,
    save_teacher_follow_settings,
    scan_pending_follow_matches,
)

TEACHER_SCOPE = {"school_code": "sch", "school_name": "测试校区", "college": "", "department": ""}


def _days(delta: int) -> str:
    return (datetime.now() + timedelta(days=delta)).strftime("%Y-%m-%d 09:00:00")


class GongwenFollowTestBase(unittest.TestCase):
    def setUp(self):
        schema_gongwen._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_gongwen_schema(self.conn)

        @contextlib.contextmanager
        def _shared_conn():
            yield self.conn

        self._conn_patcher = patch.object(follow, "get_db_connection", _shared_conn)
        self._conn_patcher.start()
        self.addCleanup(self._conn_patcher.stop)
        self.addCleanup(self.conn.close)

    def _insert_document(self, *, title="关于教学比赛的通知", parsed_text="", publish_time=None,
                         parsed_status="done", reminder_status="none") -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO gongwen_documents
                (remote_id, attr_school_code, attr_level, openness, title, parsed_text,
                 publish_time, parsed_status, reminder_status)
            VALUES (?, 'sch', 'school', 'school', ?, ?, ?, ?, ?)
            """,
            (f"r{datetime.now().timestamp()}-{title}", title, parsed_text,
             publish_time or _days(-1), parsed_status, reminder_status),
        )
        return int(cursor.lastrowid)


class FollowSettingsTests(GongwenFollowTestBase):
    def test_save_and_get_roundtrip_with_dedupe(self):
        saved = save_teacher_follow_settings(
            self.conn, 9,
            items=["师范认证", " 师范认证 ", "教学比赛"],
            keywords=["实验室", "实验室", "安全"],
        )
        self.assertEqual(saved["items"], ["师范认证", "教学比赛"])
        self.assertEqual(saved["keywords"], ["实验室", "安全"])
        loaded = get_teacher_follow_settings(self.conn, 9)
        self.assertEqual(loaded["items"], ["师范认证", "教学比赛"])
        self.assertEqual(loaded["keywords"], ["实验室", "安全"])

    def test_save_updates_existing_row(self):
        save_teacher_follow_settings(self.conn, 9, items=["A"], keywords=[])
        save_teacher_follow_settings(self.conn, 9, items=["B"], keywords=["k"])
        row_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM teacher_gongwen_follow_settings WHERE teacher_id = 9"
        ).fetchone()["c"]
        self.assertEqual(row_count, 1)
        self.assertEqual(get_teacher_follow_settings(self.conn, 9)["items"], ["B"])

    def test_rejects_too_many_and_too_long_terms(self):
        with self.assertRaises(ValueError):
            save_teacher_follow_settings(self.conn, 9, items=[f"i{n}" for n in range(21)], keywords=[])
        with self.assertRaises(ValueError):
            save_teacher_follow_settings(self.conn, 9, items=[], keywords=["x" * 41])
        with self.assertRaises(ValueError):
            save_teacher_follow_settings(self.conn, 9, items="not-a-list", keywords=[])

    def test_empty_settings_returns_defaults(self):
        loaded = get_teacher_follow_settings(self.conn, 404)
        self.assertEqual(loaded, {"items": [], "keywords": [], "enabled": True, "updated_at": ""})


class KeywordMatchTests(unittest.TestCase):
    def test_match_keywords_case_insensitive_substring(self):
        text = "关于开展 AI 教学竞赛的通知\n各学院注意实验室安全。"
        self.assertEqual(match_keywords(text, ["ai", "实验室", "无关词"]), ["ai", "实验室"])
        self.assertEqual(match_keywords("", ["ai"]), [])

    def test_build_document_match_text_includes_parsed_fields(self):
        text = build_document_match_text({
            "title": "标题", "sn": "教学发〔2026〕1号", "parsed_summary": "摘要",
            "parsed_keywords": "关键词A", "parsed_text": "全文内容",
        })
        for fragment in ("标题", "教学发〔2026〕1号", "摘要", "关键词A", "全文内容"):
            self.assertIn(fragment, text)


class FollowMatchingTests(GongwenFollowTestBase):
    def setUp(self):
        super().setUp()
        scope_patcher = patch(
            "classroom_app.services.organization_scope_service.load_teacher_org_scope",
            return_value=dict(TEACHER_SCOPE),
        )
        scope_patcher.start()
        self.addCleanup(scope_patcher.stop)
        self.notify_patcher = patch(
            "classroom_app.services.message_center_service._insert_notification", return_value=1
        )
        self.notify_mock = self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_keyword_hit_creates_hit_and_notification(self):
        save_teacher_follow_settings(self.conn, 9, items=[], keywords=["实验室"])
        doc_id = self._insert_document(parsed_text="请各单位检查实验室安全。")
        with patch.object(follow, "ai_match_follow_items", new=AsyncMock(return_value={})):
            import asyncio

            result = asyncio.run(match_document_for_followers(doc_id))
        self.assertEqual(result["hits"], 1)
        self.assertEqual(self.notify_mock.call_count, 1)
        payload = self.notify_mock.call_args[0][1]
        self.assertEqual(payload["category"], "gongwen_follow")
        self.assertEqual(payload["recipient_user_pk"], 9)
        self.assertIn(f"doc={doc_id}", payload["link_url"])
        hits = load_follow_hits_for_documents(self.conn, 9, [doc_id])
        self.assertEqual(hits[doc_id]["matched_keywords"], ["实验室"])
        self.assertEqual(hits[doc_id]["match_type"], "keyword")

    def test_ai_item_hit_maps_back_to_teacher(self):
        save_teacher_follow_settings(self.conn, 9, items=["师范认证"], keywords=[])
        save_teacher_follow_settings(self.conn, 10, items=["运动会"], keywords=[])
        doc_id = self._insert_document(parsed_text="师范类专业认证工作部署。")
        ai_result = {"师范认证": "公文部署师范专业认证工作"}
        with patch.object(follow, "ai_match_follow_items", new=AsyncMock(return_value=ai_result)) as ai_mock:
            import asyncio

            result = asyncio.run(match_document_for_followers(doc_id))
        # union of both teachers' items goes into ONE AI call
        self.assertEqual(ai_mock.call_count, 1)
        self.assertIn("师范认证", ai_mock.call_args[0][1])
        self.assertIn("运动会", ai_mock.call_args[0][1])
        self.assertEqual(result["hits"], 1)  # only teacher 9 matched
        hits = load_follow_hits_for_documents(self.conn, 9, [doc_id])
        self.assertEqual(hits[doc_id]["matched_items"], ["师范认证"])
        self.assertEqual(hits[doc_id]["match_type"], "ai")
        self.assertEqual(load_follow_hits_for_documents(self.conn, 10, [doc_id]), {})

    def test_rerun_does_not_duplicate_notification(self):
        save_teacher_follow_settings(self.conn, 9, items=[], keywords=["实验室"])
        doc_id = self._insert_document(parsed_text="实验室例行检查。")
        import asyncio

        with patch.object(follow, "ai_match_follow_items", new=AsyncMock(return_value={})):
            asyncio.run(match_document_for_followers(doc_id))
            second = asyncio.run(match_document_for_followers(doc_id))
        self.assertEqual(second["hits"], 0)
        self.assertEqual(self.notify_mock.call_count, 1)

    def test_invisible_document_not_matched(self):
        save_teacher_follow_settings(self.conn, 9, items=[], keywords=["实验室"])
        doc_id = self._insert_document(parsed_text="实验室检查")
        self.conn.execute(
            "UPDATE gongwen_documents SET attr_school_code = 'other-campus' WHERE id = ?", (doc_id,)
        )
        import asyncio

        with patch.object(follow, "ai_match_follow_items", new=AsyncMock(return_value={})):
            result = asyncio.run(match_document_for_followers(doc_id))
        self.assertEqual(result["status"], "no_visible_followers")
        self.assertEqual(self.notify_mock.call_count, 0)

    def test_seen_tracking_roundtrip(self):
        save_teacher_follow_settings(self.conn, 9, items=[], keywords=["实验室"])
        doc_id = self._insert_document(parsed_text="实验室检查")
        import asyncio

        with patch.object(follow, "ai_match_follow_items", new=AsyncMock(return_value={})):
            asyncio.run(match_document_for_followers(doc_id))
        self.assertEqual(count_unseen_follow_hits(self.conn, 9), 1)
        recent = list_teacher_follow_hits(self.conn, 9)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["document_id"], doc_id)
        mark_follow_hit_seen(self.conn, 9, doc_id)
        self.assertEqual(count_unseen_follow_hits(self.conn, 9), 0)


class FollowScanWorkerTests(GongwenFollowTestBase):
    def setUp(self):
        super().setUp()
        scope_patcher = patch(
            "classroom_app.services.organization_scope_service.load_teacher_org_scope",
            return_value=dict(TEACHER_SCOPE),
        )
        scope_patcher.start()
        self.addCleanup(scope_patcher.stop)
        notify_patcher = patch(
            "classroom_app.services.message_center_service._insert_notification", return_value=1
        )
        notify_patcher.start()
        self.addCleanup(notify_patcher.stop)

    def _reminder_status(self, doc_id: int) -> str:
        return self.conn.execute(
            "SELECT reminder_status FROM gongwen_documents WHERE id = ?", (doc_id,)
        ).fetchone()["reminder_status"]

    def test_old_documents_marked_skipped_not_notified(self):
        save_teacher_follow_settings(self.conn, 9, items=[], keywords=["实验室"])
        old_id = self._insert_document(parsed_text="实验室", publish_time=_days(-60))
        new_id = self._insert_document(parsed_text="实验室安全检查", publish_time=_days(-1))
        import asyncio

        with patch.object(follow, "ai_match_follow_items", new=AsyncMock(return_value={})):
            result = asyncio.run(scan_pending_follow_matches())
        self.assertEqual(self._reminder_status(old_id), "skipped")
        self.assertEqual(self._reminder_status(new_id), "done")
        self.assertEqual(result["hits"], 1)
        self.assertEqual(count_unseen_follow_hits(self.conn, 9), 1)

    def test_unparsed_documents_left_pending(self):
        save_teacher_follow_settings(self.conn, 9, items=[], keywords=["实验室"])
        pending_id = self._insert_document(parsed_text="实验室", parsed_status="pending")
        import asyncio

        with patch.object(follow, "ai_match_follow_items", new=AsyncMock(return_value={})):
            asyncio.run(scan_pending_follow_matches())
        self.assertEqual(self._reminder_status(pending_id), "none")

    def test_no_followers_marks_done_without_ai(self):
        doc_id = self._insert_document(parsed_text="实验室")
        import asyncio

        with patch.object(follow, "ai_match_follow_items", new=AsyncMock(return_value={})) as ai_mock:
            result = asyncio.run(scan_pending_follow_matches())
        self.assertEqual(self._reminder_status(doc_id), "done")
        self.assertEqual(result["hits"], 0)
        self.assertEqual(ai_mock.call_count, 0)


class AiMatchParsingTests(unittest.TestCase):
    def test_ai_match_filters_fabricated_items(self):
        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"response_json": {"matches": [
                    {"item": "师范认证", "reason": "相关"},
                    {"item": "编造的条目", "reason": "不应出现"},
                ]}}

        import asyncio

        with patch("classroom_app.core.ai_client.post", new=AsyncMock(return_value=_Resp())):
            result = asyncio.run(follow.ai_match_follow_items("公文文本", ["师范认证", "运动会"]))
        self.assertEqual(result, {"师范认证": "相关"})

    def test_ai_failure_degrades_to_empty(self):
        import asyncio

        with patch("classroom_app.core.ai_client.post", new=AsyncMock(side_effect=RuntimeError("down"))):
            result = asyncio.run(follow.ai_match_follow_items("文本", ["项"]))
        self.assertEqual(result, {})


class AutoNameKeywordTests(GongwenFollowTestBase):
    """教师姓名自动加入关键字关注。"""

    def setUp(self):
        super().setUp()
        self.conn.execute("CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT)")
        self.conn.execute("INSERT INTO teachers (id, name) VALUES (9, '张三丰')")
        scope_patcher = patch(
            "classroom_app.services.organization_scope_service.load_teacher_org_scope",
            return_value=dict(TEACHER_SCOPE),
        )
        scope_patcher.start()
        self.addCleanup(scope_patcher.stop)
        self.notify_patcher = patch(
            "classroom_app.services.message_center_service._insert_notification", return_value=1
        )
        self.notify_mock = self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_effective_keywords_appends_and_dedupes_name(self):
        self.assertEqual(follow.effective_keywords(["实验室"], "张三丰"), ["实验室", "张三丰"])
        self.assertEqual(follow.effective_keywords(["张三丰"], "张三丰"), ["张三丰"])
        self.assertEqual(follow.effective_keywords([], ""), [])

    def test_teacher_name_matched_without_configured_keywords(self):
        # Arrange: 启用关注但项目/关键字全空 —— 姓名仍应自动参与匹配。
        save_teacher_follow_settings(self.conn, 9, items=[], keywords=[])
        doc_id = self._insert_document(parsed_text="请张三丰老师于周五前提交材料。")
        import asyncio

        # Act
        with patch.object(follow, "ai_match_follow_items", new=AsyncMock(return_value={})):
            result = asyncio.run(match_document_for_followers(doc_id))

        # Assert
        self.assertEqual(result["hits"], 1)
        hits = load_follow_hits_for_documents(self.conn, 9, [doc_id])
        self.assertEqual(hits[doc_id]["matched_keywords"], ["张三丰"])


class FollowRescanTests(GongwenFollowTestBase):
    """重新发现：全量回扫已解析公文 + 单条汇总提醒。"""

    def setUp(self):
        super().setUp()
        self.conn.execute("CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT)")
        self.conn.execute("INSERT INTO teachers (id, name) VALUES (9, '张三丰')")
        scope_patcher = patch(
            "classroom_app.services.organization_scope_service.load_teacher_org_scope",
            return_value=dict(TEACHER_SCOPE),
        )
        scope_patcher.start()
        self.addCleanup(scope_patcher.stop)
        self.notify_patcher = patch(
            "classroom_app.services.message_center_service._insert_notification", return_value=1
        )
        self.notify_mock = self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_rescan_covers_old_docs_with_single_summary_notification(self):
        # Arrange: 老公文（远超 30 天窗口）也要被重新发现覆盖。
        save_teacher_follow_settings(self.conn, 9, items=[], keywords=["实验室"])
        old_hit = self._insert_document(parsed_text="实验室安全大检查", publish_time=_days(-400))
        self._insert_document(title="无关公文", parsed_text="运动会安排", publish_time=_days(-1))
        unparsed = self._insert_document(parsed_text="实验室", parsed_status="pending")
        import asyncio

        # Act
        with patch.object(follow, "ai_match_follow_items_bulk", new=AsyncMock(return_value={})):
            result = asyncio.run(follow.rescan_teacher_follow_matches(9))

        # Assert: 命中 1 篇（未解析的不扫），notified=1（不逐篇提醒），仅一条汇总。
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["new_hits"], 1)
        hit_row = dict(self.conn.execute(
            "SELECT * FROM gongwen_follow_hits WHERE teacher_id = 9 AND document_id = ?", (old_hit,)
        ).fetchone())
        self.assertEqual(hit_row["notified"], 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS c FROM gongwen_follow_hits WHERE document_id = ?", (unparsed,)
            ).fetchone()["c"],
            0,
        )
        self.assertEqual(self.notify_mock.call_count, 1)
        payload = self.notify_mock.call_args[0][1]
        self.assertIn("重新发现", payload["title"])
        self.assertIn("follow=1", payload["link_url"])

    def test_rescan_ai_bulk_items_map_to_hits(self):
        save_teacher_follow_settings(self.conn, 9, items=["师范认证"], keywords=[])
        doc_id = self._insert_document(title="师范类专业认证工作部署", parsed_text="部署细节", publish_time=_days(-200))
        import asyncio

        bulk = AsyncMock(return_value={doc_id: {"师范认证": "部署认证工作"}})
        with patch.object(follow, "ai_match_follow_items_bulk", new=bulk):
            result = asyncio.run(follow.rescan_teacher_follow_matches(9))

        self.assertEqual(result["new_hits"], 1)
        hits = load_follow_hits_for_documents(self.conn, 9, [doc_id])
        self.assertEqual(hits[doc_id]["matched_items"], ["师范认证"])
        self.assertEqual(hits[doc_id]["ai_reason"], "部署认证工作")

    def test_rescan_existing_hits_not_renotified(self):
        save_teacher_follow_settings(self.conn, 9, items=[], keywords=["实验室"])
        self._insert_document(parsed_text="实验室检查", publish_time=_days(-100))
        import asyncio

        with patch.object(follow, "ai_match_follow_items_bulk", new=AsyncMock(return_value={})):
            asyncio.run(follow.rescan_teacher_follow_matches(9))
            second = asyncio.run(follow.rescan_teacher_follow_matches(9))
        self.assertEqual(second["new_hits"], 0)
        self.assertEqual(self.notify_mock.call_count, 1)  # 第二次无新命中 → 不再发汇总

    def test_rescan_without_name_or_settings_skips(self):
        # teacher 77 没有姓名也没有配置 → no_settings。
        import asyncio

        result = asyncio.run(follow.rescan_teacher_follow_matches(77))
        self.assertEqual(result["status"], "no_settings")

    def test_count_follow_hits_totals(self):
        save_teacher_follow_settings(self.conn, 9, items=[], keywords=["实验室"])
        doc_id = self._insert_document(parsed_text="实验室检查", publish_time=_days(-100))
        import asyncio

        with patch.object(follow, "ai_match_follow_items_bulk", new=AsyncMock(return_value={})):
            asyncio.run(follow.rescan_teacher_follow_matches(9))
        stats = follow.count_follow_hits(self.conn, 9)
        self.assertEqual(stats, {"total": 1, "unseen": 1})
        mark_follow_hit_seen(self.conn, 9, doc_id)
        self.assertEqual(follow.count_follow_hits(self.conn, 9), {"total": 1, "unseen": 0})


class AiBulkMatchParsingTests(unittest.TestCase):
    def test_bulk_filters_fabricated_doc_ids_and_items(self):
        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"response_json": {"matches": [
                    {"doc": 11, "item": "师范认证", "reason": "相关"},
                    {"doc": 999, "item": "师范认证", "reason": "编造的公文ID"},
                    {"doc": 11, "item": "编造的条目", "reason": "不应出现"},
                    {"doc": "not-an-id", "item": "师范认证", "reason": "坏ID"},
                ]}}

        import asyncio

        docs = [{"id": 11, "title": "认证工作"}, {"id": 12, "title": "其他"}]
        with patch("classroom_app.core.ai_client.post", new=AsyncMock(return_value=_Resp())):
            result = asyncio.run(follow.ai_match_follow_items_bulk(docs, ["师范认证"]))
        self.assertEqual(result, {11: {"师范认证": "相关"}})

    def test_bulk_ai_failure_degrades_to_empty(self):
        import asyncio

        with patch("classroom_app.core.ai_client.post", new=AsyncMock(side_effect=RuntimeError("down"))):
            result = asyncio.run(follow.ai_match_follow_items_bulk([{"id": 1, "title": "t"}], ["项"]))
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
