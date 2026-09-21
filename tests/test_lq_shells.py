from pathlib import Path
import unittest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup
from classroom_app.lq_shells import SHELL_KINDS, lq_shell_props, presentation_props

ROOT = Path(__file__).resolve().parents[1]


class LqShellTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=True, undefined=StrictUndefined)
        self.env.globals['lq_props'] = lambda component, **p: lq_shell_props(component, **p) if component in SHELL_KINDS else presentation_props(component, **p)
        self.m = self.env.get_template('macros/lq/shells.html').module

    def test_plain_text_even_when_marked_safe(self):
        html = str(self.m.lq_topbar('x', Markup('<img src=x>')))
        self.assertIn('&lt;img', html)
        self.assertNotIn('<img', html)

    def test_no_raw_attributes_or_html_bypass(self):
        for props in ({'attrs': {'onclick': 'x'}}, {'rawHTML': '<b>x</b>'}, {'attrs': {'title': {}}}):
            with self.assertRaises(ValueError):
                self.m.lq_topbar('x', 'x', **props)

    def test_editor_has_native_main_and_unique_panel_relations(self):
        html = str(self.m.lq_editor('exam', '试卷'))
        self.assertIn('<main id="exam--lq-main"', html)
        self.assertEqual(html.count('id="exam--lq-rail"'), 1)
        self.assertIn('aria-controls="exam--lq-rail"', html)
        self.assertNotIn('app-bottomnav', html)

    def test_sidebar_uses_native_details_without_permission_synthesis(self):
        html = str(self.m.lq_sidebar('nav', [{'key':'teaching','label':'教学','items':[{'key':'course','label':'课程','href':'/course','current':True}]}]))
        self.assertIn('<details', html)
        self.assertIn('aria-current="page"', html)
        self.assertNotIn('管理权限', html)

    def test_navigation_items_reject_dangerous_url(self):
        for href in ('javascript:x', '//evil.test', 'x\\y', 'https://example.test/\n'):
            with self.assertRaises(ValueError):
                self.m.lq_nav_item('x', {'key':'x','label':'x','href':href})

    def test_dock_cap_does_not_invent_hidden_actions(self):
        with self.assertRaises(ValueError):
            self.m.lq_dock('x', mode='actions', items=[{'key':f'a{i}','label':str(i)} for i in range(6)])
        with self.assertRaises(ValueError):
            self.m.lq_dock('x', mode='tabs', items=[{'key':'x','label':'x'}])

    def test_steps_need_one_current_and_counts_are_not_computed(self):
        with self.assertRaises(ValueError):
            self.m.lq_steps('x', [{'key':'x','label':'x'}])
        html = str(self.m.lq_steps('x', [{'key':'x','label':'当前','state':'current'}]))
        self.assertIn('aria-current="step"', html)

    def test_prominent_fab_and_topbar_declarations_are_typed(self):
        html = str(self.m.lq_fab('create', {'key': 'create', 'label': '创建', 'icon': 'plus'}, variant='prominent'))
        self.assertIn('lq-fab--prominent', html)
        self.assertNotIn('lq-glass', html)
        for variant in ('danger', None, True):
            with self.assertRaises(ValueError):
                self.m.lq_fab('create', {'key': 'create', 'label': '创建', 'icon': 'plus'}, variant=variant)
        html = str(self.m.lq_topbar('top', '标题', view_transition=True))
        self.assertIn('data-lq-view-transition="true"', html)
        self.assertIn('<dialog', html)
        self.assertIn('open=""', html)
        self.assertIn('aria-controls="top--lq-actions"', html)
        with self.assertRaises(ValueError):
            self.m.lq_topbar('top', '标题', view_transition='true')

    def test_step_nodes_and_registered_crumb_chevrons_are_decorative(self):
        html = str(self.m.lq_steps('steps', [{'key':'a','label':'当前','state':'current'},{'key':'b','label':'下一步'}]))
        self.assertEqual(html.count('class="lq-steps__node" aria-hidden="true"'), 2)
        self.assertEqual(html.count('aria-current="step"'), 1)
        html = str(self.m.lq_crumbs('crumbs', [{'key':'a','label':'上级','href':'/a'},{'key':'b','label':'当前'}]))
        self.assertEqual(html.count('class="lq-crumbs__separator" aria-hidden="true"'), 1)
        self.assertIn('class="lq-icon"', html)

    def test_persistence_is_explicit_identity_resource_key(self):
        with self.assertRaises(ValueError):
            self.m.lq_sidebar('x', [{'key':'g','label':'g','items':[]}], persist={'key':'global'})

    def test_layouts_are_fragments_and_slots_are_real_jinja(self):
        for kind in ('list','dashboard','detail','editor','take','immersive','reading'):
            html = str(self.m.lq_page_layout('x', kind))
            self.assertIn('data-lq-layout="'+kind+'"', html)
            self.assertNotIn('<html', html)
        html = self.env.from_string("{% from 'macros/lq/shells.html' import lq_editor %}{% call(slot) lq_editor('x','x') %}{% if slot=='main' %}<form id='f'><input name='draft' value='0'></form>{% endif %}{% endcall %}").render()
        self.assertIn("<form id='f'><input name='draft' value='0'></form>", html)


if __name__ == '__main__':
    unittest.main()
