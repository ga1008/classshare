import { describe, it, expect } from 'vitest';
import { composerProps, composerMarkup } from '../../static/js/lq/composer.js';

describe('LQ opt-in composer contract', () => {
    it('defaults to multiline and real submit, preserves exact text and attachment-only readiness', () => {
        const tree = composerProps('composer', { value: '  多行\n草稿  ', required: true, submit_name: 'action', submit_value: 'send' });
        expect(tree.attrs['data-lq-enter']).toBe('newline'); expect(tree.children[1].children).toEqual(['  多行\n草稿  ']);
        expect(tree.children[1].attrs.required).toBe(''); expect(tree.children[2].children[1].button.type).toBe('submit');
        expect(composerProps('composer', {}).children[2].children[1].button.disabled).toBe(true);
        expect(composerProps('composer', { hasContent: true }).children[2].children[1].button.disabled).toBe(false);
    });
    it('busy keeps a named readonly input, disables actual submitter, never claims success', () => {
        const tree = composerProps('composer', { value: 'draft', busy: true, form: 'external' });
        expect(tree.children[1].attrs).toMatchObject({ readonly: '', name: 'content', form: 'external' });
        expect(tree.children[1].attrs).not.toHaveProperty('disabled'); expect(tree.children[2].children[1].button.disabled).toBe(true);
        expect(composerMarkup('composer', { busy: true })).not.toContain('aria-live');
    });
    it('rejects prototype, false scalar, invalid native and policy props without injecting markup', () => {
        for (const p of [Object.create({ value: 'x' }), JSON.parse('{"__proto__":{}}'), { enter: 'auto' }, { busy: 1 }, { value: null }, { maxlength: true }, { maxlength: 0 }, { form: '' }, { submit_value: 'x' }, { attrs: { onclick: 'x' } }, { attachment: '' }]) expect(() => composerProps('composer', p)).toThrow();
        const html = composerMarkup('composer', { value: '</textarea><img src=x onerror=alert(1)>', submit_name: 'x" onclick="x', submit_value: '<script>' });
        expect(html).not.toContain('<img'); expect(html).toContain('&lt;/textarea&gt;'); expect(html).toContain('&quot;');
    });
});
