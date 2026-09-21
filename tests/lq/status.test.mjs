import { describe, it, expect } from 'vitest';
import { statusProps, statusMarkup } from '../../static/js/lq/status.js';

describe('LQ status presentation contract', () => {
    it('never upgrades an unknown or local-only save to server success', () => {
        for (const state of ['__proto__', 'constructor', 'toString', '<img src=x>', 'new-state']) {
            const tree = statusProps('save_status', { state, label: '已保存' });
            expect(tree.attrs['data-tone']).toBe('neutral');
            expect(tree.attrs['data-lq-save-state']).toBe('unknown');
            expect(statusMarkup('save_status', { state, label: '已保存' })).toContain('保存状态未知');
        }
        expect(statusProps('save_status', { state: 'local_saved' }).attrs['data-lq-tone-level']).toBe('neutral');
        expect(statusMarkup('save_status', { state: 'local_saved' })).toContain('已保存到本机');
        expect(statusMarkup('save_status', { state: 'local_saved' })).not.toContain('已同步到服务器');
    });
    it('rejects inherited, handler, disabled-action and raw-markup bypasses', () => {
        expect(() => statusProps('save_status', Object.create({ state: 'synced' }))).toThrow();
        for (const action of [{ label: '重试', onClick() {} }, { label: '重试', attrs: { onclick: 'alert(1)' } },
            { label: '重试', disabled: true }, { label: '重试', href: 'javascript:alert(1)' }]) {
            expect(() => statusProps('save_status', { state: 'error', action })).toThrow();
        }
        expect(() => statusProps('conflict', { action: { label: '核对' }, local: { html: '<b>x</b>' } })).toThrow();
        const html = statusMarkup('alert', { body: '<img src=x onerror=alert(1)>', action: { label: '<重新核对>' } });
        expect(html).not.toContain('<img'); expect(html).toContain('&lt;img');
    });
    it('does not put actions or all visual changes in a live root', () => {
        const save = statusProps('save_status', { state: 'error', action: { label: '重试' } });
        expect(save.attrs['aria-live']).toBeUndefined();
        expect(save.children.at(-1).children).toEqual([]);
        expect(save.children.at(-1).attrs['aria-live']).toBe('polite');
        const alert = statusProps('alert', { body: '可检查的说明' });
        expect(alert.children[0].attrs.role).toBeUndefined();
        expect(statusProps('alert', { body: '明确业务告警', announce: 'assertive' }).children[0].attrs.role).toBe('alert');
    });
});
