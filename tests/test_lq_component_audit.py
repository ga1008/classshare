import json
from pathlib import Path
import tempfile
import unittest

from tools.ui.audit_lq_components import build_report, scan_source, source_files


class LqComponentAuditTests(unittest.TestCase):
    def test_jinja_raw_controls_alias_calls_and_dynamic_roots_are_preserved(self):
        source = '''{% from "macros/lq/button.html" import lq_btn as action %}
<button class="btn" {% if current > 2 %}disabled{% endif %}>Save</button>
<select id="course"><option>Course</option></select>
{{ action('Save') }}
<{{ root.tag }} class="{{ root.classes }}">Body</{{ root.tag }}>
'''
        rows = scan_source('templates/sample.html', source)
        self.assertEqual([r['kind'] for r in rows], ['button', 'select', 'button', 'unknown'])
        self.assertEqual([r['line'] for r in rows], [2, 3, 4, 5])
        self.assertEqual(rows[0]['class'], 'btn')
        self.assertEqual(rows[2]['provenance'], 'shared-component-call')
        self.assertEqual(rows[3]['status'], 'unknown')
        self.assertTrue(all(r['status'] != 'accepted' for r in rows))

    def test_comments_css_json_and_plain_text_do_not_create_controls(self):
        source = '''<!-- <button>comment</button> -->
{# <select>Jinja comment</select> #}
<style>.example::after{content:"<button>"}</style>
<script type="application/json">{"example":"<button>"}</script>
<script>
// <button>line comment</button>
/* <dialog>block comment</dialog> */
node.textContent = "<button>plain text</button>";
node.innerText = '<select>plain text</select>';
node.value = `<input>`;
node.append(document.createTextNode('<button>text</button>'));
</script>
<p>&lt;button&gt;escaped documentation&lt;/button&gt;</p>
<input type="hidden" name="csrf">
'''
        self.assertEqual(scan_source('templates/sample.html', source), [])

    def test_native_templates_create_element_and_dynamic_factories_are_found(self):
        source = '''const html = `<button class="legacy">Save</button><select class="form-control"></select>`;
const save = document.createElement('button'); save.className = 'old-button';
const arbitrary = document.createElement(tagName);
const text = document.createElement('span');
const hidden = `<input type="hidden">`;
'''
        rows = scan_source('static/js/owner.js', source)
        self.assertEqual([r['kind'] for r in rows], ['button', 'select', 'button', 'unknown'])
        created = [r for r in rows if r['syntax'] == 'create-element']
        self.assertEqual(created[0]['class'], 'old-button')
        self.assertEqual(created[1]['tagOrFactory'], 'tagName')
        self.assertEqual(created[1]['status'], 'unknown')

    def test_inline_javascript_keeps_original_html_line_numbers(self):
        source = '<main>Example</main>\n<script>\nnode.innerHTML = `<button>Real</button>`;\n</script>'
        rows = scan_source('templates/sample.html', source)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['line'], 3)
        self.assertEqual(source[rows[0]['offset']:][:7], '<button')

    def test_react_aliases_and_native_jsx_do_not_hide_unowned_components(self):
        source = '''import { LqButton as Action } from '../components/lq-presentation';
const view = <section><Action label="Save" /><button className={busy ? 'btn busy' : 'btn'}>Other</button><MysteryWidget /></section>;
'''
        rows = scan_source('frontend/src/islands/sample.tsx', source)
        self.assertEqual([r['kind'] for r in rows], ['button', 'button', 'unknown'])
        self.assertEqual(rows[0]['provenance'], 'shared-component-call')
        self.assertEqual(rows[1]['provenance'], 'legacy-or-native')
        self.assertEqual(rows[2]['status'], 'unknown')

    def test_verified_factory_alias_and_unresolved_same_name_are_distinct(self):
        source = '''import { createComponent as build } from '/static/js/lq/components.js';
const first = build('button', {label: 'Save'});
const second = createComponent('button', {label: 'Unresolved'});
'''
        rows = scan_source('static/js/sample.js', source)
        self.assertEqual([r['provenance'] for r in rows], ['shared-component-call', 'factory-ownership-unknown'])

    def test_typescript_generics_are_not_misreported_as_jsx(self):
        source = '''const element = document.querySelector<HTMLElement>('#sample');
const promise: Promise<Result> = Promise.resolve({});
const bag = new Set<HTMLButtonElement>();
const view = <button className="btn">Save</button>;
'''
        rows = scan_source('frontend/src/sample.tsx', source)
        self.assertEqual([(r['kind'], r['tagOrFactory']) for r in rows], [('button', 'button')])

    def test_invalid_typescript_cannot_silently_claim_zero_controls(self):
        with self.assertRaisesRegex(ValueError, 'invalid TypeScript'):
            scan_source('frontend/src/sample.tsx', 'const view = <button>')

    def test_typescript_offsets_preserve_non_bmp_text_before_jsx(self):
        source = 'const label = "🫧 中文"; const view = <button>Save</button>;'
        rows = scan_source('frontend/src/sample.tsx', source)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['offset'], source.index('<button>'))

    def test_dynamic_html_outputs_are_explicit_unknown_but_clears_are_not(self):
        source = '''host.innerHTML = renderRows(records);
host.insertAdjacentHTML('beforeend', incoming);
host.innerHTML = '';
host.innerHTML = '<button>Static</button>';
'''
        rows = scan_source('static/js/sample.js', source)
        self.assertEqual(sum(r['kind'] == 'button' for r in rows), 1)
        sinks = [r for r in rows if r['syntax'] == 'dynamic-html-sink']
        self.assertEqual([r['line'] for r in sinks], [1, 2])
        self.assertTrue(all(r['status'] == 'unknown' for r in sinks))

    def test_button_label_parts_are_not_buttons(self):
        rows = scan_source('templates/sample.html', '<a class="lq-btn"><span class="lq-btn__label">Save</span><span class="lq-btn__icon"></span></a>')
        self.assertEqual([r['kind'] for r in rows], ['button'])

    def test_declared_component_is_pending_and_classes_do_not_accept_it(self):
        rows = scan_source('templates/sample.html', '''<button data-lq-component="button" class="lq-btn">Save</button>
<button class="lq-btn">Style only</button><section data-lq-component="surface" class="lq-surface"></section>
<div data-lq-component="invented"></div>''')
        self.assertEqual([r['provenance'] for r in rows], ['component-declared', 'lq-marked-native', 'component-declared', 'component-declared'])
        self.assertEqual([r['status'] for r in rows], ['pending', 'pending', 'pending', 'unknown'])
        self.assertEqual(rows[0]['declaredComponent'], 'button')

    def test_dialog_parts_are_not_counted_as_separate_dialogs(self):
        rows = scan_source('templates/sample.html', '<div class="school-modal"><h2 class="school-modal-title">Title</h2><div class="school-modal-body">Body</div><button class="school-modal-close">Close</button></div>')
        self.assertEqual([r['kind'] for r in rows], ['dialog', 'button'])

    def test_verified_adapter_is_candidate_and_escaped_markup_is_not_lost(self):
        source = "import { adoptDomainControl as adopt } from './lq/domain-controls.js';\nconst button = document.createElement('button'); button.className='legacy'; adopt(button, { kind: 'choice' });"
        rows = scan_source('static/js/example.js', source)
        self.assertEqual(rows[0]['provenance'], 'shared-adapter-call-candidate')
        self.assertEqual(rows[0]['kind'], 'choice')
        self.assertEqual(rows[0]['status'], 'pending')
        rows = scan_source('static/js/example.js', r'const html = "<button class=\"btn\">Save</button>";')
        self.assertTrue(any(row['syntax'] == 'escaped-html-candidate' and row['status'] == 'unknown' for row in rows))

    def test_known_svg_icons_and_subpart_labels_do_not_mask_controls(self):
        source = "import { ClipboardList, CircleAlert } from 'lucide-react'; const view = <><ClipboardList /><CircleAlert onClick={act} role='button'/><button>Save</button></>;"
        rows = scan_source('frontend/src/example.tsx', source)
        self.assertEqual([r['kind'] for r in rows], ['content-slot', 'button', 'button'])
        self.assertEqual(rows[0]['provenance'], 'external-icon-content-slot')
        rows = scan_source('templates/example.html', '<div role="tablist" class="real-tabs"></div><span class="teaching-timeline-segment-title">Text</span><label class="afm-search--dialog"><input></label>')
        self.assertEqual([r['kind'] for r in rows], ['tab', 'field'])

    def test_native_factory_dataset_and_content_slot_adapter_are_retained(self):
        source = "import { adoptDomainContentSlot } from './lq/domain-controls.js'; const helper = document.createElement('textarea'); adoptDomainContentSlot(helper); const root = document.createElement('div'); root.className='lq-domain-region overlay'; root.dataset.lqComponent='layer';"
        rows = scan_source('static/js/example.js', source)
        self.assertEqual([r['kind'] for r in rows], ['content-slot', 'layer'])
        self.assertEqual(rows[0]['provenance'], 'shared-adapter-call-candidate')
        self.assertEqual(rows[1]['canonicalContract']['status'], 'canonical-declared-pending')

    def test_nested_document_factory_preserves_verified_handle_adapter(self):
        rows = scan_source('static/js/example.js', "import {adoptDomainHandle} from '../lq/domain-controls.js'; const handle = this.doc.createElement('button'); handle.type='button'; adoptDomainHandle(handle,{kind}); const bare = this.frame.document.createElement('button');")
        self.assertEqual([row['kind'] for row in rows], ['handle', 'button'])
        self.assertEqual(rows[0]['provenance'], 'shared-adapter-call-candidate')
        self.assertEqual(rows[1]['canonicalContract']['status'], 'not-declared')

    def test_structural_components_are_known_but_declaration_requires_canonical_class(self):
        rows = scan_source('templates/sample.html', '<div data-lq-component="layer" class="lq-domain-region"><div data-lq-component="surface" class="lq-surface"></div></div><div data-lq-component="content-slot" class="lq-domain-content-slot"></div><button data-lq-component="handle" class="lq-domain-handle"></button><span data-lq-component="chip" class="lq-chip"></span><div data-lq-component="domain" class="lq-domain-region"></div><button data-lq-component="button" class="custom"></button>')
        self.assertEqual([r['kind'] for r in rows], ['layer', 'surface', 'content-slot', 'handle', 'chip', 'domain', 'button'])
        self.assertTrue(all(r['status'] == 'pending' for r in rows))
        self.assertTrue(all(r['canonicalContract']['status'] == 'canonical-declared-pending' for r in rows[:-1]))
        self.assertEqual(rows[-1]['canonicalContract']['status'], 'declared-missing-canonical')

    def test_raised_recipe_requires_material_and_factory_scope_does_not_leak(self):
        rows = scan_source('templates/example.html', '<section data-lq-component="drawer" class="lq-domain-raised" data-lq-material="raised"></section><section data-lq-component="drawer" class="lq-domain-raised"></section><span data-lq-component="progress" class="lq-progress"></span>')
        self.assertEqual([row['canonicalContract']['status'] for row in rows], ['canonical-declared-pending', 'declared-missing-canonical', 'canonical-declared-pending'])
        self.assertEqual(rows[-1]['kind'], 'status')
        rows = scan_source('static/js/example.js', "function first(){ const node=document.createElement('button'); return node; } function second(){ const node=document.createElement('button'); node.className='lq-btn'; node.dataset.lqComponent='button'; return node; }")
        self.assertEqual(rows[0]['canonicalContract']['status'], 'not-declared')
        self.assertEqual(rows[1]['canonicalContract']['status'], 'canonical-declared-pending')

    def test_python_generated_iframe_controls_are_inventoried_without_docstrings(self):
        source = '''"""Example <button>documentation</button>"""
def render(name):
    return f'<button class="doc-preview-btn" data-key="{name}">Next</button><input type="hidden">'
'''
        rows = scan_source('classroom_app/services/renderer.py', source)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'button')
        self.assertEqual(rows[0]['line'], 3)
        self.assertEqual(rows[0]['syntax'], 'python-generated-jinja-html')

    def test_file_scope_and_report_are_deterministic_without_registry_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contents = {
                'templates/macros/lq/icons.generated.html': '<svg></svg>',
                'templates/page.html': '<button>Save</button>',
                'templates/partials/core.js': "document.createElement('button')",
                'static/js/page.js': 'const html = `<select></select>`',
                'static/js/icons.generated.js': 'const data = `<button>icon</button>`',
                'frontend/src/view.tsx': '<input />',
                'frontend/src/view.test.tsx': '<button />',
                'static/dist/bundle.js': '<button />',
            }
            for name, content in contents.items():
                destination = root / name; destination.parent.mkdir(parents=True, exist_ok=True); destination.write_text(content, encoding='utf-8')
            self.assertEqual(len(list(source_files(root))), 5)
            first = build_report(root); second = build_report(root)
            self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
            self.assertEqual(first['summary']['htmlTemplates'], 2)
            self.assertFalse((root / 'docs/lq-migration-registry.json').exists())


if __name__ == '__main__':
    unittest.main()
