/**
 * 散修管理页（templates/manage/system/wanderers.html）。
 *
 * 只做数据绑定：按钮、选择卡、标签页、列表、统计条全部来自 LQ 工厂或模板里的
 * 规范 DOM；浮窗的栈 / 焦点 / 滚动锁由 createDomainModal（LQ.layer）持有。
 * API：/api/manage/system/wanderers/*（超管）。
 */
import { apiFetch } from './api.js';
import { showToast } from './ui.js';
import { createDomainModal } from './lq/domain-modal.js';
import { createComponent } from './lq/components.js';
import { createContent } from './lq/content.js';
import { createForm } from './lq/forms.js';
import { createInsight } from './lq/insights.js';
import { tabs as createTabs } from './lq/navigation.js';

const API_ROOT = '/api/manage/system/wanderers';
const PAGE_LIMIT = 30;
const KNOWLEDGE_LIMIT = 50;
const STATUS_TONES = { active: 'success', paused: 'warning', dead: 'neutral' };
const BEHAVIOR_LABELS = {
    blog_browse: '浏览博客', blog_like: '点赞', blog_comment: '评论帖子', reply: '回复他人', dm_reply: '回复私信',
    classroom_visit: '旁听课堂', classroom_message: '课堂留言', knowledge_read: '研读固化', promotion: '申请升阶',
    blog_post: '发帖',
};
const GENESIS_NUMBER_FIELDS = ['genesis_min_real_actions_24h', 'genesis_max_total', 'genesis_max_per_day', 'genesis_max_per_week'];
const GENESIS_SUMMARY_KEYS = { status: '状态', created: '新增' };
const ACTIVITY_LABELS = {
    real_actions_24h: '真实动作', real_posts_24h: '发帖', real_comments_24h: '评论', real_likes_24h: '点赞',
    real_chat_messages_24h: '课堂留言', real_logins_24h: '登录', wanderer_actions_24h: '散修动作',
};
const REPLY_POLICY_FIELDS = ['ignore_prob', 'emoji_only_prob', 'multi_part_prob', 'max_parts'];
// Manual runs are started in the background (202); the overview is re-read after this delay.
const MANUAL_RUN_REFRESH_MS = 8000;
const TICK_SUMMARY_KEYS = { status: '状态', processed: '散修', actions: '动作', ai_calls: 'AI', failed: '失败' };

const boot = window.WANDERER_ADMIN_BOOT || {};
const state = {
    overview: boot.overview || {},
    wanderers: Array.isArray(boot.wanderers) ? boot.wanderers : [],
    detail: null,
    detailId: null,
    pages: { actions: 1, reviews: 1, knowledge: 1 },
    serial: 0,
};

const $ = (selector, root = document) => root?.querySelector(selector) ?? null;
const els = {
    enabled: $('#wd-enabled'),
    tickBtn: $('#wd-tick-btn'),
    reviewBtn: $('#wd-review-btn'),
    genesisBtn: $('#wd-genesis-btn'),
    reseedBtn: $('#wd-reseed-btn'),
    refreshBtn: $('#wd-refresh-btn'),
    envNote: $('#wd-env-note'),
    grid: $('#wd-card-grid'),
    empty: $('#wd-card-empty'),
    count: $('#wd-card-count'),
    settingsForm: $('#wd-settings-form'),
    detailRoot: $('#wd-detail'),
};
const detailEls = {
    title: $('[data-wd-detail-title]', els.detailRoot),
    subtitle: $('[data-wd-detail-subtitle]', els.detailRoot),
    close: $('[data-wd-detail-close]', els.detailRoot),
    surface: $('.lq-dialog__surface', els.detailRoot),
    tierForm: $('[data-wd-tier-form]', els.detailRoot),
    wipeConfirm: $('[data-wd-wipe-confirm]', els.detailRoot),
    panels: {
        overview: $('[data-wd-panel="overview"]', els.detailRoot),
        actions: $('[data-wd-panel="actions"]', els.detailRoot),
        reviews: $('[data-wd-panel="reviews"]', els.detailRoot),
    },
    knowledge: $('[data-wd-knowledge]', els.detailRoot),
    personaForm: $('[data-wd-persona-form]', els.detailRoot),
    personaError: $('[data-wd-persona-error]', els.detailRoot),
    behaviors: $('[data-wd-behaviors]', els.detailRoot),
    avatarForm: $('[data-wd-avatar-form]', els.detailRoot),
    avatarPreview: $('[data-wd-avatar-preview]', els.detailRoot),
    avatarSource: $('[data-wd-avatar-source]', els.detailRoot),
};
let modal = null;
let tabController = null;

/* ---------------------------------------------------------------- helpers */

function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = String(value);
        else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of [].concat(children)) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

const safeHref = (href) => (typeof href === 'string' && href.startsWith('/') && !href.startsWith('//') ? href : null);
const chip = (label, tone = 'neutral') => createComponent('chip', { label: String(label), tone, size: 'sm' });
const button = (label, attrs = {}, variant = 'glass') => createComponent('button', { label, variant, size: 'sm', attrs });
const emptyState = (description, reason = 'empty') => createContent('empty', { reason, description });

function formatTime(value) {
    return value ? String(value).replace('T', ' ').slice(0, 16) : '';
}

function errorMessage(error, fallback) {
    return error && error.message ? error.message : fallback;
}

async function withBusy(control, task) {
    if (control) {
        control.disabled = true;
        control.setAttribute('aria-busy', 'true');
    }
    try {
        return await task();
    } finally {
        if (control) {
            control.disabled = false;
            control.removeAttribute('aria-busy');
        }
    }
}

/* ---------------------------------------------------------------- overview + cards */

function renderOverview(overview) {
    const counts = overview.counts || {};
    const stats = {
        ai: `${overview.ai_calls_today ?? 0} / ${overview.daily_ai_call_cap ?? 0}`,
        counts: `${counts.active ?? 0} / ${counts.paused ?? 0} / ${counts.dead ?? 0}`,
        origin: `${counts.total ?? 0} · ${counts.dao_created ?? 0}`,
        promotion: `${overview.promotion_requested ?? 0} · ${overview.warnings_today ?? 0}`,
        tick: formatTime(overview.last_tick_at) || '暂无',
        dao: formatTime(overview.last_dao_review_at) || '暂无',
    };
    for (const [key, text] of Object.entries(stats)) {
        const node = document.querySelector(`[data-wd-stat="${key}"]`);
        if (node) node.textContent = text;
    }
    if (els.envNote) els.envNote.hidden = Boolean(overview.env_enabled);
    if (els.enabled && 'runtime_enabled' in overview) els.enabled.checked = Boolean(overview.runtime_enabled);
    renderManualSummary(overview);
}

function manualRunText(record, label, keys) {
    if (!record || typeof record !== 'object' || !record.status) return '';
    const when = formatTime(record.finished_at);
    if (record.status === 'error') return `${label}失败（${when}）：${record.error || '未知错误'}`;
    const summary = record.summary || {};
    const source = keys ? summary : (summary.counts && typeof summary.counts === 'object' ? summary.counts : summary);
    const parts = Object.entries(source)
        .filter(([key, value]) => (!keys || key in keys) && ['number', 'string', 'boolean'].includes(typeof value))
        .slice(0, 6)
        .map(([key, value]) => `${keys ? keys[key] : key} ${value}`);
    return `${label}（${when}）${parts.length ? `：${parts.join('，')}` : ''}`;
}

function renderManualSummary(overview) {
    const node = document.getElementById('wd-manual-summary');
    if (!node) return;
    const lines = [
        manualRunText(overview.last_manual_tick, '上次手动节拍', TICK_SUMMARY_KEYS),
        manualRunText(overview.last_manual_review, '上次手动天道审核', null),
        manualRunText(overview.last_manual_genesis, '上次手动造人', GENESIS_SUMMARY_KEYS),
    ].filter(Boolean);
    node.textContent = lines.join('；');
    node.hidden = lines.length === 0;
}

function cardAvatar(card) {
    const avatar = el('span', { class: 'wd-card__avatar' }, [
        el('img', { src: card.avatar_url, alt: '', width: 44, height: 44, loading: 'lazy', decoding: 'async' }),
    ]);
    if (card.emblem?.emblem_url) {
        avatar.append(el('img', {
            class: 'lq-cultivation-emblem-img wd-card__emblem', src: card.emblem.emblem_url,
            alt: `${card.tier_name}标志`, width: 22, height: 22, decoding: 'async',
        }));
    }
    return avatar;
}

function cardChips(card) {
    const chips = [chip(card.status_label, STATUS_TONES[card.status] || 'neutral'), chip(card.tier_name, 'info')];
    if (card.promotion_requested) chips.push(chip('申请升阶', 'primary'));
    if (card.warnings_count) chips.push(chip(`警告 ${card.warnings_count}`, 'danger'));
    if (card.origin === 'dao') chips.push(chip(card.origin_label || '天道所造', 'warning'));
    return el('span', { class: 'wd-card__chips' }, chips);
}

function renderCard(card) {
    return el('button', {
        'data-lq-component': 'choice', 'data-lq-shape': 'surface', type: 'button',
        class: 'lq-btn lq-btn--sm lq-btn--glass lq-domain-choice wd-card',
        'data-wanderer-id': card.id, 'data-status': card.status, 'aria-haspopup': 'dialog',
    }, [
        el('span', { class: 'wd-card__head' }, [
            cardAvatar(card),
            el('span', { class: 'wd-card__title' }, [el('strong', { text: card.display_name }), el('span', { class: 'wd-muted', text: card.archetype })]),
        ]),
        cardChips(card),
        el('span', { class: 'wd-card__meta wd-muted', text: `今日动作 ${card.today_actions} / ${card.daily_action_budget}` }),
        el('span', { class: 'wd-card__preview', text: card.last_action_preview || '尚无动作' }),
    ]);
}

function replaceCardNode(card) {
    const current = els.grid.querySelector(`[data-wanderer-id="${card.id}"]`);
    if (!current) return false;
    // Update in place: a card may be the open dialog's trigger, and LQ.layer
    // destroys a layer whose trigger leaves the document.
    current.dataset.status = card.status;
    current.replaceChildren(...renderCard(card).childNodes);
    return true;
}

function renderCards(cards) {
    const sameSet = cards.length === els.grid.children.length
        && cards.every(card => els.grid.querySelector(`[data-wanderer-id="${card.id}"]`));
    if (sameSet) cards.forEach(replaceCardNode);
    else els.grid.replaceChildren(...cards.map(renderCard));
    if (els.empty) els.empty.hidden = cards.length > 0;
    if (els.count) els.count.textContent = `共 ${cards.length} 位`;
}

function replaceCard(card) {
    state.wanderers = state.wanderers.map(item => (item.id === card.id ? card : item));
    replaceCardNode(card);
}

async function refreshAll() {
    const data = await apiFetch(API_ROOT, { silent: true });
    state.overview = data.overview || {};
    state.wanderers = Array.isArray(data.wanderers) ? data.wanderers : [];
    renderOverview(state.overview);
    renderCards(state.wanderers);
}

async function refreshQuietly() {
    try {
        await refreshAll();
    } catch (error) {
        showToast(`列表刷新失败：${errorMessage(error, '请稍后重试')}`, 'error');
    }
}

/* ---------------------------------------------------------------- toolbar + settings */

async function saveSettings(patch) {
    const data = await apiFetch(`${API_ROOT}/settings`, { method: 'POST', body: patch, silent: true });
    showToast('设置已保存', 'success');
    await refreshQuietly();
    return data.settings;
}

async function runEngine(path, body, label) {
    try {
        const data = await apiFetch(`${API_ROOT}${path}`, { method: 'POST', body: body || {}, silent: true });
        showToast(`${label}：${data.message || '已开始，稍后刷新查看结果'}`, 'success', 5000);
        window.setTimeout(refreshQuietly, MANUAL_RUN_REFRESH_MS);
        return data;
    } catch (error) {
        showToast(`${label}失败：${errorMessage(error, '请稍后重试')}`, 'error', 5000);
        return null;
    }
}

function settingsPayload(form) {
    const data = new FormData(form);
    return {
        daily_ai_call_cap: Number(data.get('daily_ai_call_cap')),
        tick_batch_size: Number(data.get('tick_batch_size')),
        dao_review_hour: String(data.get('dao_review_hour') || ''),
        classroom_visits_enabled: form.elements.classroom_visits_enabled.checked ? 1 : 0,
        genesis_enabled: form.elements.genesis_enabled.checked ? 1 : 0,
        ...Object.fromEntries(GENESIS_NUMBER_FIELDS.map(name => [name, Number(data.get(name))])),
    };
}

async function onEnabledChange() {
    const wanted = els.enabled.checked;
    await withBusy(els.enabled, async () => {
        try {
            await saveSettings({ enabled: wanted ? 1 : 0 });
        } catch (error) {
            els.enabled.checked = !wanted;
            showToast(`开关保存失败：${errorMessage(error, '请稍后重试')}`, 'error');
        }
    });
}

async function onSettingsSubmit(event) {
    event.preventDefault();
    if (!els.settingsForm.reportValidity()) return;
    await withBusy(els.settingsForm.querySelector('[type="submit"]'), async () => {
        try {
            await saveSettings(settingsPayload(els.settingsForm));
        } catch (error) {
            showToast(`设置保存失败：${errorMessage(error, '输入已保留')}`, 'error', 5000);
        }
    });
}

async function onReseed() {
    await withBusy(els.reseedBtn, async () => {
        try {
            const data = await apiFetch(`${API_ROOT}/reseed`, { method: 'POST', body: {}, silent: true });
            showToast(`种子已同步：新增 ${data.report?.created?.length ?? 0} 位，共 ${data.report?.total ?? 0} 位`, 'success');
            await refreshQuietly();
        } catch (error) {
            showToast(`重置种子失败：${errorMessage(error, '请稍后重试')}`, 'error');
        }
    });
}

function bindToolbar() {
    els.enabled?.addEventListener('change', onEnabledChange);
    els.tickBtn?.addEventListener('click', () => withBusy(els.tickBtn, () => runEngine('/tick-now', {}, '跑一轮')));
    els.reviewBtn?.addEventListener('click', () => withBusy(els.reviewBtn, () => runEngine('/review-now', {}, '天道审核')));
    els.genesisBtn?.addEventListener('click', () => withBusy(els.genesisBtn, () => runEngine('/genesis-now', {}, '天道造人')));
    els.refreshBtn?.addEventListener('click', () => withBusy(els.refreshBtn, refreshQuietly));
    els.reseedBtn?.addEventListener('click', onReseed);
    els.settingsForm?.addEventListener('submit', onSettingsSubmit);
    els.grid.addEventListener('click', (event) => {
        const card = event.target.closest('[data-wanderer-id]');
        if (card) openDetail(Number(card.dataset.wandererId), card);
    });
}

/* ---------------------------------------------------------------- detail: overview tab */

function statsSection(detail) {
    const { stats, knowledge } = detail;
    const percent = Math.min(100, Math.round((knowledge.ratio || 0) * 100));
    const meter = createInsight('insight_meter', {
        title: '知识容量', value: knowledge.chars || 0, percent, unit: ' 字', zero: 'value',
        caption: `${knowledge.active_count} 条有效知识 · 容量 ${knowledge.capacity} 字（${percent}%）`,
    });
    const daily = createInsight('insight_bars', {
        title: '最近 7 日动作', layout: 'columns', limit: 7, zero: 'value', tone: 'primary',
        items: stats.daily.map(day => ({ label: `${Number(String(day.date).slice(8, 10))}日`, value: day.count })),
        caption: `累计 ${stats.total_actions} 次动作`,
    });
    const kinds = createInsight('insight_bars', {
        title: '动作分布（全部）', limit: 8, tone: 'info',
        items: stats.by_kind.map(item => ({ label: item.label, value: item.count })),
    });
    return el('div', { class: 'wd-stats' }, [meter, daily, kinds]);
}

function listOrEmpty(label, rows) {
    return rows.length ? createContent('list', { label, items: rows }) : emptyState(`暂无${label}`);
}

function actionRow(item) {
    const title = `${item.action_label}${item.content_preview ? ` · ${item.content_preview}` : ''}`;
    const detail = item.detail && typeof item.detail === 'object' ? item.detail : {};
    const reason = item.outcome === 'skipped' && detail.reason ? `原因：${detail.reason}` : '';
    const meta = [formatTime(item.created_at), item.outcome !== 'ok' ? item.outcome : '', reason, item.ai_calls ? `AI ${item.ai_calls} 次` : '', item.mood]
        .filter(Boolean).join(' · ');
    const href = safeHref(item.href);
    return href ? { title, meta, primary: { href } } : { title, meta };
}

function activityText(activity) {
    if (!activity || typeof activity !== 'object') return '';
    const parts = Object.entries(ACTIVITY_LABELS)
        .filter(([key]) => Number.isFinite(Number(activity[key])))
        .map(([key, label]) => `${label} ${activity[key]}`);
    return parts.length ? `近 24 小时：${parts.join('，')}` : '';
}

function reviewRow(item) {
    const changed = item.from_tier !== null && item.to_tier !== null && item.from_tier !== item.to_tier;
    const tierChange = changed ? ` · ${item.from_tier} → ${item.to_tier} 境` : '';
    const genesis = item.review_kind === 'genesis'
        ? [item.seed_summary?.archetype ? `原型：${item.seed_summary.archetype}` : '', activityText(item.activity)]
        : [];
    const meta = [item.reason, item.advice ? `建议：${item.advice}` : '', ...genesis, `${formatTime(item.created_at)} · ${item.created_by}`]
        .filter(Boolean).join(' ｜ ');
    return { title: `${item.review_kind_label} · ${item.verdict_label}${tierChange}`, meta };
}

function renderOverviewTab(detail) {
    const card = detail.card;
    const facts = el('div', { class: 'wd-detail__chips' }, [
        chip(card.status_label, STATUS_TONES[card.status] || 'neutral'),
        chip(`${card.tier_name} · 第 ${card.tier} 境`, 'info'),
        chip(`修为 ${card.cultivation_score}`, 'neutral'),
        chip(`警告 ${card.warnings_count}`, card.warnings_count ? 'danger' : 'neutral'),
        ...(card.promotion_requested ? [chip('申请升阶中', 'primary')] : []),
    ]);
    const intro = detail.profile.public_intro ? el('p', { class: 'wd-detail__intro', text: detail.profile.public_intro }) : null;
    detailEls.panels.overview.replaceChildren(...[
        facts, intro, statsSection(detail),
        el('h3', { class: 'wd-detail__section', text: '最近天道裁决' }), listOrEmpty('天道裁决', detail.reviews.map(reviewRow)),
        el('h3', { class: 'wd-detail__section', text: '最近动作' }), listOrEmpty('动作记录', detail.actions.map(actionRow)),
    ].filter(Boolean));
}

/* ---------------------------------------------------------------- detail: paged tabs */

function knowledgeRow(item) {
    const tags = (item.tags || []).slice(0, 4).join('、');
    const meta = [item.status === 'active' ? '有效' : '已遗忘', `${item.chars} 字`, `引用 ${item.use_count}`, item.source_kind, tags]
        .filter(Boolean).join(' · ');
    return { title: item.title || item.digest.slice(0, 40), meta: `${meta}｜${item.digest.slice(0, 160)}` };
}

const PAGED_TABS = {
    actions: { label: '行为记录', limit: PAGE_LIMIT, target: () => detailEls.panels.actions, toRow: actionRow },
    reviews: { label: '天道裁决', limit: PAGE_LIMIT, target: () => detailEls.panels.reviews, toRow: reviewRow },
    knowledge: { label: '知识条目', limit: KNOWLEDGE_LIMIT, target: () => detailEls.knowledge, toRow: knowledgeRow },
};

function pager(kind, meta) {
    const prev = button('上一页', { 'data-wd-page': 'prev' }, 'ghost');
    const next = button('下一页', { 'data-wd-page': 'next' }, 'ghost');
    prev.disabled = meta.page <= 1;
    next.disabled = meta.page >= meta.pages;
    const go = (page) => { state.pages[kind] = page; showPaged(kind); };
    prev.addEventListener('click', () => go(meta.page - 1));
    next.addEventListener('click', () => go(meta.page + 1));
    return el('div', { class: 'wd-pager', role: 'group', 'aria-label': `${PAGED_TABS[kind].label}分页` }, [
        prev, el('span', { class: 'wd-muted', text: `第 ${meta.page} / ${meta.pages} 页 · 共 ${meta.total} 条` }), next,
    ]);
}

async function showPaged(kind) {
    const spec = PAGED_TABS[kind];
    const target = spec.target();
    const serial = state.serial;
    target.setAttribute('aria-busy', 'true');
    try {
        const url = `${API_ROOT}/${state.detailId}/${kind}?page=${state.pages[kind]}&limit=${spec.limit}`;
        const data = await apiFetch(url, { silent: true });
        if (serial !== state.serial) return; // a different wanderer was opened meanwhile
        state.pages[kind] = data.page;
        target.replaceChildren(listOrEmpty(spec.label, data.items.map(spec.toRow)), pager(kind, data));
    } catch (error) {
        if (serial === state.serial) target.replaceChildren(emptyState(errorMessage(error, '读取失败'), 'error'));
    } finally {
        target.removeAttribute('aria-busy');
    }
}

/* ---------------------------------------------------------------- detail: persona tab */

function behaviorSwitches(behaviors) {
    return Object.keys(BEHAVIOR_LABELS).map(key => createForm('switch', {
        id: `wd-behavior-${key}`, name: `behavior:${key}`, label: BEHAVIOR_LABELS[key],
        checked: Boolean(Number(behaviors[key] ?? 1)),
    }));
}

function showPersonaError(message) {
    detailEls.personaError.textContent = message;
    detailEls.personaError.hidden = !message;
}

function fillPersonaForm(profile) {
    const form = detailEls.personaForm;
    form.elements.persona_prompt.value = profile.persona_prompt;
    form.elements.activity_level.value = profile.activity_level;
    form.elements.daily_action_budget.value = profile.daily_action_budget;
    for (const field of REPLY_POLICY_FIELDS) form.elements[field].value = profile.reply_policy?.[field] ?? '';
    form.elements.active_hours.value = JSON.stringify(profile.schedule?.active_hours || []);
    detailEls.behaviors.replaceChildren(...behaviorSwitches(profile.behaviors || {}));
    showPersonaError('');
}

function parseActiveHours(raw) {
    let hours;
    try {
        hours = JSON.parse(raw || '[]');
    } catch {
        throw new Error('活跃时段不是合法 JSON，示例：[[7,9],[21,24]]');
    }
    const validPair = (pair) => Array.isArray(pair) && pair.length === 2 && pair[0] < pair[1]
        && pair.every(hour => Number.isInteger(hour) && hour >= 0 && hour <= 24);
    if (!Array.isArray(hours) || !hours.every(validPair)) {
        throw new Error('活跃时段须为 [[开始,结束],…]，小时为 0–24 的整数且开始小于结束');
    }
    return hours;
}

function replyPolicyPatch(form, current) {
    const entries = REPLY_POLICY_FIELDS
        .map(field => [field, String(form.elements[field].value).trim()])
        .filter(([, raw]) => raw !== '')
        .map(([field, raw]) => [field, Number(raw)]);
    return { ...current, ...Object.fromEntries(entries) };
}

function personaPatch() {
    const form = detailEls.personaForm;
    const profile = state.detail.profile;
    const behaviors = Object.fromEntries(Object.keys(BEHAVIOR_LABELS)
        .map(key => [key, form.elements[`behavior:${key}`]?.checked ? 1 : 0]));
    return {
        persona_prompt: form.elements.persona_prompt.value,
        activity_level: Number(form.elements.activity_level.value),
        daily_action_budget: Number(form.elements.daily_action_budget.value),
        reply_policy_json: replyPolicyPatch(form, profile.reply_policy || {}),
        schedule_json: { ...(profile.schedule || {}), active_hours: parseActiveHours(form.elements.active_hours.value) },
        behaviors_json: { ...(profile.behaviors || {}), ...behaviors },
    };
}

async function submitPersona(event) {
    event.preventDefault();
    const form = detailEls.personaForm;
    if (!form.reportValidity()) return;
    let patch;
    try {
        patch = personaPatch();
    } catch (error) {
        showPersonaError(error.message);
        return;
    }
    showPersonaError('');
    await withBusy(form.querySelector('[type="submit"]'), async () => {
        try {
            const data = await apiFetch(`${API_ROOT}/${state.detailId}`, { method: 'PATCH', body: patch, silent: true });
            applyDetail(data.detail);
            showToast('人格与行为已保存', 'success');
        } catch (error) {
            showPersonaError(errorMessage(error, '保存失败，输入已保留'));
        }
    });
}

/* ---------------------------------------------------------------- detail: avatar (design §11) */

function fillAvatarForm(detail) {
    const avatar = detail.avatar || {};
    detailEls.avatarPreview.src = detail.card.avatar_url;
    detailEls.avatarPreview.alt = `${detail.card.display_name}的头像`;
    const suggestion = avatar.suggested_silhouette ? `，建议剪影：${silhouetteLabel(avatar, avatar.suggested_silhouette)}` : '';
    detailEls.avatarSource.textContent = `当前来源：${avatar.source_label || '几何'}${avatar.has_illustration ? '（有人设插画）' : ''}${suggestion}`;
    detailEls.avatarForm.elements.avatar_silhouette.value = avatar.silhouette || '';
}

function silhouetteLabel(avatar, key) {
    return (avatar.silhouettes || []).find(item => item.key === key)?.label || key;
}

async function patchAvatar(value, control, okMessage) {
    await withBusy(control, async () => {
        try {
            const data = await apiFetch(`${API_ROOT}/${state.detailId}`, {
                method: 'PATCH', body: { avatar_silhouette: value }, silent: true,
            });
            applyDetail(data.detail, { keepForm: true });
            showToast(okMessage, 'success');
        } catch (error) {
            showToast(errorMessage(error, '头像保存失败'), 'error', 5000);
        }
    });
}

function submitAvatar(event) {
    event.preventDefault();
    const value = detailEls.avatarForm.elements.avatar_silhouette.value;
    if (!value) {
        showToast('请先选择一个剪影；恢复人设插画请点「恢复默认」', 'warning');
        return;
    }
    patchAvatar(value, detailEls.avatarForm.querySelector('[type="submit"]'), '已改用剪影头像');
}

function onAvatarReset(event) {
    const control = event.target.closest('[data-wd-avatar-reset]');
    if (control && state.detail) patchAvatar('', control, '头像已恢复默认');
}

/* ---------------------------------------------------------------- detail: actions */

function actionButton(name) {
    return els.detailRoot.querySelector(`[data-wd-act="${name}"]`);
}

function syncActionButtons(card) {
    const dead = card.status === 'dead';
    const status = actionButton('status');
    const label = card.status === 'paused' ? '恢复' : '暂停';
    status.querySelector('.lq-btn__label').textContent = label;
    status.setAttribute('aria-label', label);
    for (const name of ['status', 'tier', 'reset-warnings', 'review', 'tick', 'wipe']) actionButton(name).hidden = dead;
    actionButton('revive').hidden = !dead;
}

function applyDetail(detail, { keepForm = false } = {}) {
    state.detail = detail;
    detailEls.title.textContent = detail.card.display_name;
    detailEls.subtitle.textContent = `${detail.card.archetype} · ${detail.card.slug}`;
    renderOverviewTab(detail);
    fillAvatarForm(detail);
    if (!keepForm) fillPersonaForm(detail.profile);
    syncActionButtons(detail.card);
    replaceCard(detail.card);
}

async function reloadDetail(options = {}) {
    const serial = state.serial;
    const data = await apiFetch(`${API_ROOT}/${state.detailId}`, { silent: true });
    if (serial === state.serial) applyDetail(data.detail, options);
}

async function postAction(path, body, control, okMessage) {
    return withBusy(control, async () => {
        try {
            const data = await apiFetch(`${API_ROOT}/${state.detailId}${path}`, { method: 'POST', body: body || {}, silent: true });
            showToast(okMessage, 'success');
            await reloadDetail({ keepForm: true });
            await refreshQuietly();
            return data;
        } catch (error) {
            showToast(errorMessage(error, '操作失败'), 'error', 5000);
            return null;
        }
    });
}

async function runEngineForDetail(control, path, body, label) {
    await withBusy(control, async () => {
        const accepted = await runEngine(path, body, label);
        if (!accepted) return;
        const detailId = state.detailId;
        window.setTimeout(async () => {
            if (state.detailId !== detailId) return;
            try {
                await reloadDetail({ keepForm: true });
            } catch (error) {
                showToast(`详情刷新失败：${errorMessage(error, '请稍后重试')}`, 'error');
            }
        }, MANUAL_RUN_REFRESH_MS);
    });
}

function toggleInline(node, open) {
    node.hidden = !open;
    if (open) node.querySelector('input, button')?.focus();
}

const DETAIL_ACTIONS = {
    status: (control) => {
        const next = state.detail?.card.status === 'paused' ? 'active' : 'paused';
        return postAction('/status', { status: next }, control, next === 'paused' ? '已暂停' : '已恢复');
    },
    tier: () => {
        detailEls.tierForm.elements.tier.value = state.detail?.card.tier ?? 0;
        detailEls.tierForm.elements.reason.value = '';
        toggleInline(detailEls.tierForm, true);
    },
    'reset-warnings': (control) => postAction('/reset-warnings', {}, control, '警告已清零'),
    review: (control) => runEngineForDetail(control, `/${state.detailId}/review-now`, {}, '天道审核'),
    tick: (control) => runEngineForDetail(control, '/tick-now', { wanderer_id: state.detailId }, '跑一轮'),
    wipe: () => toggleInline(detailEls.wipeConfirm, true),
    revive: (control) => postAction('/revive', {}, control, '散修已复活'),
};

async function confirmWipe(control) {
    const result = await postAction('/wipe', { confirm: true }, control, '已抹除，散修陨落');
    if (result) toggleInline(detailEls.wipeConfirm, false);
}

function onDetailClick(event) {
    if (!state.detail) return;
    const act = event.target.closest('[data-wd-act]');
    if (act && DETAIL_ACTIONS[act.dataset.wdAct]) {
        DETAIL_ACTIONS[act.dataset.wdAct](act);
        return;
    }
    const cancel = event.target.closest('[data-wd-cancel]');
    if (cancel) {
        toggleInline(cancel.dataset.wdCancel === 'tier' ? detailEls.tierForm : detailEls.wipeConfirm, false);
        return;
    }
    const confirm = event.target.closest('[data-wd-confirm="wipe"]');
    if (confirm) confirmWipe(confirm);
}

async function onTierSubmit(event) {
    event.preventDefault();
    const form = detailEls.tierForm;
    if (!form.reportValidity()) return;
    const body = { tier: Number(form.elements.tier.value), reason: form.elements.reason.value };
    const result = await postAction('/tier', body, form.querySelector('[type="submit"]'), '境界已调整');
    if (result) toggleInline(form, false);
}

function bindDetail() {
    els.detailRoot.addEventListener('click', onDetailClick);
    detailEls.tierForm.addEventListener('submit', onTierSubmit);
    detailEls.personaForm.addEventListener('submit', submitPersona);
    detailEls.avatarForm.addEventListener('submit', submitAvatar);
    detailEls.avatarForm.addEventListener('click', onAvatarReset);
    detailEls.close?.addEventListener('click', () => modal?.close('button'));
}

/* ---------------------------------------------------------------- modal lifecycle */

function onTabChange({ key }) {
    if (key === 'actions' || key === 'reviews') showPaged(key);
    if (key === 'persona') showPaged('knowledge');
}

function resetDetailPanels() {
    state.detail = null;
    state.pages = { actions: 1, reviews: 1, knowledge: 1 };
    detailEls.tierForm.hidden = true;
    detailEls.wipeConfirm.hidden = true;
    for (const panel of [...Object.values(detailEls.panels), detailEls.knowledge]) {
        panel.replaceChildren(el('p', { class: 'wd-muted', text: '正在读取…' }));
    }
}

function ensureModal() {
    modal ??= createDomainModal(els.detailRoot, {
        type: 'modal', surface: detailEls.surface, initialFocus: detailEls.close,
        onClose: () => { els.detailRoot.hidden = true; state.serial += 1; },
    });
    tabController ??= createTabs($('#wd-tabs', els.detailRoot), { onChange: onTabChange });
}

async function openDetail(id, trigger) {
    if (!Number.isInteger(id) || id <= 0) return;
    state.serial += 1;
    state.detailId = id;
    resetDetailPanels();
    const card = state.wanderers.find(item => item.id === id);
    detailEls.title.textContent = card?.display_name || '散修';
    detailEls.subtitle.textContent = card ? `${card.archetype} · ${card.slug}` : '';
    ensureModal();
    els.detailRoot.hidden = false;
    modal.open({ trigger });
    tabController.select('overview', { notify: false });
    try {
        await reloadDetail();
    } catch (error) {
        detailEls.panels.overview.replaceChildren(emptyState(errorMessage(error, '散修详情读取失败'), 'error'));
    }
}

/* ---------------------------------------------------------------- init */

function init() {
    if (!els.grid || !els.detailRoot) return;
    renderOverview(state.overview);
    bindToolbar();
    bindDetail();
}

init();
