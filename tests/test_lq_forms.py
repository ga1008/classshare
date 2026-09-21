"""Real opt-in form macros and typed state, without app or database imports."""
from html.parser import HTMLParser
import math
from pathlib import Path
import unittest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup
from classroom_app.lq_forms import lq_form_props

ROOT = Path(__file__).resolve().parents[1]


class Parsed(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)

    def first(self, tag):
        return next(attrs for name, attrs in self.nodes if name == tag)


class LqFormsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=True, undefined=StrictUndefined)
        cls.env.globals['lq_props'] = lq_form_props
        cls.macros = cls.env.get_template('macros/lq/forms.html').module

    def render(self, kind, **props):
        return str(getattr(self.macros, 'lq_' + kind)(**props))

    def test_native_value_name_form_and_required_survive_server_render(self):
        doc = Parsed(self.render('input', id='name', label='姓名', name='student_name', form='external', value='原始输入', required=True))
        self.assertEqual(doc.first('input')['value'], '原始输入')
        self.assertEqual(doc.first('input')['name'], 'student_name')
        self.assertEqual(doc.first('input')['form'], 'external')
        self.assertIn('required', doc.first('input'))
        self.assertEqual(doc.first('label')['for'], 'name')

    def test_help_error_and_external_description_are_deduplicated_and_authoritative(self):
        doc = Parsed(self.render('input', id='name', label='姓名', help='说明', error='冲突',
                                 attrs={'aria-describedby': 'outside outside name--lq-spoof', 'aria-invalid': False, 'aria-label': '错误', 'aria-labelledby': 'other', 'aria-hidden': True}))
        attrs = doc.first('input')
        self.assertEqual(attrs['aria-describedby'], 'outside name--lq-help name--lq-error')
        self.assertEqual(attrs['aria-invalid'], 'true')
        for key in ('aria-label', 'aria-labelledby', 'aria-hidden'):
            self.assertNotIn(key, attrs)

    def test_markup_is_always_plain_text_in_values_labels_and_messages(self):
        payload = Markup('<img src=x onerror="alert(1)">')
        doc = Parsed(self.render('input', id='safe', label=payload, value=payload, help=payload, error=payload))
        self.assertFalse(any(tag == 'img' for tag, _ in doc.nodes))
        self.assertEqual(doc.first('input')['value'], str(payload))
        self.assertIn(str(payload), ''.join(doc.text))

    def test_state_booleans_are_strict_and_reserved_runtime_attrs_cannot_override_props(self):
        for key in ('disabled', 'required', 'readonly', 'clearable'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                lq_form_props('input', id='x', label='x', **{key: 'false'})
        doc = Parsed(self.render('textarea', id='x', label='x', attrs={'data-lq-auto-grow': 'true', 'data-lq-count': 'other'}))
        self.assertNotIn('data-lq-auto-grow', doc.first('textarea'))
        self.assertNotIn('data-lq-count', doc.first('textarea'))

    def test_unsupported_attrs_and_nonfinite_scalars_fail_before_html(self):
        for attrs in ({'onclick': 'x'}, {'style': 'x'}, {'role': 'button'}, {'value': 'x'}, {'data-x': {}}, {'data-x': math.nan}, {'data-lq-x': []}):
            with self.subTest(attrs=attrs), self.assertRaises(ValueError):
                self.render('input', id='x', label='x', attrs=attrs)

    def test_readonly_is_native_and_rejected_for_controls_without_it(self):
        for kind in ('input', 'textarea'):
            self.assertIn('readonly', Parsed(self.render(kind, id='x', label='x', readonly=True)).first(kind))
        for kind in ('select', 'checkbox', 'radio', 'range', 'switch'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                lq_form_props(kind, id='x', label='x', readonly=True)

    def test_disabled_is_present_without_any_enhancement(self):
        for kind in ('input', 'textarea', 'select', 'checkbox', 'radio', 'range', 'switch'):
            doc = Parsed(self.render(kind, id='x', label='x', name='group', disabled=True))
            self.assertIn('disabled', doc.first(kind if kind in ('textarea', 'select') else 'input'))

    def test_select_is_native_selected_value_and_disabled_option(self):
        doc = Parsed(self.render('select', id='term', label='学期', value='b', options=[{'value': 'a', 'label': 'A', 'disabled': True}, {'value': 'b', 'label': 'B'}]))
        self.assertIn('disabled', doc.first('option'))
        self.assertTrue(any(tag == 'option' and a.get('value') == 'b' and 'selected' in a for tag, a in doc.nodes))

    def test_select_does_not_silently_discard_unknown_or_duplicate_values(self):
        for options in ([{'value': 'a', 'label': 'A'}], [{'value': 'b', 'label': 'B'}, {'value': 'b', 'label': 'B2'}]):
            with self.assertRaises(ValueError):
                lq_form_props('select', id='x', label='x', value='b', options=options)

    def test_radio_requires_group_name_and_switch_is_native_checkbox_with_role(self):
        with self.assertRaises(ValueError):
            lq_form_props('radio', id='x', label='x')
        attrs = Parsed(self.render('switch', id='x', label='公开', checked=True, value='1')).first('input')
        self.assertEqual(attrs['type'], 'checkbox')
        self.assertEqual(attrs['role'], 'switch')
        self.assertIn('checked', attrs)
        self.assertNotIn('aria-checked', attrs)

    def test_range_bounds_and_native_values_are_exact(self):
        attrs = Parsed(self.render('range', id='x', label='权重', min=0, max=1, step=.1, value=.5)).first('input')
        self.assertEqual((attrs['min'], attrs['max'], attrs['step'], attrs['value']), ('0', '1', '0.1', '0.5'))
        for props in ({'min': 1, 'max': 1}, {'value': 101}, {'step': 0}, {'value': math.inf}, {'value': True}):
            with self.assertRaises(ValueError):
                lq_form_props('range', id='x', label='x', **props)

    def test_textarea_count_matches_native_utf16_length_and_normalizes_newlines(self):
        tree = lq_form_props('textarea', id='x', label='x', value='😀\r\n中', count=True, maxlength=10)
        doc = Parsed(self.render('textarea', id='x', label='x', value='😀\r\n中', count=True, maxlength=10))
        self.assertIn('4 / 10 字', doc.text)
        self.assertIn('😀\n中', doc.text)
        self.assertEqual(tree['children'][-1]['attrs']['id'], 'x--lq-count')

    def test_generated_description_ids_are_reserved(self):
        for identity in ('x--lq-help', 'x x', '', 'javascript:x'):
            with self.assertRaises(ValueError):
                lq_form_props('input', id=identity, label='x')

    def test_field_wrapper_preserves_value_and_parent_identity_wins(self):
        doc = Parsed(self.render('field', id='outer', label='外层标签', control='textarea', control_props={'id': 'inner', 'label': '内层', 'value': '不可丢失'}, error='409 版本冲突'))
        self.assertEqual(doc.first('textarea')['id'], 'outer')
        self.assertIn('不可丢失', doc.text)
        self.assertIn('409 版本冲突', doc.text)

    def test_summary_only_links_to_valid_fields_without_submit_or_live_side_effects(self):
        doc = Parsed(self.render('error_summary', id='errors', title='请修正', errors=[{'id': 'name', 'message': '姓名错误'}]))
        self.assertEqual(doc.first('a')['href'], '#name')
        self.assertEqual(doc.first('section')['aria-labelledby'], 'errors--lq-title')
        self.assertFalse(any(tag == 'button' or 'aria-live' in attrs or attrs.get('role') == 'alert' for tag, attrs in doc.nodes))

    def test_real_caller_slots_compose_existing_controls_without_safe_text_bypass(self):
        html = self.env.from_string("{% from 'macros/lq/forms.html' import lq_form_section,lq_form_actions,lq_input %}{% call lq_form_section('group','组',disabled=true) %}{{ lq_input('name','姓名') }}{% endcall %}{% call lq_form_actions(hint='说明') %}<button type='submit'>保存</button>{% endcall %}").render()
        doc = Parsed(html)
        self.assertIn('disabled', doc.first('fieldset'))
        self.assertEqual(doc.first('legend'), {'class': 'lq-form-section__title'})
        self.assertEqual(doc.first('button')['type'], 'submit')
        self.assertEqual(doc.first('input')['id'], 'name')


if __name__ == '__main__':
    unittest.main()
