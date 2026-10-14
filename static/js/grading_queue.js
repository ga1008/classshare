/**
 * Grading queue: the order of answer cards a teacher is working through.
 *
 * The assignment page records the submissions currently listed (after its
 * filters and sort) when the teacher opens one; the answer card then offers
 * 上一份 / 下一份 within that same sequence. Kept per tab in sessionStorage and
 * scoped to the teacher, so another account on the same browser never sees it.
 */
const PREFIX = 'lanshare:grading-queue:v1';
const MAX_IDS = 2000;

function storageKey(teacherId, assignmentId) {
    return `${PREFIX}:${teacherId}:${assignmentId}`;
}

/** Remember the visible order; ``ids`` are submission ids as listed. */
export function rememberGradingQueue({ teacherId, assignmentId, ids }) {
    const clean = [...new Set((ids || []).map(Number).filter((id) => Number.isInteger(id) && id > 0))].slice(0, MAX_IDS);
    if (!teacherId || !assignmentId || !clean.length) return;
    try {
        sessionStorage.setItem(storageKey(teacherId, assignmentId), JSON.stringify(clean));
    } catch { /* Navigation still works without the queue. */ }
}

/** ``{ prev, next, position, total }`` for this card, or null when unknown. */
export function gradingQueueNeighbors({ teacherId, assignmentId, submissionId }) {
    let ids = [];
    try {
        ids = JSON.parse(sessionStorage.getItem(storageKey(teacherId, assignmentId)) || '[]');
    } catch {
        return null;
    }
    const index = Array.isArray(ids) ? ids.indexOf(Number(submissionId)) : -1;
    if (index < 0) return null;
    return {
        prev: index > 0 ? ids[index - 1] : null,
        next: index < ids.length - 1 ? ids[index + 1] : null,
        position: index + 1,
        total: ids.length,
    };
}

export default { rememberGradingQueue, gradingQueueNeighbors };
