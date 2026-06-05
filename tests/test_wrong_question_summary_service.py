import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from classroom_app.services.wrong_question_summary_service import (
    PROMPT_VERSION,
    _attach_text_answer_clusters,
    _build_ai_status,
    _build_question_error_stats,
    _build_score_based_hard_questions,
    _clear_assignment_wrong_summary_ai_state,
    _extract_exam_questions,
    _score_based_difficulty_summary,
    ensure_wrong_summary_cache_tables,
    expire_interrupted_wrong_summary_jobs,
)


def _feedback(*scores: tuple[int, float, float]) -> str:
    sections = ["## 逐题反馈"]
    for question_no, score, max_score in scores:
        sections.extend(
            [
                "",
                f"### 第{question_no}题",
                f"- 本题得分：{score}/{max_score}",
                "- 扣分点：测试扣分点",
                "- 评价：测试评价",
            ]
        )
    return "\n".join(sections)


class WrongQuestionSummaryServiceTests(unittest.TestCase):
    def test_counts_only_non_full_scores_and_builds_choice_option_bars(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "radio",
                                "text": "How many layers are in the OSI model?",
                                "options": ["A. 5 layers", "B. 7 layers", "C. 4 layers"],
                                "answer": "B",
                                "points": 1,
                            },
                            {
                                "id": "q2",
                                "type": "checkbox",
                                "text": "Which are transport-layer protocols?",
                                "options": ["A. TCP", "B. IP", "C. UDP"],
                                "answer": ["A", "C"],
                                "points": 2,
                            },
                            {
                                "id": "q3",
                                "type": "text",
                                "text": "Default HTTP port?",
                                "answer": "80",
                                "points": 1,
                            },
                            {
                                "id": "q4",
                                "type": "textarea",
                                "text": "Explain what ARP resolves.",
                                "answer": "ARP resolves an IP address to a MAC address on the local network.",
                                "points": 5,
                            },
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps(
                    {
                        "answers": [
                            {"question_id": "q1", "answer": "A. 5 layers"},
                            {"question_id": "q2", "answer": "A. TCP|||C. UDP"},
                            {"question_id": "q3", "answer": "8080"},
                            {"question_id": "q4", "answer": "ARP gets MAC addresses."},
                        ]
                    },
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 1, 1), (2, 2, 2), (3, 1, 1), (4, 5, 5)),
            },
            {
                "id": 2,
                "student_name": "Student B",
                "status": "submitted",
                "answers_json": json.dumps(
                    {
                        "answers": [
                            {"question_id": "q1", "answer": "A. 5 layers"},
                            {"question_id": "q2", "answer": "A. TCP"},
                            {"question_id": "q3", "answer": "8080"},
                            {"question_id": "q4", "answer": "ARP broadcasts to find an IP address."},
                        ]
                    },
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 0, 1), (2, 1, 2), (3, 0, 1), (4, 3, 5)),
            },
            {
                "id": 3,
                "student_name": "Student C",
                "status": "submitted",
                "answers_json": json.dumps(
                    {
                        "answers": [
                            {"question_id": "q1", "answer": "C. 4 layers"},
                            {"question_id": "q2", "answer": "B. IP"},
                            {"question_id": "q3", "answer": "9999"},
                            {"question_id": "q4", "answer": "IP to MAC."},
                        ]
                    },
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 0, 1), (2, 2, 2), (3, 1, 1), (4, 5, 5)),
            },
        ]

        stats = _build_question_error_stats(questions, submissions)
        by_id = {item["question"]["id"]: item for item in stats}

        self.assertEqual(by_id["q1"]["wrong_count"], 3)
        self.assertEqual(by_id["q1"]["correct_count"], 0)
        self.assertEqual(by_id["q1"]["option_total_count"], 3)
        q1_bars = {item["label"]: item for item in by_id["q1"]["option_bars"]}
        self.assertEqual(q1_bars["A. 5 layers"]["count"], 2)
        self.assertEqual(q1_bars["A. 5 layers"]["percent"], 67)
        self.assertEqual(q1_bars["A. 5 layers"]["tone"], "wrong")
        self.assertEqual(q1_bars["B. 7 layers"]["count"], 0)
        self.assertEqual(q1_bars["B. 7 layers"]["percent"], 0)
        self.assertEqual(q1_bars["B. 7 layers"]["tone"], "correct")
        self.assertEqual(q1_bars["C. 4 layers"]["count"], 1)
        self.assertEqual(q1_bars["C. 4 layers"]["percent"], 33)

        self.assertEqual(by_id["q2"]["wrong_count"], 2)
        q2_bars = {item["label"]: item for item in by_id["q2"]["option_bars"]}
        self.assertEqual(q2_bars["A. TCP"]["count"], 2)
        self.assertEqual(q2_bars["A. TCP"]["percent"], 67)
        self.assertEqual(q2_bars["A. TCP"]["tone"], "correct")
        self.assertEqual(q2_bars["B. IP"]["count"], 1)
        self.assertEqual(q2_bars["B. IP"]["percent"], 33)
        self.assertEqual(q2_bars["B. IP"]["tone"], "wrong")
        self.assertEqual(q2_bars["C. UDP"]["count"], 1)
        self.assertEqual(q2_bars["C. UDP"]["percent"], 33)
        self.assertEqual(q2_bars["C. UDP"]["tone"], "correct")

        self.assertEqual(by_id["q3"]["wrong_count"], 1)
        self.assertEqual(by_id["q3"]["top_wrong_answers"][0]["label"], "8080")
        self.assertEqual(by_id["q3"]["top_wrong_answers"][0]["details"][0]["student_name"], "Student B")
        self.assertEqual(by_id["q4"]["wrong_count"], 1)

        hard_questions = _build_score_based_hard_questions(stats)
        self.assertEqual(hard_questions[0]["question"]["id"], "q1")
        self.assertIn("率", hard_questions[0]["difficulty_reason"])

    def test_missing_question_score_is_not_counted_as_wrong(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "text",
                                "text": "Default HTTP port?",
                                "answer": "80",
                                "points": 1,
                            }
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": "8080"}]}),
                "feedback_md": "",
            }
        ]

        stats = _build_question_error_stats(questions, submissions)
        self.assertEqual(stats[0]["wrong_count"], 0)
        self.assertEqual(stats[0]["scored_count"], 0)

    def test_choice_option_bars_match_wrong_count_when_answers_use_question_number_aliases(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "p1_q1",
                                "type": "radio",
                                "text": "域名服务 DNS 的正确解析是（ ）。",
                                "options": [
                                    "A. 将域名转换为物理地址",
                                    "B. 将域名转换为 IP 地址",
                                    "C. 将 IP 地址转换为物理地址",
                                    "D. 将 IP 地址转换为域名",
                                ],
                                "answer": "B",
                                "points": 1,
                            }
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps(
                    {"answers": [{"question_no": 1, "answer": "A. 将域名转换为物理地址"}]},
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 0, 1)),
            },
            {
                "id": 2,
                "student_name": "Student B",
                "status": "submitted",
                "answers_json": json.dumps(
                    {"answers": [{"ordinal": 1, "answer": "B. 将域名转换为 IP 地址"}]},
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 1, 1)),
            },
        ]

        stats = _build_question_error_stats(questions, submissions)
        item = stats[0]
        bars = {bar["label"]: bar for bar in item["option_bars"]}

        self.assertEqual(item["wrong_count"], 1)
        self.assertEqual(item["attempted_count"], 2)
        self.assertEqual(bars["A. 将域名转换为物理地址"]["count"], 1)
        self.assertEqual(bars["A. 将域名转换为物理地址"]["tone"], "wrong")
        self.assertEqual(bars["B. 将域名转换为 IP 地址"]["count"], 1)
        self.assertEqual(bars["B. 将域名转换为 IP 地址"]["tone"], "correct")

    def test_choice_wrong_count_uses_answer_not_stale_score(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "radio",
                                "text": "Ethernet V2 minimum frame length is 60 bytes.",
                                "options": ["A. Correct", "B. Wrong"],
                                "answer": "B",
                                "points": 1,
                            }
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": "A. Correct"}]}),
                "feedback_md": _feedback((1, 0, 1)),
            },
            {
                "id": 2,
                "student_name": "Student B",
                "status": "submitted",
                "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": "B. Wrong"}]}),
                "feedback_md": _feedback((1, 0, 1)),
            },
            {
                "id": 3,
                "student_name": "Student C",
                "status": "submitted",
                "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": "B. Wrong"}]}),
                "feedback_md": _feedback((1, 1, 1)),
            },
        ]

        stats = _build_question_error_stats(questions, submissions)
        item = stats[0]
        bars = {bar["label"]: bar for bar in item["option_bars"]}

        self.assertEqual(item["wrong_count"], 1)
        self.assertEqual(item["correct_count"], 2)
        self.assertEqual(item["average_score_percent"], 67)
        self.assertEqual(bars["A. Correct"]["count"], 1)
        self.assertEqual(bars["B. Wrong"]["count"], 2)

    def test_choice_blank_answers_are_visible_in_option_bars(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "radio",
                                "text": "Which answer is correct?",
                                "options": ["A. Correct", "B. Wrong"],
                                "answer": "A",
                                "points": 1,
                            }
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": "A. Correct"}]}),
                "feedback_md": _feedback((1, 1, 1)),
            },
            {
                "id": 2,
                "student_name": "Student B",
                "status": "submitted",
                "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": ""}]}),
                "feedback_md": _feedback((1, 0, 1)),
            },
        ]

        stats = _build_question_error_stats(questions, submissions)
        item = stats[0]
        bars = {bar["label"]: bar for bar in item["option_bars"]}

        self.assertEqual(item["wrong_count"], 1)
        self.assertEqual(item["blank_wrong_count"], 1)
        self.assertEqual(item["option_total_count"], 2)
        self.assertEqual(bars["未作答"]["count"], 1)
        self.assertEqual(bars["未作答"]["tone"], "wrong")

    def test_checkbox_option_text_with_comma_is_not_split_before_option_matching(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "checkbox",
                                "text": "Which troubleshooting direction is correct?",
                                "options": [
                                    "A. Check PC1 and PC2 are in the same subnet, with matching masks",
                                    "B. Check the default gateway",
                                    "C. Check both PCs are powered on",
                                ],
                                "answer": ["A", "C"],
                                "points": 2,
                            }
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps(
                    {
                        "answers": [
                            {
                                "question_id": "q1",
                                "answer": "A. Check PC1 and PC2 are in the same subnet, with matching masks",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 0, 2)),
            }
        ]

        stats = _build_question_error_stats(questions, submissions)
        bars = {bar["label"]: bar for bar in stats[0]["option_bars"]}

        self.assertEqual(stats[0]["wrong_count"], 1)
        self.assertEqual(bars["A. Check PC1 and PC2 are in the same subnet, with matching masks"]["count"], 1)
        self.assertNotIn("答案未匹配当前选项", bars)

    def test_choice_option_bars_expose_unmatched_legacy_answers(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "radio",
                                "text": "Wi-Fi uses RTS/CTS to solve what?",
                                "options": [
                                    "A. Signal attenuation",
                                    "B. Hidden station problem",
                                    "C. Rate adaptation",
                                ],
                                "answer": "B",
                                "points": 1,
                            }
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": "B. 80Mbps"}]}),
                "feedback_md": _feedback((1, 0, 1)),
            }
        ]

        stats = _build_question_error_stats(questions, submissions)
        bars = {bar["label"]: bar for bar in stats[0]["option_bars"]}

        self.assertEqual(stats[0]["wrong_count"], 1)
        self.assertEqual(bars["答案未匹配当前选项"]["count"], 1)
        self.assertEqual(bars["答案未匹配当前选项"]["tone"], "wrong")

    def test_cached_mode_marks_subjective_ai_work_pending_without_sync_generation(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "text",
                                "text": "Default HTTP port?",
                                "answer": "80",
                                "points": 1,
                            }
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": idx,
                "student_name": f"Student {idx}",
                "status": "submitted",
                "answers_json": json.dumps(
                    {"answers": [{"question_id": "q1", "answer": f"808{idx}"}]},
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 0, 1)),
            }
            for idx in range(3)
        ]
        stats = _build_question_error_stats(questions, submissions)

        with patch(
            "classroom_app.services.wrong_question_summary_service._load_text_cluster_cache",
            return_value=None,
        ), patch(
            "classroom_app.services.wrong_question_summary_service._generate_text_wrong_clusters",
            new=AsyncMock(),
        ) as generate_text:
            asyncio.run(_attach_text_answer_clusters("assignment-1", stats, allow_generate=False))

        self.assertEqual(stats[0]["text_cluster_status"], "pending")
        generate_text.assert_not_awaited()

        with patch(
            "classroom_app.services.wrong_question_summary_service._load_wrong_summary_job",
            return_value=None,
        ):
            ai_status = _build_ai_status(
                {
                    "assignment": {"id": "assignment-1"},
                    "questions_signature": "signature-1",
                },
                stats,
                _score_based_difficulty_summary(_build_score_based_hard_questions(stats)),
            )

        self.assertTrue(ai_status["needs_ai"])
        self.assertFalse(ai_status["is_active"])
        self.assertEqual(ai_status["job_status"], "manual_required")
        self.assertEqual(ai_status["pending_difficulty"], 0)

        with patch(
            "classroom_app.services.wrong_question_summary_service._load_wrong_summary_job",
            return_value={"status": "queued"},
        ):
            active_status = _build_ai_status(
                {
                    "assignment": {"id": "assignment-1"},
                    "questions_signature": "signature-1",
                },
                stats,
                _score_based_difficulty_summary(_build_score_based_hard_questions(stats)),
            )

        self.assertTrue(active_status["is_active"])
        self.assertEqual(active_status["job_status"], "queued")

        with patch(
            "classroom_app.services.wrong_question_summary_service._load_wrong_summary_job",
            return_value={"status": "completed", "error_message": ""},
        ):
            incomplete_completed_status = _build_ai_status(
                {
                    "assignment": {"id": "assignment-1"},
                    "questions_signature": "signature-1",
                },
                stats,
                _score_based_difficulty_summary(_build_score_based_hard_questions(stats)),
            )

        self.assertFalse(incomplete_completed_status["is_active"])
        self.assertEqual(incomplete_completed_status["job_status"], "failed")
        self.assertIn("没有生成全部", incomplete_completed_status["message"])

    def test_sparse_fill_answers_use_local_cluster_without_ai_call(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            @contextmanager
            def connect():
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                finally:
                    conn.close()

            questions = _extract_exam_questions(
                {
                    "pages": [
                        {
                            "name": "Basics",
                            "questions": [
                                {
                                    "id": "q1",
                                    "type": "text",
                                    "text": "Congestion window term?",
                                    "answer": "congestion",
                                    "points": 1,
                                }
                            ],
                        }
                    ]
                }
            )
            submissions = [
                {
                    "id": 1,
                    "student_name": "Student A",
                    "status": "submitted",
                    "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": "congestion"}]}),
                    "feedback_md": _feedback((1, 0, 1)),
                },
                {
                    "id": 2,
                    "student_name": "Student B",
                    "status": "submitted",
                    "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": ""}]}),
                    "feedback_md": _feedback((1, 0, 1)),
                },
            ]
            stats = _build_question_error_stats(questions, submissions)

            with patch(
                "classroom_app.services.wrong_question_summary_service.get_db_connection",
                connect,
            ), patch(
                "classroom_app.services.wrong_question_summary_service._generate_text_wrong_clusters",
                new=AsyncMock(),
            ) as generate_text:
                asyncio.run(_attach_text_answer_clusters("assignment-1", stats, allow_generate=True))

            with connect() as conn:
                row = conn.execute(
                    "SELECT result_json FROM assignment_wrong_answer_ai_cache WHERE assignment_id = 'assignment-1'"
                ).fetchone()

            self.assertEqual(stats[0]["text_cluster_status"], "local")
            generate_text.assert_not_awaited()
            self.assertEqual([group["source"] for group in stats[0]["top_wrong_answers"]], ["local", "local"])
            self.assertIsNotNone(row)
            cached = json.loads(row["result_json"])
            self.assertTrue(cached["local_only"])
            self.assertEqual(cached["groups"][0]["source"], "local")
        finally:
            try:
                os.remove(db_path)
            except OSError:
                pass

    def test_empty_ai_groups_fall_back_to_local_groups_and_cache(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            @contextmanager
            def connect():
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                finally:
                    conn.close()

            questions = _extract_exam_questions(
                {
                    "pages": [
                        {
                            "name": "Basics",
                            "questions": [
                                {
                                    "id": "q1",
                                    "type": "text",
                                    "text": "Default HTTP port?",
                                    "answer": "80",
                                    "points": 1,
                                }
                            ],
                        }
                    ]
                }
            )
            submissions = [
                {
                    "id": idx,
                    "student_name": f"Student {idx}",
                    "status": "submitted",
                    "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": f"808{idx}"}]}),
                    "feedback_md": _feedback((1, 0, 1)),
                }
                for idx in range(3)
            ]
            stats = _build_question_error_stats(questions, submissions)

            with patch(
                "classroom_app.services.wrong_question_summary_service.get_db_connection",
                connect,
            ), patch(
                "classroom_app.services.wrong_question_summary_service._generate_text_wrong_clusters",
                new=AsyncMock(return_value={"groups": []}),
            ):
                asyncio.run(_attach_text_answer_clusters("assignment-1", stats, allow_generate=True))

            with connect() as conn:
                row = conn.execute(
                    "SELECT result_json FROM assignment_wrong_answer_ai_cache WHERE assignment_id = 'assignment-1'"
                ).fetchone()

            self.assertEqual(stats[0]["text_cluster_status"], "fallback")
            self.assertIn("AI", stats[0]["text_cluster_error"])
            self.assertEqual(stats[0]["top_wrong_answers"][0]["source"], "local_fallback")
            self.assertIsNotNone(row)
            cached = json.loads(row["result_json"])
            self.assertTrue(cached["fallback"])
            self.assertEqual(cached["groups"][0]["source"], "local_fallback")
        finally:
            try:
                os.remove(db_path)
            except OSError:
                pass

    def test_failed_job_with_cached_fallback_is_reported_as_usable_completion(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "text",
                                "text": "Default HTTP port?",
                                "answer": "80",
                                "points": 1,
                            }
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": ""}]}),
                "feedback_md": _feedback((1, 0, 1)),
            }
        ]
        stats = _build_question_error_stats(questions, submissions)
        stats[0]["text_cluster_status"] = "fallback_cached"
        stats[0]["text_cluster_error"] = "AI 未返回可用错答分组，已先展示本地错答分组。"

        with patch(
            "classroom_app.services.wrong_question_summary_service._load_wrong_summary_job",
            return_value={
                "assignment_id": "assignment-1",
                "status": "failed",
                "error_message": "第 1 题错答归集失败",
            },
        ):
            ai_status = _build_ai_status(
                {
                    "assignment": {"id": "assignment-1"},
                    "questions_signature": "signature-1",
                },
                stats,
                _score_based_difficulty_summary(_build_score_based_hard_questions(stats)),
            )

        self.assertFalse(ai_status["needs_ai"])
        self.assertFalse(ai_status["is_active"])
        self.assertEqual(ai_status["job_status"], "completed")
        self.assertEqual(ai_status["fallback_text_questions"], 1)
        self.assertIn("本地错答分组", ai_status["message"])

    def test_timed_out_ai_groups_fall_back_to_local_groups_and_cache(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            @contextmanager
            def connect():
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                finally:
                    conn.close()

            async def slow_cluster(*args, **kwargs):
                await asyncio.sleep(0.2)
                return {"groups": [{"label": "8080", "count": 1, "examples": ["8080"]}]}

            questions = _extract_exam_questions(
                {
                    "pages": [
                        {
                            "name": "Basics",
                            "questions": [
                                {
                                    "id": "q1",
                                    "type": "textarea",
                                    "text": "Explain the default HTTP port.",
                                    "answer": "HTTP uses port 80 by default.",
                                    "points": 1,
                                }
                            ],
                        }
                    ]
                }
            )
            submissions = [
                {
                    "id": 1,
                    "student_name": "Student A",
                    "status": "submitted",
                    "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": "It uses 8080."}]}),
                    "feedback_md": _feedback((1, 0, 1)),
                }
            ]
            stats = _build_question_error_stats(questions, submissions)

            with patch(
                "classroom_app.services.wrong_question_summary_service.get_db_connection",
                connect,
            ), patch(
                "classroom_app.services.wrong_question_summary_service.WRONG_SUMMARY_QUESTION_TIMEOUT_SECONDS",
                0.01,
            ), patch(
                "classroom_app.services.wrong_question_summary_service._generate_text_wrong_clusters",
                slow_cluster,
            ):
                asyncio.run(_attach_text_answer_clusters("assignment-1", stats, allow_generate=True))

            with connect() as conn:
                cache_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM assignment_wrong_answer_ai_cache"
                ).fetchone()["count"]

            self.assertEqual(stats[0]["text_cluster_status"], "fallback")
            self.assertIn("超过", stats[0]["text_cluster_error"])
            self.assertEqual(stats[0]["top_wrong_answers"][0]["source"], "local_fallback")
            self.assertEqual(cache_count, 1)
        finally:
            try:
                os.remove(db_path)
            except OSError:
                pass

    def test_reorganize_clear_only_removes_ai_cache_and_jobs(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            @contextmanager
            def connect():
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                finally:
                    conn.close()

            with connect() as conn:
                ensure_wrong_summary_cache_tables(conn)
                conn.execute("CREATE TABLE submissions (id INTEGER PRIMARY KEY, assignment_id TEXT, answers_json TEXT)")
                conn.execute(
                    "INSERT INTO submissions (id, assignment_id, answers_json) VALUES (1, 'assignment-1', '{}')"
                )
                conn.execute(
                    """
                    INSERT INTO assignment_wrong_answer_ai_cache (
                        assignment_id, question_key, answer_signature, prompt_version, result_json
                    ) VALUES ('assignment-1', 'q1', 'sig-1', 'v-test', '{}')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO assignment_wrong_answer_ai_cache (
                        assignment_id, question_key, answer_signature, prompt_version, result_json
                    ) VALUES ('assignment-2', 'q1', 'sig-2', 'v-test', '{}')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO assignment_wrong_summary_jobs (
                        assignment_id, teacher_id, questions_signature, prompt_version, status
                    ) VALUES ('assignment-1', 1, 'paper-sig', 'v-test', 'queued')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO assignment_wrong_summary_jobs (
                        assignment_id, teacher_id, questions_signature, prompt_version, status
                    ) VALUES ('assignment-2', 1, 'paper-sig', 'v-test', 'queued')
                    """
                )
                conn.commit()

            with patch(
                "classroom_app.services.wrong_question_summary_service.get_db_connection",
                connect,
            ):
                result = _clear_assignment_wrong_summary_ai_state("assignment-1", "paper-sig")

            with connect() as conn:
                remaining_cache = conn.execute(
                    "SELECT assignment_id FROM assignment_wrong_answer_ai_cache ORDER BY assignment_id"
                ).fetchall()
                remaining_jobs = conn.execute(
                    "SELECT assignment_id FROM assignment_wrong_summary_jobs ORDER BY assignment_id"
                ).fetchall()
                submission_count = conn.execute("SELECT COUNT(*) AS count FROM submissions").fetchone()["count"]

            self.assertEqual(result["cleared_cache_rows"], 1)
            self.assertEqual(result["cleared_job_rows"], 1)
            self.assertEqual([row["assignment_id"] for row in remaining_cache], ["assignment-2"])
            self.assertEqual([row["assignment_id"] for row in remaining_jobs], ["assignment-2"])
            self.assertEqual(submission_count, 1)
        finally:
            try:
                os.remove(db_path)
            except OSError:
                pass

    def test_interrupted_wrong_summary_jobs_are_failed_on_startup(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            @contextmanager
            def connect():
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                finally:
                    conn.close()

            with connect() as conn:
                ensure_wrong_summary_cache_tables(conn)
                for idx, status in enumerate(["queued", "running", "completed"], start=1):
                    conn.execute(
                        """
                        INSERT INTO assignment_wrong_summary_jobs (
                            assignment_id, teacher_id, questions_signature, prompt_version,
                            status, run_token
                        ) VALUES (?, 1, 'paper-sig', ?, ?, ?)
                        """,
                        (f"assignment-{idx}", PROMPT_VERSION, status, f"token-{idx}"),
                    )
                conn.commit()

            with patch(
                "classroom_app.services.wrong_question_summary_service.get_db_connection",
                connect,
            ):
                reclaimed = expire_interrupted_wrong_summary_jobs()

            with connect() as conn:
                rows = conn.execute(
                    "SELECT assignment_id, status, error_message FROM assignment_wrong_summary_jobs ORDER BY id"
                ).fetchall()

            self.assertEqual(reclaimed, 2)
            self.assertEqual(rows[0]["status"], "failed")
            self.assertIn("重新", rows[0]["error_message"])
            self.assertEqual(rows[1]["status"], "failed")
            self.assertEqual(rows[2]["status"], "completed")
        finally:
            try:
                os.remove(db_path)
            except OSError:
                pass

    def test_stale_wrong_summary_job_token_cannot_write_ai_cache(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            @contextmanager
            def connect():
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                finally:
                    conn.close()

            with connect() as conn:
                ensure_wrong_summary_cache_tables(conn)
                conn.execute(
                    """
                    INSERT INTO assignment_wrong_summary_jobs (
                        assignment_id, teacher_id, questions_signature, prompt_version,
                        status, run_token
                    ) VALUES ('assignment-1', 1, 'paper-sig', ?, 'running', 'new-token')
                    """,
                    (PROMPT_VERSION,),
                )
                conn.commit()

            questions = _extract_exam_questions(
                {
                    "pages": [
                        {
                            "name": "Basics",
                            "questions": [
                                {
                                    "id": "q1",
                                    "type": "text",
                                    "text": "Default HTTP port?",
                                    "answer": "80",
                                    "points": 1,
                                }
                            ],
                        }
                    ]
                }
            )
            submissions = [
                {
                    "id": 1,
                    "student_name": "Student A",
                    "status": "submitted",
                    "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": "8080"}]}),
                    "feedback_md": _feedback((1, 0, 1)),
                }
            ]
            stats = _build_question_error_stats(questions, submissions)

            with patch(
                "classroom_app.services.wrong_question_summary_service.get_db_connection",
                connect,
            ), patch(
                "classroom_app.services.wrong_question_summary_service._generate_text_wrong_clusters",
                new=AsyncMock(
                    return_value={
                        "groups": [
                            {
                                "label": "8080",
                                "count": 1,
                                "examples": ["8080"],
                                "likely_issue": "Confused with a common dev port.",
                            }
                        ]
                    }
                ),
            ):
                asyncio.run(
                    _attach_text_answer_clusters(
                        "assignment-1",
                        stats,
                        allow_generate=True,
                        questions_signature="paper-sig",
                        job_run_token="old-token",
                    )
                )

            with connect() as conn:
                cache_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM assignment_wrong_answer_ai_cache"
                ).fetchone()["count"]

            self.assertEqual(stats[0]["text_cluster_status"], "stale")
            self.assertEqual(cache_count, 0)
        finally:
            try:
                os.remove(db_path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
