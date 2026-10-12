/**
 * Blog moderation UI shared by the blog page and the review desk (plan §5).
 *
 * Every dialog is an LQ dialog (createDialog/openDialog + LQ.layer.close); the
 * banner is an LQ status alert. No private materials or motion here — layout
 * hooks only (blog-mod-*), styled in blog-paper.css / manage_blog_review.css.
 */
import { getLQ } from '/static/js/lq/index.js';

const LQ = getLQ();

export const REASON_OPTIONS = [
    ['spam', '广告或引流'],
    ['abuse', '攻击、骚扰或不友善'],
    ['privacy', '泄露个人隐私'],
    ['illegal', '违法违规'],
    ['misinformation', '不实信息'],
    ['off_topic', '与板块无关'],
    ['other', '其他'],
];
export const REVEAL_OPTIONS = [
    ['report', '处理举报'],
    ['safety', '安全风险'],
    ['discipline', '学业纪律'],
    ['other', '其他（请说明）'],
];
const ACTION_LABELS = {
    limit: '限制可见（仅作者可见）',
    remove: '移除',
    restore: '恢复公开',
    confirm: '确认当前处理，移出待办',
};
// Same patterns as blog_safety_service.PRIVACY_PATTERNS (plan §5.1).
const PRIVACY_PATTERNS = [
    /(?<!\d)1[3-9]\d{9}(?!\d)/,
    /(?<![\dXx])\d{17}[\dXx](?![\dXx])/,
    /(?:qq|扣扣)\s*[:：号]?\s*\d{5,11}/i,
    /(?:微信|vx|wx|v信)\s*[:：号]?\s*[a-z][-_a-z0-9]{5,19}/i,
];

export function detectPrivacy(text) {
    const value = String(text || '');
    return PRIVACY_PATTERNS.some((pattern) => pattern.test(value));
}

/** Ask before publishing text that looks like personal information. */
export async function confirmPrivacy(text) {
    if (!detectPrivacy(text)) return true;
    return LQ.confirm({
        title: '可能包含个人信息',
        message: '内容里像是有手机号、身份证号或社交账号。公开后能看到这条内容的人都会看到，确定继续吗？',
        confirmLabel: '仍然发布',
        cancelLabel: '返回修改',
    });
}

function element(tag, attrs = {}, text) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === false || value == null) continue;
        node.setAttribute(key, value === true ? '' : String(value));
    }
    if (text !== undefined) node.textContent = text;
    return node;
}

function fieldControl(field) {
    if (field.type === 'select') {
        const select = element('select', {
            name: field.name, required: field.required, 'data-lq-component': 'select', class: 'lq-select',
            'aria-label': field.label,
        });
        if (field.placeholder) select.append(element('option', { value: '' }, field.placeholder));
        for (const [value, label] of field.options || []) select.append(element('option', { value }, label));
        if (field.value !== undefined) select.value = field.value;
        return select;
    }
    const textarea = element('textarea', {
        name: field.name, required: field.required, rows: field.rows || 3, maxlength: field.maxlength,
        placeholder: field.placeholder || '', 'data-lq-component': 'textarea', class: 'lq-textarea',
    });
    textarea.value = field.value || '';
    return textarea;
}

/**
 * A small form in an LQ dialog. Resolves with the field values, or null when
 * dismissed. ``validate(values)`` may return a message to block submission.
 */
export async function openFormDialog({ title, description = '', fields, confirmLabel = '确定', danger = false, validate = null }) {
    const dialogs = await LQ.load('dialogs');
    const form = element('form', { class: 'blog-mod-form', novalidate: true });
    if (description) form.append(element('p', { class: 'blog-mod-form__lead' }, description));
    for (const field of fields) {
        const label = element('label', { class: 'blog-mod-form__field', 'data-blog-mod-field': field.name });
        label.append(element('span', { class: 'blog-mod-form__label' }, field.label));
        label.append(fieldControl(field));
        if (field.hint) label.append(element('span', { class: 'blog-mod-form__hint' }, field.hint));
        form.append(label);
    }
    const error = element('p', { class: 'blog-mod-form__error', role: 'alert', hidden: true });
    form.append(error);
    const footer = element('div', { class: 'blog-mod-form__actions' });
    const cancel = element('button', { type: 'button', 'data-lq-component': 'button', class: 'lq-btn lq-btn--sm lq-btn--glass', 'data-lq-dialog-close': true }, '取消');
    const submit = element('button', {
        type: 'button', 'data-lq-component': 'button',
        class: `lq-btn lq-btn--sm ${danger ? 'lq-btn--destructive' : 'lq-btn--prominent'}`,
    }, confirmLabel);
    footer.append(cancel, submit);
    const root = dialogs.createDialog({ title, body: form, footer, size: 'sm' });
    return new Promise((resolve) => {
        let settled = false;
        const finish = (value) => { if (!settled) { settled = true; resolve(value); } };
        const handle = dialogs.openDialog(root, { onClose: () => finish(null), onDestroy: () => finish(null) });
        const accept = async () => {
            const values = Object.fromEntries(new FormData(form).entries());
            const missing = fields.find((field) => field.required && !String(values[field.name] || '').trim());
            const message = missing ? `请填写${missing.label}` : (validate ? validate(values) : '');
            if (message) {
                error.textContent = message;
                error.hidden = false;
                return;
            }
            finish(values);
            await LQ.layer.close(handle, 'button');
        };
        submit.addEventListener('click', accept);
        form.addEventListener('submit', (event) => { event.preventDefault(); accept(); });
    });
}

/** Which actions a person may take on an item in this state. */
export function availableActions({ moderator, state }) {
    const isPublic = ['none', 'passed', 'flagged'].includes(state || 'none');
    if (!moderator) return isPublic ? ['limit'] : [];
    const actions = [];
    if (state !== 'limited') actions.push('limit');
    if (state !== 'removed') actions.push('remove');
    if (!['none', 'passed'].includes(state || 'none')) actions.push('restore');
    // Pending or appealed items need a real decision, not a confirmation.
    if (['flagged', 'limited', 'removed'].includes(state)) actions.push('confirm');
    return actions;
}

/** Ask a teacher how to handle an item. Resolves {action, reason_code, note} or null. */
export function moderationDialog({ moderator, state, subject = '这条内容' }) {
    const actions = availableActions({ moderator, state });
    if (!actions.length) return Promise.resolve(null);
    return openFormDialog({
        title: moderator ? `处理${subject}` : `紧急限制${subject}`,
        description: moderator
            ? '处理结果会通知作者；限制与移除需要选择原因，恢复需要写一句说明。'
            : '限制后只有作者能看到，博客管理员会在审核台复核。请选择原因。',
        fields: [
            { name: 'action', label: '处理方式', type: 'select', options: actions.map((key) => [key, ACTION_LABELS[key]]), value: actions[0], required: true },
            { name: 'reason_code', label: '原因', type: 'select', options: REASON_OPTIONS, placeholder: '请选择原因' },
            { name: 'note', label: '说明（作者会看到）', type: 'textarea', maxlength: 300, placeholder: '简要说明处理依据，避免复述违规原文' },
        ],
        confirmLabel: '确认处理',
        danger: true,
        validate: (values) => {
            if (['limit', 'remove'].includes(values.action) && !values.reason_code) return '请选择处理原因';
            if (values.action === 'restore' && !String(values.note || '').trim()) return '恢复内容请写一句说明';
            return '';
        },
    });
}

export async function appealDialog() {
    const values = await openFormDialog({
        title: '申诉',
        description: '每次处理只能申诉一次。博客管理员会尽快复核，结果会通过消息通知你。',
        fields: [{ name: 'reason', label: '申诉理由', type: 'textarea', maxlength: 300, rows: 4, required: true, placeholder: '说明你认为处理不当的原因（不超过 300 字）' }],
        confirmLabel: '提交申诉',
    });
    return values ? String(values.reason || '').trim() : null;
}

export function revealDialog() {
    return openFormDialog({
        title: '查看真实作者',
        description: '查看会写入审计记录，仅在确有必要时使用。作者不会收到通知。',
        fields: [
            { name: 'reason_kind', label: '查看原因', type: 'select', options: REVEAL_OPTIONS, required: true },
            { name: 'note', label: '补充说明', type: 'textarea', maxlength: 300 },
        ],
        confirmLabel: '确认查看',
        validate: (values) => (values.reason_kind === 'other' && !String(values.note || '').trim() ? '请说明查看原因' : ''),
    });
}

const BANNERS = {
    pending: { tone: 'info', title: '正在进行内容检查', body: '通常 1 分钟内完成，完成后自动对大家可见。' },
    limited: { tone: 'warning', title: '这篇帖子暂时仅你可见' },
    removed: { tone: 'danger', title: '这篇帖子已被移除，仅你可见' },
    appeal_pending: { tone: 'info', title: '申诉已提交', body: '博客管理员会尽快复核，结果会通过消息通知你。' },
};

/** LQ alert markup for the author's (or a moderator's) view of a post's state. */
export async function bannerMarkup(moderation, { isAuthor }) {
    const resubmitted = moderation?.state === 'pending' && moderation.resubmitted;
    const spec = resubmitted
        ? { tone: 'info', title: isAuthor ? '修改已提交，等待博客管理员复核' : '作者修改后重新提交', body: isAuthor ? '复核通过后会恢复公开，结果会通过消息通知你。' : '请在审核台复核修改后的内容。' }
        : BANNERS[moderation?.state];
    if (!spec) return '';
    const status = await LQ.load('status');
    const reason = moderation.reason_label ? `原因：${moderation.reason_label}。` : '';
    const note = moderation.note ? `${String(moderation.note).replace(/。$/, '')}。` : '';
    let body = spec.body || `${reason}${note}${isAuthor ? '修改后会重新检查。' : ''}`;
    let title = spec.title;
    if (!isAuthor && !resubmitted) {
        // A moderator sees the same state, worded for them.
        title = { pending: '等待自动检查', limited: '已限制可见', removed: '已移除', appeal_pending: '作者已申诉' }[moderation.state] || title;
        if (moderation.state === 'pending' || moderation.state === 'appeal_pending') body = `${reason}${note}` || '请在审核台处理。';
    }
    const canAppeal = isAuthor && moderation.appeal?.can_appeal;
    if (isAuthor && moderation.appeal?.used && !moderation.appeal?.pending && !canAppeal && spec.tone !== 'info') {
        body += '这次处理已申诉过。';
    }
    return status.statusMarkup('alert', {
        tone: spec.tone,
        title,
        body: body || title,
        announce: moderation.state === 'pending' ? 'polite' : 'off',
        action: canAppeal ? { label: '申诉', attrs: { 'data-blog-appeal': '' } } : undefined,
        attrs: { 'data-blog-moderation-alert': moderation.state },
    });
}
