import { apiFetch } from './api.js';

export const FEEDBACK_TYPES = { bug: 'Bug 修复', feature: '功能建议', report: '举报' };
export function feedbackStatus(item) {
    return item.status_label || ({ pending: '待处理', viewed: '已查看', processing: '沟通中', closed: '已关闭', resolved: '已关闭' }[item.status] || '待处理');
}
export function feedbackTime(value) {
    if (!value) return '';
    // SQLite's original CURRENT_TIMESTAMP values are UTC without a suffix;
    // keep explicit offsets and newer local ISO timestamps unchanged.
    const raw = String(value);
    const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(raw)
        ? `${raw.replace(' ', 'T')}Z` : raw;
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
export function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text != null) el.textContent = String(text);
    return el;
}
export function button(text, handler, className = 'fb-thread-button') {
    const el = node('button', className, text);
    el.type = 'button';
    el.addEventListener('click', handler);
    return el;
}
const request = (url, options = {}) => apiFetch(url, { ...options, silent: true });
const requestId = () => globalThis.crypto?.randomUUID?.() || `feedback-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const drafts = new Map();

/** Shared, access-controlled feedback conversation for both the owner and super admins. */
export class FeedbackConversation {
    constructor(container, feedbackId, { onChange, onWithdraw } = {}) {
        this.container = container;
        this.feedbackId = Number(feedbackId);
        this.onChange = onChange;
        this.onWithdraw = onWithdraw;
        this.messages = new Map();
        this.readThrough = -1;
        this.root = node('section', 'fb-thread');
        this.root.setAttribute('aria-label', '反馈对话');
        this.notice = node('p', 'fb-thread-notice', '正在加载对话…');
        this.noticeKind = 'load';
        this.notice.setAttribute('role', 'status');
        this.root.append(this.notice);
        container.replaceChildren(this.root);
        this.load();
        this.timer = window.setInterval(() => {
            if (!document.hidden && this.root.getClientRects().length && !this.busy && !this.loading) this.load({ quiet: true });
        }, 30000);
    }

    load({ quiet = false } = {}) {
        if (this.destroyed) return Promise.resolve();
        if (this.loadingPromise) {
            this.queuedQuiet = this.queuedLoad ? this.queuedQuiet && quiet : quiet;
            if (!this.queuedLoad) {
                this.queuedLoad = this.loadingPromise.then(() => {
                    this.queuedLoad = null;
                    return this.load({ quiet: this.queuedQuiet });
                });
            }
            return this.queuedLoad;
        }
        this.loading = true;
        this.loadingPromise = this.refreshDetail({ quiet }).finally(() => {
            this.loading = false;
            this.loadingPromise = null;
        });
        return this.loadingPromise;
    }

    async refreshDetail({ quiet }) {
        try {
            const data = await request(`/api/feedback/${this.feedbackId}/detail`);
            if (this.destroyed) return;
            this.data = data;
            this.feedback = data.feedback;
            const oldLastId = Math.max(0, ...this.messages.keys());
            const incoming = data.messages || [];
            const newFirstId = incoming.length ? Math.min(...incoming.map((message) => message.id)) : 0;
            if (this.initialized && data.has_more && newFirstId > oldLastId) {
                this.hasMore = true;
                this.beforeId = data.next_before_id;
            }
            for (const message of incoming) this.messages.set(message.id, message);
            if (!this.initialized) {
                this.hasMore = Boolean(data.has_more);
                this.beforeId = data.next_before_id;
                this.build();
            }
            this.update();
            this.onChange?.(this.feedback);
            if (!quiet && this.noticeKind === 'load') this.showNotice('');
            await this.markRead();
        } catch (error) {
            if (!quiet || !this.initialized) this.showNotice(`加载失败：${error.message}`, () => this.load(), 'load');
        }
    }

    build() {
        this.initialized = true;
        this.root.replaceChildren();
        const toolbar = node('div', 'fb-thread-toolbar');
        this.state = node('span', 'fb-thread-status');
        toolbar.append(this.state, button('刷新对话', () => this.load()));
        this.root.append(toolbar);

        const original = node('div', 'fb-thread-original');
        original.append(node('div', 'fb-thread-message-meta', `最初反馈 · ${feedbackTime(this.feedback.created_at)}`), node('p', 'fb-thread-text', this.feedback.description));
        if (this.feedback.section) original.append(node('p', 'fb-thread-context', `所在板块：${this.feedback.section}`));
        if (this.feedback.page_url) original.append(node('p', 'fb-thread-context', `反馈页面：${this.feedback.page_url}`));
        const attachments = node('div', 'fb-thread-attachments');
        for (const attachment of this.data.attachments || []) {
            const link = node('a');
            link.href = `/api/feedback/${this.feedbackId}/attachment/${encodeURIComponent(attachment.file_hash)}`;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            const img = node('img');
            img.src = link.href;
            img.alt = attachment.original_filename || '反馈截图';
            img.loading = 'lazy';
            link.append(img);
            attachments.append(link);
        }
        original.append(attachments);
        this.root.append(original);
        this.older = button('查看更早的消息', () => this.loadOlder());
        this.root.append(this.older);
        this.history = node('div', 'fb-thread-history');
        this.history.setAttribute('role', 'log');
        this.history.setAttribute('aria-label', '回复记录');
        this.root.append(this.history);
        this.notice = node('p', 'fb-thread-notice');
        this.notice.setAttribute('role', 'status');
        this.root.append(this.notice);

        this.form = node('form', 'fb-thread-composer');
        const label = node('label', '', '回复内容');
        this.input = node('textarea');
        this.input.id = `fb-thread-input-${this.feedbackId}-${requestId()}`;
        label.htmlFor = this.input.id;
        this.input.rows = 3;
        this.input.maxLength = 5000;
        this.input.placeholder = '补充问题详情，或回复对方的消息…';
        this.input.required = true;
        const savedDraft = drafts.get(this.feedbackId);
        this.input.value = savedDraft?.content || '';
        this.pendingReply = savedDraft?.pendingReply || null;
        this.input.addEventListener('input', () => {
            this.updateCounter();
            if (this.pendingReply && this.pendingReply.content !== this.input.value.trim()) this.pendingReply = null;
        });
        const actions = node('div', 'fb-thread-compose-actions');
        this.counter = node('span', 'fb-thread-context', '0 / 5000');
        this.send = node('button', 'fb-thread-button fb-thread-button--primary', '发送回复');
        this.send.type = 'submit';
        actions.append(this.counter, this.send);
        this.form.append(label, this.input, actions);
        this.updateCounter();
        this.form.addEventListener('submit', (event) => { event.preventDefault(); this.sendReply(); });
        this.root.append(this.form);

        this.closedHint = node('p', 'fb-thread-closed', '此反馈已由超管关闭，历史记录仍可查看。如需继续沟通，可由超管重新开启。');
        this.root.append(this.closedHint);
        this.adminActions = node('div', 'fb-thread-admin-actions');
        this.statusButton = button('关闭反馈', () => this.showStatusConfirmation());
        this.adminActions.append(node('span', 'fb-thread-context', '处理完成后由超管关闭反馈。'), this.statusButton);
        this.root.append(this.adminActions);
        this.confirmation = node('div', 'fb-thread-confirm');
        this.confirmation.hidden = true;
        this.root.append(this.confirmation);
        this.withdraw = button('撤回此反馈', () => this.onWithdraw?.(this.feedbackId), 'fb-withdraw-btn');
        this.root.append(this.withdraw);
    }

    update() {
        const closed = ['closed', 'resolved'].includes(this.feedback.status);
        this.state.textContent = feedbackStatus(this.feedback);
        this.state.classList.toggle('is-closed', closed);
        this.form.hidden = !this.data.can_reply;
        this.closedHint.hidden = !closed;
        this.adminActions.hidden = !this.data.can_manage;
        this.statusButton.textContent = closed ? '重新开启' : '关闭反馈';
        this.withdraw.hidden = !(this.data.can_withdraw && this.onWithdraw);
        this.older.hidden = !this.hasMore;
        this.setBusy(Boolean(this.busy));
        this.renderMessages();
    }

    renderMessages() {
        const messages = [...this.messages.values()].sort((a, b) => a.id - b.id);
        // Avoid replacing the live log on unchanged polling responses.
        const signature = messages.map((item) => `${item.id}:${item.event_type}`).join(',');
        if (signature === this.messageSignature) return;
        this.messageSignature = signature;
        const nearBottom = this.history.scrollHeight - this.history.scrollTop - this.history.clientHeight < 80;
        this.history.replaceChildren();
        if (!messages.length) this.history.append(node('p', 'fb-thread-context fb-thread-start', '尚无回复。您可以在这里与超管继续沟通。'));
        for (const message of messages) {
            const isEvent = message.event_type && message.event_type !== 'reply';
            const row = node('article', isEvent ? 'fb-thread-event' : `fb-thread-message${message.sender_is_super_admin ? ' is-admin' : ''}`);
            row.append(node('div', 'fb-thread-message-meta', `${message.sender_name || '用户'}${message.sender_is_super_admin ? ' · 超管' : ''} · ${feedbackTime(message.created_at)}`));
            if (isEvent) row.append(node('strong', '', message.event_type === 'closed' ? '已关闭反馈' : '已重新开启反馈'));
            if (message.content) row.append(node('p', 'fb-thread-text', message.content));
            this.history.append(row);
        }
        if (nearBottom && !this.loadingOlder) this.history.scrollTop = this.history.scrollHeight;
    }

    async markRead() {
        const lastId = Number(this.feedback.last_message_id || 0);
        if (lastId <= this.readThrough || !this.root.getClientRects().length || document.hidden) return;
        try {
            await request(`/api/feedback/${this.feedbackId}/read`, { method: 'POST', body: { last_message_id: lastId } });
            this.readThrough = lastId;
            this.onChange?.({ ...this.feedback, unread_count: 0 });
        } catch { /* Reading remains available if the read-receipt request fails. */ }
    }

    async loadOlder() {
        if (this.loadingOlder || !this.hasMore) return;
        this.loadingOlder = true;
        this.older.disabled = true;
        try {
            const data = await request(`/api/feedback/${this.feedbackId}/messages?before_id=${encodeURIComponent(this.beforeId)}&limit=50`);
            if (this.destroyed) return;
            for (const message of data.messages || data.items || []) this.messages.set(message.id, message);
            this.hasMore = Boolean(data.has_more);
            this.beforeId = data.next_before_id;
            this.update();
        } catch (error) { this.showNotice(`历史消息加载失败：${error.message}`, () => this.loadOlder()); }
        finally { this.loadingOlder = false; this.older.disabled = false; }
    }

    showNotice(text, retry, kind = 'operation') {
        if (this.destroyed) return;
        this.noticeKind = text ? kind : null;
        this.notice.replaceChildren(node('span', '', text));
        this.notice.hidden = !text;
        if (retry) this.notice.append(button('重试', retry));
    }

    setBusy(busy) {
        this.busy = busy;
        this.input.disabled = busy || !this.data.can_reply;
        this.send.disabled = busy || !this.data.can_reply;
        this.statusButton.disabled = busy;
        this.withdraw.disabled = busy;
        this.send.textContent = busy ? '请稍候…' : '发送回复';
        this.confirmation.querySelectorAll('button, textarea').forEach((el) => { el.disabled = busy; });
    }

    updateCounter() { this.counter.textContent = `${this.input.value.length} / 5000`; }

    async sendReply() {
        if (this.busy || !this.data.can_reply) return;
        const content = this.input.value.trim();
        if (!content) { this.input.focus(); return; }
        if (!this.pendingReply || this.pendingReply.content !== content) this.pendingReply = { content, client_message_id: requestId() };
        this.setBusy(true);
        this.showNotice('');
        let failure = null;
        try {
            await request(`/api/feedback/${this.feedbackId}/messages`, { method: 'POST', body: this.pendingReply });
            this.pendingReply = null;
            this.input.value = '';
            drafts.delete(this.feedbackId);
            this.updateCounter();
        } catch (error) { failure = error; }
        await this.load();
        this.setBusy(false);
        if (failure) this.showNotice(`发送失败：${failure.message}。输入内容已保留。`, () => this.sendReply());
        else this.showNotice('回复已发送。');
    }

    showStatusConfirmation() {
        if (this.busy || !this.data.can_manage) return;
        const closing = !['closed', 'resolved'].includes(this.feedback.status);
        this.confirmation.replaceChildren(node('strong', '', closing ? '确认关闭这条反馈？' : '重新开启这条反馈？'), node('p', '', closing ? '关闭后双方将无法继续回复，历史消息和截图会保留。超管可随时重新开启。' : '开启后双方可以继续回复，提交人会收到通知。'));
        const reason = node('textarea');
        reason.rows = 2;
        reason.maxLength = 5000;
        reason.placeholder = closing ? '处理结果或关闭说明（选填）' : '重新开启说明（选填）';
        reason.setAttribute('aria-label', reason.placeholder);
        const action = {
            status: closing ? 'closed' : 'open', expected_status: this.feedback.status,
            expected_last_message_id: Number(this.feedback.last_message_id || 0), client_message_id: requestId(),
        };
        const controls = node('div', 'fb-thread-compose-actions');
        controls.append(button('取消', () => { this.confirmation.hidden = true; }), button(closing ? '确认关闭' : '确认开启', () => this.changeStatus(action, reason.value.trim()), 'fb-thread-button fb-thread-button--primary'));
        this.confirmation.append(reason, controls);
        this.confirmation.hidden = false;
        reason.focus();
    }

    async changeStatus(action, content) {
        if (this.busy) return;
        this.setBusy(true);
        let failure = null;
        try {
            await request(`/api/feedback/${this.feedbackId}/status`, { method: 'POST', body: { ...action, content } });
            this.confirmation.hidden = true;
        } catch (error) { failure = error; }
        await this.load();
        this.setBusy(false);
        if (failure) {
            if (failure.status === 409) this.confirmation.hidden = true;
            this.showNotice(failure.status === 409 ? '对话已更新，请查看最新消息，再决定是否关闭或开启。' : `操作失败：${failure.message}，可重试。`);
        }
    }

    destroy() {
        if (this.input?.value) drafts.set(this.feedbackId, { content: this.input.value, pendingReply: this.pendingReply });
        else drafts.delete(this.feedbackId);
        this.destroyed = true;
        window.clearInterval(this.timer);
    }
}
