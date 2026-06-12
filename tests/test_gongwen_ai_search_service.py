"""公文 AI 检索 (gongwen AI search) — 意图识别、候选获取、AI 筛选与上下文块。"""

import contextlib
import os
import sqlite3
import unittest

# 必须在导入 schema 模块前固定引擎，否则宿主机的 DB_ENGINE=postgres 会让
# DDL 走 postgres 方言（GENERATED ... AS IDENTITY）而在 sqlite 上报错。
os.environ["DB_ENGINE"] = "sqlite"
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import classroom_app.db.schema_gongwen as schema_gongwen
import classroom_app.services.gongwen_ai_search_service as search
from classroom_app.db.schema_gongwen import ensure_gongwen_schema

TEACHER_SCOPE = {"school_code": "sch", "school_name": "测试校区", "college": "", "department": ""}


def _run(coro):
    return asyncio.run(coro)


def _ai_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response


def _days(delta: int) -> str:
    return (datetime.now() + timedelta(days=delta)).strftime("%Y-%m-%d 09:00:00")


class PrefilterTests(unittest.TestCase):
    def test_mentions_gongwen_terms(self):
        for message in (
            "最近有哪些关于师范认证的公文？",
            "学校对监考有什么规定吗",
            "帮我看看教学发的红头文件",
            "上个月学院下发的通知里有提到申报截止吗",
        ):
            self.assertTrue(search.message_may_mention_gongwen(message), message)

    def test_ignores_unrelated_chat(self):
        for message in ("帮我批改这份作业", "明天的课讲什么内容好", "写一首关于春天的诗"):
            self.assertFalse(search.message_may_mention_gongwen(message), message)

    def test_extract_local_keywords_skips_stopwords(self):
        keywords = search._extract_local_keywords("学校最近关于师范认证的公文有哪些")
        self.assertIn("师范认证", keywords)
        self.assertNotIn("学校", keywords)
        self.assertNotIn("公文", keywords)


class DetectIntentTests(unittest.TestCase):
    def test_related_intent_parsed(self):
        ai_payload = {
            "response_json": {
                "related": True,
                "query": "师范认证相关公文",
                "keywords": ["师范认证"],
                "recent_months": 3,
            }
        }
        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(return_value=_ai_response(ai_payload))
            intent = _run(search.detect_gongwen_intent("最近有哪些师范认证的公文？"))
        self.assertIsNotNone(intent)
        self.assertEqual(intent["query"], "师范认证相关公文")
        self.assertEqual(intent["keywords"], ["师范认证"])
        self.assertEqual(intent["recent_months"], 3)
        self.assertFalse(intent["fallback"])

    def test_unrelated_returns_none(self):
        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(return_value=_ai_response({"response_json": {"related": False}}))
            self.assertIsNone(_run(search.detect_gongwen_intent("公文格式怎么排版（闲聊）")))

    def test_ai_failure_falls_back_on_strong_signal(self):
        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(side_effect=RuntimeError("ai down"))
            intent = _run(search.detect_gongwen_intent("最近的公文里有提到师范认证吗"))
        self.assertIsNotNone(intent)
        self.assertTrue(intent["fallback"])
        self.assertEqual(intent["recent_months"], search.DEFAULT_RECENT_MONTHS)

    def test_ai_failure_without_strong_signal_returns_none(self):
        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(side_effect=RuntimeError("ai down"))
            self.assertIsNone(_run(search.detect_gongwen_intent("学校的规定我有点疑问")))

    def test_recent_pattern_forces_time_window(self):
        ai_payload = {
            "response_json": {"related": True, "query": "q", "keywords": [], "recent_months": 0}
        }
        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(return_value=_ai_response(ai_payload))
            intent = _run(search.detect_gongwen_intent("最近学校发的公文有哪些"))
        self.assertEqual(intent["recent_months"], search.DEFAULT_RECENT_MONTHS)


class CandidateFetchTestBase(unittest.TestCase):
    def setUp(self):
        schema_gongwen._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_gongwen_schema(self.conn)
        self.addCleanup(self.conn.close)

    def _insert_document(self, *, title, parsed_text="", publish_time=None, sn="") -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO gongwen_documents
                (remote_id, attr_school_code, attr_level, openness, title, sn, parsed_text,
                 publish_time, parsed_status)
            VALUES (?, 'sch', 'school', 'school', ?, ?, ?, ?, 'done')
            """,
            (f"r-{title}-{publish_time}", title, sn, parsed_text, publish_time or _days(-1)),
        )
        return int(cursor.lastrowid)


class CandidateFetchTests(CandidateFetchTestBase):
    def test_keyword_match_hits_title_and_parsed_text(self):
        hit_title = self._insert_document(title="关于师范认证工作的通知")
        hit_text = self._insert_document(title="普通文件", parsed_text="本文件涉及师范认证评估安排")
        self._insert_document(title="实验室安全检查")
        docs = search._fetch_candidate_documents(
            self.conn, TEACHER_SCOPE, is_super_admin=False,
            keywords=["师范认证"], recent_months=0,
        )
        ids = {doc["id"] for doc in docs}
        self.assertIn(hit_title, ids)
        self.assertIn(hit_text, ids)

    def test_recent_months_filters_old_documents(self):
        recent = self._insert_document(title="师范认证近期通知", publish_time=_days(-10))
        old = self._insert_document(title="师范认证历史通知", publish_time=_days(-200))
        docs = search._fetch_candidate_documents(
            self.conn, TEACHER_SCOPE, is_super_admin=False,
            keywords=["师范认证"], recent_months=2,
        )
        ids = {doc["id"] for doc in docs}
        self.assertIn(recent, ids)
        self.assertNotIn(old, ids)

    def test_supplements_recent_docs_when_keyword_hits_sparse(self):
        for index in range(5):
            self._insert_document(title=f"普通公文{index}", publish_time=_days(-index - 1))
        docs = search._fetch_candidate_documents(
            self.conn, TEACHER_SCOPE, is_super_admin=False,
            keywords=["不存在的词"], recent_months=0,
        )
        self.assertEqual(len(docs), 5)

    def test_other_school_documents_invisible(self):
        self.conn.execute(
            """
            INSERT INTO gongwen_documents
                (remote_id, attr_school_code, attr_level, openness, title, publish_time, parsed_status)
            VALUES ('other-1', 'other', 'school', 'school', '他校公文', ?, 'done')
            """,
            (_days(-1),),
        )
        docs = search._fetch_candidate_documents(
            self.conn, TEACHER_SCOPE, is_super_admin=False, keywords=[], recent_months=0,
        )
        self.assertEqual(docs, [])


class AiSelectTests(unittest.TestCase):
    def test_filters_invented_doc_ids(self):
        docs = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]
        ai_payload = {
            "response_json": {
                "matches": [
                    {"doc": 1, "reason": "相关"},
                    {"doc": 999, "reason": "编造"},
                    "garbage",
                ]
            }
        }
        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(return_value=_ai_response(ai_payload))
            result = _run(search.ai_select_relevant_documents("问题", docs))
        self.assertEqual(result, {1: "相关"})

    def test_ai_failure_returns_empty(self):
        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(side_effect=RuntimeError("down"))
            result = _run(search.ai_select_relevant_documents("问题", [{"id": 1}]))
        self.assertEqual(result, {})


class ContextBlockTests(unittest.TestCase):
    def test_block_includes_metadata_and_link(self):
        docs = [{
            "id": 7, "title": "关于师范认证的通知", "sn": "教学发〔2026〕48号",
            "author": "教务处", "category_name": "教学文件", "publish_time": "2026-05-20 09:00:00",
            "parsed_summary": "认证安排", "parsed_text": "正文内容若干", "parsed_status": "done",
            "relevance_reason": "直接相关",
        }]
        block = search.build_gongwen_context_block(docs, intent={"recent_months": 3})
        for fragment in ("关于师范认证的通知", "教学发〔2026〕48号", "/manage/academic/gongwen?doc=7",
                         "正文内容若干", "最近 3 个月", "直接相关"):
            self.assertIn(fragment, block)

    def test_empty_docs_returns_empty_string(self):
        self.assertEqual(search.build_gongwen_context_block([]), "")

    def test_unparsed_document_noted(self):
        docs = [{"id": 3, "title": "未解析公文", "parsed_status": "pending", "parsed_text": ""}]
        block = search.build_gongwen_context_block(docs)
        self.assertIn("尚未完成解析", block)


class RetrievalPipelineTests(CandidateFetchTestBase):
    def setUp(self):
        super().setUp()

        @contextlib.contextmanager
        def _shared_conn():
            yield self.conn

        for target in (
            patch.object(search, "get_db_connection", _shared_conn),
            patch(
                "classroom_app.services.organization_scope_service.load_teacher_org_scope",
                return_value=dict(TEACHER_SCOPE),
            ),
            patch(
                "classroom_app.services.resource_access_service.is_super_admin_teacher",
                return_value=False,
            ),
        ):
            target.start()
            self.addCleanup(target.stop)

    def test_full_retrieval_with_ai_selection(self):
        hit = self._insert_document(title="关于师范认证的通知", parsed_text="认证安排详情")
        self._insert_document(title="实验室安全检查")
        intent = {"related": True, "query": "师范认证", "keywords": ["师范认证"], "recent_months": 0}
        with patch.object(search, "ai_select_relevant_documents", AsyncMock(return_value={hit: "直接相关"})):
            result = _run(search.run_gongwen_retrieval(9, "师范认证的公文", intent=intent))
        self.assertEqual(result["doc_count"], 1)
        self.assertTrue(result["ai_selected"])
        self.assertEqual(result["documents"][0]["id"], hit)
        self.assertIn("/manage/academic/gongwen?doc=", result["documents"][0]["url"])
        self.assertIn("师范认证", result["context_block"])

    def test_ai_selection_failure_degrades_to_keyword_candidates(self):
        self._insert_document(title="关于师范认证的通知")
        intent = {"related": True, "query": "师范认证", "keywords": ["师范认证"], "recent_months": 0}
        with patch.object(search, "ai_select_relevant_documents", AsyncMock(return_value={})):
            result = _run(search.run_gongwen_retrieval(9, "师范认证的公文", intent=intent))
        self.assertGreaterEqual(result["doc_count"], 1)
        self.assertFalse(result["ai_selected"])

    def test_no_intent_and_no_mention_returns_none(self):
        result = _run(search.run_gongwen_retrieval(9, "帮我批改作业"))
        self.assertIsNone(result)

    def test_result_caps_at_max_docs(self):
        ids = [self._insert_document(title=f"师范认证通知{index}") for index in range(10)]
        intent = {"related": True, "query": "师范认证", "keywords": ["师范认证"], "recent_months": 0}
        reasons = {doc_id: "相关" for doc_id in ids}
        with patch.object(search, "ai_select_relevant_documents", AsyncMock(return_value=reasons)):
            result = _run(search.run_gongwen_retrieval(9, "师范认证", intent=intent))
        self.assertEqual(result["doc_count"], search.MAX_RESULT_DOCS)


if __name__ == "__main__":
    unittest.main()
