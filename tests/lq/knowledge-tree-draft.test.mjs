import { afterEach, describe, expect, it, vi } from 'vitest';
import { addToTree, normalizeTreeWeights, removeFromTree, progressFor } from '../../static/js/knowledge_tree/model.js';
import { createEditHistory } from '../../static/js/knowledge_tree/history.js';

const tree = () => ({ title: '网络原理', summary: '', nodes: [
    { id: 'a', label: '可靠传输', children: [{ id: 'a1', label: '确认', weight: 60 }, { id: 'a2', label: '重传', weight: 40 }] },
] });
const state = (value = tree(), done = ['a1']) => ({ tree: value, ...progressFor(value, done) });

afterEach(() => vi.unstubAllGlobals());

describe('knowledge tree draft operations', () => {
    it('subdivision preserves completed weight and works on an HTTP LAN origin', () => {
        vi.stubGlobal('crypto', {});
        const source = tree();
        const added = addToTree(source, ['a1'], 'a1');
        expect(added.created_id).toMatch(/^n[A-Za-z0-9_-]{1,31}$/);
        expect(added.completed_ids).toEqual([added.created_id]);
        expect(progressFor(added.tree, added.completed_ids).progress).toBe(60);
        expect(added.tree.nodes[0].children[0].children[0].weight).toBe(60);
        expect(source).toEqual(tree());
        expect(addToTree(added.tree, added.completed_ids, added.created_id)).toBeNull();
    });

    it('deletion and undo restore the exact subtree and completed flags', () => {
        const history = createEditHistory();
        const original = state(); history.reset(original);
        const result = removeFromTree(original.tree, 'a1', original.completed_ids);
        const removed = state(result.tree, result.completed);
        history.push(original, removed, '删除');
        expect(history.dirty(removed)).toBe(true);
        const undo = history.undo();
        expect(undo.tree).toEqual(original.tree);
        expect(undo.completed_ids).toEqual(['a1']);
        expect(history.dirty(undo)).toBe(false);
    });

    it('save advances the baseline while retaining undo and ignoring server timestamps', () => {
        const history = createEditHistory();
        const original = state(); history.reset(original);
        const next = state({ ...tree(), title: '更名' });
        history.push(original, next, '修改');
        const response = { ...next, tree: { ...next.tree, depth: 2, edited_at: '2026-10-21T00:00:00' } };
        history.saved(response);
        expect(history.dirty(response)).toBe(false);
        expect(history.size).toBe(1);
        expect(history.dirty(history.undo())).toBe(true);
    });

    it('bounds history and does not record unchanged operations', () => {
        const history = createEditHistory(2);
        const a = state(); history.reset(a);
        const b = state({ ...tree(), title: 'B' });
        const c = state({ ...tree(), title: 'C' });
        const d = state({ ...tree(), title: 'D' });
        expect(history.push(a, a, '无改动')).toBe(false);
        history.push(a, b, 'A'); history.push(b, c, 'B'); history.push(c, d, 'C');
        expect(history.size).toBe(2);
        expect(history.undo().tree.title).toBe('C');
        expect(history.undo().tree.title).toBe('B');
        expect(history.undo()).toBeNull();
    });

    it('normalization preserves explicit zero and gives small positive units at least one', () => {
        const source = { title: '比重', nodes: [{ id: 'a', label: '大知识点', children: [
            { id: 'x', label: '零', weight: 0 }, { id: 'y', label: '重点', weight: 10000 }, { id: 'z', label: '基础', weight: 1 },
        ] }] };
        const result = normalizeTreeWeights(source);
        expect(result.nodes[0].children.map(node => node.weight)).toEqual([0, 99, 1]);
        expect(source.nodes[0].children[1].weight).toBe(10000);
    });
});
