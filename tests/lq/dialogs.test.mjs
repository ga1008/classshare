import { describe, expect, it } from 'vitest';
import { choiceProps, dialogProps, dialogMarkup } from '../../static/js/lq/dialogs.js';

describe('LQ dialogs typed contract', () => {
    it('escapes all title, body, footer and close text through existing button markup', () => {
        const markup = dialogMarkup({ id: 'safe', title: '<img src=x onerror=alert(1)>', body: '<script>alert(2)</script>', footer: '"<svg>', closeLabel: '<close>' });
        expect(markup).not.toContain('<img'); expect(markup).not.toContain('<script');
        expect(markup).toContain('&lt;script&gt;'); expect(markup).toContain('aria-label="&lt;close&gt;"');
        expect(markup).toContain('lq-btn--ghost'); expect(markup.match(/class="lq-scrim"/g)).toHaveLength(1);
    });
    it.each([{ type: 'menu' }, { type: null }, { size: null }, { size: 'wide' }, { type: 'drawer', size: 'full' }, { side: 'left' }, { title: '' }, { closeButton: 'false' }, { body: { html: 'x' } }, { rawHTML: '<b>x</b>' }, { id: 'a--lq-title' }, { id: 'bad id' }])('rejects invalid structure %j', props => {
        expect(() => dialogProps({ id: 'safe', title: '标题', ...props })).toThrow();
    });
    it.each(['onclick', 'style', 'class', 'role', 'tabindex', 'hidden'])('rejects attribute bypass %s', name => {
        expect(() => dialogMarkup({ id: 'safe', title: '标题', attrs: { [name]: 'bad' } })).toThrow();
    });
    it('owns its semantic identity and ignores forged reserved data attributes', () => {
        expect(dialogProps({ id: 'safe', title: '标题', attrs: { id: 'evil', 'data-lq-dialog': 'menu', 'aria-labelledby': 'evil', 'data-user': 'kept' } }).rootAttrs)
            .toMatchObject({ id: 'safe', 'data-lq-dialog': 'modal', 'data-user': 'kept' });
        expect(dialogProps({ id: 'p', title: '浮层', type: 'popover' }).surfaceAttrs.class).not.toContain('lq-glass--thick');
    });
    it('requires one to three unique explicit actions and retains disabled/danger', () => {
        const p = choiceProps({ title: '三态', choices: [{ value: 'save', label: '保存' }, { value: 'discard', label: '放弃', danger: true }, { value: 'later', label: '稍后', disabled: true }] });
        expect(p.cancelLabel).toBe('返回'); expect(p.choices[2].disabled).toBe(true); expect(p.choices[1].danger).toBe(true);
        for (const choices of [[], Array(4).fill({ value: 'x', label: 'x' }), [{ value: 'x', label: 'x' }, { value: 'x', label: '重复' }], [{ value: false, label: '假' }], [{ value: 'x', label: '' }]]) {
            expect(() => choiceProps({ title: '选择', choices })).toThrow();
        }
    });
});
