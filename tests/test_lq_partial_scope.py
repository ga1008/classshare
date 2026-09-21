"""Partial migration guards; all fixtures are disposable source trees, no app/DB."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.ui.lint_lq import audit
from tools.ui.lq_inventory import build_registry


class LqPartialScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write('templates/page.html', '<button class="btn btn-primary">Retained dialog</button>')
        self.write('static/js/pilot.js', 'export function mount() {}')
        self.write('static/js/business.js', 'window.confirm("retained")')
        self.write('static/css/compiled.css', '.old { color:#fff; z-index:99; }')
        self.write('tests/ssr.py', 'def test_real_macro(): pass')
        self.entry = dict(id='page', template='templates/page.html', status='迁移中',
            assets=['js/pilot.js', 'js/business.js', 'css/compiled.css'],
            controller=['static/js/pilot.js', 'static/js/business.js'],
            controllerNotes=[dict(path='static/js/pilot.js', entrypoint='mount', description='Only shell owner')],
            generatedAssets=[dict(path='static/css/compiled.css', kind='compiled', reason='Bundled source guarded separately')],
            migrationScope=dict(kind='partial', owner='fixture owner', description='Only shell', reviewAt='Next shell change',
                activeSources=['static/js/pilot.js'], sharedSources=[dict(path='templates/page.html',
                    sha256=self.digest('templates/page.html'), scope='Opt-in call and old dialog coexist', tests=['tests/ssr.py'])]))

    def write(self, path, source):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(source, encoding='utf-8')

    def digest(self, path):
        return hashlib.sha256((self.root / path).read_bytes()).hexdigest()

    def report(self, extra=None, **kwargs):
        return audit(self.root, {'entries': [self.entry, *(extra or [])]}, [], **kwargs)

    def test_partial_keeps_retained_warnings_and_guards_new_source(self):
        first = self.report()
        self.assertEqual([], first['blocking'])
        self.assertEqual({'templates/page.html': 1, 'static/js/business.js': 1}, first['warningCountsByPath'])
        self.assertEqual(['static/css/compiled.css'], first['generatedAssets'])
        self.write('static/js/pilot.js', 'window.confirm("new forbidden call")')
        self.assertEqual([dict(path='static/js/pilot.js', line=1, rule='native-confirm')], self.report()['blocking'])

    def test_every_shared_source_change_requires_review_even_without_a_rule_violation(self):
        self.write('templates/page.html', '<p>Changed macro ownership</p>')
        self.assertEqual([dict(path='templates/page.html', line=0, rule='reviewed-source-changed')], self.report()['blocking'])

    def test_generated_and_retained_files_and_real_test_evidence_must_exist(self):
        for path in ('static/css/compiled.css', 'static/js/business.js', 'tests/ssr.py', 'templates/page.html'):
            with self.subTest(path=path):
                file = self.root / path
                source = file.read_bytes(); file.unlink()
                try:
                    self.assertIn(dict(path=path, line=0, rule='missing-file'), self.report()['blocking'])
                finally:
                    file.write_bytes(source)

    def test_complete_active_consumer_wins_over_partial_dependency_and_artifact(self):
        extra = [dict(id='whole', template='templates/page.html', status='迁移中', assets=['js/business.js', 'css/compiled.css'])]
        result = self.report(extra)
        self.assertEqual({'templates/page.html', 'static/js/business.js', 'static/css/compiled.css'}, {item['path'] for item in result['blocking']})
        self.assertEqual([], result['generatedAssets'])

    def test_foundation_cannot_be_downgraded_by_partial_generated_classification(self):
        path = 'static/js/lq/core.js'
        self.write(path, 'window.confirm("foundation")')
        self.entry['assets'].append('js/lq/core.js')
        self.entry['generatedAssets'].append(dict(path=path, kind='compiled', reason='Must not waive foundation'))
        self.assertIn(dict(path=path, line=1, rule='native-confirm'), self.report()['blocking'])
        self.assertIn(dict(path=path, line=1, rule='native-confirm'), self.report(page='page')['blocking'])

    def test_missing_scope_contract_and_malformed_paths_fail_closed(self):
        original = copy.deepcopy(self.entry)
        for key in ('owner', 'description', 'reviewAt', 'activeSources', 'sharedSources'):
            with self.subTest(key=key):
                self.entry = copy.deepcopy(original)
                self.entry['migrationScope'].pop(key)
                with self.assertRaises(ValueError): self.report()
        for path in ('../escape.js', '/absolute.js', 'static\\js\\pilot.js', 'static/js/pilot.js#mount', 'static/js/pilot.js:mount'):
            with self.subTest(path=path):
                self.entry = copy.deepcopy(original)
                self.entry['controller'] = [path]
                with self.assertRaises(ValueError): self.report()
        self.entry = copy.deepcopy(original)
        self.entry['controller'] = ['js/pilot.js -> mount']
        self.entry['controllerNotes'] = []
        self.assertIn(dict(path='js/pilot.js -> mount', line=0, rule='missing-file'), self.report()['blocking'])

    def test_template_coverage_test_links_and_generated_roles_are_required(self):
        original = copy.deepcopy(self.entry)
        mutations = [
            lambda entry: entry['migrationScope']['sharedSources'][0].update(tests=[]),
            lambda entry: entry['migrationScope']['sharedSources'][0].update(tests=['static/js/pilot.js']),
            lambda entry: entry['migrationScope']['sharedSources'][0].update(sha256='not-a-hash'),
            lambda entry: entry.update(template='templates/unreviewed.html'),
            lambda entry: entry['generatedAssets'][0].update(path='static/js/pilot.js'),
            lambda entry: entry['generatedAssets'][0].update(kind='legacy'),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.entry = copy.deepcopy(original); mutate(self.entry)
                with self.assertRaises(ValueError): self.report()

    def test_controller_descriptions_cannot_be_disguised_as_file_records(self):
        original = copy.deepcopy(self.entry)
        for notes in ('static/js/pilot.js -> mount', ['plain description'], [dict(path='missing.js', description='unlisted')], [dict(path='static/js/pilot.js', description='')]):
            with self.subTest(notes=notes):
                self.entry = copy.deepcopy(original)
                self.entry['controllerNotes'] = notes
                with self.assertRaises(ValueError): self.report()

    def test_page_selection_keeps_the_same_partial_contract(self):
        self.assertEqual([], self.report(page='page')['blocking'])
        self.write('static/js/pilot.js', 'window.alert("x")')
        self.assertEqual('native-confirm', self.report(page='page')['blocking'][0]['rule'])
        with self.assertRaises(ValueError): self.report(page='unknown')

    def test_nonpartial_active_rules_and_unmigrated_warnings_are_unchanged(self):
        self.entry = dict(id='old', template='templates/page.html', status='已盘点')
        self.assertEqual(1, self.report()['warningCount'])
        self.entry['status'] = '迁移中'
        self.assertEqual('legacy-class', self.report()['blocking'][0]['rule'])

    def test_inventory_preserves_reviewed_contract_and_transitive_dependencies(self):
        (self.root / 'classroom_app/routers').mkdir(parents=True)
        self.write('classroom_app/routers/pages.py', '@router.get("/page")\ndef page():\n return templates.TemplateResponse(request, "page.html", {})')
        first = build_registry(self.root)
        entry = first['entries'][0]
        for key in ('controller', 'controllerNotes', 'migrationScope', 'generatedAssets', 'assets'):
            entry[key] = copy.deepcopy(self.entry[key])
        second = build_registry(self.root, first)['entries'][0]
        for key in ('controller', 'controllerNotes', 'migrationScope', 'generatedAssets'):
            self.assertEqual(entry[key], second[key], key)
        self.assertEqual(set(entry['assets']), set(second['assets']))


class LqRealPartialScopeContractTests(unittest.TestCase):
    def test_all_nine_pilots_have_real_reviewed_sources_and_ssr_contracts(self):
        root = Path(__file__).resolve().parents[1]
        registry = json.loads((root / 'docs/lq-migration-registry.json').read_text(encoding='utf-8'))
        pilots = [entry for entry in registry['entries'] if str(entry.get('migrationFlag') or '').startswith('LANSHARE_LQ_PILOT')]
        self.assertEqual(9, len(pilots))
        self.assertEqual(8, sum(entry['routePattern'].startswith('/manage/') for entry in pilots))
        for entry in pilots:
            with self.subTest(page=entry['routePattern']):
                scope = entry['migrationScope']
                self.assertEqual('partial', scope['kind'])
                self.assertIn(entry['template'], [item['path'] for item in scope['sharedSources']])
                for path in entry['controller']:
                    self.assertTrue((root / path).is_file(), path)
                ssr = 'tests/test_manage_lq_pilot_templates.py' if entry['routePattern'].startswith('/manage/') else 'tests/test_lq_report_card_pilot.py'
                for item in scope['sharedSources']:
                    self.assertEqual(item['sha256'], hashlib.sha256((root / item['path']).read_bytes()).hexdigest(), item['path'])
                    self.assertIn(ssr, item['tests'])


if __name__ == '__main__':
    unittest.main()
