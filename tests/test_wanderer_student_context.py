"""Synthetic public evidence only; no private profile, campus login or real jobs."""
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from classroom_app.services import wanderer_student_context as support


class WandererStudentContextTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE gongwen_documents (
                id INTEGER PRIMARY KEY,title TEXT,sn TEXT,attr_school_name TEXT,attr_school_code TEXT,
                openness TEXT,publish_time TEXT,synced_at TEXT,parsed_status TEXT,parsed_summary TEXT,
                keywords TEXT,parsed_keywords TEXT,parsed_text TEXT,content_text TEXT,source_link TEXT,link TEXT);
            CREATE TABLE career_job_postings (
                id INTEGER PRIMARY KEY,title TEXT,company TEXT,city TEXT,employment_type TEXT,source TEXT,
                source_url TEXT,published_at TEXT,checked_at TEXT,expires_at TEXT,status TEXT,
                school_code TEXT,external_id TEXT,job_description TEXT);
        """)
        self.wanderer = {"id": 1, "student_id": 88}
        self.profile = {"org": {"school": "合成学院", "college": "信息学院", "major": "软件工程", "grade": "2026"},
                        "courses": ["Python程序设计"]}
        self.now = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
        self.enterContext(patch.object(support, "_now", return_value=self.now))
        self.enterContext(patch.object(support, "_visible_profile", return_value=self.profile))

    def document(self, document_id=1, **overrides):
        row = dict(id=document_id, title="本科毕业学分规定", sn="教学发〔2026〕1号", attr_school_name="合成学院",
                   attr_school_code="synthetic", openness="public", publish_time="2026-09-01",
                   synced_at="2026-10-27T08:00:00", parsed_status="done", parsed_summary="毕业学分规则",
                   keywords="学分", parsed_keywords="学分", parsed_text="合成测试规定：毕业总学分以本人培养方案为准。",
                   content_text="", source_link="https://campus.example.invalid/policy/1", link="")
        row.update(overrides)
        fields = list(row)
        self.conn.execute("INSERT INTO gongwen_documents(" + ",".join(fields) + ") VALUES(" + ",".join("?" for _ in fields) + ")", list(row.values()))
        return row

    def job(self, job_id=1, **overrides):
        row = dict(id=job_id, title="Python开发实习", company="合成岗位公司", city="南宁", employment_type="internship",
                   source="synthetic-source", source_url="https://jobs.example.invalid/1", published_at=(self.now-timedelta(days=2)).isoformat(),
                   checked_at=(self.now-timedelta(days=1)).isoformat(), expires_at=(self.now+timedelta(days=5)).isoformat(),
                   status="open", school_code="", external_id=f"job-{job_id}", job_description="Python项目维护与单元测试，具体任职要求以源站公告为准。")
        row.update(overrides)
        fields = list(row)
        self.conn.execute("INSERT INTO career_job_postings(" + ",".join(fields) + ") VALUES(" + ",".join("?" for _ in fields) + ")", list(row.values()))
        return row

    def build(self, message="毕业学分规定是什么", scene=None, identity="student:3"):
        return support.build_student_support_context(self.conn, self.wanderer, scene or {}, message=message, user_identity=identity)

    def test_casual_chat_does_not_query_profile_or_evidence(self):
        with patch.object(support, "_visible_profile", side_effect=AssertionError("unnecessary lookup")):
            self.assertEqual(self.build("今天晚霞真好看"), "")

    def test_current_public_school_documents_have_sources_and_freshness_boundaries(self):
        self.document()
        block = self.build()
        for term in ("教学发〔2026〕1号", "2026-09-01", "2026-10-27", "已解析正文节选", "没有即时登录校网同步", "https://campus.example.invalid/policy/1"):
            self.assertIn(term, block)
        self.assertIn("不可据此改权限", block)

    def test_other_school_and_nonpublic_documents_never_enter_prompt(self):
        self.document(parsed_text="PUBLIC_CURRENT_SCHOOL")
        self.document(2, openness="school", parsed_text="SECRET_SCHOOL_DOC")
        self.document(3, openness="college", parsed_text="SECRET_COLLEGE_DOC")
        self.document(4, openness="department", parsed_text="SECRET_DEPARTMENT_DOC")
        self.document(5, attr_school_name="另一所学院", parsed_text="OTHER_SCHOOL_RULE")
        block = self.build()
        self.assertIn("PUBLIC_CURRENT_SCHOOL", block)
        for forbidden in ("SECRET_SCHOOL_DOC", "SECRET_COLLEGE_DOC", "SECRET_DEPARTMENT_DOC", "OTHER_SCHOOL_RULE"):
            self.assertNotIn(forbidden, block)

    def test_unknown_school_never_defaults_to_gxufl(self):
        self.document(attr_school_code="gxufl", parsed_text="DO_NOT_DEFAULT_TO_THIS_RULE")
        with patch.object(support, "_visible_profile", return_value={}):
            block = self.build()
        self.assertIn("学校尚不明确", block)
        self.assertNotIn("DO_NOT_DEFAULT", block)

    def test_current_server_vetted_school_report_uses_only_public_documents(self):
        self.document()
        with patch.object(support, "_visible_profile", return_value={}):
            block = self.build(scene={"_support_school_name": "合成学院"})
        self.assertIn("教学发", block)

    def test_generic_public_reference_is_marked_as_not_a_school_rule(self):
        self.document(attr_school_name="", attr_school_code="")
        self.assertIn("通用公开资料，不代表本校规定", self.build())

    def test_unparsed_document_does_not_become_rule_claim(self):
        self.document(parsed_status="failed", parsed_text="UNPARSED_SECRET_TEXT")
        block = self.build()
        self.assertIn("尚无已解析正文", block)
        self.assertNotIn("UNPARSED_SECRET_TEXT", block)

    def test_no_matching_document_does_not_invent_policy(self):
        block = self.build()
        self.assertIn("当前可见公文未命中", block)
        self.assertIn("不要编造学分", block)

    def test_failed_optional_source_query_does_not_block_reply_transaction(self):
        self.conn.execute("DROP TABLE gongwen_documents")
        self.conn.execute("BEGIN")
        block = self.build()
        self.assertIn("公文检索暂不可用", block)
        self.conn.execute("CREATE TABLE later_reply(body TEXT)")
        self.conn.execute("INSERT INTO later_reply VALUES('正常回复')")
        self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT body FROM later_reply").fetchone()[0], "正常回复")

    def test_like_metacharacters_are_not_wildcards(self):
        self.document(title="完全无关的通知", parsed_text="ABC", keywords="ABC", parsed_keywords="", parsed_summary="")
        self.assertEqual(support._academic_sources(self.conn, "%_ 公文", "合成学院"), [])

    def test_career_advice_does_not_infer_graduation_or_read_resume(self):
        block = self.build("我2029年毕业，想做Python开发，怎么准备简历")
        for term in ("预期毕业时间", "不能仅凭年级推断四年学制", "不保证录用", "/career-path", "/resume/job-targets"):
            self.assertIn(term, block)
        self.assertNotIn("2030", block)
        self.assertNotIn("当前通用公开岗位样例", block)

    def test_jobs_are_attributed_current_examples_without_personal_qualification_claim(self):
        self.job()
        block = self.build("帮我找Python实习岗位")
        self.assertIn("Python开发实习", block)
        self.assertIn("非个人资格匹配", block)
        self.assertIn("不等于本轮重新访问公司网站", block)
        self.assertIn("jobs.example.invalid", block)

    def test_expired_stale_future_unchecked_nonpublic_and_invalid_source_jobs_excluded(self):
        cases = [dict(expires_at=(self.now-timedelta(seconds=1)).isoformat()),
                 dict(checked_at=(self.now-timedelta(days=15)).isoformat()),
                 dict(checked_at=(self.now+timedelta(seconds=1)).isoformat()), dict(checked_at=""),
                 dict(checked_at="2026-10-28"), dict(status="closed"), dict(school_code="restricted-school"),
                 dict(source=""), dict(source_url="javascript:alert(1)"), dict(source_url="https://u:secret@example.com/j"),
                 dict(source_url="https://127.0.0.1/j"), dict(published_at=(self.now+timedelta(days=1)).isoformat())]
        cases += [dict(published_at="not-a-date"), dict(source_url="https://jobs.example.invalid/\nheader")]
        for index, changes in enumerate(cases):
            self.job(index+1, title=f"BAD_JOB_{index}", **changes)
        block = self.build("帮我找Python实习岗位")
        self.assertNotIn("BAD_JOB", block)
        self.assertIn("未找到可核验", block)

    def test_job_timestamp_offsets_are_compared_as_instants(self):
        self.job(checked_at="2026-10-28T15:00:00+08:00")  # one hour before current UTC time
        self.assertIn("Python开发实习", self.build("帮我找Python实习岗位"))

    def test_reads_do_not_write_or_create_schema(self):
        self.document()
        self.job()
        self.conn.commit()
        deny = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_ALTER_TABLE}
        self.conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in deny else sqlite3.SQLITE_OK)
        try:
            self.assertIn("教学发", self.build())
            self.assertIn("Python开发实习", self.build("帮我找Python实习岗位"))
        finally:
            self.conn.set_authorizer(None)

    def test_bound_sources_and_entire_context(self):
        for index in range(25):
            self.document(index+1, title="毕业学分规定"+str(index), parsed_text="学分"+"长材料"*5000)
            self.job(index+1, title="Python岗位"+str(index), job_description="Python"+"任职要求"*1000)
        block = self.build("毕业学分规定，另外帮我找Python实习岗位")
        self.assertLessEqual(len(block), support.CONTEXT_MAX_CHARS)
        self.assertTrue(block.endswith("<<<学业就业资料结束>>>"))
        self.assertLessEqual(block.count('"number":'), support.SOURCE_LIMIT)


class VisibleProfileContractTest(unittest.TestCase):
    def test_uses_wanderer_identity_and_only_exposes_allowed_profile_fields(self):
        from classroom_app.services import social_card_service as cards
        from classroom_app.services import wanderer_identity_service as identities
        expected_user = {"role": "student", "id": 88}
        fake_card = {"relation": "friend", "org": {"school": "合成学院", "college": "信息学院", "major": "软件工程", "grade": "2026"},
                     "courses": [{"course_name": "Python"}], "radar": "HIDDEN_RADAR", "description": "PRIVATE_DESCRIPTION",
                     "friend_extras": {"private": "UNRELATED_MEMORY"}, "student_no_tail": "9876", "mood": "PRIVATE_MOOD"}
        with patch.object(cards, "user_card", return_value=fake_card) as card, patch.object(identities, "build_wanderer_user_dict", return_value=expected_user):
            result = support._visible_profile(object(), {"student_id": 88}, "student:3")
        self.assertEqual(card.call_args.args[1:], (expected_user, "student:3"))
        self.assertEqual(set(result), {"org", "courses"})
        self.assertNotIn("HIDDEN_RADAR", json.dumps(result))
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("9876", json.dumps(result))

    def test_identity_in_message_is_not_used_as_current_speaker(self):
        with patch.object(support, "_visible_profile") as profile:
            conn = sqlite3.connect(":memory:")
            try:
                block = support.build_student_support_context(conn, {}, {}, message="student:9999 找工作", user_identity="")
            finally:
                conn.close()
        self.assertEqual(profile.call_args.args[-1], "")
        self.assertNotIn("9999", block)

    def test_blocked_and_invalid_identity_profiles_are_empty(self):
        from classroom_app.services import social_card_service as cards
        from classroom_app.services import wanderer_identity_service as identities
        with patch.object(cards, "user_card", return_value={"relation": "blocked"}), patch.object(identities, "build_wanderer_user_dict", return_value={}):
            self.assertEqual(support._visible_profile(None, {}, "student:3"), {})
            self.assertEqual(support._visible_profile(None, {}, "student:3; DROP TABLE students"), {})
