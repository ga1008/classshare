import { api, button, el, field, formPanel, onAction, panel, run, showControl, statusChip, notice } from './ui.js';

const base = '/moderation/reviews';
const form = document.querySelector('[data-review-filter]');
const list = document.querySelector('[data-review-list]');
const names = Object.fromEntries(Array.from(form.elements.status.options, (item) => [item.value, item.textContent]));
const categoryNames = { sexual: '色情内容', political: '政治敏感内容', drugs: '毒品相关内容' };
const offsets = [0];
let nextOffset = 0;
let request = 0;

async function load() {
    const serial = ++request;
    const params = new URLSearchParams({ status: form.elements.status.value, offset: offsets.at(-1) });
    const result = await api(base + '?' + params);
    if (serial !== request) return;
    const rows = result.items.map((item) => {
        const row = el('li', null, 'lq-row game-admin-row');
        const text = el('div', null, 'game-row-main');
        text.append(el('strong', item.title), el('small', [item.version, item.reason].filter(Boolean).join(' · '), 'game-muted'));
        row.append(text, statusChip(names[item.status] || item.status), button('查看审核', () => review(item.versionId)));
        return row;
    });
    list.replaceChildren(...(rows.length ? rows : [el('li', '暂无此状态的版本', 'game-empty-row')]));
    nextOffset = offsets.at(-1) + rows.length;
    showControl('next', result.hasMore && rows.length > 0);
    showControl('previous', offsets.length > 1);
}

async function review(id) {
    const path = base + '/' + encodeURIComponent(id);
    const item = await api(path);
    const body = el('div', null, 'game-form');
    const status = statusChip(names[item.status] || item.status);
    const reasons = el('p', item.reason || '');
    body.append(el('strong', item.title + ' · ' + item.version), status, reasons);
    if (item.aiReason && item.aiReason !== item.reason) body.append(el('p', 'AI 复核依据 · ' + item.aiReason));
    if (item.categories?.length) body.append(el('p', item.categories.map((name) => categoryNames[name] || name).join('、')));
    for (const finding of item.evidence || []) {
        body.append(el('p', [finding.file, finding.reason].filter(Boolean).join(' · ')));
    }
    const files = el('details');
    const summary = el('summary', '包内文件（' + (item.files || []).length + '）', 'lq-disclosure-trigger');
    summary.dataset.lqComponent = 'disclosure';
    const fileList = el('ul', null, 'game-review-files');
    for (const file of item.files || []) fileList.append(el('li', file.path + ' · ' + file.size + ' B'));
    files.append(summary, fileList);
    body.append(files);
    if (item.packageUrl) {
        const link = el('a', '下载待审 ZIP', 'lq-btn lq-btn--glass lq-btn--sm');
        link.dataset.lqComponent = 'button';
        link.href = item.packageUrl;
        body.append(link);
    }
    if (item.status === 'manual') {
        const actions = el('div', null, 'game-actions');
        for (const [decision, label] of [['approve', '审核通过'], ['reject', '退回作者']]) {
            const decide = async () => {
                const reason = field('审核意见', 'reason');
                const input = reason.querySelector('input');
                input.required = true;
                input.maxLength = 400;
                await formPanel(label, reason, '确认' + label, async (data) => {
                    const result = await api(path + '/decision', {
                        method: 'POST',
                        body: { decision, reason: String(data.get('reason')).trim(), expectedDigest: item.digest },
                    });
                    status.replaceWith(statusChip(names[result.status] || result.status));
                    reasons.textContent = result.reason;
                    actions.remove();
                    notice('已保存审核决定');
                    run(null, load);
                }, { danger: decision === 'reject' });
            };
            actions.append(button(label, decide, decision === 'approve' ? 'prominent' : 'destructive'));
        }
        body.append(actions);
    }
    await panel('版本内容审核', body);
}

form.addEventListener('submit', (event) => {
    event.preventDefault();
    offsets.splice(0, offsets.length, 0);
    run(event.submitter, load);
});
onAction({
    next: () => { offsets.push(nextOffset); return load(); },
    previous: () => { if (offsets.length > 1) offsets.pop(); return load(); },
});
run(null, load);
const linked = new URLSearchParams(location.search).get('version');
if (linked) run(null, () => review(linked));
