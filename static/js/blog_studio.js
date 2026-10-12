/**
 * Creator studio and author pages (docs/blog-closed-loop-plan-2026-10-12.md §9.4, P6).
 *
 * Mounted by blog.js into [data-blog-studio-root] / [data-blog-author-root].
 * Everything is built from LQ components (Segment, Insight, List/Row, Chip,
 * Alert, Empty, Avatar, Button) through their DOM factories, so user text only
 * ever becomes text nodes. Layout hooks (blog-studio__*, blog-author-page__*)
 * live in blog-paper.css.
 */
import { escapeHtml, showToast } from '/static/js/ui.js';
import { getLQ } from '/static/js/lq/index.js';
import { appealDialog } from '/static/js/blog_moderation_ui.js';

const LQ = getLQ();
const TABS = [
    { key: 'overview', label: '概览' },
    { key: 'works', label: '作品' },
    { key: 'drafts', label: '草稿箱' },
    { key: 'interactions', label: '互动' },
    { key: 'fans', label: '粉丝与关注' },
];
const TAB_KEYS = TABS.map((tab) => tab.key);
const WORK_FILTERS = [['all', '全部'], ['published', '已发布'], ['draft', '草稿'], ['attention', '需要关注']];
const WORK_SORTS = [['latest', '最新'], ['views', '阅读'], ['engagement', '互动']];
const FACE_LABELS = { real_name: '实名', nickname: '笔名', anonymous: '匿名' };
const TREND_BARS = 7;
const SANCTION_TITLES = { limited: '暂时仅你可见', removed: '已被移除，仅你可见', appeal_pending: '申诉中' };

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function shortDate(value) {
    const text = String(value || '');
    return text ? text.slice(0, 16).replace('T', ' ') : '';
}

function chipRow(options, current, onPick, label) {
    const row = el('div', 'blog-studio__chips');
    row.setAttribute('role', 'group');
    row.setAttribute('aria-label', label);
    for (const [value, text] of options) {
        const chip = LQ.chip({ label: text, kind: 'filter', size: 'sm', pressed: value === current });
        chip.addEventListener('click', () => onPick(value));
        row.append(chip);
    }
    return row;
}

/** Mounted once; tabs load lazily and reload on refresh(). */
export class BlogStudio {
    constructor(app, api, root) {
        this.app = app;
        this.api = api;
        this.root = root;
        this.panels = new Map();
        this.tab = 'overview';
        this.mode = '';
        this.workFilter = 'all';
        this.workSort = 'latest';
        this.box = 'received';
        this.loaded = new Set();
        this.tokens = new Map();
        this.building = null;
    }

    /** Entering the studio (again) always shows fresh data. */
    async open(tab) {
        const next = TAB_KEYS.includes(tab) ? tab : 'overview';
        if (!this.segment) {
            this.building = this.building || this.build(next);
            await this.building;
        }
        this.tab = next;
        this.loaded.clear();
        this.segment.select(next, { notify: false });
        await this.load(next);
    }

    refresh() {
        this.loaded.clear();
        if (this.segment) void this.load(this.tab);
    }

    async build(selected) {
        const [navigation, content, status] = await Promise.all([
            LQ.load('navigation'), LQ.load('content'), LQ.load('status'),
        ]);
        this.content = content;
        this.status = status;
        for (const tab of TABS) this.panels.set(tab.key, el('div', 'blog-studio__panel'));
        const segmentRoot = navigation.createSegment(
            { id: 'blog-studio-tabs', label: '创作中心', items: TABS, selected },
            document,
            this.panels,
        );
        this.root.replaceChildren(segmentRoot);
        this.segment = navigation.segment(segmentRoot, {
            onChange: ({ key }) => {
                this.tab = key;
                this.app.onStudioTab?.(key);
                void this.load(key);
            },
        });
    }

    async load(tab) {
        if (this.loaded.has(tab)) return;
        this.loaded.add(tab);
        // A newer load of the same tab wins; slower, older answers are dropped.
        const token = (this.tokens.get(tab) || 0) + 1;
        this.tokens.set(tab, token);
        const live = () => this.tokens.get(tab) === token;
        const panel = this.panels.get(tab);
        panel.replaceChildren(el('p', 'blog-studio__status', '正在加载…'));
        const renderers = {
            overview: () => this.renderOverview(panel, live),
            works: () => this.renderWorks(panel, live),
            drafts: () => this.renderDrafts(panel, live),
            interactions: () => this.renderInteractions(panel, live),
            fans: () => this.renderFans(panel, live),
        };
        try {
            await renderers[tab]();
        } catch (error) {
            if (!live()) return;
            this.loaded.delete(tab);
            panel.replaceChildren(this.content.createContent('empty', {
                reason: 'error', title: '加载失败', description: error.message || '请稍后重试', variant: 'card',
            }));
        }
    }

    reload(tab) {
        this.loaded.delete(tab);
        void this.load(tab);
    }

    alert(tone, title, body) {
        const node = el('div', 'blog-studio__alert');
        node.innerHTML = this.status.statusMarkup('alert', { tone, title, body: body || title });
        return node;
    }

    // --- 概览 ---------------------------------------------------------------------------

    async renderOverview(panel, live) {
        const insights = await LQ.load('insights');
        const query = this.mode ? `?mode=${encodeURIComponent(this.mode)}` : '';
        const data = await this.api.get(`/api/blog/studio/overview${query}`);
        if (!live()) return;
        const stats = data.stats || {};
        this.mode = stats.mode;
        const parts = [];
        if ((data.faces || []).length > 1) {
            parts.push(chipRow(data.faces.map((face) => [face.mode, `${face.label}身份`]), stats.mode, (mode) => {
                this.mode = mode;
                this.reload('overview');
            }, '按身份查看数据'));
        }
        // LQ Insight contract: null is "unknown"; percent draws the meter track.
        const meter = (title, value, unit = '', caption = '', percent = null) => insights.createInsight('insight_meter', {
            title, value: value ?? null, unit, caption, zero: 'value', percent,
        });
        const grid = el('div', 'blog-studio__metrics');
        grid.append(
            meter('累计阅读', stats.total_views),
            meter('近 7 天阅读', stats.views_7d),
            meter('完读率', stats.completion_rate, '%', stats.completion_rate === null ? '近 30 天还没有阅读' : '近 30 天',
                stats.completion_rate),
            meter('获赞', stats.likes),
            meter('评论', stats.comments),
            meter('粉丝', stats.followers, '', stats.followers === null ? '匿名身份不能被关注' : ''),
        );
        parts.push(grid, this.trend(insights, stats.trend || []), this.todo(data.todo || {}), this.profileForm(data));
        panel.replaceChildren(...parts);
    }

    trend(insights, series) {
        const box = el('section', 'blog-studio__trend');
        box.append(insights.createInsight('insight_bars', {
            title: `近 ${TREND_BARS} 天阅读`,
            limit: TREND_BARS,
            zero: 'value',
            items: series.slice(-TREND_BARS).map((day) => ({ label: day.date.slice(5), value: day.views })),
        }));
        // The full 30-day series as an accessible table (the bars are a summary).
        const table = el('table', 'sr-only');
        table.append(el('caption', '', '近 30 天每日阅读'));
        const body = el('tbody');
        for (const day of series) {
            const row = el('tr');
            row.append(el('th', '', day.date), el('td', '', String(day.views)));
            body.append(row);
        }
        table.append(body);
        box.append(table);
        return box;
    }

    todo(todo) {
        const items = [];
        if (todo.sanctioned) items.push({ title: `${todo.sanctioned} 篇作品被处理或在申诉中`, tab: 'works', filter: 'attention' });
        if (todo.reviewing) items.push({ title: `${todo.reviewing} 篇作品正在审核`, tab: 'works', filter: 'attention' });
        if (todo.unreplied_comments) items.push({ title: `${todo.unreplied_comments} 条评论还没回复`, tab: 'interactions' });
        const section = el('section', 'blog-studio__todo');
        section.append(el('h3', 'blog-studio__heading', '待处理'));
        if (!items.length) {
            section.append(this.content.createContent('empty', { title: '没有待处理的事', variant: 'inline' }));
            return section;
        }
        const rows = items.map((item) => {
            const row = this.content.createContent('row', {
                title: item.title,
                actions: [{ label: '去处理', variant: 'soft', attrs: { 'data-studio-go': item.tab } }],
            });
            row.querySelector('[data-studio-go]').addEventListener('click', () => {
                if (item.filter) this.workFilter = item.filter;
                if (item.tab === 'interactions') this.box = 'received';
                this.loaded.delete(item.tab);
                this.segment.select(item.tab);
            });
            return row;
        });
        section.append(this.content.createContent('list', { label: '待处理' }, { items: rows }));
        return section;
    }

    profileForm(data) {
        const section = el('section', 'blog-studio__profile');
        section.append(el('h3', 'blog-studio__heading', '作者简介'));
        section.append(el('p', 'blog-studio__hint', '显示在你的作者主页上，最多 60 字。笔名简介只在笔名主页出现。'));
        const form = el('form', 'blog-studio__profile-form');
        const field = (name, label, value) => {
            const wrap = el('label', 'lq-field');
            wrap.append(el('span', 'lq-field__label', label));
            const input = el('textarea', 'lq-textarea');
            Object.assign(input, { name, rows: 2, maxLength: 60, value: value || '' });
            input.dataset.lqComponent = 'textarea';
            wrap.append(input);
            return wrap;
        };
        form.append(field('real_bio', '实名简介', data.bios?.real_bio));
        if (data.can_use_pen_name) form.append(field('pen_bio', '笔名简介', data.bios?.pen_bio));
        const actions = el('div', 'blog-studio__row-actions');
        actions.append(LQ.button({ label: '保存简介', variant: 'soft', size: 'sm', type: 'submit' }));
        for (const face of data.faces || []) {
            if (!face.ref) continue;
            const link = LQ.button({ label: `查看${face.label}主页`, variant: 'link', size: 'sm' });
            link.addEventListener('click', () => this.app.openAuthorPage(face.ref));
            actions.append(link);
        }
        form.append(actions);
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const values = Object.fromEntries(new FormData(form).entries());
            try {
                await this.api.put('/api/blog/studio/profile', values);
                showToast('简介已保存', 'success');
            } catch (error) {
                showToast(error.message || '保存失败', 'error');
            }
        });
        section.append(form);
        return section;
    }

    // --- 作品 ---------------------------------------------------------------------------

    async renderWorks(panel, live, { append = false } = {}) {
        const page = append ? (this.workPage || 1) + 1 : 1;
        const params = new URLSearchParams({ page: String(page), limit: '20', sort: this.workSort });
        if (this.workFilter !== 'all') params.set('status', this.workFilter);
        const data = await this.api.get(`/api/blog/my-posts?${params}`);
        if (!live()) return;
        this.workPage = page;  // only once the page has actually arrived
        const rows = (data.posts || []).map((post) => this.workRow(post));
        if (append) {
            panel.querySelector('[data-studio-works-list]')?.append(...rows);
        } else {
            const toolbar = el('div', 'blog-studio__toolbar');
            toolbar.append(
                chipRow(WORK_FILTERS, this.workFilter, (value) => { this.workFilter = value; this.reload('works'); }, '筛选作品'),
                chipRow(WORK_SORTS, this.workSort, (value) => { this.workSort = value; this.reload('works'); }, '排序'),
            );
            const list = rows.length
                ? this.content.createContent('list', { label: '我的作品', attrs: { 'data-studio-works-list': '' } }, { items: rows })
                : this.content.createContent('empty', { title: '这里还没有作品', description: '写下第一篇吧。', variant: 'card' });
            panel.replaceChildren(toolbar, list);
        }
        panel.querySelector('[data-studio-more]')?.remove();
        if (data.has_more) panel.append(this.moreButton(() => this.renderWorks(panel, live, { append: true })));
    }

    moreButton(onClick) {
        const button = LQ.button({ label: '加载更多', variant: 'soft', size: 'sm', attrs: { 'data-studio-more': '' } });
        button.addEventListener('click', async () => {
            button.disabled = true;
            try {
                await onClick();
            } catch (error) {
                button.disabled = false;  // the page counter only moves on success
                showToast(error.message || '加载失败', 'error');
            }
        });
        return button;
    }

    workStatus(post) {
        if (post.status === 'draft') return ['草稿', 'neutral'];
        const state = post.moderation?.state || post.moderation_state || 'none';
        return {
            pending: ['审核中', 'info'],
            limited: ['仅自己可见', 'warning'],
            removed: ['已移除', 'danger'],
            appeal_pending: ['申诉中', 'info'],
        }[state] || ['已发布', 'success'];
    }

    workRow(post) {
        const [statusLabel, statusTone] = this.workStatus(post);
        const published = post.status === 'published';
        const moderation = post.moderation || null;
        const state = moderation?.state || 'none';
        const isPublic = published && ['none', 'passed', 'flagged'].includes(state);
        const actions = [{ label: post.status === 'draft' ? '继续编辑' : '编辑', variant: 'soft', attrs: { 'data-act': 'edit' } }];
        if (published) actions.push({ label: '查看', variant: 'ghost', attrs: { 'data-act': 'view' } });
        if (isPublic) actions.push({ label: '撤回为草稿', variant: 'ghost', attrs: { 'data-act': 'withdraw' } });
        if (moderation?.appeal?.can_appeal) actions.push({ label: '申诉', variant: 'ghost', attrs: { 'data-act': 'appeal' } });
        actions.push({ label: '删除', variant: 'ghost', attrs: { 'data-act': 'delete' } });
        const counts = published ? `阅读 ${post.view_count || 0} · 赞 ${post.like_count || 0} · 评论 ${post.comment_count || 0}` : '';
        const row = this.content.createContent('row', {
            title: post.title || '无标题',
            meta: [FACE_LABELS[post.author_display_mode] || '实名', shortDate(post.published_at || post.created_at),
                post.edited_at ? '已编辑' : '', counts].filter(Boolean).join(' · '),
            actions,
        });
        row.querySelector('.lq-row__trail')?.prepend(LQ.chip({ label: statusLabel, tone: statusTone, size: 'sm' }));
        if (moderation && SANCTION_TITLES[state]) row.querySelector('.lq-row__main')?.append(this.sanctionAlert(moderation, post));
        row.addEventListener('click', (event) => {
            const button = event.target.closest('[data-act]');
            if (button) void this.workAction(button.dataset.act, post);
        });
        return row;
    }

    sanctionAlert(moderation, post) {
        const lines = [];
        if (moderation.reason_label) lines.push(`原因：${moderation.reason_label}。`);
        if (moderation.note) lines.push(`说明：${moderation.note}`);
        if (post.moderated_at) lines.push(`处理时间：${shortDate(post.moderated_at)}`);
        const appeal = moderation.appeal || {};
        if (appeal.pending) lines.push('申诉已提交，等待复核。');
        else if (appeal.used) lines.push('这次处理已申诉过。');
        return this.alert(moderation.state === 'removed' ? 'danger' : 'warning', SANCTION_TITLES[moderation.state],
            lines.join(' ') || '修改后会重新检查。');
    }

    async workAction(action, post) {
        if (action === 'edit') return this.app.editPost(post.id);
        if (action === 'view') return this.app.showDetail(post.id);
        // deletePost already refreshes the current list (this studio).
        if (action === 'delete') return this.app.deletePost(post.id);
        if (action === 'appeal') return this.appeal(post);
        if (action === 'withdraw') return this.withdraw(post);
        return undefined;
    }

    async appeal(post) {
        const reason = await appealDialog();
        if (!reason) return;
        try {
            await this.api.post(`/api/blog/posts/${post.id}/appeal`, { reason });
            showToast('申诉已提交，处理结果会通过消息通知你', 'success');
            this.refresh();
        } catch (error) {
            showToast(error.message || '申诉提交失败', 'error');
        }
    }

    async withdraw(post) {
        const confirmed = await LQ.confirm({
            title: '撤回为草稿', message: '撤回后其他人将看不到这篇帖子，你可以继续修改后再发布。',
            confirmLabel: '撤回', danger: true,
        });
        if (!confirmed) return;
        try {
            await this.api.put(`/api/blog/posts/${post.id}`, { status: 'draft', expected_revision: post.content_revision });
            showToast('已撤回为草稿', 'success');
            this.refresh();
        } catch (error) {
            showToast(error.message || '撤回失败', 'error');
        }
    }

    // --- 草稿箱 -------------------------------------------------------------------------

    async renderDrafts(panel, live) {
        const data = await this.api.get('/api/blog/my-posts?status=draft&limit=50');
        if (!live()) return;
        const serverIds = new Set((data.posts || []).map((post) => Number(post.id)));
        const rows = this.app.listLocalDrafts().map((draft) => this.localDraftRow(draft, serverIds));
        for (const post of data.posts || []) rows.push(this.workRow(post));
        panel.replaceChildren(rows.length
            ? this.content.createContent('list', { label: '草稿' }, { items: rows })
            : this.content.createContent('empty', { title: '草稿箱是空的', variant: 'card' }));
    }

    localDraftRow(draft, serverIds) {
        const isNew = draft.postId === null;
        let kind = '已发布帖子的未提交修改';
        if (isNew) kind = '新帖';
        else if (serverIds.has(draft.postId)) kind = '草稿的未保存修改';
        const row = this.content.createContent('row', {
            title: draft.title || '无标题草稿',
            meta: `${kind} · 保存于 ${shortDate(draft.savedAt)}`,
            actions: [
                { label: '恢复', variant: 'soft', attrs: { 'data-act': 'restore' } },
                { label: '丢弃', variant: 'ghost', attrs: { 'data-act': 'discard' } },
            ],
        });
        row.querySelector('.lq-row__trail')?.prepend(LQ.chip({ label: '仅本机', tone: 'warning', size: 'sm' }));
        row.addEventListener('click', async (event) => {
            const act = event.target.closest('[data-act]')?.dataset.act;
            if (act === 'restore') {
                if (isNew) this.app.openComposer(null);
                else await this.app.editPost(draft.postId);
                return;
            }
            if (act !== 'discard') return;
            const confirmed = await LQ.confirm({
                title: '丢弃本机草稿', message: '只删除这台设备上未提交的内容，服务器上的版本不受影响。',
                confirmLabel: '丢弃', danger: true,
            });
            if (confirmed) {
                this.app.clearComposerRecovery(draft.postId);
                this.reload('drafts');
            }
        });
        return row;
    }

    // --- 互动 ---------------------------------------------------------------------------

    async renderInteractions(panel, live, { append = false } = {}) {
        const page = append ? (this.commentPage || 1) + 1 : 1;
        const data = await this.api.get(`/api/blog/studio/comments?box=${this.box}&page=${page}`);
        if (!live()) return;
        this.commentPage = page;
        const rows = (data.items || []).map((item) => (this.box === 'received' ? this.receivedRow(item) : this.sentRow(item)));
        if (append) {
            panel.querySelector('[data-studio-comment-list]')?.append(...rows);
        } else {
            const switcher = chipRow([['received', '收到的'], ['sent', '我发出的']], this.box, (box) => {
                this.box = box;
                this.reload('interactions');
            }, '评论方向');
            const list = rows.length
                ? this.content.createContent('list', { label: '评论', attrs: { 'data-studio-comment-list': '' } }, { items: rows })
                : this.content.createContent('empty', {
                    title: this.box === 'received' ? '还没有收到评论' : '你还没有发过评论', variant: 'card',
                });
            panel.replaceChildren(switcher, list);
        }
        panel.querySelector('[data-studio-more]')?.remove();
        if (data.has_more) panel.append(this.moreButton(() => this.renderInteractions(panel, live, { append: true })));
    }

    receivedRow(item) {
        const author = item.author?.display_name || '同学';
        const row = this.content.createContent('row', {
            title: `${author}：${String(item.content_md || '（图片或表情）').slice(0, 80)}`,
            meta: `在《${item.post_title || '帖子'}》 · ${shortDate(item.created_at)}`,
            primary: { href: `/blog?post=${Number(item.post_id)}&comment=${Number(item.id)}` },
            actions: item.can_reply ? [{ label: '回复', variant: 'soft', attrs: { 'data-act': 'reply', 'aria-expanded': 'false' } }] : [],
        });
        const toggle = row.querySelector('[data-act="reply"]');
        toggle?.addEventListener('click', async () => {
            const existing = row.querySelector('.mc-blog-reply');
            toggle.setAttribute('aria-expanded', String(!existing));
            if (existing) {
                existing.remove();
                return;
            }
            const { openInlineReply } = await import('/static/js/message_center_blog.js');
            const panel = el('section', 'mc-blog-reply');
            panel.setAttribute('aria-label', '回复这条评论');
            row.querySelector('.lq-row__main')?.append(panel);
            await openInlineReply(panel, item.id, { afterReply: async () => { toggle.remove(); } });
        });
        return row;
    }

    sentRow(item) {
        const face = item.face || {};
        // "你以 匿名同学·青柚 发表": helps the author recognise their own voices.
        const faceNote = face.mode && face.mode !== 'real_name' ? `你以 ${face.name} 发表` : '';
        const row = this.content.createContent('row', {
            title: String(item.content_md || '（图片或表情）').slice(0, 80),
            meta: [`在《${item.post_title || '帖子'}》`, faceNote, shortDate(item.created_at), item.edited ? '已编辑' : '']
                .filter(Boolean).join(' · '),
            ...(item.post_available ? { primary: { href: `/blog?post=${Number(item.post_id)}&comment=${Number(item.id)}` } } : {}),
        });
        const moderation = item.moderation || {};
        if (['limited', 'removed'].includes(moderation.state)) {
            const body = [moderation.reason_label ? `原因：${moderation.reason_label}。` : '',
                moderation.note ? `说明：${moderation.note}` : ''].filter(Boolean).join(' ');
            row.querySelector('.lq-row__main')?.append(this.alert(
                moderation.state === 'removed' ? 'danger' : 'warning', moderation.label || '已被处理', body || '这条评论已被处理。'));
        } else if (moderation.state === 'pending') {
            row.querySelector('.lq-row__trail')?.prepend(LQ.chip({ label: '审核中', tone: 'info', size: 'sm' }));
        }
        return row;
    }

    // --- 粉丝与关注 -------------------------------------------------------------------------

    async renderFans(panel, live) {
        const data = await this.api.get('/api/blog/studio/fans');
        if (!live()) return;
        const followers = data.followers || {};
        const parts = [this.followerSection('关注我的人', followers.real || [], { followBack: true, total: followers.real_total })];
        // No "回关" for pen-name followers: following back is real-named and
        // would tell them who writes under the pen name.
        if ((followers.pen || []).length) {
            parts.push(this.followerSection('关注我笔名的人', followers.pen, { followBack: false, total: followers.pen_total }));
        }
        parts.push(this.followingSection(data.following || []));
        panel.replaceChildren(...parts);
    }

    followerSection(title, people, { followBack, total }) {
        const section = el('section', 'blog-studio__fans');
        section.append(el('h3', 'blog-studio__heading', `${title}（${total ?? people.length}）`));
        if (!people.length) {
            section.append(this.content.createContent('empty', { title: '还没有人关注', variant: 'inline' }));
            return section;
        }
        const rows = people.map((person) => {
            const row = this.content.createContent('row', {
                title: person.name,
                meta: `关注于 ${shortDate(person.followed_at)}`,
                actions: followBack && !person.follows_back ? [{ label: '回关', variant: 'soft', attrs: { 'data-act': 'follow' } }] : [],
            });
            row.querySelector('.lq-row__lead')?.append(LQ.avatar({ name: person.name, src: person.avatar_url || null, size: 32 }));
            if (followBack && person.follows_back) row.querySelector('.lq-row__trail')?.prepend(LQ.chip({ label: '互相关注', size: 'sm' }));
            row.querySelector('[data-act="follow"]')?.addEventListener('click', async (event) => {
                event.currentTarget.disabled = true;
                if (await this.setFollow('author', person.ref, true)) this.reload('fans');
            });
            return row;
        });
        section.append(this.content.createContent('list', { label: title }, { items: rows }));
        return section;
    }

    followingSection(items) {
        const section = el('section', 'blog-studio__fans');
        section.append(el('h3', 'blog-studio__heading', `我的关注（${items.length}）`));
        const form = el('form', 'blog-studio__tag-form');
        const input = el('input', 'lq-input');
        Object.assign(input, { name: 'tag', maxLength: 40, placeholder: '输入标签，例如 Python', type: 'text' });
        input.setAttribute('aria-label', '要关注的标签');
        input.dataset.lqComponent = 'input';
        form.append(input, LQ.button({ label: '关注标签', variant: 'soft', size: 'sm', type: 'submit' }));
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const tag = input.value.trim().replace(/^#/, '');
            if (tag && await this.setFollow('tag', tag, true)) this.reload('fans');
        });
        section.append(form);
        if (!items.length) {
            section.append(this.content.createContent('empty', { title: '还没有关注任何作者、板块或标签', variant: 'inline' }));
            return section;
        }
        const rows = items.map((item) => {
            const row = this.content.createContent('row', {
                title: item.name,
                meta: `${item.face} · 关注于 ${shortDate(item.followed_at)}`,
                actions: [{ label: '取消关注', variant: 'ghost', attrs: { 'data-act': 'unfollow' } }],
            });
            if (['author', 'pen'].includes(item.target_type)) {
                const open = LQ.button({ label: '主页', variant: 'link', size: 'sm' });
                open.addEventListener('click', () => this.app.openAuthorPage(item.target_key));
                row.querySelector('.lq-row__trail')?.prepend(open);
            }
            row.querySelector('[data-act="unfollow"]')?.addEventListener('click', async () => {
                if (await this.setFollow(item.target_type, item.target_key, false)) this.reload('fans');
            });
            return row;
        });
        section.append(this.content.createContent('list', { label: '我的关注' }, { items: rows }));
        return section;
    }

    async setFollow(targetType, targetKey, following) {
        try {
            await this.api.post('/api/blog/follows', { target_type: targetType, target_key: targetKey, following });
            await this.app.loadFollows();
            showToast(following ? '已关注' : '已取消关注', 'success');
            return true;
        } catch (error) {
            showToast(error.message || '关注操作失败', 'error');
            return false;
        }
    }
}

/** The author page: one face (real name or pen name), addressed by its ref. */
export class AuthorPage {
    constructor(app, api, root) {
        this.app = app;
        this.api = api;
        this.root = root;
    }

    async open(ref) {
        this.ref = ref;
        this.page = 1;
        this.root.replaceChildren(el('p', 'blog-studio__status', '正在加载…'));
        const content = await LQ.load('content');
        let data;
        try {
            data = await this.api.get(`/api/blog/authors/${encodeURIComponent(ref)}`);
        } catch (error) {
            this.root.replaceChildren(content.createContent('empty', {
                reason: 'error', title: error.message || '作者不存在', variant: 'card',
                actions: [{ label: '返回博客', variant: 'soft', attrs: { 'data-blog-nav': 'feed' } }],
            }));
            return;
        }
        if (this.ref !== ref) return;
        this.root.replaceChildren(this.header(data), this.feed(data));
    }

    header(data) {
        const author = data.author || {};
        const card = el('section', 'blog-author-page__head');
        card.append(LQ.avatar({ name: author.display_name || '作者', src: author.avatar_url || null, size: 56 }));
        const copy = el('div', 'blog-author-page__copy');
        copy.append(el('h2', 'blog-author-page__name', author.display_name || '作者'));
        const meta = el('div', 'blog-author-page__meta');
        meta.append(LQ.chip({ label: data.face, size: 'sm' }), el('span', '', `${data.followers} 人关注 · ${data.total || 0} 篇帖子`));
        copy.append(meta);
        if (data.bio) copy.append(el('p', 'blog-author-page__bio', data.bio));
        card.append(copy);
        const actions = el('div', 'blog-author-page__actions');
        if (!data.is_self) {
            actions.append(this.followButton(data));
            if (author.can_message && author.identity) {
                const message = LQ.button({ label: '私信', variant: 'soft', size: 'sm' });
                message.addEventListener('click', () => this.app.openPrivateMessage(author.identity));
                actions.append(message);
            }
        }
        card.append(actions);
        return card;
    }

    followButton(data) {
        const follow = LQ.button({
            label: data.is_following ? '已关注' : '关注', variant: data.is_following ? 'soft' : 'prominent', size: 'sm',
        });
        follow.addEventListener('click', async () => {
            follow.disabled = true;
            try {
                const result = await this.api.post('/api/blog/follows', {
                    target_type: data.follow_type, target_key: data.ref, following: !data.is_following,
                });
                await this.app.loadFollows();
                showToast(result.following ? '已关注' : '已取消关注', 'success');
                await this.open(data.ref);
            } catch (error) {
                follow.disabled = false;
                showToast(error.message || '关注操作失败', 'error');
            }
        });
        return follow;
    }

    feed(data) {
        const wrap = el('div', 'blog-feed blog-author-page__feed');
        wrap.dataset.lqMaterial = 'content';
        const posts = data.posts || [];
        // Post cards are the feed's own cards, so clicks and layout behave the same.
        wrap.innerHTML = posts.length
            ? posts.map((post) => this.app.postCardHtml(post)).join('')
            : `<p class="blog-studio__status">${escapeHtml('还没有公开的帖子')}</p>`;
        if (data.has_more) wrap.append(this.moreButton());
        return wrap;
    }

    moreButton() {
        const more = LQ.button({ label: '加载更多', variant: 'soft', size: 'sm' });
        more.addEventListener('click', async () => {
            more.disabled = true;
            this.page += 1;
            try {
                const next = await this.api.get(`/api/blog/authors/${encodeURIComponent(this.ref)}?page=${this.page}`);
                more.insertAdjacentHTML('beforebegin', (next.posts || []).map((post) => this.app.postCardHtml(post)).join(''));
                if (next.has_more) more.disabled = false;
                else more.remove();
            } catch (error) {
                more.disabled = false;
                showToast(error.message || '加载失败', 'error');
            }
        });
        return more;
    }
}
