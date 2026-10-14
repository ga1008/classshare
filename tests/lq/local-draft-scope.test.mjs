import { it, expect } from 'vitest';
import { localDraftKey, purgeForeignLocalDrafts } from '../../static/js/local_draft_scope.js';

function memoryStorage(initial = {}) {
    const data = new Map(Object.entries(initial));
    return {
        get length() { return data.size; },
        key: index => [...data.keys()][index] ?? null,
        getItem: key => (data.has(key) ? data.get(key) : null),
        setItem: (key, value) => data.set(key, String(value)),
        removeItem: key => data.delete(key),
        keys: () => [...data.keys()],
    };
}

it('scopes draft keys by user, kind, assignment and round', () => {
    expect(localDraftKey({ userId: 7, kind: 'exam', assignmentId: 12, round: 'v1' }))
        .toBe('lsdraft:v2:7:exam:12:v1');
    expect(localDraftKey({ userId: 7, kind: 'exam', assignmentId: 12, round: 'v1' }))
        .not.toBe(localDraftKey({ userId: 8, kind: 'exam', assignmentId: 12, round: 'v1' }));
});

it('rejects a key without a user so drafts can never be shared', () => {
    expect(() => localDraftKey({ userId: '', kind: 'exam', assignmentId: 12 })).toThrow();
});

it('removes other users drafts and legacy unscoped drafts, keeping own and unrelated keys', () => {
    const storage = memoryStorage({
        'lsdraft:v2:7:exam:12:v1': 'mine',
        'lsdraft:v2:8:exam:12:v1': 'theirs',
        'exam_12': 'legacy exam draft',
        'assignment_draft_5_unsubmitted': 'legacy assignment draft',
        'lanshare-theme': 'dark',
    });
    const removed = purgeForeignLocalDrafts(storage, 7);
    expect(removed).toBe(3);
    expect(storage.keys().sort()).toEqual(['lanshare-theme', 'lsdraft:v2:7:exam:12:v1']);
});

it('purges every scoped draft when no user is signed in', () => {
    const storage = memoryStorage({ 'lsdraft:v2:7:exam:12:v1': 'x', 'exam_3': 'y', other: 'z' });
    purgeForeignLocalDrafts(storage, null);
    expect(storage.keys()).toEqual(['other']);
});

it('tolerates storage that throws (private mode)', () => {
    const broken = { get length() { throw new Error('denied'); } };
    expect(purgeForeignLocalDrafts(broken, 7)).toBe(0);
});
