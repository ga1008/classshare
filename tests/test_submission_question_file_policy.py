import unittest

from classroom_app.routers.homework import (
    _allowed_file_types_for_submission_path,
    _is_allowed_assignment_submission_file,
    _question_id_from_submission_relative_path,
)


class SubmissionQuestionFilePolicyTests(unittest.TestCase):
    def test_extracts_question_id_from_question_attachment_path(self):
        self.assertEqual(
            _question_id_from_submission_relative_path("exam_question_files/exam_p3_q1/web.zip"),
            "exam_p3_q1",
        )
        self.assertIsNone(_question_id_from_submission_relative_path("web/final-exam-lost/pom.xml"))

    def test_question_policy_allows_archive_even_when_assignment_global_list_does_not(self):
        assignment_allowed = [".png", ".jpg", ".java", ".xml", ".yml", ".html"]
        policies = {"exam_p3_q1": {"allowed_file_types": [".zip", ".rar", ".7z"]}}

        self.assertTrue(
            _is_allowed_assignment_submission_file(
                "exam_question_files/exam_p3_q1/final-exam-lost.zip",
                "application/zip",
                assignment_allowed,
                policies,
            )
        )
        self.assertEqual(
            _allowed_file_types_for_submission_path(
                "exam_question_files/exam_p3_q1/final-exam-lost.zip",
                assignment_allowed,
                policies,
            ),
            [".zip", ".rar", ".7z"],
        )

    def test_question_policy_stays_stricter_than_assignment_global_list(self):
        assignment_allowed = [".png", ".jpg", ".java", ".xml"]
        policies = {"exam_p1_q1": {"allowed_file_types": [".png", ".jpg"]}}

        self.assertFalse(
            _is_allowed_assignment_submission_file(
                "exam_question_files/exam_p1_q1/answer.java",
                "text/x-java-source",
                assignment_allowed,
                policies,
            )
        )
        self.assertTrue(
            _is_allowed_assignment_submission_file(
                "web/final-exam-lost/src/main/java/App.java",
                "text/x-java-source",
                assignment_allowed,
                policies,
            )
        )


if __name__ == "__main__":
    unittest.main()
