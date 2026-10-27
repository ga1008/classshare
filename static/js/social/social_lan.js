/**
 * 局域网直传 (social doc §9): WebRTC DataChannel between two online friends.
 *
 * Signalling rides /ws/social (`lan_offer/lan_answer/lan_ice/lan_cancel`,
 * relayed only between friends/co-members). `iceServers: []` keeps every
 * candidate local; no TURN/STUN. Protocol on the channel:
 *   sender → binary chunks … → '{"t":"eof"}'
 *   receiver → '{"t":"ack","ok":true|false}' after size + SHA-256 verification
 *   sender sends the chat message (lan_media) and then '{"t":"bound",...}'
 *   mapping the stored media id/transfer id to the received bytes.
 * The signalling `transfer_id` is a client token; the server-side transfer id
 * (Message.media[].transfer_id) exists only after the message is stored, and
 * both sides report the outcome there via POST /lan/{id}/status.
 * After success the sender backfills the bytes to the server (≤ 512 KB/s) and
 * calls POST /media/{id}/backfill, so other devices stop showing 补传中.
 */
import { socialApi } from './social_api.js';
import { uploadSocialFile } from './social_upload.js';
import { Sha256 } from './sha256.js';

export const LAN_MIN_BYTES = 5 * 1024 * 1024;
export const LAN_CONNECT_TIMEOUT_MS = 8000;
const STALL_TIMEOUT_MS = 15000;
const CHUNK = 64 * 1024;
const BUFFER_HIGH = 4 * CHUNK * 16;
const BACKFILL_RATE = 512 * 1024;
const RECEIVE_LIMIT = 200 * 1024 * 1024;

/** media id → { url, filename, size } for bytes received over LAN in this tab. */
const received = new Map();
const listeners = new Set();

export const lanStore = Object.freeze({
    get: (mediaId) => received.get(Number(mediaId)) || null,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
});

function publish(mediaId, entry) {
    received.set(Number(mediaId), entry);
    for (const fn of [...listeners]) { try { fn(Number(mediaId), entry); } catch { /* UI listener */ } }
}

export function lanSupported() {
    return typeof window.RTCPeerConnection === 'function';
}

const token = () => `lan-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
const text = (value) => JSON.stringify(value);

function waitFor(predicate, timeoutMs, signal) {
    return new Promise((resolve, reject) => {
        const started = Date.now();
        const tick = () => {
            if (signal?.aborted) { reject(new DOMException('已取消', 'AbortError')); return; }
            if (predicate()) { resolve(); return; }
            if (Date.now() - started > timeoutMs) { reject(new Error('timeout')); return; }
            setTimeout(tick, 50);
        };
        tick();
    });
}

/** Common signalling plumbing for one peer connection. */
function createPeer(client, peer, transferId) {
    const pc = new RTCPeerConnection({ iceServers: [] });
    const queued = [];
    const offs = [];
    pc.addEventListener('icecandidate', (event) => {
        if (event.candidate) client.send({ type: 'lan_ice', to: peer, transfer_id: transferId, payload: event.candidate.toJSON() });
    });
    offs.push(client.on('lan_ice', async (frame) => {
        if (frame.transfer_id !== transferId || frame.from !== peer || !frame.payload) return;
        if (!pc.remoteDescription) { queued.push(frame.payload); return; }
        try { await pc.addIceCandidate(frame.payload); } catch { /* stale candidate */ }
    }));
    return {
        pc,
        async flush() { while (queued.length) { try { await pc.addIceCandidate(queued.shift()); } catch { /* ignore */ } } },
        onCancel(fn) { offs.push(client.on('lan_cancel', (frame) => { if (frame.transfer_id === transferId && frame.from === peer) fn(); })); },
        close() { offs.forEach((off) => off()); try { pc.close(); } catch { /* closed */ } },
    };
}

/**
 * Sender side. Resolves `{ok:true, message}` after the message is stored, or
 * `{ok:false, reason}` when the caller must fall back to a server upload.
 * @param {{client: any, peer: string, file: File, sha256: string,
 *          sendMessage: (lanSpec: object) => Promise<any>, onProgress?: (f: number) => void, signal?: AbortSignal}} options
 */
export async function sendViaLan({ client, peer, file, sha256, sendMessage, onProgress, signal }) {
    if (!lanSupported() || client.status !== 'online') return { ok: false, reason: 'unsupported' };
    const transferId = token();
    const link = createPeer(client, peer, transferId);
    const { pc } = link;
    const channel = pc.createDataChannel('social-file', { ordered: true });
    channel.binaryType = 'arraybuffer';
    channel.bufferedAmountLowThreshold = CHUNK * 16;
    let cancelled = false;
    link.onCancel(() => { cancelled = true; });
    const replies = [];
    channel.addEventListener('message', (event) => {
        if (typeof event.data === 'string') { try { replies.push(JSON.parse(event.data)); } catch { /* ignore */ } }
    });
    const offAnswer = client.on('lan_answer', async (frame) => {
        if (frame.transfer_id !== transferId || frame.from !== peer || !frame.payload?.sdp) return;
        try { await pc.setRemoteDescription(frame.payload.sdp); await link.flush(); } catch { cancelled = true; }
    });
    const fail = (reason) => {
        offAnswer();
        client.send({ type: 'lan_cancel', to: peer, transfer_id: transferId, payload: { reason } });
        link.close();
        return { ok: false, reason };
    };
    let stored = false;
    try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        const meta = { filename: file.name, size: file.size, mime: file.type || 'application/octet-stream', sha256 };
        if (!client.send({ type: 'lan_offer', to: peer, transfer_id: transferId, payload: { sdp: pc.localDescription.toJSON(), meta } })) return fail('offline');
        await waitFor(() => cancelled || channel.readyState === 'open', LAN_CONNECT_TIMEOUT_MS, signal);
        if (cancelled) return fail('declined');
        let sent = 0;
        let lastProgress = 0;
        for (let offset = 0; offset < file.size; offset += CHUNK) {
            if (cancelled || channel.readyState !== 'open') return fail('interrupted');
            if (channel.bufferedAmount > BUFFER_HIGH) {
                await waitFor(() => channel.bufferedAmount <= channel.bufferedAmountLowThreshold || channel.readyState !== 'open', STALL_TIMEOUT_MS, signal);
            }
            const bytes = await file.slice(offset, Math.min(file.size, offset + CHUNK)).arrayBuffer();
            channel.send(bytes);
            sent += bytes.byteLength;
            if (Date.now() - lastProgress > 120) { onProgress?.(sent / file.size); lastProgress = Date.now(); }
        }
        channel.send(text({ t: 'eof' }));
        await waitFor(() => cancelled || replies.some((item) => item.t === 'ack'), STALL_TIMEOUT_MS + file.size / 50_000, signal);
        const ack = replies.find((item) => item.t === 'ack');
        if (!ack?.ok) return fail('verify');
        onProgress?.(1);
        const message = await sendMessage({ sha256, size: file.size, mime: meta.mime, filename: meta.filename });
        stored = true;
        const media = (message?.media || []).find((item) => item.delivered_via === 'lan') || null;
        if (channel.readyState === 'open' && media) channel.send(text({ t: 'bound', media_id: media.id, transfer_id: media.transfer_id, message_id: message.id }));
        if (media?.transfer_id) socialApi.lanStatus(media.transfer_id, 'p2p_done').catch(() => {});
        // The sending tab already holds the bytes: show them while the backfill runs.
        if (media) publish(media.id, { url: URL.createObjectURL(file), filename: meta.filename, size: file.size });
        offAnswer();
        setTimeout(() => link.close(), 1500);
        if (media) void backfill(file, sha256, media.id);
        return { ok: true, message };
    } catch (error) {
        if (stored) throw error;
        if (error?.name === 'AbortError') { fail('cancelled'); throw error; }
        // A refused send (403/429…) must surface; transport problems fall back.
        if (error?.status) { fail('send'); throw error; }
        return fail(error?.message === 'timeout' ? 'timeout' : 'error');
    }
}

/** Paced server copy of LAN-delivered bytes; retried a few times, silently. */
async function backfill(file, sha256, mediaId) {
    for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
            const { media_token: mediaToken } = await uploadSocialFile(file, { sha256, rateBytesPerSec: BACKFILL_RATE });
            await socialApi.backfill(mediaId, mediaToken);
            return true;
        } catch {
            await new Promise((resolve) => setTimeout(resolve, 5000 * (attempt + 1)));
        }
    }
    return false;
}

/**
 * Receiver side: answer offers automatically (the server relays only between
 * friends/co-members and never across a block), verify, keep the Blob in memory.
 * @param {any} client SocialClient
 * @param {{onIncoming?: Function, onProgress?: Function, onDone?: Function}} [hooks]
 * @returns {() => void} unsubscribe
 */
export function listenForLan(client, hooks = {}) {
    if (!lanSupported()) return () => {};
    return client.on('lan_offer', async (frame) => {
        const meta = frame.payload?.meta || {};
        const size = Number(meta.size) || 0;
        if (!frame.payload?.sdp || size <= 0 || size > RECEIVE_LIMIT) return;
        const transferId = frame.transfer_id;
        const link = createPeer(client, frame.from, transferId);
        const info = { transferId, from: frame.from, filename: String(meta.filename || 'file'), size, mime: String(meta.mime || '') };
        const parts = [];
        const hasher = new Sha256();
        let got = 0;
        let entry = null;
        link.onCancel(() => { link.close(); if (!entry) hooks.onDone?.({ ...info, ok: false }); });
        link.pc.addEventListener('datachannel', (event) => {
            const channel = event.channel;
            channel.binaryType = 'arraybuffer';
            hooks.onIncoming?.(info);
            channel.addEventListener('message', (msg) => {
                if (typeof msg.data !== 'string') {
                    const bytes = new Uint8Array(msg.data);
                    if (got + bytes.byteLength > size) return;
                    parts.push(bytes);
                    hasher.update(bytes);
                    got += bytes.byteLength;
                    hooks.onProgress?.({ ...info, fraction: got / size });
                    return;
                }
                let control;
                try { control = JSON.parse(msg.data); } catch { return; }
                if (control.t === 'eof') {
                    const ok = got === size && hasher.hex() === String(meta.sha256 || '').toLowerCase();
                    channel.send(text({ t: 'ack', ok }));
                    if (!ok) { hooks.onDone?.({ ...info, ok: false }); return; }
                    const blob = new Blob(parts, { type: info.mime || 'application/octet-stream' });
                    parts.length = 0;
                    entry = { url: URL.createObjectURL(blob), filename: info.filename, size };
                } else if (control.t === 'bound' && entry && control.media_id) {
                    publish(control.media_id, entry);
                    if (control.transfer_id) socialApi.lanStatus(control.transfer_id, 'p2p_done').catch(() => {});
                    hooks.onDone?.({ ...info, ok: true, mediaId: control.media_id });
                    setTimeout(() => link.close(), 500);
                }
            });
        });
        try {
            await link.pc.setRemoteDescription(frame.payload.sdp);
            await link.flush();
            const answer = await link.pc.createAnswer();
            await link.pc.setLocalDescription(answer);
            client.send({ type: 'lan_answer', to: frame.from, transfer_id: transferId, payload: { sdp: link.pc.localDescription.toJSON() } });
        } catch {
            client.send({ type: 'lan_cancel', to: frame.from, transfer_id: transferId, payload: { reason: 'answer' } });
            link.close();
        }
    });
}
