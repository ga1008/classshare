import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import { selectionProps, selectionMarkup, selectionOptions } from '../../static/js/lq/selection.js';
const fixture = JSON.parse(fs.readFileSync(new URL('../e2e/components/fixtures/lq-selection.json', import.meta.url), 'utf8'));
describe('LQ selection native fallback contract', () => {
    for (const item of fixture.invalid) it(`rejects ${JSON.stringify(item)}`, () => expect(() => selectionProps(item.kind, item.props)).toThrow());
    it('uses only native select as named required/reset value owner before enhancement', () => {
        const html = selectionMarkup('combobox', fixture.cases[0].props); expect(html).toContain('<select'); expect(html).toContain('name="sourceName"'); expect(html).toContain('required=""'); expect(html).not.toContain('role="combobox"'); expect(html).toContain('data-source-name="existing-contract"');
    });
    it('keeps multiple defaults in native selected attributes', () => { const p = selectionProps('listbox', fixture.cases[1].props); expect(p.attrs.multiple).toBe(''); expect(p.options[0].attrs.selected).toBe(''); expect(p.options[1].attrs.selected).toBeUndefined(); });
    it('escapes label/options/help and rejects partial result options before commit', () => { const html = selectionMarkup('combobox', fixture.cases[4].props); expect(html).not.toContain('<script>'); expect(html).toContain('&lt;script&gt;'); expect(() => selectionOptions([{ value: 'a', label: 'A' }, { value: 'b', label: { nodeType: 1 } }])).toThrow(); });
});
