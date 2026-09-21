"""Native disclosure structure and safety without application imports."""
import json
from pathlib import Path
import unittest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup
from classroom_app.lq_collapsible import lq_collapsible_props

ROOT = Path(__file__).resolve().parents[1]


class LqCollapsibleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=True, undefined=StrictUndefined)
        cls.env.globals['lq_props'] = lq_collapsible_props
        cls.macro = cls.env.get_template('macros/lq/collapsible.html').module.lq_collapsible

    def test_native_details_summary_and_default_open(self):
        html = str(self.macro('x', '说明'))
        self.assertIn('<details', html)
        self.assertIn('<summary', html)
        self.assertIn('open=""', html)
        self.assertIn('aria-controls="x--lq-content"', html)

    def test_each_explicit_guard_keeps_ssr_content_visible(self):
        for guard in ('keep_open', 'has_error', 'current', 'dirty'):
            with self.subTest(guard=guard):
                self.assertIn('open', lq_collapsible_props('collapsible', id='x', title='x', open=False, **{guard: True})['attrs'])
        self.assertNotIn('open', lq_collapsible_props('collapsible', id='x', title='x', open=False)['attrs'])

    def test_identity_resource_and_key_are_required_and_independent(self):
        for identity in ('teacher:1', 'student:1'):
            props = lq_collapsible_props('collapsible', id='x', title='x', persist={'identity': identity, 'resource': 'course:2', 'key': 'notes'})
            self.assertEqual(json.loads(props['attrs']['data-lq-persist']), [identity, 'course:2', 'notes'])
        for persist in ('global', {}, {'key': 'notes'}, {'identity': '', 'resource': '2', 'key': 'x'}):
            with self.assertRaises(ValueError):
                lq_collapsible_props('collapsible', id='x', title='x', persist=persist)

    def test_markup_text_and_attrs_cannot_become_html_or_override_core_state(self):
        html = str(self.macro('x', Markup('<b>标题</b>'), description=Markup('<img src=x>'), attrs={'aria-hidden': True, 'data-lq-mode': 'always'}))
        self.assertIn('&lt;b&gt;', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('aria-hidden="true" data-lq', html)
        props = lq_collapsible_props('collapsible', id='x', title='x', attrs={'aria-hidden': True, 'aria-label': 'wrong', 'data-lq-mode': 'always'})
        self.assertNotIn('aria-hidden', props['attrs'])
        self.assertNotIn('aria-label', props['attrs'])
        self.assertEqual(props['attrs']['data-lq-mode'], 'responsive')

    def test_invalid_states_attributes_and_ids_fail_closed(self):
        for props in ({'open': 1}, {'dirty': 'false'}, {'mode': 'x'}, {'attrs': []}, {'attrs': {'style': 'x'}}, {'attrs': {'onclick': 'x'}}, {'attrs': {'data-x': {}}}):
            with self.assertRaises(ValueError):
                self.macro('x', 'x', **props)
        with self.assertRaises(ValueError):
            self.macro('x--lq-content', 'x')

    def test_caller_uses_real_existing_input_not_a_serialized_html_prop(self):
        html = self.env.from_string("{% from 'macros/lq/collapsible.html' import lq_collapsible %}{% call lq_collapsible('x','编辑') %}<textarea name='draft'>原输入</textarea>{% endcall %}").render()
        self.assertIn("<textarea name='draft'>原输入</textarea>", html)
        self.assertNotIn('&lt;textarea', html)


if __name__ == '__main__':
    unittest.main()
