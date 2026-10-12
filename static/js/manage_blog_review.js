/**
 * Blog review desk (plan §5.5): queue tabs, report/appeal handling, identity
 * reveal, audit log and moderator grants. LQ tabs, surfaces, chips, avatars
 * and dialogs only; layout lives in blog_moderation.css.
 */
import { apiFetch } from '/static/js/api.js';
import { getLQ } from '/static/js/lq/index.js';
import { escapeHtml, showToast } from '/static/js/ui.js';
import { REASON_OPTIONS, moderationDialog, openFormDialog, revealDialog } from '/static/js/blog_moderation_ui.js';

const LQ = getLQ();
const root = document.querySelector('[data-blog-review]');
const TAB_ID = 'blog-review-tabs';
const RISK_LABELS = { none: '无风险', low: '低风险', medium: '中风险', high: '高风险' };
const ACTION_LABELS = {
    limit: '限制可见', remove: '移除', restore: '恢复公开', confirm: '确认处理', emergency_limit: '紧急限制',
    ai_remove: 'AI 移除', report_limit: '多人举报暂限', dismiss_reports: '驳回举报', appeal: '提交申诉',
    appeal_accept: '申诉通过', appeal_reject: '申诉驳回', identity_reveal: '查看真实作者',
    grant_moderator: '授权博客管理员', revoke_moderator: '撤销博客管理员', pin: '置顶', unpin: '取消置顶',
    feature: '设为精华', unfeature: '取消精华', hide: '隐藏（旧）',
};
const state = { tab: '' };

function button(label, attrs = {}, variant = 'glass') {
    const extra = Object.entries(attrs).map(([key, value]) => ` ${key}="${escapeHtml(String(value))}"`).join('');
    return `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--${variant}"${extra}>${escapeHtml(label)}</button>`;
}

function chip(label, tone = 'neutral') {
    return label ? LQ.html.chip({ label, tone, size: 'sm' }) : '';
}

function timeText(value) {
    return value ? String(value).replace('T', ' ').slice(0, 16) : '';
}

function updateBadges(counts = {}) {
    for (const [key, count] of Object.entries(counts)) {
        const tab = document.getElementById(`${TAB_ID}--lq-tab-${key}`);
        if (!tab) continue;
        let badge = tab.querySelector('.lq-badge');
        if (!badge && count) {
            tab.insertAdjacentHTML('beforeend', LQ.html.badge({ value: count }));
            badge = tab.querySelector('.lq-badge');
        }
        if (badge) {
            badge.textContent = String(count);
            badge.hidden = !count;
        }
    }
}

function factsHtml(item) {
    const facts = [];
    if (item.ai) {
        const verdict = item.ai.failed
            ? '检查失败'
            : `${RISK_LABELS[item.ai.risk] || item.ai.status}${item.ai.categories.length ? ` · ${item.ai.categories.join('、')}` : ''}`;
        facts.push(['AI 结论', `${verdict}${item.ai.evidence ? `：${item.ai.evidence}` : ''}`]);
    }
    if (item.reports?.count) {
        facts.push(['举报', `${item.reports.count} 人 · ${item.reports.reasons.map((r) => `${r.label}×${r.count}`).join('、')}`]);
        if (item.reports.reporters.length) facts.push(['举报人', item.reports.reporters.join('、')]);
    }
    if (item.reason_label) facts.push(['处理原因', item.reason_label]);
    if (item.note) facts.push(['说明', item.note]);
    facts.push(['作者历史处置', `${item.author_history} 次`]);
    return `<dl class="br-card__facts">${facts.map(([term, value]) => `<div><dt>${escapeHtml(term)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl>`;
}

function itemActions(item, tab) {
    const target = { 'data-target-type': item.target_type, 'data-target-id': item.target_id };
    const actions = [];
    if (tab === 'reports') {
        actions.push(button('处理举报…', { 'data-review-reports': '', ...target }, 'prominent'));
    } else if (tab === 'appeals' && item.appeal) {
        actions.push(button('裁决申诉…', { 'data-review-appeal': item.appeal.id }, 'prominent'));
    } else if (item.is_live) {
        actions.push(button('处理…', { 'data-review-moderate': '', 'data-state': item.state, ...target }, 'prominent'));
    }
    if (item.can_reveal) actions.push(button('查看作者', { 'data-review-reveal': '', ...target }));
    actions.push(`<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--ghost" href="${escapeHtml(item.link_url)}" target="_blank" rel="noopener">打开原帖</a>`);
    return actions.join('');
}

function cardHtml(item, tab) {
    const kind = item.target_type === 'post' ? '帖子' : '评论';
    const author = item.author || {};
    const stateTone = item.state === 'removed' ? 'danger' : ['flagged', 'pending'].includes(item.state) ? 'warning' : 'neutral';
    return `
        <article data-lq-component="surface" class="lq-surface br-card" data-lq-shape="surface" data-review-item="${item.target_type}:${item.target_id}">
            <div class="br-card__head">
                ${chip(kind, 'info')}
                ${chip(item.state_label, stateTone)}
                ${chip(item.source_label)}
                ${item.reports?.identity_exposed ? chip('举报：暴露身份', 'danger') : ''}
            </div>
            <h3 class="br-card__title"><a href="${escapeHtml(item.link_url)}" target="_blank" rel="noopener">${escapeHtml(item.title || '（无标题）')}</a></h3>
            <p class="br-card__meta">
                ${LQ.html.avatar({ name: author.display_name || '用户', src: author.avatar_url || null, size: 24 })}
                <span>${escapeHtml(author.display_name || '未知作者')}${author.role_label ? ` · ${escapeHtml(author.role_label)}` : ''}</span>
                <span>${escapeHtml(timeText(item.created_at))}</span>
            </p>
            <p class="br-card__excerpt">${escapeHtml(item.excerpt || '（无文字内容）')}</p>
            ${item.appeal ? `<blockquote class="br-card__quote">申诉理由：${escapeHtml(item.appeal.reason)}</blockquote>` : ''}
            ${(item.reports?.details || []).map((detail) => `<blockquote class="br-card__quote">举报说明：${escapeHtml(detail)}</blockquote>`).join('')}
            ${factsHtml(item)}
            <div class="br-card__actions">${itemActions(item, tab)}</div>
        </article>`;
}

async function loadTab(key) {
    state.tab = key;
    if (key === 'audit') return loadAudit(1);
    if (key === 'moderators') return loadModerators();
    const list = root.querySelector(`[data-review-list="${key}"]`);
    if (!list) return undefined;
    list.setAttribute('aria-busy', 'true');
    try {
        const data = await apiFetch(`/api/blog/review/queue?tab=${encodeURIComponent(key)}`);
        list.innerHTML = data.items.length
            ? data.items.map((item) => cardHtml(item, key)).join('')
            : '<p class="br-empty">这里暂时没有待处理的内容。</p>';
        updateBadges(data.counts);
    } catch (error) {
        list.innerHTML = `<p class="br-empty">${escapeHtml(error.message || '加载失败')}</p>`;
    } finally {
        list.removeAttribute('aria-busy');
    }
    return undefined;
}

async function post(url, body, success) {
    try {
        await apiFetch(url, { method: 'POST', body });
        showToast(success, 'success');
        await loadTab(state.tab);
    } catch (error) {
        showToast(error.message || '操作失败', 'error');
    }
}

async function handleModerate(target) {
    const subject = target.dataset.targetType === 'post' ? '这篇帖子' : '这条评论';
    const values = await moderationDialog({ moderator: true, state: target.dataset.state, subject });
    if (values) await post(`/api/blog/moderation/${target.dataset.targetType}/${target.dataset.targetId}`, values, '已处理，结果已通知作者');
}

async function handleReports(target) {
    const values = await openFormDialog({
        title: '处理举报',
        description: '驳回时，因多人举报而暂限的内容会恢复公开。每位举报人都会收到处理结果。',
        fields: [
            { name: 'decision', label: '处理方式', type: 'select', required: true, value: 'dismiss', options: [['dismiss', '驳回：未发现违规'], ['limit', '限制可见'], ['remove', '移除']] },
            { name: 'reason_code', label: '原因（限制或移除时必选）', type: 'select', placeholder: '请选择原因', options: REASON_OPTIONS },
            { name: 'note', label: '说明', type: 'textarea', maxlength: 300 },
        ],
        confirmLabel: '确认',
        validate: (values) => (values.decision !== 'dismiss' && !values.reason_code ? '请选择处理原因' : ''),
    });
    if (values) await post(`/api/blog/review/reports/${target.dataset.targetType}/${target.dataset.targetId}`, values, '举报已处理');
}

async function handleAppeal(target) {
    const values = await openFormDialog({
        title: '裁决申诉',
        description: '通过后帖子恢复公开；驳回后维持原处理，需要写明理由。结果会通知作者。',
        fields: [
            { name: 'decision', label: '裁决', type: 'select', required: true, value: 'accept', options: [['accept', '通过申诉，恢复公开'], ['reject', '驳回申诉，维持原处理']] },
            { name: 'note', label: '说明（作者会看到）', type: 'textarea', maxlength: 300 },
        ],
        confirmLabel: '提交裁决',
        validate: (values) => (values.decision === 'reject' && !String(values.note || '').trim() ? '驳回申诉请写明理由' : ''),
    });
    if (values) await post(`/api/blog/review/appeals/${target.dataset.reviewAppeal}`, values, '申诉已裁决');
}

async function handleReveal(target) {
    const values = await revealDialog();
    if (!values) return;
    try {
        const data = await apiFetch(`/api/blog/review/reveal/${target.dataset.targetType}/${target.dataset.targetId}`, { method: 'POST', body: values });
        const detail = [data.class_name, data.student_number].filter(Boolean).join(' · ');
        await LQ.confirm({ title: '真实作者（已记录审计）', message: `${data.name || '未知'}${detail ? `（${detail}）` : ''}`, confirmLabel: '知道了', cancelLabel: '关闭' });
    } catch (error) {
        showToast(error.message || '查看失败', 'error');
    }
}

function auditTarget(item) {
    const noun = { comment: '评论', teacher: '教师' }[item.target_type] || '帖子';
    const label = `${noun} #${Number(item.target_id || item.post_id || 0)}`;
    if (item.target_type === 'teacher' || !item.post_id) return escapeHtml(label);
    return `<a href="/blog?post=${Number(item.post_id)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`;
}

async function loadAudit(page) {
    const list = root.querySelector('[data-review-audit]');
    if (!list) return;
    try {
        const data = await apiFetch(`/api/blog/review/audit?page=${page}`);
        const rows = data.items.map((item) => `
            <tr>
                <td>${escapeHtml(timeText(item.created_at))}</td>
                <td>${escapeHtml(item.actor_name || item.moderator_identity || '')}</td>
                <td>${escapeHtml(ACTION_LABELS[item.action] || item.action)}</td>
                <td>${auditTarget(item)}</td>
                <td>${escapeHtml(item.reason_label || '')}</td>
                <td>${escapeHtml(item.note || '')}</td>
            </tr>`).join('');
        const pager = [page > 1 ? button('上一页', { 'data-audit-page': page - 1 }) : '', data.has_more ? button('下一页', { 'data-audit-page': page + 1 }) : ''].join('');
        list.innerHTML = rows
            ? `<div data-lq-component="surface" class="lq-surface br-card br-audit" data-lq-shape="surface"><table class="br-audit-table"><thead><tr><th>时间</th><th>执行人</th><th>动作</th><th>对象</th><th>原因</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div>
               <div class="br-card__actions">${pager}</div>`
            : '<p class="br-empty">还没有审计记录。</p>';
    } catch (error) {
        list.innerHTML = `<p class="br-empty">${escapeHtml(error.message || '加载失败')}</p>`;
    }
}

async function loadModerators() {
    const list = root.querySelector('[data-review-moderators]');
    if (!list) return;
    try {
        const data = await apiFetch('/api/blog/review/moderators');
        const rows = data.items.map((item) => `
            <div class="br-moderator-row">
                ${LQ.html.avatar({ name: item.name || '教师', size: 24 })}
                <span>${escapeHtml(item.name || '')}</span>
                ${button('撤销', { 'data-moderator-set': item.teacher_id, 'data-granted': 'false' })}
            </div>`).join('');
        list.innerHTML = `
            <div data-lq-component="surface" class="lq-surface br-card" data-lq-shape="surface">
                <p class="br-card__meta">超级管理员始终是博客管理员。被授权的教师可以进入审核台处理内容、裁决申诉、查看匿名作者（会记录审计）。</p>
                ${rows || '<p class="br-empty">还没有授权其他教师。</p>'}
                <div class="br-moderator-row">
                    <input data-lq-component="input" class="lq-input" type="search" placeholder="输入教师姓名或邮箱" aria-label="搜索教师" data-moderator-search>
                    ${button('搜索', { 'data-moderator-search-run': '' })}
                </div>
                <div class="br-list" data-moderator-results></div>
            </div>`;
    } catch (error) {
        list.innerHTML = `<p class="br-empty">${escapeHtml(error.message || '加载失败')}</p>`;
    }
}

async function searchTeachers() {
    const input = root.querySelector('[data-moderator-search]');
    const results = root.querySelector('[data-moderator-results]');
    if (!input || !results || !input.value.trim()) return;
    try {
        const data = await apiFetch(`/api/blog/review/teachers?q=${encodeURIComponent(input.value.trim())}`);
        results.innerHTML = data.items.length ? data.items.map((item) => `
            <div class="br-moderator-row">
                <span>${escapeHtml(item.name)}${item.email ? ` · ${escapeHtml(item.email)}` : ''}</span>
                ${item.granted ? chip('已授权', 'success') : button('授权', { 'data-moderator-set': item.id, 'data-granted': 'true' })}
            </div>`).join('') : '<p class="br-empty">没有找到匹配的教师。</p>';
    } catch (error) {
        showToast(error.message || '搜索失败', 'error');
    }
}

async function setModerator(target) {
    const granted = target.dataset.granted === 'true';
    if (!granted && !(await LQ.confirm({ title: '撤销博客管理员', message: '撤销后该教师不能再进入审核台。确定撤销吗？', confirmLabel: '撤销', danger: true }))) return;
    await post(`/api/blog/review/moderators/${target.dataset.moderatorSet}`, { granted }, granted ? '已授权' : '已撤销');
}

function bindEvents() {
    root.addEventListener('click', (event) => {
        const target = event.target.closest('button');
        if (!target || !root.contains(target)) return;
        if (target.hasAttribute('data-review-moderate')) handleModerate(target);
        else if (target.hasAttribute('data-review-reports')) handleReports(target);
        else if (target.hasAttribute('data-review-appeal')) handleAppeal(target);
        else if (target.hasAttribute('data-review-reveal')) handleReveal(target);
        else if (target.hasAttribute('data-audit-page')) loadAudit(Number(target.dataset.auditPage));
        else if (target.hasAttribute('data-moderator-search-run')) searchTeachers();
        else if (target.hasAttribute('data-moderator-set')) setModerator(target);
    });
    root.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && event.target.matches('[data-moderator-search]') && !event.isComposing) {
            event.preventDefault();
            searchTeachers();
        }
    });
}

async function init() {
    if (!root) return;
    bindEvents();
    const tabsRoot = document.getElementById(TAB_ID);
    const navigation = await LQ.load('navigation');
    navigation.tabs(tabsRoot);
    // Load on selection itself: the LQ change event waits for the panel's
    // entrance animation, which never settles in a background tab.
    const selectedKey = () => tabsRoot.querySelector('[role="tab"][aria-selected="true"]')?.id.split('--lq-tab-').pop() || 'machine';
    new MutationObserver(() => {
        const key = selectedKey();
        if (key !== state.tab) loadTab(key);
    }).observe(tabsRoot, { subtree: true, attributes: true, attributeFilter: ['aria-selected'] });
    await loadTab(selectedKey());
}

init();
