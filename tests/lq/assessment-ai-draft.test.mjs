import { afterEach, expect, test, vi } from 'vitest';
import { createWorkspaceState } from '../../static/js/ai_workspace_state.js';

afterEach(() => vi.unstubAllGlobals());

test('assessment AI drafts and pending requests are isolated by the server permission scope', () => {
    const values = new Map();
    vi.stubGlobal('sessionStorage', {
        getItem: key => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, String(value)),
        removeItem: key => values.delete(key),
    });
    vi.stubGlobal('window', {indexedDB: null});
    const ordinary = createWorkspaceState('student:1');
    ordinary.patch({draft: '普通聊天草稿', pendingRequest: {requestId: 'outside'}});
    const first = createWorkspaceState('student:1', 'a'.repeat(64));
    expect(first.value).toEqual({});
    first.patch({draft: '当前允许题的草稿'});
    expect(createWorkspaceState('student:1', 'a'.repeat(64)).value.draft).toBe('当前允许题的草稿');
    expect(createWorkspaceState('student:1', 'b'.repeat(64)).value).toEqual({});
    expect(createWorkspaceState('student:1').value.draft).toBe('普通聊天草稿');
});
