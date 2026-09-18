/**
 * Client half of the per-assignment screenshot hash guard.
 *
 * Images picked by the student are hashed in the browser (SHA-256 via
 * WebCrypto) and checked, before any upload, against
 *   1. the files already sitting in the same or sibling upload areas,
 *   2. the server-side hash library of this assignment
 *      (POST /api/assignments/{id}/attachment-hash-check).
 * Conflicts are removed from the batch with a specific toast.  The server
 * re-checks on every draft save and on submit, so this layer is UX only.
 */

const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.heic', '.heif', '.tif', '.tiff', '.avif']);
const DRAWING_PREFIX = 'exam_drawings/';
const HASH_CONCURRENCY = 3;

export function isImageLikeFile(file, relativePath = '') {
    const mime = String(file?.type || '').toLowerCase();
    if (mime.startsWith('image/')) return true;
    const name = String(relativePath || file?.name || '').toLowerCase();
    const dot = name.lastIndexOf('.');
    return dot >= 0 && IMAGE_EXTENSIONS.has(name.slice(dot));
}

export function isHashGuardedEntry(entry) {
    if (!entry?.file) return false;
    const path = String(entry.relativePath || entry.file.name || '').replace(/\\/g, '/').toLowerCase();
    if (path.startsWith(DRAWING_PREFIX)) return false;
    if (entry.kind === 'exam_drawing' || entry.kind === 'drawing') return false;
    return isImageLikeFile(entry.file, entry.relativePath);
}

export function canHashInBrowser() {
    return typeof crypto !== 'undefined' && Boolean(crypto.subtle?.digest);
}

export async function hashFileSha256(file) {
    if (!file || !canHashInBrowser()) return '';
    try {
        const buffer = await file.arrayBuffer();
        const digest = await crypto.subtle.digest('SHA-256', buffer);
        return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, '0')).join('');
    } catch (error) {
        console.warn('[image-guard] hashing failed', error);
        return '';
    }
}

async function mapLimit(items, limit, worker) {
    const results = new Array(items.length);
    let cursor = 0;
    async function run() {
        while (cursor < items.length) {
            const index = cursor++;
            results[index] = await worker(items[index], index);
        }
    }
    await Promise.all(Array.from({ length: Math.min(limit, items.length) }, run));
    return results;
}

/** Fill `entry.hash` for every guarded entry that does not have one yet. */
export async function ensureEntryHashes(entries) {
    const pending = (entries || []).filter((entry) => isHashGuardedEntry(entry) && !entry.hash);
    await mapLimit(pending, HASH_CONCURRENCY, async (entry) => {
        entry.hash = await hashFileSha256(entry.file);
    });
    return entries;
}

export async function checkImageHashes(assignmentId, items, { replaceQuestionIds = [], fetchImpl } = {}) {
    const doFetch = fetchImpl || ((url, init) => fetch(url, init));
    const response = await doFetch(`/api/assignments/${encodeURIComponent(assignmentId)}/attachment-hash-check`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ items, replace_question_ids: replaceQuestionIds }),
    });
    let payload = {};
    try { payload = await response.json(); } catch { payload = {}; }
    if (!response.ok) {
        const error = new Error(payload?.detail?.message || payload?.detail || `hash check failed (${response.status})`);
        error.status = response.status;
        throw error;
    }
    return Array.isArray(payload?.results) ? payload.results : [];
}

function displayName(entry) {
    const path = String(entry?.relativePath || entry?.file?.name || '').replace(/\\/g, '/');
    return path.split('/').filter(Boolean).pop() || '截图';
}

function summarize(messages, notify) {
    if (!messages.length || typeof notify !== 'function') return;
    const preview = messages.slice(0, 3).join('；');
    const remaining = messages.length - 3;
    notify(remaining > 0 ? `${preview}；还有 ${remaining} 张截图也重复。` : preview, 'warning');
}

/**
 * Build a `beforeAdd` gate for SubmissionUploadManager.
 *
 * @param {object} options
 * @param {string} options.assignmentId
 * @param {() => string} [options.questionId]         scope of this upload area ('' = whole assignment)
 * @param {() => string} [options.questionLabel]      human label, e.g. '第 2 题'
 * @param {() => string[]} [options.replaceQuestionIds] questions whose server-draft files will be replaced
 * @param {() => Array<{hash:string,label:string,name:string}>} [options.knownHashes]
 *        hashes already held locally in sibling areas (other questions / server-draft chips)
 * @param {(message:string, type:string) => void} [options.notify]
 * @param {(entries:Array) => void} [options.onRejected]
 */
export function createDuplicateImageGuard(options = {}) {
    const assignmentId = String(options.assignmentId || '');
    const getQuestionId = typeof options.questionId === 'function' ? options.questionId : () => String(options.questionId || '');
    const getQuestionLabel = typeof options.questionLabel === 'function' ? options.questionLabel : () => String(options.questionLabel || '本次作业');
    const getReplaceIds = typeof options.replaceQuestionIds === 'function' ? options.replaceQuestionIds : () => (options.replaceQuestionIds || []);
    const getKnownHashes = typeof options.knownHashes === 'function' ? options.knownHashes : () => [];
    const notify = options.notify;

    return async function duplicateImageGate(candidates, manager) {
        const guarded = (candidates || []).filter(isHashGuardedEntry);
        if (!guarded.length || !canHashInBrowser()) return candidates;

        await ensureEntryHashes(guarded);
        const rejected = new Set();
        const messages = [];

        // 1. local knowledge: files already in this area, sibling areas, server-draft chips.
        const known = new Map();
        (manager?.entries || []).forEach((entry) => {
            if (entry?.hash) known.set(entry.hash, { label: getQuestionLabel(), name: displayName(entry) });
        });
        (getKnownHashes() || []).forEach((item) => {
            if (item?.hash && !known.has(item.hash)) known.set(item.hash, { label: item.label || '', name: item.name || '' });
        });
        const seenInBatch = new Map();
        guarded.forEach((entry) => {
            if (!entry.hash) return;
            const local = known.get(entry.hash);
            if (local) {
                rejected.add(entry);
                const where = local.label ? `已在${local.label}添加过` : '已经添加过';
                const which = local.name && local.name !== displayName(entry) ? `（文件名「${local.name}」）` : '';
                messages.push(`「${displayName(entry)}」${where}${which}，请勿重复上传。`);
                return;
            }
            const first = seenInBatch.get(entry.hash);
            if (first) {
                rejected.add(entry);
                messages.push(`「${displayName(entry)}」与本次选择的「${displayName(first)}」是同一张截图，请勿重复上传。`);
                return;
            }
            seenInBatch.set(entry.hash, entry);
        });

        // 2. server-side hash library of the whole assignment.
        const remaining = guarded.filter((entry) => entry.hash && !rejected.has(entry));
        if (remaining.length && assignmentId) {
            try {
                const questionId = getQuestionId();
                const results = await checkImageHashes(assignmentId, remaining.map((entry) => ({
                    hash: entry.hash,
                    question_id: questionId,
                    file_name: displayName(entry),
                    relative_path: entry.relativePath || '',
                })), { replaceQuestionIds: getReplaceIds() });
                const byHash = new Map(results.map((item) => [item.hash, item]));
                remaining.forEach((entry) => {
                    const result = byHash.get(entry.hash);
                    if (!result || result.status === 'ok') return;
                    rejected.add(entry);
                    messages.push(result.message || `「${displayName(entry)}」已在本次作业上传过，请重新选取其他截图。`);
                });
            } catch (error) {
                // Advisory only — the server enforces on upload. Keep the files.
                console.warn('[image-guard] server pre-check unavailable', error);
            }
        }

        if (rejected.size) {
            summarize(messages, notify);
            options.onRejected?.(Array.from(rejected));
        }
        return (candidates || []).filter((entry) => !rejected.has(entry));
    };
}

export default createDuplicateImageGuard;
