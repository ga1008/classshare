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
    _attach_assignment_knowledge_analysis,
    _attach_class_knowledge_comparison,
    _attach_text_answer_clusters,
    _answer_value,
    _answers_by_question,
    _build_ai_status,
    _build_local_knowledge_analysis,
    _build_question_error_stats,
    _build_score_based_hard_questions,
    _clear_assignment_wrong_summary_ai_state,
    _extract_exam_questions,
    _get_answer_record,
    _mark_wrong_summary_job_running_with_connection,
    _normalize_knowledge_analysis_result,
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
    def _patch_engine(self, value: str):
        import classroom_app.services.wrong_question_summary_service as service

        original = service.get_configured_db_engine
        service.get_configured_db_engine = lambda: value
        return service, original

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
        self.assertEqual(
            [item["student_name"] for item in q1_bars["A. 5 layers"]["details"]],
            ["Student A", "Student B"],
        )
        self.assertEqual(q1_bars["A. 5 layers"]["details"][0]["submission_id"], 1)
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
        self.assertEqual(
            [item["student_name"] for item in q2_bars["A. TCP"]["details"]],
            ["Student A", "Student B"],
        )
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

    def test_local_knowledge_analysis_builds_mastery_points_from_all_questions(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Network",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "radio",
                                "text": "Which protocol assigns IP addresses automatically?",
                                "options": ["A. DHCP", "B. DNS", "C. ARP"],
                                "answer": "A",
                                "points": 1,
                                "knowledge_points": ["DHCP 地址获取"],
                            },
                            {
                                "id": "q2",
                                "type": "text",
                                "text": "Default HTTP port?",
                                "answer": "80",
                                "points": 1,
                                "knowledge_points": ["应用层端口"],
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
                    {"answers": [{"question_id": "q1", "answer": "B. DNS"}, {"question_id": "q2", "answer": "80"}]},
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 0, 1), (2, 1, 1)),
            },
            {
                "id": 2,
                "student_name": "Student B",
                "status": "submitted",
                "answers_json": json.dumps(
                    {"answers": [{"question_id": "q1", "answer": "C. ARP"}, {"question_id": "q2", "answer": "80"}]},
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 0, 1), (2, 1, 1)),
            },
        ]

        stats = _build_question_error_stats(questions, submissions)
        analysis = _build_local_knowledge_analysis(stats)
        by_name = {item["name"]: item for item in analysis["points"]}

        self.assertEqual(by_name["DHCP 地址获取"]["mastery"], 0)
        self.assertEqual(by_name["DHCP 地址获取"]["tone"], "danger")
        self.assertEqual(by_name["应用层端口"]["mastery"], 100)
        self.assertEqual(by_name["应用层端口"]["tone"], "mastered")
        self.assertIn("DHCP 地址获取", analysis["summary"])

    def test_exam_section_titles_are_not_used_as_knowledge_points(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "六",
                        "questions": [
                            {
                                "id": "q37",
                                "type": "text",
                                "text": "TCP 的拥塞窗口 cwnd 大小与传输轮次 n 的关系如下表所示。",
                                "answer": "慢启动、拥塞避免、快速恢复",
                                "points": 19,
                            }
                        ],
                    },
                    {
                        "name": "综合题（共 2 小题，第 1 小题 19 分，第 2 小题 11 分，共 30 分）",
                        "questions": [
                            {
                                "id": "q38",
                                "type": "radio",
                                "text": "HTTP 默认端口是多少？",
                                "options": ["A. 80", "B. 53", "C. 443"],
                                "answer": "A",
                                "points": 5,
                            }
                        ],
                    },
                    {
                        "name": "每小题 10 分",
                        "questions": [
                            {
                                "id": "q39",
                                "type": "radio",
                                "text": "DNS 主要用于完成哪项工作？",
                                "options": ["A. 域名解析", "B. IP 分配", "C. 拥塞控制"],
                                "answer": "A",
                                "points": 10,
                            }
                        ],
                    },
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps(
                    {"answers": [{"question_id": "q38", "answer": "B. 53"}, {"question_id": "q39", "answer": "B. IP 分配"}]},
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((37, 10, 19), (38, 0, 5), (39, 0, 10)),
            }
        ]

        self.assertTrue(all(not question["knowledge_points"] for question in questions))
        stats = _build_question_error_stats(questions, submissions)
        analysis = _build_local_knowledge_analysis(stats, status="pending")

        self.assertEqual(analysis["points"], [])
        self.assertEqual(analysis["tested_point_count"], 0)

    def test_ai_knowledge_analysis_rejects_exam_section_labels(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "六",
                        "questions": [
                            {
                                "id": "q37",
                                "type": "text",
                                "text": "TCP 的拥塞窗口 cwnd 大小与传输轮次 n 的关系如下表所示。",
                                "answer": "慢启动、拥塞避免、快速恢复",
                                "points": 19,
                            }
                        ],
                    }
                ]
            }
        )
        stats = _build_question_error_stats(
            questions,
            [
                {
                    "id": 1,
                    "student_name": "Student A",
                    "status": "submitted",
                    "answers_json": json.dumps({"answers": [{"question_id": "q37", "answer": "慢启动"}]}, ensure_ascii=False),
                    "feedback_md": _feedback((37, 8, 19)),
                }
            ],
        )

        analysis = _normalize_knowledge_analysis_result(
            {
                "summary": "结构性标题不应作为知识点。",
                "knowledge_points": [
                    {"name": "六", "mastery": 80, "reason": "大题编号", "evidence_questions": [37]},
                    {"name": "每小题 10 分", "mastery": 80, "reason": "分值说明", "evidence_questions": [37]},
                    {"name": "综合题（共 2 小题）", "mastery": 90, "reason": "题型标题", "evidence_questions": [37]},
                ],
            },
            stats,
            source_label="ai",
        )

        self.assertEqual(analysis["points"], [])
        self.assertEqual(analysis["source"], "local_fallback")

    def test_knowledge_analysis_ai_result_is_normalized_and_cached(self):
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
                            "name": "Network",
                            "questions": [
                                {
                                    "id": "q1",
                                    "type": "radio",
                                    "text": "Which protocol assigns IP addresses automatically?",
                                    "options": ["A. DHCP", "B. DNS", "C. ARP"],
                                    "answer": "A",
                                    "points": 1,
                                    "knowledge_points": ["DHCP 地址获取"],
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
                    "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": "B. DNS"}]}),
                    "feedback_md": _feedback((1, 0, 1)),
                }
            ]
            stats = _build_question_error_stats(questions, submissions)
            source = {
                "assignment": {"id": "assignment-1", "title": "Network Quiz"},
                "paper": {"title": "Paper A"},
                "stats": {"submitted_count": 1},
                "questions_signature": "paper-sig",
            }

            with patch(
                "classroom_app.services.wrong_question_summary_service.get_db_connection",
                connect,
            ), patch(
                "classroom_app.services.wrong_question_summary_service._generate_assignment_knowledge_analysis",
                new=AsyncMock(
                    return_value={
                        "summary": "DHCP 地址获取薄弱。",
                        "knowledge_points": [
                            {
                                "name": "DHCP 地址获取",
                                "mastery": 42,
                                "reason": "把 DHCP 与 DNS/ARP 混淆。",
                                "evidence_questions": [1],
                                "risk_level": "danger",
                            }
                        ],
                        "weak_points": [
                            {
                                "name": "DHCP 地址获取",
                                "issue": "不能区分地址分配和域名解析。",
                                "questions": [1],
                                "priority": "high",
                            }
                        ],
                    }
                ),
            ) as generate_analysis:
                analysis = asyncio.run(
                    _attach_assignment_knowledge_analysis(source, stats, allow_generate=True)
                )
                cached = asyncio.run(
                    _attach_assignment_knowledge_analysis(source, stats, allow_generate=False)
                )

            with connect() as conn:
                row = conn.execute(
                    "SELECT exam_paper_id, result_json FROM exam_paper_difficulty_ai_cache"
                ).fetchone()

            self.assertEqual(analysis["status"], "completed")
            self.assertEqual(analysis["points"][0]["tone"], "danger")
            self.assertEqual(analysis["weak_points"][0]["issue"], "不能区分地址分配和域名解析。")
            self.assertEqual(cached["status"], "completed")
            self.assertEqual(cached["points"][0]["name"], "DHCP 地址获取")
            self.assertEqual(generate_analysis.await_count, 1)
            self.assertEqual(row["exam_paper_id"], "assignment-knowledge:assignment-1")
            self.assertIn("DHCP 地址获取", json.loads(row["result_json"])["points"][0]["name"])
        finally:
            try:
                os.remove(db_path)
            except OSError:
                pass

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

    def test_text_score_uses_visible_ordinal_not_numeric_fragment_from_question_id(self):
        raw_questions = []
        for index in range(1, 13):
            raw_questions.append(
                {
                    "id": "set2_blank_02" if index == 12 else f"set2_single_{index:02d}",
                    "type": "text",
                    "text": (
                        "Most common internal gateway protocols include RIP and ____."
                        if index == 12
                        else f"Question {index}"
                    ),
                    "answer": "OSPF" if index == 12 else "A",
                    "points": 1,
                }
            )
        questions = _extract_exam_questions({"pages": [{"name": "Paper", "questions": raw_questions}]})
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps(
                    {
                        "answers": [
                            {"question_id": "set2_blank_02", "answer": "OSPF"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((2, 0, 1), (12, 1, 1)),
            }
        ]

        stats = _build_question_error_stats(questions, submissions)
        q12 = next(item for item in stats if item["question"]["ordinal"] == 12)

        self.assertEqual(q12["wrong_count"], 0)
        self.assertEqual(q12["correct_count"], 1)
        self.assertEqual(q12["average_score_percent"], 100)

    def test_answer_lookup_uses_exact_and_ordinal_buckets_not_id_numeric_fragments(self):
        raw_questions = []
        answer_items = []
        for index in range(1, 13):
            question_id = "set2_blank_02" if index == 12 else f"set2_single_{index:02d}"
            raw_questions.append(
                {
                    "id": question_id,
                    "type": "text",
                    "text": f"Question {index}",
                    "answer": "OSPF" if index == 12 else "A",
                    "points": 1,
                }
            )
            if index == 12:
                answer_items.append({"answer": "OSPF"})
            else:
                answer_items.append({"question_id": question_id, "answer": f"answer-{index}"})
        questions = _extract_exam_questions({"pages": [{"name": "Paper", "questions": raw_questions}]})
        answer_map = _answers_by_question(json.dumps({"answers": answer_items}, ensure_ascii=False))

        q12_answer = _get_answer_record(answer_map, questions[11])

        self.assertEqual(_answer_value(q12_answer), "OSPF")

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

    def test_knowledge_only_pending_does_not_fail_completed_job(self):
        # 主观题已完成、仅知识点掌握度归因走了本地兜底时，整体应判定为完成而非失败。
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
                                "options": ["A. 5 layers", "B. 7 layers"],
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
                "student_name": "Student 1",
                "status": "submitted",
                "answers_json": json.dumps(
                    {"answers": [{"question_id": "q1", "answer": "A"}]}, ensure_ascii=False
                ),
                "feedback_md": "",
            }
        ]
        stats = _build_question_error_stats(questions, submissions)
        knowledge_fallback = {
            "status": "fallback",
            "source": "local_fallback",
            "points": [],
            "weak_points": [],
            "error": "AI 知识点归因失败",
        }
        with patch(
            "classroom_app.services.wrong_question_summary_service._load_wrong_summary_job",
            return_value={
                "status": "completed",
                "error_message": "",
                "run_token": "token-1",
                "assignment_id": "assignment-1",
            },
        ):
            ai_status = _build_ai_status(
                {"assignment": {"id": "assignment-1"}, "questions_signature": "signature-1"},
                stats,
                _score_based_difficulty_summary(_build_score_based_hard_questions(stats)),
                knowledge_fallback,
            )

        self.assertEqual(ai_status["pending_text_questions"], 0)
        self.assertEqual(ai_status["pending_difficulty"], 1)
        self.assertNotEqual(ai_status["job_status"], "failed")
        self.assertEqual(ai_status["job_status"], "completed")
        self.assertFalse(ai_status["is_active"])

    def test_class_knowledge_comparison_maps_history_average_and_delta(self):
        knowledge = {
            "points": [
                {"name": "TCP拥塞控制", "label": "TCP拥塞控制", "mastery": 70, "tone": "warning"},
                {"name": "VLAN划分", "label": "VLAN划分", "mastery": 50, "tone": "danger"},
            ]
        }
        source = {"assignment": {"id": 5, "class_offering_id": 9}}
        with patch(
            "classroom_app.services.wrong_question_summary_service._load_class_knowledge_history",
            return_value={
                "point_stats": {"TCP拥塞控制": {"total": 160.0, "count": 2}},
                "assignment_count": 2,
                "titles": ["期中考试", "随堂测验"],
            },
        ):
            result = _attach_class_knowledge_comparison(source, knowledge)

        comparison = result["comparison"]
        self.assertEqual(comparison["assignment_count"], 2)
        self.assertEqual(len(comparison["points"]), 1)
        entry = comparison["points"][0]
        self.assertEqual(entry["name"], "TCP拥塞控制")
        self.assertEqual(entry["class_average"], 80)
        self.assertEqual(entry["delta"], -10)
        self.assertEqual(entry["sample_count"], 2)
        # 当前点也回填了历史均值，供柱状图详情逐点展示
        self.assertEqual(result["points"][0]["class_average"], 80)
        self.assertEqual(result["points"][0]["class_delta"], -10)

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

    def test_wrong_summary_postgres_schema_validation_does_not_run_sqlite_ddl(self):
        import classroom_app.services.wrong_question_summary_service as service

        class FakeCursor:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return list(self._rows)

        class FakeConnection:
            def __init__(self):
                self.sql_calls: list[str] = []

            def execute(self, sql, params=()):
                normalized = " ".join(str(sql).split())
                self.sql_calls.append(normalized)
                if "information_schema.columns" not in normalized:
                    raise AssertionError(f"Unexpected SQL: {normalized}")
                table = params[0]
                return FakeCursor([{"name": column} for column in service.WRONG_SUMMARY_POSTGRES_REQUIRED_COLUMNS[table]])

        conn = FakeConnection()
        service_module, original = self._patch_engine("postgres")
        try:
            ensure_wrong_summary_cache_tables(conn)
        finally:
            service_module.get_configured_db_engine = original

        sql_text = "\n".join(conn.sql_calls)
        self.assertIn("information_schema.columns", sql_text)
        self.assertNotIn("CREATE TABLE", sql_text)
        self.assertNotIn("PRAGMA", sql_text)

    def test_wrong_summary_schema_rejects_unsupported_engine_before_sqlite_ddl(self):
        class FakeConnection:
            def execute(self, sql, params=()):
                raise AssertionError(f"SQLite DDL must not run for unsupported engine: {sql}")

        service_module, original = self._patch_engine("mysql")
        try:
            with self.assertRaisesRegex(ValueError, "Unsupported wrong-summary database engine"):
                ensure_wrong_summary_cache_tables(FakeConnection())
        finally:
            service_module.get_configured_db_engine = original

    def test_wrong_summary_sqlite_claim_requires_queued_status(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            ensure_wrong_summary_cache_tables(conn)
            conn.execute(
                """
                INSERT INTO assignment_wrong_summary_jobs (
                    assignment_id, teacher_id, questions_signature, prompt_version,
                    status, run_token
                ) VALUES ('assignment-1', 1, 'paper-sig', ?, 'queued', 'token-1')
                """,
                (PROMPT_VERSION,),
            )
            conn.execute(
                """
                INSERT INTO assignment_wrong_summary_jobs (
                    assignment_id, teacher_id, questions_signature, prompt_version,
                    status, run_token
                ) VALUES ('assignment-2', 1, 'paper-sig', ?, 'completed', 'token-2')
                """,
                (PROMPT_VERSION,),
            )
            conn.commit()

            claimed = _mark_wrong_summary_job_running_with_connection(
                conn,
                assignment_id="assignment-1",
                questions_signature="paper-sig",
                run_token="token-1",
                now="2026-01-01T00:10:00",
                engine="sqlite",
            )
            completed_claim = _mark_wrong_summary_job_running_with_connection(
                conn,
                assignment_id="assignment-2",
                questions_signature="paper-sig",
                run_token="token-2",
                now="2026-01-01T00:10:00",
                engine="sqlite",
            )
            rows = conn.execute(
                "SELECT assignment_id, status, started_at FROM assignment_wrong_summary_jobs ORDER BY assignment_id"
            ).fetchall()

            self.assertTrue(claimed)
            self.assertFalse(completed_claim)
            self.assertEqual("running", rows[0]["status"])
            self.assertEqual("2026-01-01T00:10:00", rows[0]["started_at"])
            self.assertEqual("completed", rows[1]["status"])
        finally:
            conn.close()

    def test_wrong_summary_postgres_claim_uses_skip_locked_and_returning(self):
        class FakeCursor:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        class FakeConnection:
            def __init__(self):
                self.calls: list[tuple[str, tuple]] = []
                self.commits = 0

            def execute(self, sql, params=()):
                normalized = " ".join(str(sql).split())
                self.calls.append((normalized, tuple(params)))
                return FakeCursor({"id": 9})

            def commit(self):
                self.commits += 1

        conn = FakeConnection()

        claimed = _mark_wrong_summary_job_running_with_connection(
            conn,
            assignment_id="assignment-1",
            questions_signature="paper-sig",
            run_token="token-1",
            now="2026-01-01T00:10:00",
            engine="postgres",
        )

        self.assertTrue(claimed)
        self.assertEqual(1, conn.commits)
        self.assertEqual(1, len(conn.calls))
        sql, params = conn.calls[0]
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)
        self.assertIn("RETURNING assignment_wrong_summary_jobs.id", sql)
        self.assertIn("AND status = ?", sql)
        self.assertEqual(
            (
                "assignment-1",
                "paper-sig",
                PROMPT_VERSION,
                "token-1",
                "queued",
                "running",
                "2026-01-01T00:10:00",
                "2026-01-01T00:10:00",
            ),
            params,
        )


if __name__ == "__main__":
    unittest.main()
