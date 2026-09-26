"""C1 appearance SSR only: real macros, pure helpers, no app/DB fixture."""
import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
PALETTES = ('teal', 'indigo', 'sky', 'mint', 'violet', 'rose')


def load_helper(filename):
    spec = importlib.util.spec_from_file_location('profile_pure_' + filename, ROOT / 'classroom_app' / (filename + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


class ProfileAppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        components, forms = load_helper('lq_components'), load_helper('lq_forms')
        cls.env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=True, undefined=StrictUndefined)
        cls.env.globals['lq_props'] = lambda component, **props: (forms.lq_form_props if component in forms.FORM_KINDS else components.lq_props)(component, **props)

    def render(self, **overrides):
        preferences = {'enabled': True, 'available': True, 'palette_key': 'indigo', 'appearance': 'auto', 'glass': 'tinted',
                       'presets': [{'key': key, 'name': key} for key in PALETTES], **overrides}
        return self.env.get_template('partials/profile/appearance.html').render(ui_palette=preferences)

    def test_real_macros_and_native_controls_reflect_all_stored_values(self):
        for palette in PALETTES:
            for appearance in ('auto', 'light', 'dark'):
                for glass in ('off', 'tinted'):
                    with self.subTest(palette=palette, appearance=appearance, glass=glass):
                        nodes = Document(self.render(palette_key=palette, appearance=appearance, glass=glass)).nodes
                        radios = [a for tag, a in nodes if tag == 'input' and a.get('type') == 'radio']
                        self.assertEqual([a['value'] for a in radios if 'checked' in a], [appearance])
                        self.assertEqual({a['name'] for a in radios}, {'profile-appearance'})
                        switch = next(a for tag, a in nodes if a.get('role') == 'switch')
                        self.assertEqual(switch['type'], 'checkbox')
                        self.assertEqual('checked' in switch, glass == 'tinted')
                        chips = [a for tag, a in nodes if a.get('data-ui-preference-choice') == 'palette_key']
                        self.assertEqual([a['data-ui-preference-value'] for a in chips], list(PALETTES))
                        self.assertEqual([a['data-ui-preference-value'] for a in chips if a['aria-pressed'] == 'true'], [palette])
                        self.assertTrue(all(a['type'] == 'button' and 'lq-chip--filter' in a['class'] for a in chips))

    def test_associations_unique_ids_and_one_persistent_live_region(self):
        nodes = Document(self.render()).nodes
        ids = [a['id'] for _, a in nodes if 'id' in a]
        self.assertEqual(len(ids), len(set(ids)))
        for _, attrs in nodes:
            for key in ('for', 'aria-labelledby', 'aria-describedby'):
                for identity in attrs.get(key, '').split():
                    self.assertIn(identity, ids)
        self.assertEqual(len([a for _, a in nodes if a.get('role') == 'status']), 1)
        self.assertEqual(len([a for tag, a in nodes if tag == 'fieldset']), 2)
        self.assertFalse([a for _, a in nodes if a.get('role') in ('tab', 'tablist', 'tabpanel') or 'data-lq-tabs' in a])
        self.assertFalse([a for tag, a in nodes if tag in ('form', 'script', 'iframe')])
        # The 页面背景 mode / 图库筛选 controls (2026-09-23 backdrop feature) are native selects; each must be labelled.
        self.assertTrue(all(a.get('aria-label') or a.get('id') for tag, a in nodes if tag == 'select'))

    def test_disabled_context_and_unavailable_context_are_explicit_without_fake_save(self):
        html = self.render(enabled=False)
        self.assertIn('当前账号暂不提供外观设置', html)
        self.assertFalse([a for tag, a in Document(html).nodes if tag in ('button', 'input')])
        html = self.render(available=False)
        self.assertIn('下一次选择时会先核对服务器', html)
        self.assertIn('<noscript>', html)
        self.assertIn('启用 JavaScript 后才能预览和保存外观', html)

    def test_preset_text_and_attributes_are_escaped_without_inline_style_or_handlers(self):
        html = self.render(presets=[{'key': 'rose" onclick="bad', 'name': '<img src=x onerror=bad>'}])
        self.assertIn('&lt;img', html)
        self.assertFalse([a for tag, a in Document(html).nodes if tag == 'img' or any(key.startswith('on') or key == 'style' for key in a)])


if __name__ == '__main__':
    unittest.main()
