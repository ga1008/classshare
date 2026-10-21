// 知识树浮窗的静态外壳：LQ Dialog、工具栏、通知、空态、画布图层与侧栏容器，
// 以及只依赖服务端状态的头部/进度/通知/空态渲染。画布行为在 session_knowledge_tree.js。
import { createDialog } from '../lq/dialogs.js';
import { createComponent } from '../lq/components.js';
import { createStatus } from '../lq/status.js';
import { createDomainButton, adoptDomainContentSlot, adoptDomainControl } from '../lq/domain-controls.js';
import { weightStatus } from './side.js';

export const KNOWLEDGE_TREE_THEMES = Object.freeze([
    { key: 'glass', label: '玻璃' },
    { key: 'ink', label: '墨线' },
    { key: 'ribbon', label: '彩带' },
    { key: 'blueprint', label: '蓝图' },
    { key: 'paper', label: '暖纸' },
]);
export const DIRECTIONS = Object.freeze([{ key: 'lr', label: '由左向右' }, { key: 'bt', label: '由下向上' }]);
const LIFECYCLE = {
    none: { label: '未生成', tone: 'neutral' },
    not_started: { label: '未开始', tone: 'neutral' },
    in_progress: { label: '进行中', tone: 'warning' },
    completed: { label: '已完成', tone: 'success' },
};

const PREFS_KEY = 'lanshare:knowledge-tree:prefs';

export function readPrefs() {
    try {
        const raw = JSON.parse(window.localStorage.getItem(PREFS_KEY) || '{}');
        return {
            direction: DIRECTIONS.some(item => item.key === raw.direction) ? raw.direction : 'lr',
            theme: KNOWLEDGE_TREE_THEMES.some(item => item.key === raw.theme) ? raw.theme : 'glass',
        };
    } catch { return { direction: 'lr', theme: 'glass' }; }
}
export function writePrefs(prefs) {
    try { window.localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* storage unavailable */ }
}


export const lifecycleLabel = state => (LIFECYCLE[state] || LIFECYCLE.none).label;

export const el = (tag, className = '', text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};

function group(label, className, children) {
    const box = el('div', className);
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', label);
    box.append(...children);
    return box;
}

/** Build the dialog and every static element the controller works with. */
export function buildChrome({ canEdit, prefs, session }) {
    const shell = el('div', 'kt-shell');
    shell.dataset.ktTheme = prefs.theme;
    shell.dataset.ktDirection = prefs.direction;
    if (canEdit) shell.dataset.ktEditable = 'true';

    const progressRingSlot = el('span', 'kt-progress__ring');
    const progressTitle = el('strong', 'kt-progress__title');
    const progressMeta = el('span', 'kt-progress__meta');
    const progressText = el('div', 'kt-progress__text');
    progressText.append(progressTitle, progressMeta);
    const progressBox = el('div', 'kt-progress');
    progressBox.append(progressRingSlot, progressText);

    const directionButtons = DIRECTIONS.map(item => createDomainButton({ label: item.label, variant: 'ghost', attrs: { 'data-kt-direction': item.key } }));
    const themeSelect = el('select', 'kt-theme-select');
    themeSelect.setAttribute('data-lq-dropdown', '');
    themeSelect.setAttribute('aria-label', '知识树主题');
    for (const theme of KNOWLEDGE_TREE_THEMES) {
        const option = el('option', '', `主题：${theme.label}`);
        option.value = theme.key;
        themeSelect.append(option);
    }
    themeSelect.value = prefs.theme;
    adoptDomainControl(themeSelect, { kind: 'select' });
    const completeAllBtn = createDomainButton({ label: '全部完成', variant: 'glass', attrs: { 'data-kt-bulk': 'done' } });
    const clearAllBtn = createDomainButton({ label: '全部取消', variant: 'glass', attrs: { 'data-kt-bulk': 'clear' } });
    const zoomValue = el('span', 'kt-zoom__value', '100%');
    zoomValue.setAttribute('aria-live', 'polite');
    const controls = el('div', 'kt-controls');
    controls.append(
        group('缩放', 'kt-zoom', [
            createDomainButton({ label: '缩小', variant: 'ghost', attrs: { 'data-kt-zoom': 'out' } }),
            zoomValue,
            createDomainButton({ label: '放大', variant: 'ghost', attrs: { 'data-kt-zoom': 'in' } }),
            createDomainButton({ label: '适应', variant: 'ghost', attrs: { 'data-kt-zoom': 'fit' } }),
        ]),
        group('展开与收起', 'kt-zoom', [
            createDomainButton({ label: '全部展开', variant: 'ghost', attrs: { 'data-kt-fold-all': 'open' } }),
            createDomainButton({ label: '只看大知识点', variant: 'ghost', attrs: { 'data-kt-fold-all': 'major' } }),
        ]),
        group('知识树方向', 'kt-segment', directionButtons),
        themeSelect,
    );
    if (canEdit) controls.append(completeAllBtn, clearAllBtn);
    const toolbar = el('div', 'kt-toolbar');
    toolbar.append(progressBox, controls);

    const viewport = el('div', 'kt-viewport');
    viewport.tabIndex = 0;
    viewport.setAttribute('aria-label', canEdit
        ? '知识树画布：滚轮以指针为中心缩放，鼠标左键拖动空白处平移；Tab 添加下级、F2 编辑、Delete 删除、Ctrl+Z 撤销、Ctrl+S 保存'
        : '知识树画布：滚轮以指针为中心缩放，鼠标左键拖动空白处平移，+/− 键缩放，0 键适应');
    const canvas = el('div', 'kt-canvas');
    adoptDomainContentSlot(canvas);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'kt-edges');
    svg.setAttribute('aria-hidden', 'true');
    const nodesLayer = el('div', 'kt-nodes');
    nodesLayer.setAttribute('role', 'tree');
    nodesLayer.setAttribute('aria-label', '本次课知识树');
    const foldLayer = el('div', 'kt-folds');
    canvas.append(svg, nodesLayer, foldLayer);
    const sizer = el('div', 'kt-sizer');
    sizer.append(canvas);
    viewport.append(sizer);
    const empty = el('div', 'kt-empty');
    const side = el('aside', 'kt-side');
    side.hidden = true;
    side.setAttribute('aria-label', '知识点详情');
    const hint = el('div', 'kt-hint', canEdit
        ? '左键拖动空白处平移 · 滚轮缩放 · Ctrl+Z 撤销 · Ctrl+S 保存 · 点圆圈标记完成'
        : '滚轮缩放 · 左键拖动空白处平移 · 点圆点展开/收起 · 双击空白处适应');
    hint.setAttribute('aria-hidden', 'true');
    const notice = el('div', 'kt-notice');
    const main = el('div', 'kt-main');
    main.append(viewport, empty, side, hint);
    shell.append(toolbar, notice, main);

    const root = createDialog({ title: '知识树', body: shell, size: 'full', closeLabel: '关闭知识树' });
    root.querySelector('.lq-dialog__surface').classList.add('kt-dialog');
    const head = root.querySelector('.lq-dialog__head');
    head.querySelector('h2').append(el('span', 'kt-subtitle', session.detail_title || session.title || ''));
    const generateBtn = createDomainButton({ label: '生成知识树', variant: 'prominent', attrs: { 'data-kt-generate': '' } });
    const saveBtn = createDomainButton({ label: '保存', variant: 'prominent', attrs: { 'data-kt-save': '', 'aria-keyshortcuts': 'Control+S Meta+S' } });
    const undoBtn = createDomainButton({ label: '撤销', variant: 'glass', attrs: { 'data-kt-undo': '', 'aria-keyshortcuts': 'Control+Z Meta+Z' } });
    const saveStatus = el('span', 'kt-save-status');
    saveStatus.dataset.ktSaveStatus = '';
    saveStatus.setAttribute('role', 'status');
    const reloadBtn = createDomainButton({ label: '放弃草稿并刷新', variant: 'ghost', attrs: { 'data-kt-reload': '' } });
    reloadBtn.hidden = true;
    if (canEdit) {
        const actions = group('知识树编辑', 'kt-save-actions', [saveStatus, reloadBtn, undoBtn, saveBtn, generateBtn]);
        head.insertBefore(actions, head.querySelector('[data-lq-dialog-close]'));
    }

    return {
        root, shell, main, notice, viewport, canvas, svg, nodesLayer, foldLayer, sizer, empty, side, hint,
        progressRingSlot, progressTitle, progressMeta, directionButtons, themeSelect, completeAllBtn, clearAllBtn,
        zoomValue, generateBtn, saveBtn, undoBtn, saveStatus, reloadBtn,
    };
}

const setLabel = (button, label) => { const text = button.querySelector('.lq-btn__label'); if (text) text.textContent = label; };

/** Header, progress, notice and empty state for the current server ``data``. */
export function paintChrome(ui, { data, canEdit, total, updateDismissed = false, dirty = false, busy = false, undoLabel = '', saveError = '', saveErrorDetail = '', requestingGeneration = false }) {
    const generating = data?.status === 'generating';
    const leafCount = Number(data?.leaf_count || 0);
    if (canEdit) {
        const button = ui.generateBtn;
        setLabel(button, requestingGeneration ? '正在提交生成…' : generating ? 'AI 生成中…' : (data?.has_tree ? '重新生成' : '生成知识树'));
        button.disabled = !data || generating || busy || dirty || !data.has_material;
        if (generating || requestingGeneration) button.setAttribute('aria-busy', 'true'); else button.removeAttribute('aria-busy');
        button.classList.toggle('kt-attention', Boolean(data?.is_stale) && !generating);
        button.title = data && !data.has_material ? '请先为本课次绑定学习材料'
            : (data?.is_stale ? '学习文档已更新，可重新生成（会清空当前完成记录与手动修改）' : '');
    }

    ui.notice.replaceChildren();
    if (generating) {
        const box = el('div', 'kt-generating');
        box.setAttribute('role', 'status');
        box.append(createComponent('spinner', { size: 'sm' }), el('span', '', 'AI 正在依据课程简介、教材目录和本课学习文档梳理知识树，通常需要 1–3 分钟；可以先关闭浮窗，完成后课次卡片会显示进度。'));
        ui.notice.append(box);
    } else if (data?.error) {
        ui.notice.append(createStatus('alert', { tone: 'danger', announce: 'polite', title: '知识树生成未完成', body: data.error }));
    } else if (data?.is_stale && !updateDismissed) {
        const alert = createStatus('alert', { tone: 'warning', announce: 'polite', title: '学习文档已更新', body: '可点击右上角“重新生成”获取新的知识树；重新生成后完成进度会归零，请按新知识树重新勾选。' });
        alert.classList.add('kt-update-alert');
        alert.append(createComponent('button', { icon: 'x', variant: 'ghost', size: 'sm', attrs: { 'aria-label': '关闭本次文档更新提醒', 'data-kt-dismiss-update': '' } }));
        ui.notice.append(alert);
    }
    if (saveError) ui.notice.prepend(createStatus('alert', { tone: 'danger', announce: 'polite', title: saveError, body: saveErrorDetail }));
    ui.notice.hidden = !ui.notice.childElementCount;

    const percent = Math.max(0, Math.min(100, Number(data?.progress || 0)));
    const lifecycle = data?.lifecycle || 'none';
    ui.progressRingSlot.replaceChildren(createComponent('progress', {
        variant: 'ring', value: data ? (data.has_tree ? percent : 0) : null, label: '本课完成进度',
    }));
    ui.progressTitle.textContent = data?.has_tree ? `本课完成 ${percent}%` : '本课知识树';
    const chip = createComponent('chip', { label: lifecycleLabel(lifecycle), kind: 'status', size: 'sm', tone: (LIFECYCLE[lifecycle] || LIFECYCLE.none).tone });
    const meta = !data ? '正在读取知识树…' : data.has_tree
        ? `已完成 ${data.completed_count || 0}/${leafCount} 个基础单元`
        : (canEdit ? '生成后逐项勾选已讲完的基础单元' : '教师尚未生成本课知识树');
    ui.progressMeta.replaceChildren(chip, document.createTextNode(meta));
    if (canEdit && data?.has_tree && total !== 100) ui.progressMeta.append(weightStatus(total, { canEdit, busy: generating || busy }));
    ui.completeAllBtn.disabled = !data?.has_tree || generating || busy || Number(data?.completed_count) === leafCount;
    ui.clearAllBtn.disabled = !data?.has_tree || generating || busy || !Number(data?.completed_count);
    if (canEdit) {
        ui.saveBtn.disabled = !data?.has_tree || generating || busy || !dirty;
        setLabel(ui.saveBtn, busy && !requestingGeneration ? '保存中…' : '保存');
        ui.saveBtn.setAttribute('aria-busy', String(busy));
        ui.undoBtn.disabled = !undoLabel || generating || busy;
        ui.undoBtn.title = undoLabel ? `撤销：${undoLabel}（Ctrl+Z）` : '没有可撤销的操作';
        ui.saveStatus.replaceChildren(createComponent('chip', { label: saveError || (requestingGeneration ? '正在提交生成' : busy ? '正在保存' : dirty ? '有未保存修改' : '已保存'), tone: saveError ? 'danger' : dirty ? 'warning' : 'success', kind: 'status', size: 'sm' }));
        ui.saveStatus.hidden = !data?.has_tree;
        ui.reloadBtn.hidden = !saveError;
        ui.reloadBtn.disabled = busy;
    }

    const { empty } = ui;
    empty.replaceChildren();
    const showEmpty = !data?.has_tree;
    empty.hidden = !showEmpty;
    ui.viewport.hidden = showEmpty;
    ui.hint.hidden = showEmpty;
    if (!showEmpty) return;
    const add = (title, text) => empty.append(el('strong', '', title), el('p', '', text));
    if (!data) add('正在读取', '请稍候。');
    else if (generating) add('正在生成知识树', '生成完成后会自动显示。');
    else if (!canEdit) add('本课暂无知识树', '教师生成后，这里会显示本次课的知识结构与讲授进度。');
    else if (!data.has_material) add('本课次还没有绑定学习材料', '请先通过“管理课次”添加课堂材料，再生成知识树。');
    else {
        add('还没有知识树', 'AI 会把本次课的学习文档整理成 1–4 个大知识点、两到三层的思维导图，并为每个基础单元分配比重。勾选已讲完的单元即可更新本课进度。');
        empty.append(createDomainButton({ label: '生成知识树', variant: 'prominent', attrs: { 'data-kt-generate': '' } }));
    }
}
