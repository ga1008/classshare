import { describe, it, expect } from 'vitest';
import { insightProps, insightMarkup } from '../../static/js/lq/insights.js';

describe('LQ optional insight semantics', () => {
    it('keeps absent, decorative zero and meaningful zero distinct without creating a false percentage', () => {
        for (const [value, zero, state] of [[null, 'empty', 'missing'], [0, 'empty', 'empty'], [0, 'value', 'value']]) {
            const props = { title: '分数', value, percent: 0, zero, unit: '分' };
            expect(insightProps('insight_meter', props).attrs['data-state']).toBe(state);
            const html = insightMarkup('insight_meter', props); expect(html).not.toContain('lq-meter__track');
            if (state === 'value') expect(html).toContain('0分'); else expect(html).not.toContain('0分');
        }
        const ring = insightMarkup('insight_ring', { title: '比例', value: 0, total: 0, zero: 'value' });
        expect(ring).not.toContain('%'); expect(ring).toContain('尚未提供统计数据');
    });
    it('rejects unbounded/inconsistent data, CSS injection and inherited props before rendering', () => {
        for (const value of [-1, NaN, Infinity, 2 ** 53, true, '3']) expect(() => insightProps('insight_meter', { title: '数据', value, percent: 20 })).toThrow();
        expect(() => insightProps('insight_ring', { title: '比例', value: 3, total: 2 })).toThrow();
        expect(() => insightProps('insight_meter', { title: '量', value: 3, percent: 101 })).toThrow();
        expect(() => insightProps('insight_bars', { title: '分类', items: [{ label: 'A', value: 1, tone: 'url(x)' }] })).toThrow();
        expect(() => insightProps('avatar_stack', Object.create({ items: [] }))).toThrow();
        expect(() => insightProps('avatar_stack', { items: Array(2) })).toThrow();
    });
    it('limits avatar images without omitting hidden member names or validating only the visible four', () => {
        const items = Array.from({ length: 6 }, (_, i) => ({ name: `成员${i}`, detail: `职责${i}`, src: '/avatar.png' }));
        const tree = insightProps('avatar_stack', { items });
        expect(tree.attrs['aria-label']).toContain('成员5（职责5）');
        const html = insightMarkup('avatar_stack', { items });
        expect((html.match(/<img /g) || [])).toHaveLength(4); expect(html).toContain('+2');
        items[5].src = 'javascript:alert(1)'; expect(() => insightProps('avatar_stack', { items })).toThrow();
    });
    it('keeps exact supplied values separate from derived visual proportions and requires semantic tones', () => {
        const p = { title: '分类', items: [{ label: 'A', value: .5, tone: 'teal' }, { label: 'B', value: 1.5 }, { label: '未知', value: null }] };
        const html = insightMarkup('insight_bars', p);
        expect(html).toContain('data-tone="teal"'); expect(html).toContain('>0.5<'); expect(html).toContain('>1.5<'); expect(html).toContain('未提供');
        expect(html).toContain('--lq-insight-percent: 33.33'); expect(html).not.toContain('#0d9488');
        expect(() => insightProps('insight_bars', { ...p, tone: '#0d9488' })).toThrow();
    });
});
