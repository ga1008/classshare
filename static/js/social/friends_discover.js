/**
 * 好友浮窗推荐分组与「新朋友」二层内容 (social doc §12.1–§12.3, §12.5/§12.6):
 *   - 境界解锁阶梯：LQ Steps（已解锁=complete、当前=current）+ 下一境界提示（Alert）；
 *   - 推荐：「互补搭档」「志趣相近」两组，各 ≤5 张，只显示接口给的 hint；
 *   - AI 找人：scope.ai_search 时出现，示例 Chip、条件摘要、理由、今日剩余次数。
 * 后端未就绪（404）时：阶梯与 AI 找人隐藏、搜索照旧，推荐显示“暂未开放”。
 * 界面不解释匹配依据，只有一句中性说明 NEUTRAL_LINE。
 */
import { createComponent } from '../lq/components.js';
import { createContent } from '../lq/content.js';
import { createForm } from '../lq/forms.js';
import { createShell } from '../lq/shells.js';
import { createStatus } from '../lq/status.js';
import { socialApi, errorText } from './social_api.js';
import { list, lockedReason, relationAction, userRow } from './social_people.js';
import { normalizeCardIdentity } from './user_card_trigger.js';
import { el, emptyState, lqButton } from './social_dom.js';

export const NEUTRAL_LINE = '每天根据大家的学习与互动特点更新';
export const TIER0_TITLE = '突破第一境界后即可添加好友';
export const TIER0_BODY = '别人仍可以添加你，收到的申请随时可以通过。';
const REC_LIMIT = 5;
const STEP_ICONS = Object.freeze({ complete: 'check', current: 'user', upcoming: 'lock' });
const AI_PROMPT_MAX = 200;
const AI_EXAMPLES = Object.freeze(['信息学院喜欢打篮球的大二同学', '和我同课程、境界相近的同学', '喜欢写博客的散修']);
const CONDITION_LABELS = Object.freeze({
    school: '学校', college: '学院', major: '专业', grade: '年级', class_name: '班级', course_name: '课程', courses: '课程',
    interests: '兴趣', keywords: '关键词', tier: '境界', tier_range: '境界', tier_min: '最低境界', tier_max: '最高境界',
    role: '角色', is_wanderer: '散修', name: '姓名',
});

/* ---------------------------------------------------------------- scope */

let scopePromise = null;

/**
 * GET /scope, cached until `force`. Resolves null when the server has no
 * scope endpoint yet (404) so the UI keeps the round-one behaviour.
 * @param {boolean} [force]
 * @returns {Promise<Record<string, any>|null>}
 */
export function loadScope(force = false) {
    if (force || !scopePromise) {
        scopePromise = socialApi.scope().catch((failure) => {
            scopePromise = null;
            if (failure?.status === 404) return null;
            throw failure;
        });
    }
    return scopePromise;
}

/** @param {Record<string, any>|null} scope */
export const isLocked = (scope) => Boolean(scope) && !scope.is_teacher && scope.can_add === false;

function currentTier(scope, ladder) {
    if (scope.is_teacher) return ladder[ladder.length - 1].tier;
    const reached = ladder.filter((step) => step.tier <= Number(scope.tier || 0));
    return (reached.length ? reached[reached.length - 1] : ladder[0]).tier;
}

/**
 * 境界解锁阶梯 strip. Returns null without a usable ladder.
 * @param {Record<string, any>|null} scope
 * @returns {HTMLElement|null}
 */
export function renderLadder(scope) {
    const ladder = (Array.isArray(scope?.ladder) ? scope.ladder : [])
        .filter((step) => Number.isInteger(Number(step?.tier)))
        .map((step) => ({ ...step, tier: Number(step.tier) }))
        .sort((a, b) => a.tier - b.tier);
    if (!ladder.length) return null;
    const current = currentTier(scope, ladder);
    const steps = createShell('steps', {
        id: 'social-scope-ladder', label: '境界解锁阶梯',
        // Icons instead of step numbers: 1..6 would read as 境界 numbers (凡人 is tier 0).
        items: ladder.map((step) => {
            const state = step.tier === current ? 'current' : (step.unlocked ? 'complete' : 'upcoming');
            return { key: `tier${step.tier}`, label: step.tier_name || `第 ${step.tier} 境`, state, icon: STEP_ICONS[state] };
        }),
    });
    const tierName = scope.tier_name || '凡人';
    // Tier 0 only describes receiving requests, not a range you can add from.
    const unlocked = ladder.filter((step) => step.tier > 0 && step.unlocked && step.label).map((step) => step.label);
    let summary = `当前「${tierName}」还不能主动添加好友。`;
    if (isLocked(scope) && scope.next_unlock) summary = `突破至「${scope.next_unlock.tier_name || ''}」即可添加：${scope.next_unlock.label || ''}`;
    if (scope.is_teacher) summary = '教师不受境界限制，可以添加平台上的任何人。';
    else if (unlocked.length) summary = `当前「${tierName}」可以添加：${unlocked.join('、')}`;
    // Tier 0 already shows the locked Alert; the summary line names the next unlock there.
    const next = scope.next_unlock && !scope.is_teacher && !isLocked(scope) ? createStatus('alert', {
        tone: 'info',
        title: `下一境界「${scope.next_unlock.tier_name || ''}」解锁`,
        body: scope.next_unlock.label || '更大的添加范围',
    }) : null;
    return el('section', { class: 'social-ladder', 'aria-label': '境界解锁阶梯' }, [
        steps, el('p', { class: 'social-hint social-ladder__summary', text: summary }), next,
    ]);
}

/** Tier-0 encouragement (no search box, no list). */
export function lockedState() {
    return createStatus('alert', { tone: 'info', title: TIER0_TITLE, body: TIER0_BODY, attrs: { 'data-social-locked': '' } });
}

/* ---------------------------------------------------------------- recommendations */

function recommendCard(user) {
    const identity = normalizeCardIdentity(user.identity);
    const meta = [user.role_label, user.org_label, user.is_wanderer ? '散修' : ''].filter(Boolean).join(' · ');
    const reason = lockedReason(user);
    const body = el('div', { class: 'social-rec__body' }, [
        el('span', { class: 'social-avatar' }, [createComponent('avatar', { name: String(user.name || '?'), src: user.avatar_url || null, size: 40 })]),
        el('div', { class: 'social-rec__copy' }, [
            user.hint ? el('p', { class: 'social-rec__hint', text: user.hint }) : null,
            reason ? el('p', { class: 'social-rec__hint', text: reason }) : null,
        ]),
        createComponent('button', { size: 'sm', ...relationAction(user) }),
    ]);
    const props = { title: user.name || '用户', meta, variant: 'flat' };
    if (identity) props.primary = { attrs: { 'data-user-card': identity, 'aria-haspopup': 'dialog' } };
    const card = createContent('card', props, { body: [body] });
    card.dataset.match = user.match || '';
    return el('li', { class: 'social-rec' }, [card]);
}

function recommendGroup(title, items) {
    const shown = (Array.isArray(items) ? items : []).slice(0, REC_LIMIT);
    if (!shown.length) return null;
    return el('section', { class: 'social-rec-group', 'aria-label': title }, [
        el('h3', { class: 'social-section-title', text: title }),
        el('ul', { class: 'social-rec-grid', role: 'list' }, shown.map(recommendCard)),
    ]);
}

/** Pure content slot; empty recommendation groups never occupy the friends page. */
export function recommendationContent(data) {
    if (!data || data.locked) return [];
    const groups = [recommendGroup('互补搭档', data.complement), recommendGroup('志趣相近', data.similar)].filter(Boolean);
    return groups.length ? [el('p', { class: 'social-hint social-rec__lead', text: NEUTRAL_LINE }), ...groups] : [];
}

/* ---------------------------------------------------------------- AI 找人 */

function conditionChips(conditions) {
    if (!conditions || typeof conditions !== 'object') return [];
    if (typeof conditions.summary === 'string' && conditions.summary.trim()) {
        return [createComponent('chip', { label: conditions.summary.trim(), tone: 'info', size: 'sm' })];
    }
    return Object.entries(conditions).flatMap(([key, value]) => {
        if (value === null || value === undefined || value === '' || value === false || (Array.isArray(value) && !value.length)) return [];
        if (typeof value === 'object' && !Array.isArray(value)) return [];
        const text = Array.isArray(value) ? value.join('、') : (value === true ? '是' : String(value));
        return [createComponent('chip', { label: `${CONDITION_LABELS[key] || key}：${text}`, tone: 'neutral', size: 'sm' })];
    });
}

function aiFailure(failure) {
    if (failure?.status === 429) return ['今天的 AI 找人次数已用完', errorText(failure, '明天再来试试吧。')];
    if (failure?.status === 403) return ['AI 找人尚未解锁', errorText(failure, '突破到第五境界后即可使用。')];
    if (failure?.status === 404) return ['AI 找人暂未开放', '稍后再来试试。'];
    return ['AI 找人暂时不可用', errorText(failure)];
}

function aiResults(data) {
    const items = data?.items || [];
    const remaining = Number.isFinite(Number(data?.remaining_today)) && data?.remaining_today !== null
        ? `今天还可以使用 ${Number(data.remaining_today)} 次` : '';
    const chips = conditionChips(data?.conditions);
    return [
        chips.length ? el('div', { class: 'social-ai__conditions', 'aria-label': '理解的条件' }, [el('span', { class: 'social-hint', text: '按这些条件查找：' }), ...chips]) : null,
        items.length
            ? list('AI 找人结果', items.map((user) => userRow(user, [user.reason || ''])))
            : emptyState('没有找到符合描述的人', '换个说法，或放宽一些条件试试。', 'no-results'),
        remaining ? el('p', { class: 'social-hint', text: remaining }) : null,
    ].filter(Boolean);
}

/**
 * The AI 找人 block (only mounted when scope.ai_search).
 * @returns {HTMLElement}
 */
export function createAiSearch() {
    const field = createForm('input', {
        id: 'social-ai-search', label: '描述想认识的人', name: 'prompt', maxLength: AI_PROMPT_MAX,
        placeholder: `例如：${AI_EXAMPLES[0]}`,
    });
    const input = field.querySelector('input');
    const submit = lqButton({ label: '找人', icon: 'search', variant: 'prominent', type: 'submit' });
    const examples = el('div', { class: 'social-ai__examples', role: 'group', 'aria-label': '示例' }, AI_EXAMPLES.map((text) => (
        createComponent('chip', { kind: 'filter', label: text, size: 'sm', attrs: { 'data-ai-example': text } })
    )));
    const out = el('div', { class: 'social-ai__results', 'aria-live': 'polite' });
    const form = el('form', { class: 'social-ai__form', novalidate: true }, [field, submit]);
    let abort = null;

    examples.addEventListener('click', (event) => {
        const chip = event.target.closest?.('[data-ai-example]');
        if (!chip) return;
        input.value = chip.dataset.aiExample;
        input.focus();
    });

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const prompt = input.value.trim();
        if (!prompt) { input.focus(); return; }
        abort?.abort();
        abort = new AbortController();
        submit.disabled = true;
        out.setAttribute('aria-busy', 'true');
        out.replaceChildren(el('p', { class: 'social-hint', role: 'status', text: 'AI 正在理解你的描述并查找…' }));
        try {
            out.replaceChildren(...aiResults(await socialApi.aiSearch(prompt, abort.signal)));
        } catch (failure) {
            if (failure?.name === 'AbortError') return;
            const [title, body] = aiFailure(failure);
            out.replaceChildren(emptyState(title, body, 'error'));
        } finally {
            submit.disabled = false;
            out.removeAttribute('aria-busy');
        }
    });

    return el('section', { class: 'social-ai', 'aria-label': 'AI 找人' }, [
        el('h3', { class: 'social-section-title', text: 'AI 找人' }),
        el('p', { class: 'social-hint', text: '用一句话描述想认识的人，只在公开资料里查找。' }),
        form, examples, out,
    ]);
}
