"""Decorative placeholders must never announce fake loading content."""
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from classroom_app.lq_components import lq_props


class SkeletonTests(unittest.TestCase):
    def test_real_macro_is_hidden_and_not_focusable(self):
        env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "templates"),
                          autoescape=True, undefined=StrictUndefined)
        env.globals["lq_props"] = lq_props
        html = str(env.get_template("macros/lq/skeleton.html").module.lq_skeleton(
            lines=3, attrs={"aria-label": "Fake content", "aria-hidden": False, "aria-busy": True}))
        self.assertIn('aria-hidden="true"', html)
        self.assertNotIn('aria-label', html)
        self.assertNotIn('aria-busy', html)
        self.assertNotIn('tabindex', html)
        self.assertEqual(html.count('class="lq-skeleton__part"'), 3)

    def test_bounds_and_shapes(self):
        for lines in (0, 9, 1.5, True, "3"):
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                lq_props("skeleton", lines=lines)
        with self.assertRaises(ValueError):
            lq_props("skeleton", shape="avatar", lines=3)
