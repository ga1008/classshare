"""Regression tests for student-only topbar personal-menu entries."""

import unittest

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _render_personal_menu(role: str) -> str:
    env = Environment(
        loader=FileSystemLoader("templates"),
        autoescape=select_autoescape(("html", "xml")),
    )
    template = env.get_template("partials/app_topbar_utility_actions.html")
    return template.render(user_info={"role": role})


class StudentTopbarEntriesTests(unittest.TestCase):
    def test_student_personal_menu_has_career_and_resume_entries(self):
        html = _render_personal_menu("student")

        self.assertIn('href="/career-path"', html)
        self.assertIn("职业路径", html)
        self.assertIn("推荐方向与准备清单", html)
        self.assertIn('href="/resume"', html)
        self.assertIn("个人简历", html)
        self.assertIn("资料优化与简历生成", html)

    def test_teacher_personal_menu_does_not_show_student_entries(self):
        html = _render_personal_menu("teacher")

        self.assertNotIn('href="/career-path"', html)
        self.assertNotIn("职业路径", html)
        self.assertNotIn('href="/resume"', html)
        self.assertNotIn("个人简历", html)


if __name__ == "__main__":
    unittest.main()
