/**
 * Social media upload pipeline (social doc §6):
 *   SHA-256 → POST /media/precheck → (dedup hit: token, nothing uploaded)
 *   → POST /media/upload/init → PUT 1 MB chunks (sequential, retried with
 *   backoff, resumed from `received_bytes`) → POST complete → media_token.
 *
 * Hashing: files ≤ 64 MB use crypto.subtle.digest on one ArrayBuffer; larger
 * files (or insecure origins) stream 4 MB slices through the incremental
 * hasher, so a 100 MB file is never held in memory twice.
 * The server allows one upload session per user, so callers upload one file
 * at a time. `rateBytesPerSec` paces chunk PUTs (LAN backfill uses 512 KB/s).
 */
import { socialApi, putChunk } from './social_api.js';
import { Sha256, bufferToHex } from './sha256.js';

export const WHOLE_DIGEST_LIMIT = 64 * 1024 * 1024;
const HASH_SLICE = 4 * 1024 * 1024;
const MAX_RETRIES = 5;
const RETRYABLE = new Set([0, 408, 425, 429, 500, 502, 503, 504]);

const abortError = () => new DOMException('已取消', 'AbortError');
const sleep = (ms, signal) => new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(abortError()); return; }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => { clearTimeout(timer); reject(abortError()); }, { once: true });
});

/**
 * @param {Blob} file
 * @param {{signal?: AbortSignal, onProgress?: (fraction: number) => void}} [options]
 * @returns {Promise<string>} lowercase hex digest
 */
export async function sha256File(file, { signal, onProgress } = {}) {
    const subtle = globalThis.crypto?.subtle;
    if (subtle && file.size <= WHOLE_DIGEST_LIMIT) {
        const buffer = await file.arrayBuffer();
        if (signal?.aborted) throw abortError();
        const digest = bufferToHex(await subtle.digest('SHA-256', buffer));
        onProgress?.(1);
        return digest;
    }
    const hasher = new Sha256();
    for (let offset = 0; offset < file.size; offset += HASH_SLICE) {
        if (signal?.aborted) throw abortError();
        const slice = await file.slice(offset, Math.min(file.size, offset + HASH_SLICE)).arrayBuffer();
        hasher.update(new Uint8Array(slice));
        onProgress?.(Math.min(1, (offset + slice.byteLength) / Math.max(1, file.size)));
        await sleep(0, signal); // yield so the page stays responsive
    }
    return hasher.hex();
}

export function fileSpec(file, sha256) {
    return {
        sha256,
        size: file.size,
        mime: file.type || 'application/octet-stream',
        filename: file.name || 'file',
    };
}

/** Client-side limits so the user sees the reason before any byte moves. */
export function checkLimits(file, quota) {
    const mib = (bytes) => Math.round(bytes / 1024 / 1024);
    if (file.size <= 0) return `「${file.name}」是空文件。`;
    if (quota?.file_limit && file.size > quota.file_limit) return `「${file.name}」超过单个文件上限 ${mib(quota.file_limit)} MB。`;
    return '';
}

async function withRetry(task, signal) {
    let attempt = 0;
    for (;;) {
        try {
            return await task();
        } catch (error) {
            if (error?.name === 'AbortError' || signal?.aborted) throw abortError();
            const status = Number(error?.status) || 0;
            attempt += 1;
            if (!RETRYABLE.has(status) || attempt > MAX_RETRIES) throw error;
            const wait = error.retryAfter ? error.retryAfter * 1000 : Math.min(8000, 500 * 2 ** (attempt - 1));
            await sleep(wait, signal);
        }
    }
}

async function sendChunks(file, session, { signal, onProgress, rateBytesPerSec }) {
    const chunk = Number(session.chunk_size) || 1024 * 1024;
    let received = Number(session.received_bytes) || 0;
    const started = performance.now();
    let sentHere = 0;
    while (received < file.size) {
        const index = Math.floor(received / chunk);
        const start = index * chunk;
        const blob = file.slice(start, Math.min(file.size, start + chunk));
        let result;
        try {
            result = await withRetry(() => putChunk(session.upload_id, index, blob, signal), signal);
        } catch (error) {
            if (error?.status !== 409) throw error;
            // Out of order (a lost response): the server tells us where to resume.
            const fresh = await socialApi.uploadInit(session.spec);
            if (fresh.upload_id !== session.upload_id) throw error;
            received = Number(fresh.received_bytes) || 0;
            continue;
        }
        const next = Number(result?.received_bytes) || start + blob.size;
        sentHere += Math.max(0, next - received);
        received = next;
        onProgress?.(received / file.size);
        if (rateBytesPerSec) {
            const due = (sentHere / rateBytesPerSec) * 1000 - (performance.now() - started);
            if (due > 0) await sleep(due, signal);
        }
    }
}

/**
 * Upload (or deduplicate) one file and return its single-use media token.
 * @param {File|Blob} file
 * @param {{signal?: AbortSignal, onPhase?: (phase: 'hash'|'check'|'upload'|'done', fraction?: number) => void,
 *          rateBytesPerSec?: number, sha256?: string}} [options]
 * @returns {Promise<{media_token: string, sha256: string, deduplicated: boolean}>}
 */
export async function uploadSocialFile(file, { signal, onPhase, rateBytesPerSec = 0, sha256 = '' } = {}) {
    onPhase?.('hash', 0);
    const digest = sha256 || await sha256File(file, { signal, onProgress: (f) => onPhase?.('hash', f) });
    const spec = fileSpec(file, digest);
    onPhase?.('check', 0);
    const pre = await withRetry(() => socialApi.precheck(spec), signal);
    if (pre?.exists && pre.media_token) {
        onPhase?.('done', 1);
        return { media_token: pre.media_token, sha256: digest, deduplicated: true };
    }
    const session = { ...(await withRetry(() => socialApi.uploadInit(spec), signal)), spec };
    onPhase?.('upload', (Number(session.received_bytes) || 0) / Math.max(1, file.size));
    await sendChunks(file, session, { signal, rateBytesPerSec, onProgress: (f) => onPhase?.('upload', f) });
    const done = await withRetry(() => socialApi.uploadComplete(session.upload_id), signal);
    onPhase?.('done', 1);
    return { media_token: done.media_token, sha256: digest, deduplicated: false };
}

/** Media kind as the server derives it from the MIME major type. */
export function mediaKind(mime) {
    const major = String(mime || '').split('/', 1)[0].toLowerCase();
    return ['image', 'video', 'audio'].includes(major) ? major : 'file';
}
