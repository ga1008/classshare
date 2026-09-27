"""Migration preserves the consumer contract; no application or DB imports."""
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

from jinja2 import Template

TOOLS = Path(__file__).resolve().parents[1] / 'tools/ui'
sys.path.insert(0, str(TOOLS))
try:
    spec = importlib.util.spec_from_file_location('lq_declaration_migration_under_test', TOOLS / 'migrate_lq_declarations.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
finally:
    sys.path.remove(str(TOOLS))


class ContractParser(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.events = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.events.append(('start', tag, sorted((key, value) for key, value in attrs if key != 'class' and not key.startswith('data-lq-'))))

    def handle_endtag(self, tag):
        self.events.append(('end', tag))

    def handle_data(self, data):
        self.events.append(('text', data))


class LqDeclarationMigrationTests(unittest.TestCase):
    def assert_contract(self, original, updated):
        self.assertEqual(ContractParser(original).events, ContractParser(updated).events)

    def test_native_controls_keep_business_attributes_and_children(self):
        original = '<form id="owner"><button id="save" name="action" type="submit" form="owner" value="draft" data-save="v1" aria-expanded="false" class="btn btn-primary"><svg data-icon="save"></svg><span>Save &amp; keep</span></button><select id="select" name="term" required data-change="term"><option value="1" selected>One</option></select><input type="checkbox" name="terms" value="yes" checked disabled><textarea name="notes" rows="4">Keep my draft</textarea><input type="hidden" name="csrf" value="unchanged"></form>'
        updated, changes = migration.migrate('templates/example.html', original)
        self.assertGreater(len(changes), 0)
        self.assert_contract(original, updated)
        self.assertIn('<input type="hidden" name="csrf" value="unchanged">', updated)
        self.assertEqual(migration.migrate('templates/example.html', updated)[1], [])

    def test_jinja_conditional_variant_remains_conditional(self):
        original = '<button type="button" class="btn {{ \'btn-primary\' if active else \'btn-outline\' }}" data-id="{{ item }}">{{ title }}</button>'
        updated, _ = migration.migrate('templates/example.html', original)
        for active in (False, True):
            values = dict(active=active, item=17, title='Keep <literal>')
            before = Template(original).render(**values)
            after = Template(updated).render(**values)
            self.assert_contract(before, after)
            self.assertEqual('lq-btn--prominent' in after, active)
            self.assertEqual('lq-btn--glass' in after, not active)

    def test_javascript_template_variant_and_literal_escaping_stay_valid(self):
        original = r'''const active = process.argv[1] === 'active';
const html = `<button type="button" class="btn ${active ? 'btn-primary' : 'btn-outline'}" data-save="retain"><span>Don't &amp; leave</span></button>`;
console.log(JSON.stringify(html));'''
        updated, _ = migration.migrate('static/js/example.js', original)
        for active in ('active', 'inactive'):
            before = json.loads(subprocess.check_output(['node', '-e', original, active], text=True, encoding='utf-8'))
            after = json.loads(subprocess.check_output(['node', '-e', updated, active], text=True, encoding='utf-8'))
            self.assert_contract(before, after)
            self.assertEqual('lq-btn--prominent' in after, active == 'active')
            self.assertEqual('lq-btn--glass' in after, active != 'active')

    def test_escaped_javascript_literal_is_preserved_when_not_rewritable(self):
        original = r'const html = "<button class=\"btn btn-primary\" type=\"button\">Save</button>"; console.log(JSON.stringify(html));'
        updated, _ = migration.migrate('static/js/example.js', original)
        before = json.loads(subprocess.check_output(['node', '-e', original], text=True, encoding='utf-8'))
        after = json.loads(subprocess.check_output(['node', '-e', updated], text=True, encoding='utf-8'))
        self.assert_contract(before, after)

    def test_comment_and_plain_text_sinks_are_unchanged(self):
        original = '/* const html = `<button>Example</button>`; */\nnode.textContent = "<button>Text</button>";'
        self.assertEqual(migration.migrate('static/js/example.js', original), (original, []))


if __name__ == '__main__':
    unittest.main()
