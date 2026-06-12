import json
import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.services import learning_progress_service as service
from classroom_app.services.personalized_learning_path_service import _load_stage_retreat_path_steps
from classroom_app.services.learning_progress_service import (
    _ensure_stage_exam_scoring_payload,
    _load_stage_exam_retreat_prompt_block,
    _stage_exam_duplicate_report,
    _validate_stage_exam_quality,
    build_stage_exam_retreat_plan,
    handle_stage_exam_grading_complete,
)


class LearningProgressStageExamTests(unittest.TestCase):
    def test_stage_exam_scoring_payload_fills_missing_grading_fields(self):
        payload = {
            "pages": [
                {
                    "name": "Part 1",
                    "questions": [
                        {
                            "id": "p1_q1",
                            "type": "radio",
                            "text": "Choose one",
                            "options": ["A", "B"],
                            "answer": "A",
                            "explanation": "A is correct.",
                        },
                        {
                            "id": "p1_q2",
                            "type": "textarea",
                            "text": "Explain the process",
                            "answer": "",
                            "explanation": "Reference explanation.",
                        },
                    ],
                }
            ],
        }

        normalized = _ensure_stage_exam_scoring_payload(payload)
        questions = normalized["pages"][0]["questions"]

        self.assertEqual(100, normalized["grading"]["total_score"])
        self.assertEqual(50, questions[0]["points"])
        self.assertEqual(50, questions[1]["points"])
        for question in questions:
            self.assertTrue(question["answer"])
            self.assertTrue(question["grading_guidance"])
            self.assertTrue(question["deduction_points"])
            self.assertEqual(question["points"], question["grading"]["points"])

    def test_stage_exam_scoring_payload_rescales_existing_points_to_100(self):
        payload = {
            "grading": {"total_score": 20, "description": "Existing", "style": "strict"},
            "pages": [
                {
                    "name": "Part 1",
                    "questions": [
                        {
                            "id": "q1",
                            "type": "text",
                            "text": "Q1",
                            "answer": "A1",
                            "points": 5,
                            "grading_guidance": "Guide",
                            "deduction_points": "Deduct",
                        },
                        {
                            "id": "q2",
                            "type": "text",
                            "text": "Q2",
                            "answer": "A2",
                            "points": 15,
                            "grading_guidance": "Guide",
                            "deduction_points": "Deduct",
                        },
                    ],
                }
            ],
        }

        normalized = _ensure_stage_exam_scoring_payload(payload)
        questions = normalized["pages"][0]["questions"]

        self.assertEqual(100, normalized["grading"]["total_score"])
        self.assertEqual(25, questions[0]["points"])
        self.assertEqual(75, questions[1]["points"])

    def test_stage_exam_quality_rejects_objective_answer_outside_options(self):
        payload = {
            "pages": [
                {
                    "name": "Part 1",
                    "questions": [
                        {
                            "id": "q1",
                            "type": "radio",
                            "text": "Choose one",
                            "options": ["A", "B"],
                            "answer": "C",
                            "explanation": "A is correct.",
                        }
                    ],
                }
            ]
        }

        with self.assertRaisesRegex(ValueError, "answer option validation"):
            _validate_stage_exam_quality(payload, [])

    def test_stage_exam_quality_rejects_reused_historical_questions(self):
        payload = {
            "pages": [
                {
                    "name": "Part 1",
                    "questions": [
                        {"id": "q1", "type": "text", "text": "Explain how photosynthesis converts light energy into chemical energy."},
                        {"id": "q2", "type": "text", "text": "Compare mitosis and meiosis using chromosome number changes."},
                        {"id": "q3", "type": "text", "text": "Describe how enzymes reduce activation energy in reactions."},
                    ],
                }
            ]
        }
        historical = [
            "Explain how photosynthesis converts light energy into chemical energy.",
            "Compare mitosis and meiosis using chromosome number changes.",
        ]

        report = _stage_exam_duplicate_report(payload, historical)

        self.assertTrue(report["duplicate"])
        self.assertEqual(2, report["duplicate_count"])
        with self.assertRaisesRegex(ValueError, "duplicates historical"):
            _validate_stage_exam_quality(payload, historical)

    def test_stage_exam_quality_accepts_valid_distinct_objective_questions(self):
        payload = {
            "pages": [
                {
                    "name": "Part 1",
                    "questions": [
                        {
                            "id": "q1",
                            "type": "radio",
                            "text": "Choose one",
                            "options": [{"value": "A", "label": "Alpha"}, {"value": "B", "label": "Beta"}],
                            "answer": "A",
                            "explanation": "A is correct.",
                        },
                        {
                            "id": "q2",
                            "type": "checkbox",
                            "text": "Choose all",
                            "options": ["North", "South", "East"],
                            "answer": ["North", "East"],
                            "explanation": "North and East are correct.",
                        },
                    ],
                }
            ]
        }

        result = _validate_stage_exam_quality(payload, ["A very different historical question."])

        self.assertFalse(result["duplicate_report"]["duplicate"])


class StageExamRetreatPlanTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT,
                sect_name TEXT,
                description TEXT,
                credits REAL
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                course_id INTEGER,
                teacher_id INTEGER,
                home_learning_material_id INTEGER
            );
            CREATE TABLE class_offering_sessions (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                order_index INTEGER,
                title TEXT,
                content TEXT,
                learning_material_id INTEGER,
                generated_at TEXT
            );
            CREATE TABLE course_materials (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER,
                material_path TEXT,
                name TEXT,
                node_type TEXT DEFAULT 'file',
                ai_parse_result_json TEXT,
                ai_optimized_markdown TEXT
            );
            CREATE TABLE course_material_assignments (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                material_id INTEGER
            );
            CREATE TABLE assignments (
                id INTEGER PRIMARY KEY,
                title TEXT,
                class_offering_id INTEGER
            );
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY,
                assignment_id INTEGER,
                student_pk_id INTEGER,
                score REAL,
                status TEXT,
                feedback_md TEXT,
                student_name TEXT
            );
            CREATE TABLE learning_stage_exam_attempts (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                student_id INTEGER,
                stage_key TEXT,
                assignment_id INTEGER,
                exam_paper_id TEXT,
                status TEXT,
                score REAL,
                generated_at TEXT,
                submitted_at TEXT,
                graded_at TEXT,
                passed_at TEXT,
                ai_error TEXT,
                metadata_json TEXT DEFAULT '{}'
            );
            CREATE TABLE learning_stage_status (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                student_id INTEGER,
                stage_key TEXT,
                status TEXT,
                progress_score REAL,
                readiness_score REAL,
                last_calculated_at TEXT,
                last_exam_assignment_id INTEGER,
                certificate_id INTEGER,
                passed_at TEXT,
                metadata_json TEXT DEFAULT '{}'
            );
            CREATE TABLE message_center_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_identity TEXT NOT NULL,
                recipient_role TEXT NOT NULL,
                recipient_user_pk INTEGER NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'normal',
                actor_identity TEXT DEFAULT '',
                actor_role TEXT DEFAULT '',
                actor_user_pk INTEGER,
                actor_display_name TEXT DEFAULT '',
                title TEXT NOT NULL,
                body_preview TEXT DEFAULT '',
                link_url TEXT DEFAULT '',
                class_offering_id INTEGER,
                ref_type TEXT DEFAULT '',
                ref_id TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                email_status TEXT NOT NULL DEFAULT 'not_required',
                email_job_id INTEGER,
                email_queued_at TEXT,
                email_sent_at TEXT,
                read_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.conn.execute("INSERT INTO courses (id, name, description) VALUES (20, 'C 语言程序设计', '数组与指针')")
        self.conn.execute(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (1, 10, 20, 30)"
        )
        self.conn.execute(
            """
            INSERT INTO course_materials (
                id, teacher_id, material_path, name, node_type, ai_parse_result_json, ai_optimized_markdown
            )
            VALUES (10, 30, 'array-pointer.md', '数组边界与指针', 'file', ?, '数组边界、指针运算、越界风险。')
            """,
            (json.dumps({"summary": "数组边界与指针运算的常见混淆。"}, ensure_ascii=False),),
        )
        self.conn.execute(
            """
            INSERT INTO class_offering_sessions (
                id, class_offering_id, order_index, title, content, learning_material_id
            )
            VALUES (1000, 1, 1, '数组边界与指针', '讲解数组越界和指针偏移。', 10)
            """
        )

    def tearDown(self):
        self.conn.close()

    def _feedback(self) -> str:
        return """
        ## 逐题反馈
        ### 第1题 数组边界
        - 薄弱点：数组边界与指针运算混淆，未能解释越界风险。
        ### 第2题 函数调用
        - 不足：函数参数传递步骤不完整，需要补充调用栈变化。
        ### 第3题 调试
        - 建议：补充代码调试过程，说明如何定位错误。
        """

    def test_retreat_plan_matches_material_and_keeps_reflection_prompt(self):
        level = service.LEARNING_LEVELS[-1]
        plan = build_stage_exam_retreat_plan(
            self.conn,
            {
                "id": 7,
                "submission_id": 500,
                "class_offering_id": 1,
                "student_id": 2,
                "stage_key": level["key"],
                "submission_score": 62,
            },
            level,
            feedback_md=self._feedback(),
        )

        self.assertGreaterEqual(len(plan["items"]), 3)
        first = plan["items"][0]
        self.assertEqual("material", first["target_type"])
        self.assertEqual(10, first["material_id"])
        self.assertIn("/materials/view/10?class_offering_id=1", first["href"])
        self.assertIn("数组边界", first["weak_point"])
        self.assertIn("用自己的话", first["reflection_prompt"])

    def test_retreat_plan_falls_back_to_text_only_without_material_match(self):
        level = service.LEARNING_LEVELS[-1]
        plan = build_stage_exam_retreat_plan(
            self.conn,
            {
                "id": 8,
                "submission_id": 501,
                "class_offering_id": 1,
                "student_id": 2,
                "stage_key": level["key"],
                "submission_score": 55,
            },
            level,
            feedback_md="- 薄弱点：数据库事务隔离级别混淆。\n- 不足：索引选择理由不完整。",
        )

        self.assertGreaterEqual(len(plan["items"]), 3)
        unmatched = [item for item in plan["items"] if item["target_type"] == "stage_retreat"]
        self.assertTrue(unmatched)
        self.assertTrue(all(item["href"] == "/classroom/1" for item in unmatched))

    def test_prompt_block_reuses_previous_weak_summary(self):
        metadata = {
            service.STAGE_EXAM_RETREAT_PLAN_KEY: {
                "summary": "数组边界与指针运算混淆",
                "items": [{"weak_point": "数组边界与指针运算混淆，未能解释越界风险。"}],
            }
        }
        self.conn.execute(
            """
            INSERT INTO learning_stage_exam_attempts (
                id, class_offering_id, student_id, stage_key, status, score, generated_at, graded_at, metadata_json
            )
            VALUES (9, 1, 2, 'foundation', 'failed', 60, '2026-06-12T08:00:00', '2026-06-12T08:10:00', ?)
            """,
            (json.dumps(metadata, ensure_ascii=False),),
        )

        block = _load_stage_exam_retreat_prompt_block(self.conn, 1, 2, "foundation")

        self.assertIn("上次破境薄弱点", block)
        self.assertIn("数组边界与指针运算混淆", block)
        self.assertIn("变式重考", block)

    def test_learning_path_loads_retreat_steps_and_hides_after_pass(self):
        metadata = {
            service.STAGE_EXAM_RETREAT_PLAN_KEY: {
                "score": 62,
                "items": [
                    {
                        "key": "stage-retreat:11:1",
                        "title": "闭关 1：数组边界",
                        "description": "数组边界与指针运算混淆。",
                        "weak_point": "数组边界与指针运算混淆。",
                        "href": "/materials/view/10?class_offering_id=1&session_id=1000",
                        "target_type": "material",
                        "target_id": "10",
                        "material_id": 10,
                        "session_id": 1000,
                        "reflection_prompt": "写下越界判断方法。",
                    }
                ],
            }
        }
        self.conn.execute(
            """
            INSERT INTO learning_stage_exam_attempts (
                id, class_offering_id, student_id, stage_key, status, score, generated_at, graded_at, metadata_json
            )
            VALUES (11, 1, 2, 'foundation', 'failed', 62, '2026-06-12T08:00:00', '2026-06-12T08:20:00', ?)
            """,
            (json.dumps(metadata, ensure_ascii=False),),
        )
        offering = {"id": 1, "course_id": 20, "course_name": "C 语言程序设计", "class_name": "一班"}

        steps = _load_stage_retreat_path_steps(self.conn, offering=offering, states={}, student_id=2)

        self.assertEqual(1, len(steps))
        self.assertEqual("闭关", steps[0]["tag"])
        self.assertEqual("material", steps[0]["target_type"])
        self.assertIn("写下越界判断方法", steps[0]["description"])

        self.conn.execute(
            """
            INSERT INTO learning_stage_exam_attempts (
                id, class_offering_id, student_id, stage_key, status, score, generated_at, graded_at, passed_at, metadata_json
            )
            VALUES (12, 1, 2, 'foundation', 'passed', 88, '2026-06-12T09:00:00', '2026-06-12T09:30:00', '2026-06-12T09:30:00', '{}')
            """
        )
        self.assertEqual([], _load_stage_retreat_path_steps(self.conn, offering=offering, states={}, student_id=2))

    def test_failed_grading_writes_retreat_metadata_and_notification(self):
        self.conn.execute("INSERT INTO assignments (id, title, class_offering_id) VALUES (100, '破境试炼', 1)")
        self.conn.execute(
            """
            INSERT INTO submissions (id, assignment_id, student_pk_id, score, status, feedback_md, student_name)
            VALUES (500, 100, 2, 62, 'graded', ?, '小明')
            """,
            (self._feedback(),),
        )
        self.conn.execute(
            """
            INSERT INTO learning_stage_exam_attempts (
                id, class_offering_id, student_id, stage_key, assignment_id, status, generated_at, metadata_json
            )
            VALUES (7, 1, 2, 'foundation', 100, 'grading', '2026-06-12T08:00:00', '{"level_name":"核心筑基"}')
            """
        )
        self.conn.execute(
            """
            INSERT INTO learning_stage_status (id, class_offering_id, student_id, stage_key, status, progress_score)
            VALUES (1, 1, 2, 'foundation', 'in_exam', 70)
            """
        )

        with patch.object(service, "refresh_student_learning_state", return_value={}), patch(
            "classroom_app.services.message_center_service.queue_notification_email_if_applicable",
            return_value=False,
        ):
            result = handle_stage_exam_grading_complete(self.conn, 500)

        self.assertEqual("failed", result["status"])
        attempt = self.conn.execute("SELECT status, metadata_json FROM learning_stage_exam_attempts WHERE id = 7").fetchone()
        self.assertEqual("failed", attempt["status"])
        metadata = json.loads(attempt["metadata_json"])
        self.assertIn(service.STAGE_EXAM_RETREAT_PLAN_KEY, metadata)
        self.assertGreaterEqual(len(metadata[service.STAGE_EXAM_RETREAT_PLAN_KEY]["items"]), 3)
        notification = self.conn.execute(
            "SELECT body_preview, link_url, ref_id, metadata_json FROM message_center_notifications LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(notification)
        self.assertIn("试炼显示", notification["body_preview"])
        self.assertIn("/learning-path?status=active", notification["link_url"])
        self.assertIn("q=", notification["link_url"])
        self.assertEqual("stage-exam:7:retreat", notification["ref_id"])
        self.assertEqual(500, json.loads(notification["metadata_json"])["submission_id"])


if __name__ == "__main__":
    unittest.main()
