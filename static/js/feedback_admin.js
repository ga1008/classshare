import { apiFetch } from './api.js';
import { FeedbackConversation, FEEDBACK_TYPES, feedbackStatus, feedbackTime, node, button } from './feedback_conversation.js';

class FeedbackAdmin {
    constructor(root) {
        this.root = root;
        this.list = root.querySelector('[data-feedback-list]');
        this.count = root.querySelector('[data-feedback-count]');
        this.notice = root.querySelector('[data-feedback-notice]');
        this.more = root.querySelector('[data-feedback-more]');
        this.conversations = new Map();
        this.cards = new Map();
        const query = new URLSearchParams(location.search);
        this.filter = ['all', 'open', 'closed'].includes(query.get('status')) ? query.get('status') : 'all';
        this.filters = [...root.querySelectorAll('[data-feedback-filter]')];
        this.filters.forEach((el) => el.addEventListener('click', () => this.changeFilter(el.dataset.feedbackFilter)));
        root.querySelector('[data-feedback-refresh]').addEventListener('click', () => this.load());
        this.more.addEventListener('click', () => this.load({ append: true }));
        this.updateFilters();
        const initial = document.getElementById('feedback-initial-page');
        if (initial) {
            try { this.renderPage(JSON.parse(initial.textContent)); }
            catch { this.load(); }
        } else this.load();
        const feedbackId = Number(query.get('feedback_id'));
        if (Number.isSafeInteger(feedbackId) && feedbackId > 0) this.openLinked(feedbackId);
    }

    updateFilters() {
        this.filters.forEach((el) => {
            el.classList.toggle('is-active', el.dataset.feedbackFilter === this.filter);
            el.setAttribute('aria-pressed', String(el.dataset.feedbackFilter === this.filter));
        });
    }

    async changeFilter(filter) {
        if (this.loading || this.filter === filter) return;
        this.filter = filter;
        this.updateFilters();
        const url = new URL(location.href);
        url.searchParams.set('status', filter);
        url.searchParams.delete('feedback_id');
        history.replaceState(null, '', url);
        await this.load();
    }

    async load({ append = false } = {}) {
        if (this.loading) return;
        this.loading = true;
        this.notice.hidden = true;
        this.more.disabled = true;
        this.root.querySelectorAll('[data-feedback-filter], [data-feedback-refresh]').forEach((el) => { el.disabled = true; });
        try {
            const params = new URLSearchParams({ status: this.filter, limit: '40' });
            if (append && this.nextBeforeId) params.set('before_id', this.nextBeforeId);
            const data = await apiFetch(`/api/feedback/admin?${params}`, { silent: true });
            this.renderPage(data, append);
        } catch (error) {
            this.notice.textContent = `反馈加载失败：${error.message}。请点击“刷新列表”重试。`;
            this.notice.hidden = false;
        } finally {
            this.loading = false;
            this.more.disabled = false;
            this.root.querySelectorAll('[data-feedback-filter], [data-feedback-refresh]').forEach((el) => { el.disabled = false; });
        }
    }

    renderPage(data, append = false) {
        if (!append) {
            this.conversations.forEach((conversation) => conversation.destroy());
            this.conversations.clear();
            this.cards.clear();
            this.list.replaceChildren();
        }
        for (const item of data.items || []) if (!this.cards.has(item.id)) this.addCard(item);
        this.hasMore = Boolean(data.has_more);
        this.nextBeforeId = data.next_before_id;
        this.more.hidden = !this.hasMore;
        this.count.textContent = this.cards.size ? `已加载 ${this.cards.size} 条反馈${this.hasMore ? '，可继续加载' : ''}` : '暂无符合条件的反馈';
        if (!this.cards.size) this.list.append(node('div', 'fb-empty-state', this.filter === 'closed' ? '处理完成的反馈会保留在这里。' : '用户提交反馈后，可以在这里回复并跟进处理。'));
    }

    addCard(item, prepend = false) {
        this.list.querySelector('.fb-empty-state')?.remove();
        const card = node('article', 'fb-card');
        card.dataset.feedbackCard = item.id;
        const header = node('div', 'fb-card__header');
        const title = node('div', 'fb-card__title');
        const badges = node('div', 'fb-card__badges');
        badges.append(node('span', 'badge badge-secondary', FEEDBACK_TYPES[item.feedback_type] || '问题反馈'));
        const status = node('span', 'fb-thread-status', feedbackStatus(item));
        const unread = node('span', 'fb-thread-unread');
        badges.append(status, unread);
        title.append(badges, node('h3', '', item.title));
        header.append(title, node('span', 'fb-card__meta', feedbackTime(item.created_at)));
        const source = node('div', 'fb-card__source', `${item.user_name || '未知用户'} · ${item.user_role === 'teacher' ? '教师' : '学生'}${item.section ? ` · ${item.section}` : ''}`);
        const preview = node('p', 'fb-admin-preview', item.description);
        const footer = node('div', 'fb-admin-footer');
        const summary = node('span', 'fb-thread-context');
        const toggle = button('查看对话并回复', () => this.toggle(item.id), 'fb-thread-button fb-thread-button--primary');
        toggle.dataset.feedbackOpen = item.id;
        toggle.setAttribute('aria-expanded', 'false');
        const body = node('div');
        body.hidden = true;
        body.id = `fb-admin-conversation-${item.id}`;
        toggle.setAttribute('aria-controls', body.id);
        footer.append(summary, toggle);
        card.append(header, source, preview, footer, body);
        this.cards.set(item.id, { card, body, toggle, status, unread, summary, preview, item });
        this.updateCard(item);
        prepend ? this.list.prepend(card) : this.list.append(card);
    }

    updateCard(item) {
        const target = this.cards.get(item.id);
        if (!target) return;
        target.item = { ...target.item, ...item };
        target.status.textContent = feedbackStatus(item);
        target.status.classList.toggle('is-closed', item.status === 'closed');
        target.unread.textContent = `${item.unread_count || 0} 条未读`;
        target.unread.hidden = !item.unread_count;
        target.summary.textContent = `${item.reply_count ?? item.message_count ?? 0} 条回复${item.attachment_count ? ` · ${item.attachment_count} 张截图` : ''}`;
    }

    toggle(id, forceOpen = false) {
        const target = this.cards.get(id);
        if (!target) return;
        const opening = forceOpen || target.body.hidden;
        target.body.hidden = !opening;
        target.preview.hidden = opening;
        target.toggle.textContent = opening ? '收起对话' : '查看对话并回复';
        target.toggle.setAttribute('aria-expanded', String(opening));
        if (opening) {
            if (!this.conversations.has(id)) this.conversations.set(id, new FeedbackConversation(target.body, id, { onChange: (item) => this.updateCard(item) }));
            else this.conversations.get(id).load();
        }
    }

    async openLinked(id) {
        if (!this.cards.has(id)) {
            try {
                const data = await apiFetch(`/api/feedback/${id}/detail`, { silent: true });
                this.addCard(data.feedback, true);
            } catch (error) {
                this.notice.textContent = `无法打开此反馈：${error.message}`;
                this.notice.hidden = false;
                return;
            }
        }
        this.toggle(id, true);
        this.cards.get(id).card.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }
}

const root = document.querySelector('[data-feedback-admin]');
if (root) new FeedbackAdmin(root);
