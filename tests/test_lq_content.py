"""Pure content props and actual Jinja macros; no application requests."""
import json
from pathlib import Path
import unittest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup
from classroom_app.lq_content import CONTENT_KINDS, lq_content_props, presentation_props

ROOT = Path(__file__).resolve().parents[1]


class LqContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=True, undefined=StrictUndefined)
        cls.env.globals['lq_props'] = lambda kind, **p: lq_content_props(kind, **p) if kind in CONTENT_KINDS else presentation_props(kind, **p)
        cls.macros = cls.env.get_template('macros/lq/content.html').module

    def test_card_title_action_and_secondary_are_siblings(self):
        html = str(self.macros.lq_card('主动作', primary={'href': '/x'}, actions=[{'label': '次动作'}]))
        self.assertIn('<a class="lq-btn', html)
        self.assertIn('href="/x"', html)
        self.assertIn('</h3>', html)
        self.assertIn('class="lq-card__actions"', html)
        self.assertIn('type="button"', html)

    def test_stat_zero_is_real_and_absent_values_do_not_become_fake_counts(self):
        self.assertIn('class="lq-card__value">0</p>', str(self.macros.lq_card('余额', variant='stat', value=0)))
        for value in (None, True, float('nan'), float('inf'), '', 1.5, 9007199254740992):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.macros.lq_card('余额', variant='stat', value=value)

    def test_empty_reasons_remain_distinct_and_do_not_invent_actions(self):
        titles = set()
        for reason in ('empty', 'no-results', 'error', 'forbidden', 'offline'):
            tree = lq_content_props('empty', reason=reason)
            self.assertEqual(tree['attrs']['data-reason'], reason)
            titles.add(tree['children'][0]['children'][0]['children'][0])
            self.assertNotIn('<button', str(self.macros.lq_empty(reason)))
        self.assertEqual(len(titles), 5)

    def test_markup_remains_text_and_raw_html_has_no_bypass(self):
        html = str(self.macros.lq_prose(Markup('<script>unsafe()</script>')))
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        with self.assertRaises(ValueError):
            self.macros.lq_prose(rawHTML='<img src=x>')

    def test_list_and_row_have_native_structure(self):
        tree = lq_content_props('list', label='附件', items=[{'title': '文件'}], ordered=True)
        self.assertEqual(tree['tag'], 'ol')
        self.assertEqual(tree['children'][0]['tag'], 'li')
        self.assertEqual(tree['attrs']['role'], 'list')

    def test_old_page_head_signature_and_zero_argument_aside_caller(self):
        html = self.env.from_string("{% from 'macros/lq/content.html' import lq_page_head %}{% call lq_page_head('标题','描述','说明','功能说明',[{'label':'创建','variant':'primary'}],'不填充','title-id') %}<span data-aside>摘要</span>{% endcall %}").render()
        for hook in ('data-page-head', 'page-head__copy', 'page-head__desc', 'page-head__aside', 'page-head__actions', 'data-explain-text', 'id="title-id"', 'lq-btn--prominent'):
            self.assertIn(hook, html)
        self.assertIn('<span data-aside>摘要</span>', html)
        self.assertNotIn('不填充', html)

    def test_filter_form_is_native_and_group_variant_cannot_submit(self):
        tree = lq_content_props('filter_bar', search_id='search', search_value='保留', action='/search')
        self.assertEqual(tree['tag'], 'form')
        self.assertEqual(tree['attrs']['method'], 'get')
        self.assertIn('value="保留"', str(self.macros.lq_filter_bar('search', search_value='保留')))
        with self.assertRaises(ValueError):
            lq_content_props('filter_bar', tag='div', action='/search')

    def test_new_named_slots_and_old_no_argument_slots_both_work(self):
        html = self.env.from_string("{% from 'macros/lq/content.html' import lq_filter_bar %}{% call(slot) lq_filter_bar(label='具名槽') %}{% if slot == 'filters' %}<span>筛选控件</span>{% elif slot == 'actions' %}<button type='submit'>查询</button>{% endif %}{% endcall %}").render()
        self.assertIn('data-lq-slot="filters"><span>筛选控件</span>', html)
        self.assertIn("data-lq-slot=\"actions\"><button type='submit'>查询</button>", html)

    def test_invalid_action_urls_and_string_attributes_fail_closed(self):
        for href in ('javascript:x', '//example.test', '/x\\y', '/x\ny', 'data:text/html,x'):
            with self.subTest(href=href), self.assertRaises(ValueError):
                self.macros.lq_card('标题', primary={'href': href})
        with self.assertRaises(ValueError):
            self.macros.lq_page_head('标题', actions=[{'label': '操作', 'attrs': 'onclick="x"'}])

    def test_bubble_keeps_identity_and_time_in_ssr(self):
        html = str(self.macros.lq_bubble('教师', '10:20', text='纯文本', side='outgoing'))
        self.assertIn('class="lq-bubble__author">教师', html)
        self.assertIn('<time class="lq-bubble__time">10:20</time>', html)
        with self.assertRaises(ValueError):
            self.macros.lq_bubble('教师', '')

    def test_grouped_native_lists_have_unique_headings_and_author_slots(self):
        html = self.env.from_string("{% from 'macros/lq/content.html' import lq_list,lq_row %}{% call(slot) lq_list('分组',id='groups',groups=[{'key':'today','title':'今天'}]) %}{% if slot == 'items:today' %}{{ lq_row('调用者行') }}{% endif %}{% endcall %}").render()
        self.assertIn('<h3 class="lq-list__heading" id="groups--lq-group-today">今天</h3>', html)
        self.assertIn('aria-labelledby="groups--lq-group-today"', html)
        self.assertIn('调用者行', html)

    def test_swipe_is_one_real_native_destructive_button_and_strict_states(self):
        html = str(self.macros.lq_row('附件', id='row', swipe={'key': 'delete', 'label': '删除', 'busy': True}))
        self.assertEqual(html.count('data-lq-row-action="delete"'), 1)
        self.assertIn('lq-btn--destructive', html)
        self.assertIn('data-lq-row-reveal', html)
        self.assertIn('disabled=""', html)
        self.assertNotIn('onclick', html)

    def test_all_content_fixture_props_validate_without_application(self):
        payload = json.loads((ROOT / 'tests/e2e/components/fixtures/lq-content.json').read_text(encoding='utf-8'))
        aliases = {'titleId':'title_id','explainLabel':'explain_label','searchId':'search_id','searchValue':'search_value','searchPlaceholder':'search_placeholder','searchAttrs':'search_attrs'}
        for entry in payload['invalid']:
            with self.subTest(props=entry['props']), self.assertRaises(ValueError):
                lq_content_props(entry['kind'], **{aliases.get(k,k):v for k,v in entry['props'].items()})


if __name__ == '__main__':
    unittest.main()
