/** Schedule data composed with shared LQ Table, Chip and Button primitives. */
import { createComponent } from './lq/components.js';
import { createTable } from './lq/tables.js';

const fields = [
    { key: 'version', label: '安排', rowHeader: true },
    { key: 'week', label: '周次' }, { key: 'weekday', label: '星期' },
    { key: 'sections', label: '节次' }, { key: 'classroom', label: '教室' },
];
const days = ['一', '二', '三', '四', '五', '六', '日'];
const sections = slot => [...new Set((slot.sections || []).map(Number).filter(Number.isFinite))].sort((a, b) => a - b);
function sectionLabel(values) {
    const runs = [];
    for (let i = 0; i < values.length; i++) {
        const start = values[i];
        while (values[i + 1] === values[i] + 1) i++;
        runs.push(start === values[i] ? String(start) : `${start}–${values[i]}`);
    }
    return runs.length ? `第${runs.join('、')}节` : '节次未指定';
}
function values(slot = {}) {
    return {
        week: slot.week ? `第${slot.week}周` : '周次未指定',
        weekday: days[Number(slot.weekday) - 1] ? `周${days[Number(slot.weekday) - 1]}` : '星期未指定',
        sections: sectionLabel(sections(slot)), classroom: slot.room || slot.classroom || '教室未指定',
    };
}

/** Local candidates and confirmed remote applications have distinct identities. */
export function groupChanges(drafts) {
    const groups = new Map();
    for (const draft of drafts) {
        // A rejected remote attempt may retain an application ID without a saved
        // detail. Only a confirmed saved pair establishes application membership.
        const remoteId = draft.status === 'pushed' && draft.remote_ttk_id && draft.remote_detail_id ? draft.remote_ttk_id : '';
        const kind = remoteId ? 'remote' : draft.status === 'pushed' ? 'unverified' : 'local';
        const key = JSON.stringify([draft.teacher_id, draft.year, draft.term,
            draft.teaching_class_id || `unknown:${draft.id}`, kind, remoteId || (kind === 'unverified' ? String(draft.id) : '')]);
        if (!groups.has(key)) groups.set(key, { key, kind, remoteId, drafts: [] });
        groups.get(key).drafts.push(draft);
    }
    return [...groups.values()];
}

export function createChangeComparison(draft) {
    const before = values(draft.original), after = values(draft.proposed), slots = {};
    const changed = key => key === 'classroom'
        ? before[key] !== after[key] || (draft.original?.room_id && draft.proposed?.room_id && draft.original.room_id !== draft.proposed.room_id)
        : before[key] !== after[key];
    const rows = ['original', 'proposed'].map(version => {
        const cells = { version: version === 'original' ? '原' : '新' };
        for (const { key } of fields.slice(1)) {
            const isChanged = version === 'proposed' && changed(key);
            const label = (version === 'original' ? before : after)[key];
            const chip = createComponent('chip', { label, kind: 'status', tone: isChanged ? 'danger' : 'neutral', size: 'sm' });
            // These are comparison values, not state indicators. Their table headers own meaning.
            chip.querySelector('.lq-chip__dot')?.remove();
            if (isChanged) chip.setAttribute('aria-label', `${label}，已更改`);
            slots[`cell:${version}:${key}`] = [chip]; cells[key] = '';
        }
        return { key: version, cells };
    });
    const shell = createTable('table', { id: `cse-change-${draft.id}`, caption: `${draft.course_name}原始与调整安排对照`, mode: 'matrix', density: 'dense', columns: fields, rows }, slots);
    shell.classList.add('cse-comparison');
    const table = shell.querySelector('table'); table.dataset.cseComparison = '';
    for (const row of table.tBodies[0].rows) {
        row.dataset.cseVersion = row.dataset.lqRowKey;
        fields.forEach(({ key }, index) => {
            row.cells[index].dataset.cseField = key;
            if (key !== 'version' && row.dataset.cseVersion === 'proposed' && changed(key)) row.cells[index].classList.add('is-changed');
        });
    }
    return shell;
}

export function createDetailButton(draft, kind, label) {
    const button = createComponent('button', { label, variant: 'glass', size: 'sm', attrs: {
        'data-cse-detail': kind, 'data-cse-draft-id': String(draft.id),
        'aria-haspopup': 'dialog', 'aria-expanded': 'false', 'data-lq-overflow-label': '',
    } });
    button.classList.add('cse-detail-chip');
    const viewport = button.querySelector('.lq-btn__label');
    viewport.dataset.lqOverflowViewport = '';
    const text = document.createElement('span'); text.dataset.lqOverflowText = ''; text.textContent = label;
    viewport.replaceChildren(text);
    return button;
}

export function changeStatus(draft) {
    return ({ pushed: ['教务已保存', 'success'], conflict: ['教务冲突', 'danger'], failed: ['保存失败', 'danger'], draft: ['平台草稿', 'neutral'] })[draft.status]
        || [draft.status_label || '待核对', 'neutral'];
}
