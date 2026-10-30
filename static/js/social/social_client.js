/**
 * 好友/群聊全局客户端 (docs/social-friends-groups-2026-10-27.md §7–§9).
 *
 * One per document (Symbol-keyed singleton, safe across hashed asset URLs):
 *   - a single /ws/social socket: 25 s ping, exponential reconnect with jitter,
 *     no reconnect after a 1008 auth refusal; REST keeps working while it is down;
 *   - an event bus (`on(type, fn)`) re-emitting every server frame plus
 *     'status' / 'counts' / 'presence-changed' / 'presence-settled';
 *   - the unread badge on every `[data-social-entry]` (messages + incoming requests);
 *   - the presence cache seeded by `hello` and updated by `presence`;
 *   - title flashing while the tab is hidden and a message arrives.
 * The modals are imported lazily on the first click of a 好友 entry; the
 * 个人信息面板 (user_card.js) on the first click of a real-name avatar/name.
 */
import { socialApi } from './social_api.js';
import { bindUserCardTriggers } from './user_card_trigger.js';

const KEY = Symbol.for('lanshare.social.client.v1');
const PING_MS = 25_000;
const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 30_000;
const COUNT_DEBOUNCE_MS = 600;
// Socket chatter (a busy group) must never turn into one request per message:
// the authoritative totals are re-read at most this often.
const COUNT_CHATTER_MS = 4_000;
const FLASH_MS = 1_200;
const AUTH_CLOSE_CODE = 1008;
// The server closes the oldest socket when an account opens more than 5 tabs;
// that tab must stay quiet instead of reconnecting and evicting another one.
const TOO_MANY_SOCKETS_CODE = 4008;

class SocialClient {
    constructor(doc) {
        this.doc = doc;
        const entry = doc.querySelector('[data-social-entry]');
        this.me = entry?.dataset.socialMe || '';
        this.listeners = new Map();
        this.presence = new Map();
        this.presenceFrame = 0;     // coalesced presence repaint (one per frame)
        this.unreadMessages = 0;
        this.pendingIn = 0;
        this.mutedKeys = new Set();
        this.activeKey = null;      // conversation visible in the chat window
        this.ws = null;
        this.status = 'idle';
        this.retryDelay = RETRY_MIN_MS;
        this.retryTimer = 0;
        this.pingTimer = 0;
        this.countTimer = 0;
        this.countsAt = 0;          // when the totals were last read (throttle floor)
        this.bellTimer = 0;
        this.flashTimer = 0;
        this.baseTitle = doc.title;
        this.stopped = false;
        this.ui = null;
        this.uiPromise = null;
        this.seenMessages = new Set();
        this.bindEntries();
        doc.addEventListener('visibilitychange', () => this.onVisibility());
        window.addEventListener('online', () => this.reconnectSoon(0));
        this.connect();
        this.refreshCounts();
        const linkedKey = new URL(window.location.href).searchParams.get('social_conversation');
        if (linkedKey && /^(?:group:[1-9]\d*|dm:(?:student|teacher):[1-9]\d*\|(?:student|teacher):[1-9]\d*)$/.test(linkedKey)) void this.openChat(linkedKey);
    }

    /* ---------------------------------------------------------------- bus */
    on(type, fn) {
        if (!this.listeners.has(type)) this.listeners.set(type, new Set());
        this.listeners.get(type).add(fn);
        return () => this.listeners.get(type)?.delete(fn);
    }

    emit(type, data) {
        for (const fn of [...(this.listeners.get(type) || [])]) {
            try { fn(data); } catch (error) { console.error(`[social] listener for ${type} failed`, error); }
        }
    }

    /* ---------------------------------------------------------------- socket */
    connect() {
        if (this.stopped || this.ws) return;
        const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
        let socket;
        try {
            socket = new WebSocket(`${scheme}://${window.location.host}/ws/social`);
        } catch {
            this.setStatus('offline');
            this.reconnectSoon();
            return;
        }
        this.ws = socket;
        this.setStatus('connecting');
        socket.addEventListener('open', () => {
            this.retryDelay = RETRY_MIN_MS;
            this.setStatus('online');
            window.clearInterval(this.pingTimer);
            this.pingTimer = window.setInterval(() => this.send({ type: 'ping' }), PING_MS);
            if (this.activeKey) this.send({ type: 'focus', conversation_key: this.activeKey });
        });
        socket.addEventListener('message', (event) => this.onFrame(event.data));
        socket.addEventListener('close', (event) => {
            window.clearInterval(this.pingTimer);
            if (this.ws === socket) this.ws = null;
            this.setStatus('offline');
            if (event.code === AUTH_CLOSE_CODE || event.code === TOO_MANY_SOCKETS_CODE) { this.stopped = true; return; }
            this.reconnectSoon();
        });
        socket.addEventListener('error', () => { /* close follows; handled there */ });
    }

    reconnectSoon(delay = null) {
        if (this.stopped || this.ws) return;
        window.clearTimeout(this.retryTimer);
        const wait = delay ?? Math.round(this.retryDelay * (0.75 + Math.random() * 0.5));
        this.retryDelay = Math.min(RETRY_MAX_MS, this.retryDelay * 2);
        this.retryTimer = window.setTimeout(() => this.connect(), wait);
    }

    setStatus(status) {
        if (this.status === status) return;
        this.status = status;
        this.emit('status', status);
    }

    /**
     * A busy platform flips many friends at once. Consumers repaint whole
     * lists, so presence bursts settle into at most one paint per frame; a
     * hidden tab pays nothing until it is visible again.
     */
    queuePresencePaint() {
        if (this.presenceFrame) return;
        const paint = () => {
            this.presenceFrame = 0;
            this.emit('presence-settled', null);
        };
        this.presenceFrame = typeof window.requestAnimationFrame === 'function'
            ? window.requestAnimationFrame(paint)
            : window.setTimeout(paint, 16);
    }

    /** @returns {boolean} whether the frame was handed to an open socket */
    send(frame) {
        if (this.ws?.readyState !== WebSocket.OPEN) return false;
        try { this.ws.send(JSON.stringify(frame)); return true; } catch { return false; }
    }

    focus(key) {
        this.activeKey = key || null;
        this.send({ type: 'focus', conversation_key: this.activeKey });
    }

    onFrame(raw) {
        let frame;
        try { frame = JSON.parse(raw); } catch { return; }
        if (!frame || typeof frame !== 'object') return;
        switch (frame.type) {
        case 'pong': return;
        case 'hello':
            this.me = frame.me || this.me;
            // hello is the authoritative friend snapshot for this connection;
            // dropping stale entries is what clears rings after a reconnect.
            this.presence.clear();
            for (const identity of frame.online_friends || []) this.presence.set(identity, { online: true, last_seen_at: null });
            this.unreadMessages = Number(frame.unread_total) || 0;
            this.paintBadge();
            this.refreshCounts();
            this.emit('presence-changed', null);
            this.queuePresencePaint();
            break;
        case 'presence':
            this.presence.set(frame.identity, { online: Boolean(frame.online), last_seen_at: frame.last_seen_at || null });
            this.emit('presence-changed', frame.identity);
            this.queuePresencePaint();
            break;
        case 'message': this.onMessage(frame.message); break;
        case 'conversation_preferences':
            this.setMuted(frame.conversation_key, frame.muted);
            this.refreshCounts();
            break;
        // Someone reading my messages leaves my own unread untouched; a recall can
        // withdraw a card, so it still re-reads the totals.
        case 'read': break;
        case 'message_recalled':
            this.refreshCounts(COUNT_CHATTER_MS);
            this.refreshBellSoon();
            break;
        case 'friend_request': case 'friend_accepted': case 'friend_removed':
            this.refreshCounts();
            break;
        // Broadcast to every member on each membership edit, so a burst has to
        // collapse into one re-read.
        case 'group_changed':
            this.refreshCounts(COUNT_CHATTER_MS);
            break;
        default: break;
        }
        this.emit(frame.type, frame);
    }

    onMessage(message) {
        if (!message) return;
        if (typeof message.muted === 'boolean') this.setMuted(message.conversation_key, message.muted);
        const incoming = message.sender?.identity && message.sender.identity !== this.me
            && message.message_type !== 'system';
        const viewing = this.activeKey === message.conversation_key && !this.doc.hidden;
        // A backfill re-push repeats an old LAN message with server_ready now true.
        const backfilled = (message.media || []).some((item) => item.delivered_via === 'lan' && item.server_ready);
        const identity = `${message.conversation_key}:${message.id}`;
        if (this.seenMessages.has(identity)) return;
        this.seenMessages.add(identity);
        if (this.seenMessages.size > 500) this.seenMessages.delete(this.seenMessages.values().next().value);
        // No totals refresh here: the increment below is the same arithmetic the
        // server would return, and re-reading a 200-conversation list once per
        // received message is what made big groups crawl
        // (docs/social-group-performance-2026-10-30.md §3 R2). The socket's
        // `hello`, a visible tab, a read or `group_changed` re-sync it.
        this.refreshBellSoon();
        if (!incoming || viewing || message.recalled || backfilled) return;
        this.unreadMessages += 1;
        this.paintBadge();
        if (!this.mutedKeys.has(message.conversation_key)) {
            if (this.doc.hidden) this.startFlash(message.sender?.name || '新消息');
            else void this.toastIncoming(message);
        }
    }

    setMuted(key, muted) {
        if (!key) return;
        if (muted) this.mutedKeys.add(key); else this.mutedKeys.delete(key);
    }

    refreshBellSoon() {
        if (this.bellTimer) return;
        this.bellTimer = window.setTimeout(() => {
            this.bellTimer = 0;
            if (typeof window.refreshMessageCenterBell === 'function') void Promise.resolve(window.refreshMessageCenterBell({ allowPopup: false })).catch(() => {});
        }, 900);
    }

    /* ---------------------------------------------------------------- counts */
    /**
     * Re-read the authoritative unread totals.
     * @param {number} [minInterval] floor in ms since the previous read, so a burst
     *   of socket frames coalesces into one request. The default reproduces the
     *   original 600 ms debounce for actions that need the badge to settle now
     *   (opening the panel, marking read, adding a friend).
     */
    refreshCounts(minInterval = COUNT_DEBOUNCE_MS) {
        window.clearTimeout(this.countTimer);
        // Anchored on the last read rather than on the last call, so repeated
        // frames converge on one deadline instead of pushing it back forever.
        const wait = Math.max(COUNT_DEBOUNCE_MS, minInterval - (Date.now() - this.countsAt));
        this.countTimer = window.setTimeout(async () => {
            this.countsAt = Date.now();
            try {
                const [conversations, requests] = await Promise.all([socialApi.conversations(), socialApi.requests()]);
                const items = conversations?.items || [];
                this.mutedKeys = new Set(items.filter((item) => item.muted).map((item) => item.conversation_key));
                this.unreadMessages = items.reduce((sum, item) => sum + (Number(item.unread) || 0), 0);
                this.pendingIn = (requests?.incoming || []).length;
                this.paintBadge();
                this.emit('counts', { unread: this.unreadMessages, pendingIn: this.pendingIn, conversations: items });
            } catch { /* badge keeps its last value; the socket or next action retries */ }
        }, wait);
    }

    /**
     * The open conversation was just read.
     *
     * Drop its share from the badge straight away, then let one throttled
     * re-read confirm the total: re-reading every conversation after each read
     * is what made a busy group cost two requests per message
     * (docs/social-group-performance-2026-10-30.md §3 R2).
     * @param {number} cleared the unread count the conversation held
     */
    settleRead(cleared) {
        const count = Math.max(0, Number(cleared) || 0);
        if (count) {
            this.unreadMessages = Math.max(0, this.unreadMessages - count);
            this.paintBadge();
        }
        this.refreshCounts(COUNT_CHATTER_MS);
    }

    paintBadge() {
        const total = Math.max(0, this.unreadMessages + this.pendingIn);
        const label = total > 99 ? '99+' : String(total);
        for (const entry of this.doc.querySelectorAll('[data-social-entry]')) {
            const badge = entry.querySelector('[data-social-unread]');
            if (badge) { badge.textContent = label; badge.hidden = total === 0; }
            const base = entry.dataset.socialLabel || '好友';
            entry.setAttribute('aria-label', total ? `打开${base}（${label} 条未读）` : `打开${base}`);
        }
    }

    isOnline(identity) {
        return Boolean(this.presence.get(identity)?.online);
    }

    /* ---------------------------------------------------------------- attention */
    startFlash(name) {
        if (this.flashTimer || !this.doc.hidden) return;
        this.baseTitle = this.doc.title;
        let on = false;
        this.flashTimer = window.setInterval(() => {
            on = !on;
            this.doc.title = on ? `【新消息】${name}` : this.baseTitle;
        }, FLASH_MS);
    }

    stopFlash() {
        if (!this.flashTimer) return;
        window.clearInterval(this.flashTimer);
        this.flashTimer = 0;
        this.doc.title = this.baseTitle;
    }

    onVisibility() {
        if (this.doc.hidden) return;
        this.stopFlash();
        this.refreshCounts();
        if (!this.ws) this.reconnectSoon(0);
        this.emit('visible', null);
    }

    async toastIncoming(message) {
        const { notify } = await import('./social_dom.js');
        if (this.mutedKeys.has(message.conversation_key) || this.doc.hidden || this.activeKey === message.conversation_key) return;
        const preview = message.body ? String(message.body).replace(/\s+/g, ' ').slice(0, 40) : '[附件]';
        notify(`${message.sender?.name || '好友'}：${preview}`, 'info', {
            key: `social:${message.conversation_key}`,
            duration: 6000,
            action: { label: '查看', onClick: () => this.openChat(message.conversation_key) },
        });
    }

    /* ---------------------------------------------------------------- UI */
    bindEntries() {
        this.doc.addEventListener('click', (event) => {
            const entry = event.target.closest?.('[data-social-entry]');
            if (!entry) return;
            event.preventDefault();
            void this.openChat(null, entry);
        });
        this.paintBadge();
    }

    async loadUi() {
        if (!this.uiPromise) this.uiPromise = import('./social_ui.js').then((module) => module.createSocialUi(this))
            .then((ui) => { this.ui = ui; return ui; }).catch((error) => { this.uiPromise = null; throw error; });
        return this.uiPromise;
    }

    async openFriends(trigger = null) {
        try { (await this.loadUi()).openFriends(trigger); } catch (error) {
            console.error('[social] friends UI failed to load', error);
        }
    }

    async openChat(key, trigger = null) {
        try { await (await this.loadUi()).openChat(key, trigger); } catch (error) {
            console.error('[social] chat UI failed to load', error);
        }
    }
}

/** @returns {SocialClient|null} */
export function getSocialClient(doc = document) {
    if (!doc[KEY] && doc.querySelector('[data-social-entry]')) doc[KEY] = new SocialClient(doc);
    return doc[KEY] || null;
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => getSocialClient(), { once: true });
else getSocialClient();
bindUserCardTriggers(document, () => getSocialClient());
