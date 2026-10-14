// Browser-local answer drafts are scoped to the signed-in user.
//
// Lab and library computers are shared: a draft key without the user id let
// the next student read (and even sync) the previous student's answers. Every
// key now embeds the user id, and drafts belonging to anyone else — including
// legacy unscoped keys — are removed from the device on page load and on the
// login pages. Server drafts remain the durable copy, so nothing is lost.

const PREFIX = 'lsdraft:v2:';
const LEGACY_PATTERNS = [/^exam_\d+$/, /^assignment_draft_/];

export function localDraftKey({ userId, kind, assignmentId, round = '' }) {
    const owner = String(userId ?? '').trim();
    if (!owner) throw new Error('A local draft key requires the signed-in user id');
    return `${PREFIX}${owner}:${kind}:${assignmentId}:${round}`;
}

export function purgeForeignLocalDrafts(storage, userId) {
    const owner = userId === null || userId === undefined ? '' : String(userId).trim();
    const ownPrefix = owner ? `${PREFIX}${owner}:` : null;
    let removed = 0;
    try {
        const keys = [];
        for (let index = 0; index < storage.length; index += 1) {
            const key = storage.key(index);
            if (key) keys.push(key);
        }
        for (const key of keys) {
            const scoped = key.startsWith(PREFIX);
            const legacy = LEGACY_PATTERNS.some(pattern => pattern.test(key));
            if (!scoped && !legacy) continue;
            if (scoped && ownPrefix && key.startsWith(ownPrefix)) continue;
            storage.removeItem(key);
            removed += 1;
        }
    } catch {
        return removed;
    }
    return removed;
}
