/**
 * REST wrappers for /api/social (docs/social-friends-groups-2026-10-27.md §7).
 * Every call is `silent`: callers render their own error text. Errors keep
 * `status` and the server `detail` message (APIError from api.js).
 */
import { apiFetch } from '../api.js';

const BASE = '/api/social';
const seg = (value) => encodeURIComponent(String(value));

function call(path, { method = 'GET', body, signal } = {}) {
    return apiFetch(`${BASE}${path}`, { method, body, signal, silent: true, credentials: 'same-origin' });
}

export const socialApi = Object.freeze({
    friends: () => call('/friends'),
    requests: () => call('/friends/requests'),
    sendRequest: (target_identity, message = '') => call('/friends/requests', { method: 'POST', body: { target_identity, message } }),
    acceptRequest: (id) => call(`/friends/requests/${seg(id)}/accept`, { method: 'POST', body: {} }),
    declineRequest: (id) => call(`/friends/requests/${seg(id)}/decline`, { method: 'POST', body: {} }),
    withdrawRequest: (id) => call(`/friends/requests/${seg(id)}`, { method: 'DELETE' }),
    deleteFriend: (identity) => call(`/friends/${seg(identity)}`, { method: 'DELETE' }),
    setRemark: (identity, remark) => call(`/friends/${seg(identity)}`, { method: 'PATCH', body: { remark } }),
    search: (q, signal) => call(`/users/search?q=${seg(q)}`, { signal }),
    suggestions: () => call('/users/suggestions'),
    profile: (identity) => call(`/users/${seg(identity)}/profile`),
    blocks: () => call('/blocks'),
    block: (identity) => call('/blocks', { method: 'POST', body: { identity } }),
    unblock: (identity) => call(`/blocks/${seg(identity)}`, { method: 'DELETE' }),

    groups: () => call('/groups'),
    createGroup: (name, member_identities) => call('/groups', { method: 'POST', body: { name, member_identities } }),
    group: (id) => call(`/groups/${seg(id)}`),
    updateGroup: (id, patch) => call(`/groups/${seg(id)}`, { method: 'PATCH', body: patch }),
    addMembers: (id, identities) => call(`/groups/${seg(id)}/members`, { method: 'POST', body: { identities } }),
    removeMember: (id, identity) => call(`/groups/${seg(id)}/members/${seg(identity)}`, { method: 'DELETE' }),
    leaveGroup: (id) => call(`/groups/${seg(id)}/leave`, { method: 'POST', body: {} }),
    dissolveGroup: (id) => call(`/groups/${seg(id)}/dissolve`, { method: 'POST', body: {} }),
    transferGroup: (id, identity) => call(`/groups/${seg(id)}/transfer`, { method: 'POST', body: { identity } }),
    setAdmin: (id, identity, admin) => call(`/groups/${seg(id)}/admins`, { method: 'POST', body: { identity, admin } }),
    updateMe: (id, patch) => call(`/groups/${seg(id)}/me`, { method: 'PATCH', body: patch }),
    groupFiles: (id, kind = '', beforeId = 0) => {
        const query = new URLSearchParams();
        if (kind) query.set('kind', kind);
        if (beforeId) query.set('before_id', String(beforeId));
        const text = query.toString();
        return call(`/groups/${seg(id)}/files${text ? `?${text}` : ''}`);
    },

    conversations: () => call('/conversations'),
    messages: (key, beforeId = 0, limit = 50) => call(`/conversations/${seg(key)}/messages?limit=${limit}${beforeId ? `&before_id=${beforeId}` : ''}`),
    send: (key, payload) => call(`/conversations/${seg(key)}/messages`, { method: 'POST', body: payload }),
    read: (key, last_message_id) => call(`/conversations/${seg(key)}/read`, { method: 'POST', body: { last_message_id } }),
    nudge: (key, identity) => call(`/conversations/${seg(key)}/nudge`, { method: 'POST', body: identity ? { identity } : {} }),
    recall: (id) => call(`/messages/${seg(id)}/recall`, { method: 'POST', body: {} }),

    precheck: (spec) => call('/media/precheck', { method: 'POST', body: spec }),
    uploadInit: (spec) => call('/media/upload/init', { method: 'POST', body: spec }),
    uploadComplete: (uploadId) => call(`/media/upload/${seg(uploadId)}/complete`, { method: 'POST', body: {} }),
    backfill: (mediaId, media_token) => call(`/media/${seg(mediaId)}/backfill`, { method: 'POST', body: { media_token } }),
    lanStatus: (transferId, status) => call(`/lan/${seg(transferId)}/status`, { method: 'POST', body: { status } }),
    presence: (identities) => call(`/presence?identities=${seg(identities.join(','))}`),
    quota: () => call('/quota'),
});

/** Raw chunk PUT: apiFetch would JSON-encode a Blob body. */
export async function putChunk(uploadId, index, blob, signal) {
    const response = await fetch(`${BASE}/media/upload/${seg(uploadId)}/chunk?index=${index}`, {
        method: 'PUT', body: blob, signal, credentials: 'same-origin',
        headers: { 'Content-Type': 'application/octet-stream', Accept: 'application/json' },
    });
    let data = null;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
        const error = new Error(typeof data?.detail === 'string' ? data.detail : `上传失败（${response.status}）`);
        error.status = response.status;
        error.retryAfter = Number(response.headers.get('Retry-After')) || 0;
        throw error;
    }
    return data;
}

/** User-facing text for an API failure. */
export function errorText(error, fallback = '操作失败，请稍后重试。') {
    if (!error) return fallback;
    if (error.name === 'AbortError') return '已取消。';
    const message = String(error.message || '').trim();
    if (!message || message === '网络请求失败') return error.status ? fallback : '网络连接异常，请检查网络后重试。';
    return message;
}
