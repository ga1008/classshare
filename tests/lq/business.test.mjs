import { describe, it, expect } from 'vitest';
import { businessProps, businessMarkup } from '../../static/js/lq/business.js';
import { collectSubmissionJumpGroups } from '../../frontend/src/lib/submission-jump-nav.ts';

describe('LQ business presentation contracts', () => {
    it('keeps unknown job states neutral and does not infer progress or result authority', () => {
        for (const state of ['__proto__', 'constructor', 'toString', 'new-state']) {
            const p = { identity: 'job:1', generation: 1, state, label: '成功' };
            expect(businessProps('job_status', p).attrs['data-lq-job-state']).toBe('unknown');
            const html = businessMarkup('job_status', p);
            expect(html).toContain('任务状态未知'); expect(html).toContain('data-tone="neutral"');
            expect(html).not.toContain('<button'); expect(html).not.toContain('<progress');
        }
        const html = businessMarkup('job_status', { identity: 'job:1', generation: 1, state: 'running', label: '运行中', progress: { label: '正在处理' } });
        expect(html).toContain('<progress'); expect(html).not.toMatch(/\svalue=/); expect(html).not.toContain('%');
    });
    it('rejects inherited, sparse, unsafe and conflicting state before any DOM work', () => {
        expect(() => businessProps('job_status', Object.create({ identity: 'x', generation: 0, state: 'running', label: 'x' }))).toThrow();
        expect(() => businessProps('question_navigator', { groups: Array(1) })).toThrow();
        const job = { identity: 'x', generation: 0, state: 'running', label: 'x' };
        for (const value of [NaN, Infinity, 2 ** 53, -1]) expect(() => businessProps('job_status', { ...job, generation: value })).toThrow();
        expect(() => businessProps('job_status', { ...job, actions: [{ key: 'x', label: 'x', attrs: { onclick: 'x' } }] })).toThrow();
        expect(businessMarkup('job_status', { ...job, message: '<img src=x onerror=alert(1)>' })).not.toContain('<img');
        expect(() => businessProps('question_navigator', { groups: [{ id: 'p', label: 'P', items: [{ id: 'a', index: 1, current: true }, { id: 'b', index: 2, current: true }] }] })).toThrow();
    });
    it('presents actual submission answer snapshots without owning navigation or the existing island marker', () => {
        const existing = collectSubmissionJumpGroups({ examQuestions: { pages: [{ name: '基础', questions: [{ id: 'q1', text: '**解释**' }, { id: 'q2', text: '上传' }] }] },
            answers: { q1: '', q2: { attachments: [{ id: 9, name: '作业.pdf' }] } } });
        const tree = businessProps('question_navigator', { groups: existing.map((group, index) => ({ id: `p${index}`, label: group.title,
            items: group.items.map(item => ({ id: item.id, index: item.index, label: item.text, answered: item.answered })) })) });
        const markup = JSON.stringify(tree);
        expect(markup).toContain('未作答'); expect(markup).toContain('已作答'); expect(markup).not.toContain('submission-jump-managed');
        expect(markup).not.toContain('aria-current');
    });
});
