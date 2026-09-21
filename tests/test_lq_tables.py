"""Pure native table structures, boundary validation and actual SSR macros."""
from pathlib import Path
import unittest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup
from classroom_app.lq_tables import TABLE_KINDS, lq_table_props, presentation_props

ROOT = Path(__file__).resolve().parents[1]


class LqTablesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=True, undefined=StrictUndefined)
        cls.env.globals['lq_props'] = lambda kind, **p: lq_table_props(kind, **p) if kind in TABLE_KINDS else presentation_props(kind, **p)
        cls.macros = cls.env.get_template('macros/lq/tables.html').module

    def test_scope_headers_caption_and_zero_are_native(self):
        html = str(self.macros.lq_table('scores', '成绩', [{'key':'student','label':'学生','rowHeader':True},{'key':'score','label':'成绩'}], [{'key':'a','cells':{'student':'甲','score':0}}]))
        self.assertIn('<caption id="scores--lq-caption">成绩</caption>', html)
        self.assertIn('scope="row"', html)
        self.assertIn('headers="scores--lq-col-score scores--lq-row-a"', html)
        self.assertIn('data-label="成绩"', html)
        self.assertIn('data-lq-slot="cell:a:score">0</div>', html)

    def test_master_mixed_ignores_disabled_selected_rows_without_clearing_them(self):
        html = str(self.macros.lq_table('x', 'x', [{'key':'a','label':'A'}], [
            {'key':'a','label':'甲','selected':True,'cells':{'a':'甲'}},
            {'key':'b','label':'乙','cells':{'a':'乙'}},
            {'key':'c','label':'丙','selected':True,'disabled':True,'cells':{'a':'丙'}}], selectable=True))
        self.assertIn('data-lq-select-all="" aria-checked="mixed"', html)
        self.assertIn('value="c" checked="" disabled=""', html)

    def test_empty_master_is_disabled(self):
        html = str(self.macros.lq_table('x','x',[{'key':'a','label':'A'}], selectable=True))
        self.assertIn('data-lq-select-all="" aria-checked="false" disabled=""', html)

    def test_only_one_valid_sort_state_is_allowed(self):
        for columns in ([{'key':'a','label':'A','sort':'ascending'}], [{'key':'a','label':'A','sortable':True,'sort':'other'}], [{'key':'a','label':'A','sortable':True,'sort':'ascending'},{'key':'b','label':'B','sortable':True,'sort':'descending'}]):
            with self.assertRaises(ValueError):
                self.macros.lq_table('x','x',columns)

    def test_pager_is_bounded_even_at_maximum_safe_integer(self):
        for page, total in ((5000,10000),(9007199254740991,9007199254740991),(1,1),(0,0)):
            tree = lq_table_props('pager', page=page, total_pages=total)
            self.assertLessEqual(len(tree['children']), 10)
        for page,total in ((1,0),(0,2),(3,2),(1,9007199254740992)):
            with self.assertRaises(ValueError):
                self.macros.lq_pager(page,total)

    def test_pager_links_are_safe_and_complete_for_enabled_controls(self):
        html = str(self.macros.lq_pager(1,2,links={'2':'/x?page=2'}))
        self.assertIn('href="/x?page=2"', html)
        self.assertIn('aria-current="page"', html)
        for links in ({},{'2':'javascript:x'},{'2':'//example.test'},{'2':None}):
            with self.assertRaises(ValueError):
                self.macros.lq_pager(1,2,links=links)

    def test_zero_bulk_bar_has_no_actions(self):
        html = str(self.macros.lq_bulk_bar(0, actions=[{'label':'删除','id':'must-not-render'}]))
        self.assertIn('hidden=""', html)
        self.assertNotIn('must-not-render', html)

    def test_result_count_does_not_invent_zero_or_live_announcements(self):
        self.assertIn('data-state="empty">0 条结果', str(self.macros.lq_result_count(0)))
        html = str(self.macros.lq_result_count(state='loading'))
        self.assertIn('aria-busy="true"', html)
        self.assertIn('正在加载…', html)
        self.assertNotIn('aria-live', html)
        self.assertNotIn('>0', html)
        with self.assertRaises(ValueError):
            self.macros.lq_result_count()

    def test_markup_and_attributes_cannot_become_html(self):
        html = str(self.macros.lq_table('x',Markup('<img src=x>'),[{'key':'a','label':'A'}],[{'key':'r','cells':{'a':Markup('<script>x</script>')}}]))
        self.assertIn('&lt;img', html)
        self.assertNotIn('<script>', html)
        with self.assertRaises(ValueError):
            self.macros.lq_result_count(1,attrs={'onclick':'x'})

    def test_real_caller_slot_can_contain_an_input_without_safe_filter(self):
        html = self.env.from_string("{% from 'macros/lq/tables.html' import lq_table %}{% call(slot) lq_table('x','表',[{'key':'a','label':'字段'}],[{'key':'r','cells':{'a':''}}]) %}{% if slot == 'cell:r:a' %}<input name='draft' value='原值'>{% endif %}{% endcall %}").render()
        self.assertIn("<input name='draft' value='原值'>", html)


if __name__ == '__main__':
    unittest.main()
