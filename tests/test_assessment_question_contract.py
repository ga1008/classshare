from __future__ import annotations

import copy
import json
import unittest

from classroom_app.services.assessment_question_contract import normalize_numbered_blanks, normalize_numbered_answer_payload
from classroom_app.services.deterministic_exam_grading import build_deterministic_grading_evidence
from classroom_app.services.exam_json_service import (
    EXAM_JSON_TEMPLATE, build_exam_rubric_md, normalize_exam_json_payload,
    normalize_exam_scoring_payload, strip_exam_scoring_for_student,
)
from classroom_app.services.submission_assets import answers_have_content


class AssessmentQuestionContractTests(unittest.TestCase):
    def question(self, **values):
        return {"id": "q1", "type": "text", "text": "先 {{1}} 再 {{3}}。",
                "blanks": [{"id": "1", "answer": "def"}, {"id": "3", "answer": "return"}],
                "answer": ["def", "return"], "points": 10,
                "grading_guidance": "逐空评分", "deduction_points": "错误空扣分", **values}

    def paper(self, question):
        return {"pages": [{"name": "一", "questions": [question]}],
                "grading": {"total_score": 10, "description": "逐空评分", "style": "medium"}}

    def test_import_accepts_eight_types_and_aliases(self):
        for alias, expected in [("single_choice", "radio"), ("multi_choice", "checkbox"),
                                ("判断题", "boolean"), ("fill_blank", "text"),
                                ("short_answer", "textarea"), ("分析", "analysis"),
                                ("programming", "code"), ("综合题", "comprehensive")]:
            with self.subTest(alias=alias):
                question = self.question(type=alias, options=["A. 甲", "B. 乙"], answer="A")
                question.pop("blanks")
                normalized = normalize_exam_json_payload(self.paper(question))
                self.assertEqual(normalized["questions"]["pages"][0]["questions"][0]["type"], expected)

    def test_new_blank_contract_preserves_non_contiguous_ids_and_empty_draft_positions(self):
        question = self.question(blanks=[{"id": "1", "answer": ""}, {"id": "3", "answer": "return"}])
        result = normalize_numbered_blanks(question)
        self.assertEqual(result["answer"], ["", "return"])
        self.assertEqual(result["blanks"][1]["id"], "3")
        self.assertEqual(question["answer"], ["def", "return"], "normalizing must not mutate caller state")
        with self.assertRaisesRegex(ValueError, "缺少标准答案"):
            normalize_exam_scoring_payload(self.paper(question), require_complete=True)

    def test_complete_blank_contract_rejects_missing_extra_and_duplicate_ids(self):
        for change in [{"text": "只有 {{1}}"}, {"text": "{{1}} {{2}} {{3}}"}, {"text": "{{1}} {{3}} {{1}}"},
                       {"blanks": [{"id": "1", "answer": "a"}, {"id": "1", "answer": "b"}]},
                       {"blanks": [{"id": "0", "answer": "a"}]}, {"blanks": []}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_exam_scoring_payload(self.paper(self.question(**change)), require_complete=True)
        self.assertEqual(normalize_exam_scoring_payload(self.paper(self.question()), require_complete=True)
                         ["pages"][0]["questions"][0]["answer"], ["def", "return"])

    def test_legacy_fill_is_unchanged_and_input_is_not_mutated(self):
        legacy = {"id": "old", "type": "text", "text": "第一空___，第二空___", "answer": "甲；乙"}
        source = self.paper(legacy)
        original = copy.deepcopy(source)
        self.assertEqual(normalize_exam_scoring_payload(source)["pages"][0]["questions"][0], legacy)
        self.assertEqual(source, original)

    def test_nested_blank_answers_are_removed_from_student_projection(self):
        question = self.question()
        question["blanks"][0].update({"grading": {"secret": "secret"}, "explanation": "secret"})
        source = self.paper(question)
        safe = strip_exam_scoring_for_student(source)
        public = safe["pages"][0]["questions"][0]
        self.assertEqual(public["blanks"], [{"id": "1"}, {"id": "3"}])
        self.assertNotIn("answer", public)
        self.assertNotIn("secret", json.dumps(safe))
        self.assertEqual(source["pages"][0]["questions"][0]["blanks"][0]["answer"], "def")

    def test_subjective_variants_preserve_attachment_policy(self):
        for kind in ("textarea", "analysis", "code", "comprehensive"):
            question = self.question(type=kind, answer="```python\nprint(1)\n```",
                                     attachment_requirements={"enabled": True, "min_count": 1, "max_count": 2})
            question.pop("blanks")
            normalized = normalize_exam_json_payload(self.paper(question))["questions"]["pages"][0]["questions"][0]
            self.assertTrue(normalized["attachment_requirements"]["required"])
            self.assertEqual(normalized["answer"], question["answer"])

    def test_boolean_import_defaults_to_two_options_without_requiring_four(self):
        question = self.question(type="true_false", answer=False)
        question.pop("blanks")
        result = normalize_exam_json_payload(self.paper(question))["questions"]["pages"][0]["questions"][0]
        self.assertEqual(result["options"], ["正确", "错误"])
        self.assertEqual(result["answer"], "错误")
        evidence = build_deterministic_grading_evidence(self.paper(result), {"answers": [{"question_id": "q1", "answer": "错误"}]})
        self.assertEqual(evidence["fixed_scores"]["q1"]["fixed_score"], 10)

    def test_numbered_blank_grading_never_compacts_or_reorders_positions(self):
        for answer, ids, score in [(["def", "return"], ["1", "3"], 10),
                                   (["", "return"], ["1", "3"], None),
                                   (["return", "def"], ["3", "1"], None),
                                   (["def", "return", ""], ["1", "3", "4"], None),
                                   ("def、return", None, None), (["", ""], ["1", "3"], 0)]:
            with self.subTest(answer=answer):
                evidence = build_deterministic_grading_evidence(self.paper(self.question()),
                    {"answers": [{"question_id": "q1", "answer": answer, "blank_ids": ids}]})
                self.assertEqual(evidence["questions"][0]["fixed_score"], score)

    def test_numbered_numeric_blanks_match_independently(self):
        question = self.question(blanks=[{"id": "1", "answer": "0"}, {"id": "3", "answer": "57"}])
        evidence = build_deterministic_grading_evidence(self.paper(question),
            {"answers": [{"question_id": "q1", "answer": ["0.0", "57.0"], "blank_ids": ["1", "3"]}]})
        self.assertEqual(evidence["fixed_scores"]["q1"]["fixed_score"], 10)

    def test_blank_ids_alone_are_not_answer_content(self):
        self.assertFalse(answers_have_content([{"question_id": "q1", "type": "text", "blank_ids": ["1", "3"], "answer": ["", ""]}]))
        self.assertTrue(answers_have_content([{"question_id": "q1", "blank_ids": ["1", "3"], "answer": ["", "0"]}]))

    def test_numbered_submission_accepts_empty_initialization_and_keeps_partial_positions(self):
        paper = self.paper(self.question())
        result = normalize_numbered_answer_payload(paper, [{"question_id": "q1", "answer": ""}])
        self.assertEqual(result[0]["answer"], ["", ""])
        self.assertEqual(result[0]["blank_ids"], ["1", "3"])
        partial = [{"question_id": "q1", "answer": ["", "return"], "blank_ids": ["1", "3"]}]
        self.assertEqual(normalize_numbered_answer_payload(paper, partial), partial)
        self.assertEqual(normalize_numbered_answer_payload(paper, {"q1": ["def", ""]})["q1"]["answer"], ["def", ""])

    def test_numbered_submission_rejects_ambiguous_shapes_without_rewriting_input(self):
        paper = self.paper(self.question())
        for answer, ids in [("def, return", None), (["def"], None), (["def", {}], None),
                            (["def", "return"], ["3", "1"]), ({"1": "def"}, None)]:
            payload = [{"question_id": "q1", "answer": answer, "blank_ids": ids}]
            before = copy.deepcopy(payload)
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                normalize_numbered_answer_payload(paper, payload)
            self.assertEqual(payload, before)

    def test_legacy_submission_has_no_numbered_schema_validation(self):
        paper = self.paper({"id": "q1", "type": "text", "answer": "one;two"})
        payload = [{"question_id": "q1", "answer": "one;two"}]
        self.assertIs(normalize_numbered_answer_payload(paper, payload), payload)

    def test_draft_normalizer_preserves_legacy_bytes_and_rejects_new_bad_shape(self):
        from unittest.mock import Mock
        from fastapi import HTTPException
        from classroom_app.routers.homework_parts.common import _normalize_exam_numbered_answer_json
        conn = Mock()
        assignment = {"exam_paper_id": "paper"}
        conn.execute.return_value.fetchone.return_value = {"questions_json": json.dumps(self.paper({"id": "q1", "type": "text"}))}
        self.assertEqual(_normalize_exam_numbered_answer_json(conn, assignment, "legacy raw"), "legacy raw")
        conn.execute.return_value.fetchone.return_value = {"questions_json": json.dumps(self.paper(self.question()))}
        with self.assertRaises(HTTPException) as caught:
            _normalize_exam_numbered_answer_json(conn, assignment, json.dumps({"answers": [{"question_id": "q1", "answer": "def, return"}]}))
        self.assertEqual(caught.exception.status_code, 400)
        result = json.loads(_normalize_exam_numbered_answer_json(conn, assignment, json.dumps({"answers": [{"question_id": "q1", "answer": ""}]})))
        self.assertEqual(result["answers"][0]["answer"], ["", ""])

    def test_rubric_preserves_blank_numbers(self):
        result = build_exam_rubric_md(title="测试", exam_data=self.paper(self.question()))
        self.assertIn("（1）def；（3）return", result)

    def test_ai_normalization_keeps_types_and_structured_blank_answers(self):
        import ai_assistant
        result = ai_assistant._normalize_exam_generation_result(self.paper(self.question()))
        self.assertEqual(result["pages"][0]["questions"][0]["answer"], ["def", "return"])
        for alias, expected in [("判断", "boolean"), ("分析", "analysis"), ("programming", "code"), ("综合", "comprehensive")]:
            self.assertEqual(ai_assistant._normalize_exam_question_type(alias, [], "参考"), expected)
        self.assertEqual(ai_assistant._normalize_exam_question_type("multiple_choice", ["A", "B"], "A"), "radio")

    def test_export_and_wrong_summary_keep_empty_blank_number(self):
        from classroom_app.services import submission_export_docx_service as export
        from classroom_app.services import wrong_question_summary_service as wrong
        question = self.question()
        exported = export._export_question_from_exam_item(question, section_name="测试", number=1)
        self.assertEqual(exported.blanks, question["blanks"])
        self.assertEqual(export._correct_answer_text(exported), "（1）def；（3）return")
        self.assertEqual(wrong._format_student_answer(question, ["", "return"]), "（1）；（3）return")
        self.assertEqual(wrong._format_correct_answer(question), "（1）def；（3）return")

    def test_empty_legacy_type_is_not_reclassified_by_scoring_normalizer(self):
        source = self.paper({"id": "legacy", "type": "", "answer": "text"})
        self.assertEqual(normalize_exam_scoring_payload(source)["pages"][0]["questions"][0]["type"], "")

    def test_download_template_remains_importable_with_complete_grading(self):
        result = normalize_exam_json_payload(copy.deepcopy(EXAM_JSON_TEMPLATE), require_grading=True)
        blank = next(question for page in result["questions"]["pages"]
                     for question in page["questions"] if question["type"] == "text")
        self.assertEqual(blank["answer"], ["def", "return"])


if __name__ == "__main__":
    unittest.main()
