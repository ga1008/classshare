// 知识树侧栏：节点详情（只读）、教师编辑表单、比重设置与合计提示、结构操作按钮。
// 只负责构造 DOM；所有行为通过 data-kt-* 属性交给 session_knowledge_tree.js 统一处理。
import { createComponent } from '../lq/components.js';
import { createDomainButton } from '../lq/domain-controls.js';
import { input, textarea } from '../lq/forms.js';
import { canAddChild, leafWeight, sharePercent } from './model.js';

export const LEVEL_NAMES = ['课次主题', '大知识点', '细分知识', '基础单元'];
export const DETAIL_SECTIONS = [
    ['detail', '详细介绍', 600], ['example', '案例', 300], ['question', '思考', 200],
    ['prerequisite', '前置关联', 160], ['extension', '后续关联', 160],
];

const el = (tag, className = '', text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};

function section(title, text) {
    const box = el('section', 'kt-side__section');
    box.append(el('h4', '', title), el('p', '', text));
    return box;
}

function relationButton(info, prefix) {
    const button = createDomainButton({ label: `${prefix}${info.node.label}`, variant: 'soft', attrs: { 'data-kt-goto': info.node.id } });
    button.classList.add('kt-relation');
    return button;
}

/** "比重合计 N%" with a one-click fix when it is not 100. */
export function weightStatus(total, { canEdit, busy }) {
    const box = el('div', 'kt-weight-status');
    box.dataset.state = total === 100 ? 'ok' : 'off';
    const label = total === 100 ? '比重合计 100%' : `比重合计 ${total}%，未满足 100%`;
    box.append(createComponent('chip', { label, kind: 'status', size: 'sm', tone: total === 100 ? 'success' : 'warning' }));
    if (total !== 100 && canEdit) {
        const fix = createDomainButton({ label: '按比例调整为 100%', variant: 'soft', attrs: { 'data-kt-normalize': '' } });
        fix.disabled = Boolean(busy);
        box.append(fix);
    }
    return box;
}

function actionBar(info, { busy, siblings, position }) {
    const bar = el('div', 'kt-side__actions');
    const add = (label, attrs, disabled = false, variant = 'glass') => {
        const button = createDomainButton({ label, variant, attrs });
        button.disabled = Boolean(busy) || disabled;
        bar.append(button);
        return button;
    };
    add('编辑内容', { 'data-kt-edit': info.node.id, 'aria-keyshortcuts': 'F2' });
    if (canAddChild(info)) add(info.level === 0 ? '添加大知识点' : '添加下级', { 'data-kt-add-child': info.node.id, 'aria-keyshortcuts': 'Tab' });
    if (info.level > 0) add('添加同级', { 'data-kt-add-sibling': info.node.id });
    if (info.level > 0) {
        const lastMajor = info.level === 1 && siblings.length <= 1;
        const remove = add('删除', { 'data-kt-delete': info.node.id, 'aria-keyshortcuts': 'Delete' }, lastMajor, 'destructive');
        if (lastMajor) remove.title = '至少保留一个大知识点';
    }
    if (info.parent && siblings.length > 1) {
        add('上移', { 'data-kt-move': '-1' }, position <= 0, 'ghost');
        add('下移', { 'data-kt-move': '1' }, position >= siblings.length - 1, 'ghost');
    }
    return bar;
}

function weightEditor(info, { uid, busy, total }) {
    const box = el('div', 'kt-side__weight');
    box.append(input({
        id: `${uid}-weight`, label: '比重', type: 'number', size: 'sm', value: String(leafWeight(info.node)),
        min: '0', max: '100', step: '1', inputMode: 'numeric', suffix: '%', disabled: Boolean(busy),
        help: '修改后自动保存，本课进度按比重合计计算。',
        attrs: { 'data-kt-weight': info.node.id },
    }), weightStatus(total, { canEdit: true, busy }));
    return box;
}

/** Editing form for one node; the root edits the session title and summary. */
export function editForm(info, { uid, busy }) {
    const form = el('form', 'kt-edit');
    form.dataset.ktEditForm = info.node.id;
    const isRoot = info.level === 0;
    form.append(input({
        id: `${uid}-label`, label: isRoot ? '课次主题' : '名称', value: info.node.label || '', size: 'sm',
        required: true, maxLength: isRoot ? 60 : 40, autocomplete: 'off', attrs: { 'data-kt-field': 'label' },
    }));
    const fields = isRoot ? [['detail', '本课概述', 200]] : DETAIL_SECTIONS;
    for (const [key, title, limit] of fields) {
        const value = isRoot ? (info.node.summary || '') : (info.node[key] || '');
        form.append(textarea({
            id: `${uid}-${key}`, label: title, value, size: 'sm', rows: key === 'detail' ? 4 : 2, autoGrow: true, count: true,
            maxLength: limit, attrs: { 'data-kt-field': key },
        }));
    }
    const actions = el('div', 'kt-edit__actions');
    const save = createDomainButton({ label: '保存', variant: 'prominent', attrs: { 'data-kt-edit-save': '' } });
    save.type = 'submit';
    save.disabled = Boolean(busy);
    actions.append(save, createDomainButton({ label: '取消', variant: 'ghost', attrs: { 'data-kt-edit-cancel': '' } }),
        el('span', 'kt-side__hint', '名称框内 Enter 保存 · Esc 取消'));
    form.append(actions);
    return form;
}

/** Values the form would save, keyed like the PATCH body. */
export function readForm(form) {
    const values = {};
    for (const control of form.querySelectorAll('[data-kt-field]')) values[control.dataset.ktField] = control.value;
    return values;
}

/**
 * Render the side panel for ``info``.
 * ctx: {index, data, shares, done, canEdit, busy, editing, uid, total}
 */
export function renderSide(side, info, ctx) {
    side.replaceChildren();
    const { data, shares, done, canEdit, busy, editing, uid, total, index } = ctx;
    const headRow = el('div', 'kt-side__head');
    headRow.append(
        el('span', 'kt-side__kicker', info.isLeaf ? `${LEVEL_NAMES[3]} · 比重 ${leafWeight(info.node)}%` : LEVEL_NAMES[Math.min(info.level, 3)]),
        createComponent('button', { icon: 'x', variant: 'ghost', size: 'sm', attrs: { 'aria-label': '收起详情', 'data-kt-side-close': '' } }),
    );
    side.append(headRow);
    if (canEdit && editing) {
        side.append(el('h3', 'kt-side__title', info.level === 0 ? '编辑课次主题' : '编辑知识点'), editForm(info, { uid, busy }));
        return;
    }
    side.append(el('h3', 'kt-side__title', info.node.label || '本次课'));
    const siblings = info.parent ? info.parent.children : [];
    const position = siblings.findIndex(item => item.id === info.node.id);
    if (info.isLeaf) {
        const complete = done.has(info.node.id);
        const row = el('div', 'kt-side__state');
        row.append(createComponent('chip', { label: complete ? '已完成' : '未完成', kind: 'status', size: 'sm', tone: complete ? 'success' : 'neutral' }));
        if (canEdit) {
            const toggle = createDomainButton({ label: complete ? '标记为未完成' : '标记为已完成', variant: complete ? 'glass' : 'prominent', attrs: { 'data-kt-toggle': info.node.id } });
            toggle.disabled = Boolean(busy);
            row.append(toggle);
        }
        side.append(row);
        if (canEdit) side.append(weightEditor(info, { uid, busy, total }));
    } else if (info.level > 0) {
        const share = shares.get(info.node.id);
        side.append(el('p', 'kt-side__meta', `包含比重 ${share?.total || 0}% · 已完成 ${sharePercent(share)}%`));
    }
    if (canEdit) side.append(actionBar(info, { busy, siblings, position }));
    if (info.level === 0) {
        if (canEdit) side.append(weightStatus(total, { canEdit, busy }));
        if (info.node.summary) side.append(section('本课概述', info.node.summary));
        const meta = [
            data?.source_label ? `依据文档：${data.source_label}` : '',
            data?.generated_at ? `生成时间：${String(data.generated_at).replace('T', ' ')}` : '',
            data?.tree?.edited_at ? `最近修改：${String(data.tree.edited_at).replace('T', ' ')}` : '',
            data?.completed_at ? `完成时间：${String(data.completed_at).replace('T', ' ')}` : '',
        ].filter(Boolean);
        if (meta.length) side.append(section('来源', meta.join('\n')));
    }
    for (const [key, title] of DETAIL_SECTIONS) if (info.node[key]) side.append(section(title, info.node[key]));
    const list = el('div', 'kt-side__relations');
    if (info.parent) list.append(relationButton(info.parent, '上级：'));
    for (const child of info.children) list.append(relationButton(index.map.get(child.id), '下级：'));
    if (position > 0) list.append(relationButton(index.map.get(siblings[position - 1].id), '前：'));
    if (position >= 0 && position < siblings.length - 1) list.append(relationButton(index.map.get(siblings[position + 1].id), '后：'));
    if (list.childElementCount) {
        const relations = el('section', 'kt-side__section');
        relations.append(el('h4', '', '上下与前后关联'), list);
        side.append(relations);
    }
    if (canEdit) side.append(el('p', 'kt-side__hint', 'Tab 添加下级 · F2 编辑 · Delete 删除 · Alt+方向键调整顺序 · 按住节点拖动换位置'));
}
