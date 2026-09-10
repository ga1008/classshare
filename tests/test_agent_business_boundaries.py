import json
import sqlite3
import unittest

from fastapi import HTTPException

from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db.schema_foundation import ensure_foundation_schema
from classroom_app.db.schema_learning_blog import ensure_learning_blog_signature_schema
from classroom_app.services import agent_platform_actions, agent_task_service, blog_service


class NoDatabaseReads:
    def execute(self, *args, **kwargs):
        raise AssertionError("Untrusted context must not cause a database lookup")


class AgentPublicContextTests(unittest.TestCase):
    def test_public_context_cannot_supply_identity_or_runtime_session(self):
        malicious = {
            "follow_up": {"parent_thread_id": "another-user-thread"},
            "actor": {"role": "teacher", "id": 1, "is_super_admin": True},
            "user": {"role": "teacher", "name": "Administrator"},
            "agent_options": {"no_history": False},
            "server_context": {"lesson_document_target": {"id": 123}},
            "runtime_thread_id": "another-user-thread",
            "page": {"title": "课堂", "follow_up": {"parent_thread_id": "nested"}},
            "classroomContext": {"userRole": "admin", "selectedSession": {"title": "本次课", "actor_id": 1}},
        }
        context = agent_task_service.build_teacher_page_context(NoDatabaseReads(), 7, malicious)
        self.assertEqual(
            {"page": {"title": "课堂"}, "classroomContext": {"selectedSession": {"title": "本次课"}}, "server_context": {}},
            context,
        )
        self.assertNotIn("another-user-thread", json.dumps(context))

    def test_known_page_hints_survive_with_nested_types_and_size_bounds(self):
        context = agent_task_service._normalize_context_payload({
            "classOfferingId": "31", "sessionOrderIndex": 2,
            "page": {"path": "/classroom/31", "headings": ["课程", {"actor": "injected"}], "selectedText": "x" * 3000},
            "materialContext": {"sessionId": 8, "materialId": 12, "materialName": "讲义", "headings": ["第一章"]},
            "assignmentContext": {"title": "作业", "visibleStats": ["已交10人"]},
            "manageContext": {"pageTitle": "材料管理", "visibleSections": ["我的材料"]},
            "dashboardContext": {"activeCourseCards": ["网络工程"]},
            "classroomContext": {
                "selectedSession": {"id": 8, "orderIndex": 2, "content": "实验说明"},
                "learningProgress": {"student_count": 32, "average_score": 71.5, "distribution": [{"name": "熟练", "count": 9}]},
            },
        })
        self.assertEqual(31, context["classOfferingId"])
        self.assertEqual(["课程"], context["page"]["headings"])
        self.assertEqual(2000, len(context["page"]["selectedText"]))
        self.assertEqual(8, context["classroomContext"]["selectedSession"]["id"])
        self.assertEqual(71.5, context["classroomContext"]["learningProgress"]["average_score"])
        self.assertEqual("讲义", context["materialContext"]["materialName"])
        self.assertEqual(["已交10人"], context["assignmentContext"]["visibleStats"])
        self.assertEqual("材料管理", context["manageContext"]["pageTitle"])
        self.assertEqual(["网络工程"], context["dashboardContext"]["activeCourseCards"])

    def test_malformed_ids_and_nested_payloads_are_ignored_without_database_access(self):
        context = agent_task_service.build_teacher_page_context(NoDatabaseReads(), 7, {
            "classOfferingId": True, "assignmentId": {"id": 1}, "materialId": "9" * 5000,
            "materialContext": "not-an-object", "classroomContext": {"selectedSession": []},
        })
        self.assertEqual({"classroomContext": {}, "server_context": {}}, context)


class AgentBlogBusinessTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_foundation_schema(self.conn)
        ensure_classroom_activity_schema(self.conn)
        ensure_learning_blog_signature_schema(self.conn)
        for teacher_id in (7, 8):
            self.conn.execute(
                "INSERT INTO teachers (id, name, email, hashed_password, is_active) VALUES (?, ?, ?, 'test', 1)",
                (teacher_id, f"Teacher {teacher_id}", f"teacher{teacher_id}@example.test"),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_create_post_validates_and_links_media_through_normal_service(self):
        file_hash = "a" * 64
        self.conn.execute(
            "INSERT INTO blog_media_assets (file_hash, uploader_identity, uploader_role, uploader_user_pk, original_filename, mime_type, file_size) VALUES (?, 'teacher:7', 'teacher', 7, 'lesson.png', 'image/png', 20)",
            (file_hash,),
        )
        post = agent_platform_actions._create_teacher_blog_post(
            self.conn, teacher_id=7, title="课堂复盘", content_md=f"# 课堂\n![图示](/api/blog/image/{file_hash})", tags=["教学"], status="published",
        )
        row = self.conn.execute("SELECT * FROM blog_posts WHERE id = ?", (post["id"],)).fetchone()
        self.assertEqual(file_hash, row["cover_image_hash"])
        self.assertEqual("teacher:7", row["author_identity"])
        self.assertEqual(file_hash, blog_service.list_attachments(self.conn, post["id"])[0]["file_hash"])
        self.assertNotIn("/api/blog/image/", row["summary"])

    def test_create_post_rejects_unregistered_image_and_unmanaged_class_without_writing_post(self):
        for kwargs in (
            {"content_md": f"![图片](/api/blog/image/{'b' * 64})"},
            {"content_md": "课堂说明", "visibility": "class_visible", "visible_class_id": 999},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(HTTPException) as error:
                agent_platform_actions._create_teacher_blog_post(self.conn, teacher_id=7, title="无效发布", tags=[], **kwargs)
            self.assertEqual(400, error.exception.status_code)
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM blog_posts").fetchone()[0])

    def test_comment_matches_web_visibility_and_emits_real_notification(self):
        post = blog_service.create_post(
            self.conn, {"id": 8, "role": "teacher", "name": "Teacher 8"},
            title="定向讨论", content_md="请讨论", visibility="selected_users", visible_user_identities=["teacher:8", "student:100"],
        )
        # Teachers currently have the same visibility override in the normal Web service.
        web = blog_service.add_comment(self.conn, {"id": 7, "role": "teacher", "name": "Teacher 7"}, post["id"], content_md="Web comment")
        agent = agent_platform_actions._create_teacher_blog_comment(self.conn, teacher_id=7, post_id=post["id"], content_md="Agent comment")
        self.assertNotEqual(web["id"], agent["id"])
        self.assertEqual(2, self.conn.execute("SELECT comment_count FROM blog_posts WHERE id = ?", (post["id"],)).fetchone()[0])
        notification = self.conn.execute("SELECT * FROM message_center_notifications WHERE ref_type = 'blog_comment' AND ref_id = ?", (str(agent["id"]),)).fetchone()
        self.assertIsNotNone(notification)
        self.assertEqual("teacher:8", notification["recipient_identity"])

    def test_comment_honors_comment_lock_and_inactive_teacher(self):
        post = blog_service.create_post(self.conn, {"id": 8, "role": "teacher"}, title="只读帖子", content_md="内容", allow_comments=False)
        with self.assertRaises(HTTPException) as locked:
            agent_platform_actions._create_teacher_blog_comment(self.conn, teacher_id=7, post_id=post["id"], content_md="不应写入")
        self.assertEqual(403, locked.exception.status_code)
        self.conn.execute("UPDATE teachers SET is_active = 0 WHERE id = 7")
        with self.assertRaises(HTTPException) as inactive:
            agent_platform_actions._create_teacher_blog_draft(self.conn, teacher_id=7, title="不应创建", content_md="内容", tags=[])
        self.assertEqual(403, inactive.exception.status_code)
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM blog_comments").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
