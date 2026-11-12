"""The downloadable/course-authoring template must follow the real importer."""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import unittest

from classroom_app.services.assessment_question_contract import VALID_QUESTION_TYPES
from classroom_app.services.deterministic_exam_grading import build_deterministic_grading_evidence
from classroom_app.services.exam_json_service import (
    EXAM_JSON_TEMPLATE, get_exam_json_template_text, parse_exam_json_text,
    strip_exam_scoring_for_student,
)


class AssessmentJsonTemplateTests(unittest.TestCase):
    def test_download_and_repository_template_are_identical_and_fully_importable(self):
        text = get_exam_json_template_text()
        artifact = Path(__file__).resolve().parents[1] / 'docs/templates/assessment-eight-types.json'
        self.assertEqual(artifact.read_bytes(), text.encode('utf-8'))
        normalized = parse_exam_json_text(text)
        questions = [q for p in normalized['questions']['pages'] for q in p['questions']]
        self.assertEqual({q['type'] for q in questions}, VALID_QUESTION_TYPES)
        self.assertEqual(len(questions), 8)
        self.assertEqual(len({q['id'] for q in questions}), 8)
        self.assertEqual(sum(q['points'] for q in questions), 100)
        self.assertEqual(normalized['grading']['total_score'], 100)
        self.assertFalse(normalized['exam_config']['allow_student_ai'])
        self.assertEqual([q['type'] for q in questions if q.get('allow_ai')], ['comprehensive'])

    def test_objective_reference_answers_match_real_grader_and_student_copy_has_no_answers(self):
        normalized = parse_exam_json_text(get_exam_json_template_text())['questions']
        questions = [q for p in normalized['pages'] for q in p['questions']]
        answers = [{'question_id': q['id'], 'answer': q['answer']} for q in questions]
        evidence = build_deterministic_grading_evidence(normalized, answers)
        for question in questions:
            if question['type'] in {'radio', 'checkbox', 'boolean', 'text'}:
                self.assertEqual(evidence['fixed_scores'][question['id']]['fixed_score'], question['points'])
                if question.get('options'):
                    letters = {option.split('.')[0] for option in question['options']}
                    selected = question['answer'] if isinstance(question['answer'], list) else [question['answer']]
                    self.assertTrue(set(selected) <= letters)
        for page in strip_exam_scoring_for_student(normalized)['pages']:
            for question in page['questions']:
                self.assertNotIn('answer', question)
                self.assertNotIn('grading_guidance', question)
                for blank in question.get('blanks', []):
                    self.assertNotIn('answer', blank)

    def test_numbered_template_supports_stable_nonconsecutive_ids_and_rejects_ambiguity(self):
        payload = copy.deepcopy(EXAM_JSON_TEMPLATE)
        question = next(q for p in payload['pages'] for q in p['questions'] if q['type'] == 'text')
        question['text'] = question['text'].replace('{{2}}', '{{3}}')
        question['blanks'][1]['id'] = '3'
        normalized = parse_exam_json_text(json.dumps(payload))['questions']
        blank = next(q for p in normalized['pages'] for q in p['questions'] if q['type'] == 'text')
        self.assertEqual([b['id'] for b in blank['blanks']], ['1', '3'])
        self.assertEqual(blank['answer'], ['def', 'return'])
        question['text'] += ' 重复 {{1}}'
        with self.assertRaisesRegex(ValueError, '不能重复'):
            parse_exam_json_text(json.dumps(payload))

    def test_maintained_example_code_matches_its_stated_results(self):
        # Only execute code authored in this repository's fixed template, never
        # teacher imports or student answers.
        questions = [q for p in EXAM_JSON_TEMPLATE['pages'] for q in p['questions']]
        program = next(q for q in questions if q['type'] == 'code')
        source = re.search(r'```python\n(.*?)\n```', program['answer'], re.S).group(1)
        namespace = {}
        exec(compile(source, '<maintained-template>', 'exec'), namespace)
        for values, expected in (([1, 2, 3, 4], 6), ([], 0), ([-2, 3, 4], 2)):
            self.assertEqual(namespace['total_even'](values), expected)
        comprehensive = next(q for q in questions if q['type'] == 'comprehensive')
        source = re.search(r'```python\n(.*?)\n```', comprehensive['answer'], re.S).group(1)
        with redirect_stdout(io.StringIO()) as output:
            exec(compile(source, '<maintained-template>', 'exec'), {})
        self.assertEqual(output.getvalue().strip(), '5 60 20')


if __name__ == '__main__':
    unittest.main()
