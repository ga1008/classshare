// 课次知识树浮窗：AI 生成的 2-3 层思维导图，末梢勾选驱动课次进度。
// 共享 LQ Dialog 持有层级/焦点/关闭；画布是数据内容槽（节点、连线与主题配色），
// 工具与操作一律使用 LQ 按钮、选择、状态组件。后端合同见
// classroom_app/routers/session_knowledge_trees.py。
import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/ui.js';
import { createDialog, openDialog, confirm as lqConfirm } from './lq/dialogs.js';
import { createComponent } from './lq/components.js';
import { createStatus } from './lq/status.js';
import { createDomainButton, adoptDomainContentSlot, adoptDomainControl } from './lq/domain-controls.js';
import { enhanceDropdowns } from './lq/dropdown.js';

const PREFS_KEY = 'lanshare:knowledge-tree:prefs';
const FOLDS_KEY = 'lanshare:knowledge-tree:folds:';
export const KNOWLEDGE_TREE_THEMES = Object.freeze([
    { key: 'glass', label: '玻璃' },
    { key: 'ink', label: '墨线' },
    { key: 'ribbon', label: '彩带' },
    { key: 'blueprint', label: '蓝图' },
    { key: 'paper', label: '暖纸' },
]);
const DIRECTIONS = Object.freeze([{ key: 'lr', label: '由左向右' }, { key: 'bt', label: '由下向上' }]);
const LEVEL_NAMES = ['课次主题', '大知识点', '细分知识', '基础单元'];
const DETAIL_SECTIONS = [
    ['detail', '详细介绍'], ['example', '案例'], ['question', '思考'],
    ['prerequisite', '前置关联'], ['extension', '后续关联'],
];
const LIFECYCLE = {
    none: { label: '未生成', tone: 'neutral' },
    not_started: { label: '未开始', tone: 'neutral' },
    in_progress: { label: '进行中', tone: 'warning' },
    completed: { label: '已完成', tone: 'success' },
};
const GAP_MAIN = 64;
const GAP_CROSS = { 1: 28, 2: 14, 3: 8 };
const PAD = 32;
const HOLD_MS = 320;
const POLL_MS = 3000;
const POLL_LIMIT_MS = 22 * 60 * 1000; // server expires a stuck run after 20 min
const ZOOM_MIN = 0.4;
const ZOOM_MAX = 1.6;
const ZOOM_FIT_MIN = 0.75;

export const lifecycleLabel = state => (LIFECYCLE[state] || LIFECYCLE.none).label;

function readPrefs() {
    try {
        const raw = JSON.parse(window.localStorage.getItem(PREFS_KEY) || '{}');
        return {
            direction: DIRECTIONS.some(item => item.key === raw.direction) ? raw.direction : 'lr',
            theme: KNOWLEDGE_TREE_THEMES.some(item => item.key === raw.theme) ? raw.theme : 'glass',
        };
    } catch { return { direction: 'lr', theme: 'glass' }; }
}
function writePrefs(prefs) {
    try { window.localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* storage unavailable */ }
}

const el = (tag, className = '', text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};

/** Root + flattened index (parent links, level, top-level branch, leaf flags). */
export function indexTree(tree) {
    const root = { id: '__root', label: tree?.title || '本次课', summary: tree?.summary || '', children: tree?.nodes || [] };
    const map = new Map();
    const walk = (node, parent, level, branch) => {
        const children = Array.isArray(node.children) ? node.children : [];
        const info = { node, parent, level, branch, isLeaf: level > 0 && !children.length, children };
        map.set(node.id, info);
        children.forEach((child, index) => walk(child, info, level + 1, level === 0 ? index : branch));
    };
    walk(root, null, 0, -1);
    return { root, map };
}

/** Weight total and completed weight below ``info`` (a leaf counts itself). */
export function shareOf(index, info, done) {
    if (info.isLeaf) {
        const weight = Number(info.node.weight) || 0;
        return { total: weight, done: done.has(info.node.id) ? weight : 0 };
    }
    return info.children.reduce((sum, child) => {
        const part = shareOf(index, index.map.get(child.id), done);
        return { total: sum.total + part.total, done: sum.done + part.done };
    }, { total: 0, done: 0 });
}

/**
 * Tidy-tree layout along a main (depth) and cross (sibling) axis.
 * sizes: Map(id -> {w, h}); returns boxes Map(id -> {x, y, w, h}) and canvas size.
 */
export function layoutTree(index, sizes, direction, collapsed = new Set()) {
    const lr = direction !== 'bt';
    const cross = id => (lr ? sizes.get(id).h : sizes.get(id).w);
    const mainSize = id => (lr ? sizes.get(id).w : sizes.get(id).h);
    const kidsOf = info => (collapsed.has(info.node.id) ? [] : info.children.map(child => index.map.get(child.id)));
    const rootInfo = index.map.get('__root');
    const visible = new Set();
    const collect = info => { visible.add(info.node.id); kidsOf(info).forEach(collect); };
    collect(rootInfo);
    const depthMain = [];
    for (const id of visible) {
        const level = index.map.get(id).level;
        depthMain[level] = Math.max(depthMain[level] || 0, mainSize(id));
    }
    const span = new Map();
    const measure = info => {
        const kids = kidsOf(info);
        const own = cross(info.node.id);
        if (!kids.length) { span.set(info.node.id, own); return own; }
        const gap = GAP_CROSS[info.level + 1] || 8;
        const total = kids.reduce((sum, kid) => sum + measure(kid), 0) + gap * (kids.length - 1);
        span.set(info.node.id, Math.max(own, total));
        return span.get(info.node.id);
    };
    const totalCross = measure(rootInfo);
    const centers = new Map();
    const place = (info, start) => {
        const kids = kidsOf(info);
        const own = span.get(info.node.id);
        if (!kids.length) { centers.set(info.node.id, start + own / 2); return; }
        const gap = GAP_CROSS[info.level + 1] || 8;
        const kidsTotal = kids.reduce((sum, kid) => sum + span.get(kid.node.id), 0) + gap * (kids.length - 1);
        let cursor = start + (own - kidsTotal) / 2;
        for (const kid of kids) { place(kid, cursor); cursor += span.get(kid.node.id) + gap; }
        centers.set(info.node.id, (centers.get(kids[0].node.id) + centers.get(kids[kids.length - 1].node.id)) / 2);
    };
    place(rootInfo, 0);
    const offsets = [];
    depthMain.reduce((acc, size, level) => { offsets[level] = acc; return acc + size + GAP_MAIN; }, 0);
    const totalMain = offsets[depthMain.length - 1] + depthMain[depthMain.length - 1];
    const boxes = new Map();
    for (const id of visible) {
        const info = index.map.get(id);
        const { w, h } = sizes.get(id);
        const c = centers.get(id);
        if (lr) {
            boxes.set(id, { x: PAD + offsets[info.level], y: PAD + c - h / 2, w, h });
        } else {
            const rowBottom = totalMain - offsets[info.level];
            const rowSize = depthMain[info.level];
            boxes.set(id, { x: PAD + c - w / 2, y: PAD + rowBottom - rowSize + (rowSize - h) / 2, w, h });
        }
    }
    // Folded descendants rest on their nearest visible ancestor's fold dot, so
    // folding and unfolding animate into and out of that dot.
    for (const [id, info] of index.map) {
        if (visible.has(id)) continue;
        let anchor = info.parent;
        while (anchor && !visible.has(anchor.node.id)) anchor = anchor.parent;
        const point = foldPoint(boxes.get(anchor.node.id), direction);
        const { w, h } = sizes.get(id);
        boxes.set(id, { x: point.x - w / 2, y: point.y - h / 2, w, h });
    }
    return { boxes, visible, width: (lr ? totalMain : totalCross) + PAD * 2, height: (lr ? totalCross : totalMain) + PAD * 2 };
}

/** Point where a node's outgoing edges start (its fold dot). */
export function foldPoint(box, direction) {
    return direction === 'bt' ? { x: box.x + box.w / 2, y: box.y } : { x: box.x + box.w, y: box.y + box.h / 2 };
}

export function descendantCount(info, index) {
    return info.children.reduce((sum, child) => sum + 1 + descendantCount(index.map.get(child.id), index), 0);
}

export function edgePath(parent, child, direction) {
    if (direction === 'bt') {
        const x1 = parent.x + parent.w / 2, y1 = parent.y, x2 = child.x + child.w / 2, y2 = child.y + child.h;
        const my = (y1 + y2) / 2;
        return `M${x1} ${y1}C${x1} ${my} ${x2} ${my} ${x2} ${y2}`;
    }
    const x1 = parent.x + parent.w, y1 = parent.y + parent.h / 2, x2 = child.x, y2 = child.y + child.h / 2;
    const mx = (x1 + x2) / 2;
    return `M${x1} ${y1}C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`;
}

/** Progress fields as the server computes them, for optimistic display. */
export function progressFor(tree, completedIds) {
    const index = indexTree(tree);
    const set = new Set(completedIds);
    const leaves = [...index.map.values()].filter(info => info.isLeaf);
    const done = leaves.filter(info => set.has(info.node.id));
    let progress = done.reduce((sum, info) => sum + (Number(info.node.weight) || 0), 0);
    progress = leaves.length && done.length === leaves.length ? 100 : Math.min(99, progress);
    return {
        completed_ids: done.map(info => info.node.id),
        completed_count: done.length,
        progress,
        lifecycle: progress >= 100 ? 'completed' : (done.length ? 'in_progress' : 'not_started'),
    };
}

/**
 * Open the knowledge tree dialog for one session.
 * @param {{classOfferingId: number|string, session: object, canEdit: boolean, trigger?: Element,
 *          onChange?: (sessionId: number, state: object) => void}} options
 */
export function openKnowledgeTree({ classOfferingId, session, canEdit, trigger, onChange }) {
    const sessionId = Number(session?.id || 0);
    if (!sessionId) return null;
    const base = `/api/classrooms/${encodeURIComponent(classOfferingId)}/sessions/${sessionId}/knowledge-tree`;
    const prefs = readPrefs();
    let data = null;
    let index = null;
    let selectedId = '';
    let closed = false;
    let pollTimer = 0;
    let pollStarted = 0;
    let mutationSeq = 0;
    let queue = Promise.resolve();
    let sizes = null;
    let layout = null;
    let zoom = 1;
    const nodeEls = new Map();
    const foldEls = new Map();
    const foldsKey = `${FOLDS_KEY}${sessionId}`;
    let collapsed = new Set();
    try { collapsed = new Set(JSON.parse(window.localStorage.getItem(foldsKey) || '[]')); } catch { collapsed = new Set(); }
    // Re-measure whenever a node's rendered size changes (fonts, theme, browser
    // zoom), so the layout never keeps stale boxes and labels cannot overlap.
    let measureFrame = 0;
    const nodeResizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
        if (!layout || hold?.dragging) return;
        window.cancelAnimationFrame(measureFrame);
        measureFrame = window.requestAnimationFrame(() => { if (!closed && layout && !viewport.hidden) { sizes = null; applyLayout(); } });
    }) : null;

    // ---- static structure -------------------------------------------------
    const shell = el('div', 'kt-shell');
    shell.dataset.ktTheme = prefs.theme;
    shell.dataset.ktDirection = prefs.direction;
    const toolbar = el('div', 'kt-toolbar');
    const progressBox = el('div', 'kt-progress');
    const progressRingSlot = el('span', 'kt-progress__ring');
    const progressText = el('div', 'kt-progress__text');
    const progressTitle = el('strong', 'kt-progress__title');
    const progressMeta = el('span', 'kt-progress__meta');
    progressText.append(progressTitle, progressMeta);
    progressBox.append(progressRingSlot, progressText);

    const controls = el('div', 'kt-controls');
    const directionGroup = el('div', 'kt-segment');
    directionGroup.setAttribute('role', 'group');
    directionGroup.setAttribute('aria-label', '知识树方向');
    const directionButtons = DIRECTIONS.map(item => {
        const button = createDomainButton({ label: item.label, variant: 'ghost', attrs: { 'data-kt-direction': item.key } });
        directionGroup.append(button);
        return button;
    });
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
    const zoomGroup = el('div', 'kt-zoom');
    zoomGroup.setAttribute('role', 'group');
    zoomGroup.setAttribute('aria-label', '缩放');
    const zoomValue = el('span', 'kt-zoom__value', '100%');
    zoomValue.setAttribute('aria-live', 'polite');
    zoomGroup.append(
        createDomainButton({ label: '缩小', variant: 'ghost', attrs: { 'data-kt-zoom': 'out' } }),
        zoomValue,
        createDomainButton({ label: '放大', variant: 'ghost', attrs: { 'data-kt-zoom': 'in' } }),
        createDomainButton({ label: '适应', variant: 'ghost', attrs: { 'data-kt-zoom': 'fit' } }),
    );
    const foldGroup = el('div', 'kt-zoom');
    foldGroup.setAttribute('role', 'group');
    foldGroup.setAttribute('aria-label', '展开与收起');
    foldGroup.append(
        createDomainButton({ label: '全部展开', variant: 'ghost', attrs: { 'data-kt-fold-all': 'open' } }),
        createDomainButton({ label: '只看大知识点', variant: 'ghost', attrs: { 'data-kt-fold-all': 'major' } }),
    );
    controls.append(zoomGroup, foldGroup, directionGroup, themeSelect);
    if (canEdit) controls.append(completeAllBtn, clearAllBtn);
    toolbar.append(progressBox, controls);

    const notice = el('div', 'kt-notice');
    const main = el('div', 'kt-main');
    const viewport = el('div', 'kt-viewport');
    viewport.tabIndex = 0;
    viewport.setAttribute('aria-label', '知识树画布：滚轮上下滚动，Ctrl+滚轮缩放，拖动空白处平移，+/− 键缩放，0 键适应');
    const canvas = el('div', 'kt-canvas');
    adoptDomainContentSlot(canvas);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'kt-edges');
    svg.setAttribute('aria-hidden', 'true');
    const nodesLayer = el('div', 'kt-nodes');
    nodesLayer.setAttribute('role', 'tree');
    nodesLayer.setAttribute('aria-label', '本次课知识树');
    const dropMarker = el('div', 'kt-drop-marker');
    dropMarker.hidden = true;
    const foldLayer = el('div', 'kt-folds');
    canvas.append(svg, nodesLayer, foldLayer, dropMarker);
    const sizer = el('div', 'kt-sizer');
    sizer.append(canvas);
    viewport.append(sizer);
    const empty = el('div', 'kt-empty');
    const side = el('aside', 'kt-side');
    side.hidden = true;
    side.setAttribute('aria-label', '知识点详情');
    const hint = el('div', 'kt-hint', 'Ctrl+滚轮缩放 · 拖动空白处平移 · 点圆点展开/收起 · 双击空白处适应');
    hint.setAttribute('aria-hidden', 'true');
    main.append(viewport, empty, side, hint);
    shell.append(toolbar, notice, main);

    const root = createDialog({ title: '知识树', body: shell, size: 'full', closeLabel: '关闭知识树' });
    root.querySelector('.lq-dialog__surface').classList.add('kt-dialog');
    const head = root.querySelector('.lq-dialog__head');
    head.querySelector('h2').append(el('span', 'kt-subtitle', session.detail_title || session.title || ''));
    const generateBtn = createDomainButton({ label: '生成知识树', variant: 'prominent', attrs: { 'data-kt-generate': '' } });
    if (canEdit) head.insertBefore(generateBtn, head.querySelector('[data-lq-dialog-close]'));

    // ---- helpers ----------------------------------------------------------
    const doneSet = () => new Set(data?.completed_ids || []);
    const leafCount = () => Number(data?.leaf_count || 0);
    const isGenerating = () => data?.status === 'generating';
    const setLabel = (button, label) => { const text = button.querySelector('.lq-btn__label'); if (text) text.textContent = label; };

    function renderProgress() {
        const percent = Math.max(0, Math.min(100, Number(data?.progress || 0)));
        const lifecycle = data?.lifecycle || 'none';
        progressRingSlot.replaceChildren(createComponent('progress', {
            variant: 'ring', value: data ? (data.has_tree ? percent : 0) : null, label: '本课完成进度',
        }));
        progressTitle.textContent = data?.has_tree ? `本课完成 ${percent}%` : '本课知识树';
        const chip = createComponent('chip', { label: lifecycleLabel(lifecycle), kind: 'status', size: 'sm', tone: (LIFECYCLE[lifecycle] || LIFECYCLE.none).tone });
        const meta = !data ? '正在读取知识树…' : data.has_tree
            ? `已完成 ${data.completed_count || 0}/${leafCount()} 个基础单元`
            : (canEdit ? '生成后逐项勾选已讲完的基础单元' : '教师尚未生成本课知识树');
        progressMeta.replaceChildren(chip, document.createTextNode(meta));
        completeAllBtn.disabled = !data?.has_tree || isGenerating() || Number(data?.completed_count) === leafCount();
        clearAllBtn.disabled = !data?.has_tree || isGenerating() || !Number(data?.completed_count);
    }

    function renderHeader() {
        if (!canEdit) return;
        setLabel(generateBtn, isGenerating() ? 'AI 生成中…' : (data?.has_tree ? '重新生成' : '生成知识树'));
        generateBtn.disabled = !data || isGenerating() || !data.has_material;
        if (isGenerating()) generateBtn.setAttribute('aria-busy', 'true'); else generateBtn.removeAttribute('aria-busy');
        generateBtn.classList.toggle('kt-attention', Boolean(data?.is_stale) && !isGenerating());
        generateBtn.title = data && !data.has_material ? '请先为本课次绑定学习材料'
            : (data?.is_stale ? '学习文档已更新，可重新生成（会清空当前完成记录）' : '');
    }

    function renderNotice() {
        notice.replaceChildren();
        if (isGenerating()) {
            const box = el('div', 'kt-generating');
            box.setAttribute('role', 'status');
            box.append(createComponent('spinner', { size: 'sm' }), el('span', '', 'AI 正在依据课程简介、教材目录和本课学习文档梳理知识树，通常需要 1–3 分钟；可以先关闭浮窗，完成后课次卡片会显示进度。'));
            notice.append(box);
        } else if (data?.error) {
            notice.append(createStatus('alert', { tone: 'danger', announce: 'polite', title: '知识树生成未完成', body: data.error }));
        } else if (data?.is_stale) {
            notice.append(createStatus('alert', { tone: 'warning', announce: 'polite', title: '学习文档已更新', body: '可点击右上角“重新生成”获取新的知识树；重新生成后完成进度会归零，请按新知识树重新勾选。' }));
        }
        notice.hidden = !notice.childElementCount;
    }

    function renderEmpty() {
        empty.replaceChildren();
        const showEmpty = !data?.has_tree;
        empty.hidden = !showEmpty;
        viewport.hidden = showEmpty;
        hint.hidden = showEmpty;
        if (!showEmpty) return;
        const add = (title, text) => empty.append(el('strong', '', title), el('p', '', text));
        if (!data) add('正在读取', '请稍候。');
        else if (isGenerating()) add('正在生成知识树', '生成完成后会自动显示。');
        else if (!canEdit) add('本课暂无知识树', '教师生成后，这里会显示本次课的知识结构与讲授进度。');
        else if (!data.has_material) add('本课次还没有绑定学习材料', '请先通过“管理课次”添加课堂材料，再生成知识树。');
        else {
            add('还没有知识树', 'AI 会把本次课的学习文档整理成 1–4 个大知识点、两到三层的思维导图，并为每个基础单元分配比重。勾选已讲完的单元即可更新本课进度。');
            empty.append(createDomainButton({ label: '生成知识树', variant: 'prominent', attrs: { 'data-kt-generate': '' } }));
        }
    }

    // ---- canvas -----------------------------------------------------------
    function buildNodes() {
        nodeResizeObserver?.disconnect();
        nodeEls.clear();
        foldEls.clear();
        nodesLayer.replaceChildren();
        foldLayer.replaceChildren();
        layout = null;
        sizes = null;
        if (!index) return;
        for (const [id, info] of index.map) {
            const node = el('button', 'kt-node');
            node.type = 'button';
            node.dataset.nodeId = id;
            node.dataset.level = String(info.level);
            node.dataset.branch = String(Math.max(0, info.branch) % 4);
            node.setAttribute('role', 'treeitem');
            node.setAttribute('aria-level', String(info.level + 1));
            const label = el('span', 'kt-node__label', info.node.label || '未命名知识点');
            if (info.isLeaf) {
                node.dataset.leaf = 'true';
                const check = el('span', 'kt-node__check');
                check.setAttribute('aria-hidden', 'true');
                node.append(check, label, el('span', 'kt-node__weight', `${Number(info.node.weight) || 0}%`));
            } else if (info.level === 0) {
                node.append(el('span', 'kt-node__kicker', '本次课'), label);
            } else {
                const meter = el('span', 'kt-node__meter');
                meter.setAttribute('aria-hidden', 'true');
                node.append(label, meter);
            }
            nodesLayer.append(node);
            nodeEls.set(id, node);
            nodeResizeObserver?.observe(node);
            if (info.children.length) {
                const fold = el('button', 'kt-fold');
                fold.type = 'button';
                fold.dataset.foldId = id;
                fold.dataset.branch = node.dataset.branch;
                foldLayer.append(fold);
                foldEls.set(id, fold);
            }
        }
        // Forget folds of nodes that no longer exist (e.g. after regeneration).
        const pruned = new Set([...collapsed].filter(id => index.map.get(id)?.children.length));
        if (pruned.size !== collapsed.size) { collapsed = pruned; saveFolds(); }
    }

    function saveFolds() {
        try { window.localStorage.setItem(foldsKey, JSON.stringify([...collapsed])); } catch { /* storage unavailable */ }
    }

    function paintFolds() {
        for (const [id, fold] of foldEls) {
            const info = index.map.get(id);
            const folded = collapsed.has(id);
            const hidden = layout ? !layout.visible.has(id) : false;
            fold.setAttribute('aria-expanded', folded ? 'false' : 'true');
            fold.textContent = folded ? `+${descendantCount(info, index)}` : '';
            fold.setAttribute('aria-label', `${folded ? '展开' : '收起'}「${info.node.label}」的下级知识点`);
            fold.title = folded ? '展开下级知识点' : '收起下级知识点';
            fold.classList.toggle('is-folded', hidden);
            fold.tabIndex = hidden ? -1 : 0;
            if (layout) {
                const point = foldPoint(layout.boxes.get(id), prefs.direction);
                fold.style.setProperty('--kt-pos', `${Math.round(point.x)}px, ${Math.round(point.y)}px`);
            }
        }
    }

    /** Fold or unfold while keeping ``anchorId`` at the same place on screen. */
    function setFolds(next, anchorId = '__root') {
        const before = layout?.boxes.get(anchorId);
        const screen = before && { x: canvas.offsetLeft + before.x * zoom - viewport.scrollLeft, y: canvas.offsetTop + before.y * zoom - viewport.scrollTop };
        collapsed = next;
        saveFolds();
        applyLayout();
        const after = layout?.boxes.get(anchorId);
        if (screen && after) {
            viewport.scrollLeft = canvas.offsetLeft + after.x * zoom - screen.x;
            viewport.scrollTop = canvas.offsetTop + after.y * zoom - screen.y;
        }
        if (selectedId && layout && !layout.visible.has(selectedId)) select('');
    }

    function toggleFold(id) {
        const next = new Set(collapsed);
        if (next.has(id)) next.delete(id); else next.add(id);
        setFolds(next, id);
    }

    /** Unfold every ancestor so ``id`` is on screen. */
    function expandTo(id) {
        let info = index?.map.get(id)?.parent;
        const next = new Set(collapsed);
        while (info) { next.delete(info.node.id); info = info.parent; }
        if (next.size !== collapsed.size) setFolds(next, id);
    }

    function paintState() {
        if (!index) return;
        const done = doneSet();
        for (const [id, info] of index.map) {
            const node = nodeEls.get(id);
            if (!node) continue;
            const share = shareOf(index, info, done);
            const complete = share.total > 0 && share.done >= share.total;
            node.dataset.done = complete ? 'true' : 'false';
            node.dataset.selected = id === selectedId ? 'true' : 'false';
            const hidden = layout ? !layout.visible.has(id) : false;
            node.classList.toggle('is-folded', hidden);
            node.tabIndex = hidden ? -1 : 0;
            if (hidden) node.setAttribute('aria-hidden', 'true'); else node.removeAttribute('aria-hidden');
            if (info.children.length) node.setAttribute('aria-expanded', collapsed.has(id) ? 'false' : 'true');
            if (info.isLeaf) {
                node.setAttribute('aria-pressed', complete ? 'true' : 'false');
                node.setAttribute('aria-label', `${info.node.label}，比重 ${Number(info.node.weight) || 0}%，${complete ? '已完成' : '未完成'}${canEdit ? '，单击切换完成状态' : ''}`);
            } else {
                const pct = share.total ? Math.round((share.done / share.total) * 100) : 0;
                node.style.setProperty('--kt-share', `${pct}%`);
                node.setAttribute('aria-label', `${info.node.label}${info.level ? `，已完成 ${pct}%` : ''}，查看详情`);
            }
        }
        paintFolds();
        drawEdges();
    }

    function fitZoom() {
        if (!layout) return 1;
        const scale = Math.min((viewport.clientWidth - 8) / layout.width, (viewport.clientHeight - 8) / layout.height);
        return Math.max(ZOOM_FIT_MIN, Math.min(1, scale));
    }

    // ``anchor`` is a viewport point (e.g. the cursor) that stays over the same
    // canvas spot; omitted, the viewport centre stays put.
    function setZoom(value, { keepCenter = true, anchor = null } = {}) {
        const next = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(value * 1000) / 1000));
        const ax = anchor ? anchor.x : viewport.clientWidth / 2;
        const ay = anchor ? anchor.y : viewport.clientHeight / 2;
        const cx = (viewport.scrollLeft + ax - canvas.offsetLeft) / zoom;
        const cy = (viewport.scrollTop + ay - canvas.offsetTop) / zoom;
        zoom = next;
        canvas.style.setProperty('--kt-zoom', String(zoom));
        if (layout) {
            const width = layout.width * zoom, height = layout.height * zoom;
            // Small trees sit centred; a bottom-up tree grows from the bottom edge.
            const offsetX = Math.max(0, (viewport.clientWidth - width) / 2);
            const offsetY = prefs.direction === 'bt'
                ? Math.max(0, viewport.clientHeight - height)
                : Math.max(0, (viewport.clientHeight - height) / 2);
            canvas.style.left = `${Math.floor(offsetX)}px`;
            canvas.style.top = `${Math.floor(offsetY)}px`;
            sizer.style.width = `${Math.ceil(width + offsetX)}px`;
            sizer.style.height = `${Math.ceil(height + offsetY)}px`;
        }
        zoomValue.textContent = `${Math.round(zoom * 100)}%`;
        if (keepCenter) {
            viewport.scrollLeft = Math.max(0, canvas.offsetLeft + cx * zoom - ax);
            viewport.scrollTop = Math.max(0, canvas.offsetTop + cy * zoom - ay);
        }
    }

    function applyLayout({ center = false, fit = false } = {}) {
        if (!index || viewport.hidden) return;
        if (!sizes) {
            sizes = new Map();
            for (const [id, node] of nodeEls) sizes.set(id, { w: node.offsetWidth, h: node.offsetHeight });
        }
        layout = layoutTree(index, sizes, prefs.direction, collapsed);
        canvas.style.width = `${layout.width}px`;
        canvas.style.height = `${layout.height}px`;
        setZoom(fit ? fitZoom() : zoom, { keepCenter: false });
        svg.setAttribute('width', String(layout.width));
        svg.setAttribute('height', String(layout.height));
        svg.setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`);
        for (const [id, box] of layout.boxes) {
            const node = nodeEls.get(id);
            if (node) node.style.setProperty('--kt-pos', `${Math.round(box.x)}px, ${Math.round(box.y)}px`);
        }
        paintState();
        if (center) centerOn('__root');
    }

    function drawEdges() {
        if (!layout || !index) return;
        const done = doneSet();
        const parts = [];
        for (const [id, info] of index.map) {
            if (!info.parent || !layout.visible.has(id)) continue;
            const parentBox = layout.boxes.get(info.parent.node.id);
            const childBox = layout.boxes.get(id);
            if (!parentBox || !childBox) continue;
            const share = shareOf(index, info, done);
            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', edgePath(parentBox, childBox, prefs.direction));
            path.setAttribute('class', `kt-edge kt-edge--l${info.level}${share.total > 0 && share.done >= share.total ? ' is-done' : ''}`);
            path.dataset.branch = String(Math.max(0, info.branch) % 4);
            parts.push(path);
        }
        svg.replaceChildren(...parts);
    }

    function centerOn(id) {
        const box = layout?.boxes.get(id);
        if (!box) return;
        const bt = prefs.direction === 'bt';
        const ox = canvas.offsetLeft, oy = canvas.offsetTop;
        const left = ox + (bt ? (box.x + box.w / 2) * zoom - viewport.clientWidth / 2 : (box.x - PAD) * zoom);
        const top = oy + (bt ? (box.y + box.h + PAD) * zoom - viewport.clientHeight : (box.y + box.h / 2) * zoom - viewport.clientHeight / 2);
        viewport.scrollTo({ left: Math.max(0, left), top: Math.max(0, top) });
    }

    function revealNode(id) {
        const box = layout?.boxes.get(id);
        if (!box) return;
        const { scrollLeft, scrollTop, clientWidth, clientHeight } = viewport;
        const x = canvas.offsetLeft + box.x * zoom, y = canvas.offsetTop + box.y * zoom, w = box.w * zoom, h = box.h * zoom;
        if (x < scrollLeft || y < scrollTop || x + w > scrollLeft + clientWidth || y + h > scrollTop + clientHeight) {
            viewport.scrollTo({ left: Math.max(0, x + w / 2 - clientWidth / 2), top: Math.max(0, y + h / 2 - clientHeight / 2), behavior: 'smooth' });
        }
    }

    // ---- sidebar ----------------------------------------------------------
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

    function renderSide() {
        side.replaceChildren();
        const info = index?.map.get(selectedId);
        side.hidden = !info;
        main.classList.toggle('has-side', Boolean(info));
        if (!info) return;
        const done = doneSet();
        const headRow = el('div', 'kt-side__head');
        headRow.append(
            el('span', 'kt-side__kicker', info.isLeaf ? `基础单元 · 比重 ${Number(info.node.weight) || 0}%` : LEVEL_NAMES[Math.min(info.level, 3)]),
            createComponent('button', { icon: 'x', variant: 'ghost', size: 'sm', attrs: { 'aria-label': '收起详情', 'data-kt-side-close': '' } }),
        );
        side.append(headRow, el('h3', 'kt-side__title', info.node.label || '本次课'));
        if (info.isLeaf) {
            const complete = done.has(info.node.id);
            const row = el('div', 'kt-side__state');
            row.append(createComponent('chip', { label: complete ? '已完成' : '未完成', kind: 'status', size: 'sm', tone: complete ? 'success' : 'neutral' }));
            if (canEdit) row.append(createDomainButton({ label: complete ? '标记为未完成' : '标记为已完成', variant: complete ? 'glass' : 'prominent', attrs: { 'data-kt-toggle': info.node.id } }));
            side.append(row);
        } else if (info.level > 0) {
            const share = shareOf(index, info, done);
            side.append(el('p', 'kt-side__meta', `包含比重 ${share.total}% · 已完成 ${share.done}%`));
        }
        if (info.level === 0) {
            if (info.node.summary) side.append(section('本课概述', info.node.summary));
            const meta = [
                data?.source_label ? `依据文档：${data.source_label}` : '',
                data?.generated_at ? `生成时间：${String(data.generated_at).replace('T', ' ')}` : '',
                data?.completed_at ? `完成时间：${String(data.completed_at).replace('T', ' ')}` : '',
            ].filter(Boolean);
            if (meta.length) side.append(section('来源', meta.join('\n')));
        }
        for (const [key, title] of DETAIL_SECTIONS) if (info.node[key]) side.append(section(title, info.node[key]));
        const siblings = info.parent ? info.parent.children : [];
        const position = siblings.findIndex(item => item.id === info.node.id);
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
        if (canEdit && info.parent && siblings.length > 1) {
            const order = el('div', 'kt-side__order');
            const up = createDomainButton({ label: '上移', variant: 'ghost', attrs: { 'data-kt-move': '-1' } });
            const down = createDomainButton({ label: '下移', variant: 'ghost', attrs: { 'data-kt-move': '1' } });
            up.disabled = position <= 0 || isGenerating();
            down.disabled = position >= siblings.length - 1 || isGenerating();
            order.append(el('span', 'kt-side__hint', '同级顺序（也可按住节点拖动）'), up, down);
            side.append(order);
        }
    }

    function select(id, { reveal = false } = {}) {
        if (id && reveal) expandTo(id);
        selectedId = id;
        paintState();
        renderSide();
        if (reveal) revealNode(id);
    }

    // ---- state ------------------------------------------------------------
    function applyState(next, { relayout = false } = {}) {
        const treeChanged = relayout || !data || data.has_tree !== next.has_tree
            || JSON.stringify(data.tree) !== JSON.stringify(next.tree);
        const firstTree = !layout;
        data = next;
        renderHeader();
        renderNotice();
        renderProgress();
        renderEmpty();
        if (treeChanged) {
            index = data.has_tree ? indexTree(data.tree) : null;
            if (selectedId && !index?.map.has(selectedId)) selectedId = '';
            buildNodes();
            if (index) applyLayout({ center: firstTree, fit: firstTree });
        }
        paintState();
        renderSide();
        onChange?.(sessionId, data);
    }

    const request = (path = '', options = {}) => apiFetch(`${base}${path}`, { silent: true, ...options });

    async function refresh() {
        try {
            applyState(await request());
        } catch (error) {
            notice.replaceChildren(createStatus('alert', { tone: 'danger', title: '知识树读取失败', body: error.message || '请稍后重试。' }));
            notice.hidden = false;
        }
    }

    function schedulePoll() {
        window.clearTimeout(pollTimer);
        if (closed || !isGenerating()) return;
        if (!pollStarted) pollStarted = Date.now();
        if (Date.now() - pollStarted > POLL_LIMIT_MS) return;
        pollTimer = window.setTimeout(async () => {
            await refresh();
            if (closed) return;
            if (!isGenerating()) {
                pollStarted = 0;
                if (data?.error) showToast(data.error, 'error');
                else if (data?.has_tree) showToast('知识树已生成，请勾选已讲完的基础单元。', 'success');
                return;
            }
            schedulePoll();
        }, POLL_MS);
    }

    // Serialize writes; only the latest response is applied, a failure restores
    // the server truth instead of leaving an optimistic guess on screen.
    function mutate(path, options, optimistic) {
        if (!canEdit || !data?.has_tree || isGenerating()) return;
        const seq = ++mutationSeq;
        if (optimistic) applyState({ ...data, ...optimistic(data) });
        queue = queue.then(async () => {
            try {
                const next = await request(path, options);
                if (!closed && seq === mutationSeq) applyState(next);
            } catch (error) {
                showToast(error.message || '保存失败，请重试。', 'error');
                if (!closed && seq === mutationSeq) await refresh();
            }
        });
    }

    function toggleLeaf(id) {
        const current = doneSet();
        const done = !current.has(id);
        if (done) current.add(id); else current.delete(id);
        mutate(`/leaves/${encodeURIComponent(id)}`, { method: 'POST', body: { done } }, state => progressFor(state.tree, [...current]));
    }

    function saveOrder(parentId, order) {
        const parentKey = parentId === '__root' ? '' : parentId;
        const reorder = state => {
            const tree = JSON.parse(JSON.stringify(state.tree));
            const find = nodes => {
                for (const node of nodes) {
                    if (node.id === parentKey) return node;
                    const found = find(node.children || []);
                    if (found) return found;
                }
                return null;
            };
            const holder = parentKey ? find(tree.nodes) : null;
            const list = holder ? holder.children : tree.nodes;
            const byId = new Map(list.map(node => [node.id, node]));
            const ordered = order.map(id => byId.get(id));
            if (holder) holder.children = ordered; else tree.nodes = ordered;
            return { tree };
        };
        // No expected version: queued ticks bump it, and the server already
        // rejects an order whose sibling set no longer matches.
        mutate('/order', { method: 'PUT', body: { parent_id: parentKey, order } }, reorder);
    }

    function moveSelected(delta) {
        const info = index?.map.get(selectedId);
        if (!info?.parent) return;
        const ids = info.parent.children.map(item => item.id);
        const from = ids.indexOf(info.node.id);
        const to = from + delta;
        if (from < 0 || to < 0 || to >= ids.length) return;
        ids.splice(to, 0, ids.splice(from, 1)[0]);
        saveOrder(info.parent.node.id, ids);
    }

    async function generate() {
        if (!canEdit || !data || isGenerating() || !data.has_material) return;
        if (data.has_tree) {
            const hasProgress = Number(data.completed_count) > 0;
            const ok = await lqConfirm({
                title: '重新生成知识树？',
                message: hasProgress
                    ? `将用 AI 重新生成整棵知识树，并清空当前已完成的 ${data.progress || 0}% 进度；生成后请按新知识树重新勾选。`
                    : '将用 AI 重新生成并替换当前知识树。',
                confirmLabel: '重新生成',
                danger: hasProgress,
            });
            if (!ok || closed) return;
        }
        generateBtn.disabled = true;
        try {
            applyState(await request('/generate', { method: 'POST' }));
            pollStarted = Date.now();
            schedulePoll();
        } catch (error) {
            showToast(error.message || '无法开始生成，请稍后重试。', 'error');
            await refresh();
        }
    }

    async function bulk(done) {
        if (!data?.has_tree) return;
        if (!done && Number(data.completed_count)) {
            const ok = await lqConfirm({ title: '全部取消？', message: '将清空本课所有已完成标记，进度归零。', confirmLabel: '全部取消', danger: true });
            if (!ok || closed) return;
        }
        const ids = done ? [...index.map.values()].filter(info => info.isLeaf).map(info => info.node.id) : [];
        mutate('/bulk', { method: 'POST', body: { done } }, state => progressFor(state.tree, ids));
    }

    // ---- press-and-hold reorder -------------------------------------------
    let hold = null;
    let suppressClick = false;

    function clearHold() {
        if (!hold) return;
        window.clearTimeout(hold.timer);
        if (hold.dragging) {
            hold.node.classList.remove('is-dragging');
            hold.node.style.removeProperty('--kt-drag');
            try { hold.node.releasePointerCapture(hold.pointerId); } catch { /* already released */ }
        }
        dropMarker.hidden = true;
        hold = null;
        // The click that follows a drag's pointerup is swallowed once; never longer.
        if (suppressClick) window.setTimeout(() => { suppressClick = false; }, 0);
    }
    const canvasPoint = event => {
        const rect = canvas.getBoundingClientRect();
        return { x: (event.clientX - rect.left) / zoom, y: (event.clientY - rect.top) / zoom };
    };
    const siblingsOf = id => {
        const info = index?.map.get(id);
        return info?.parent ? info.parent.children.map(child => child.id) : [];
    };
    function dropIndex(id, point) {
        const lr = prefs.direction !== 'bt';
        const others = siblingsOf(id).filter(item => item !== id);
        const position = others.filter(other => {
            const box = layout.boxes.get(other);
            return (lr ? point.y : point.x) > (lr ? box.y + box.h / 2 : box.x + box.w / 2);
        }).length;
        return { others, position };
    }
    function showDropMarker(id, point) {
        const { others, position } = dropIndex(id, point);
        const box = layout.boxes.get(others[Math.min(position, others.length - 1)]);
        if (!box) { dropMarker.hidden = true; return; }
        const after = position >= others.length;
        const lr = prefs.direction !== 'bt';
        dropMarker.hidden = false;
        dropMarker.dataset.axis = lr ? 'h' : 'v';
        dropMarker.style.setProperty('--kt-pos', lr ? `${box.x}px, ${after ? box.y + box.h + 3 : box.y - 6}px` : `${after ? box.x + box.w + 3 : box.x - 6}px, ${box.y}px`);
        dropMarker.style.setProperty('--kt-len', `${lr ? box.w : box.h}px`);
    }

    nodesLayer.addEventListener('pointerdown', event => {
        const node = event.target.closest('.kt-node');
        if (!node || !canEdit || event.button !== 0 || node.dataset.level === '0' || isGenerating() || !layout) return;
        if (siblingsOf(node.dataset.nodeId).length < 2) return;
        clearHold();
        hold = { node, id: node.dataset.nodeId, pointerId: event.pointerId, start: { x: event.clientX, y: event.clientY }, dragging: false };
        hold.timer = window.setTimeout(() => {
            if (!hold || hold.node !== node) return;
            hold.dragging = true;
            suppressClick = true;
            node.classList.add('is-dragging');
            try { node.setPointerCapture(hold.pointerId); } catch { /* pointer already gone */ }
        }, HOLD_MS);
    });
    nodesLayer.addEventListener('pointermove', event => {
        if (!hold || event.pointerId !== hold.pointerId) return;
        const dx = event.clientX - hold.start.x, dy = event.clientY - hold.start.y;
        if (!hold.dragging) { if (Math.hypot(dx, dy) > 8) clearHold(); return; }
        event.preventDefault();
        const lr = prefs.direction !== 'bt';
        hold.node.style.setProperty('--kt-drag', `${lr ? 0 : dx / zoom}px, ${lr ? dy / zoom : 0}px`);
        showDropMarker(hold.id, canvasPoint(event));
    });
    const finishDrag = event => {
        if (!hold || event.pointerId !== hold.pointerId) return;
        if (hold.dragging && event.type === 'pointerup') {
            const { others, position } = dropIndex(hold.id, canvasPoint(event));
            const next = [...others.slice(0, position), hold.id, ...others.slice(position)];
            const parentId = index.map.get(hold.id).parent.node.id;
            const changed = next.join('|') !== siblingsOf(hold.id).join('|');
            clearHold();
            if (changed) saveOrder(parentId, next);
            return;
        }
        clearHold();
    };
    nodesLayer.addEventListener('pointerup', finishDrag);
    nodesLayer.addEventListener('pointercancel', finishDrag);
    nodesLayer.addEventListener('contextmenu', event => { if (hold) event.preventDefault(); });
    // After a press-and-hold the finger drags the node instead of panning the canvas.
    nodesLayer.addEventListener('touchmove', event => { if (hold?.dragging) event.preventDefault(); }, { passive: false });

    // ---- clicks and keys --------------------------------------------------
    nodesLayer.addEventListener('click', event => {
        const node = event.target.closest('.kt-node');
        if (!node) return;
        if (suppressClick) { suppressClick = false; return; }
        const info = index?.map.get(node.dataset.nodeId);
        if (!info) return;
        if (info.isLeaf && canEdit) toggleLeaf(info.node.id);
        select(info.node.id);
    });
    nodesLayer.addEventListener('keydown', event => {
        const node = event.target.closest('.kt-node');
        const info = node && index?.map.get(node.dataset.nodeId);
        if (!info) return;
        const go = target => { if (target) { event.preventDefault(); nodeEls.get(target.node.id)?.focus(); revealNode(target.node.id); } };
        const keys = prefs.direction !== 'bt'
            ? { parent: 'ArrowLeft', child: 'ArrowRight', prev: 'ArrowUp', next: 'ArrowDown' }
            : { parent: 'ArrowDown', child: 'ArrowUp', prev: 'ArrowLeft', next: 'ArrowRight' };
        const siblings = info.parent ? info.parent.children : [];
        const position = siblings.findIndex(item => item.id === info.node.id);
        if (event.key === keys.parent) go(info.parent);
        else if (event.key === keys.child) {
            if (collapsed.has(info.node.id)) { event.preventDefault(); toggleFold(info.node.id); }
            else go(info.children[0] ? index.map.get(info.children[0].id) : null);
        }
        else if (event.key === keys.prev && position > 0) go(index.map.get(siblings[position - 1].id));
        else if (event.key === keys.next && position >= 0 && position < siblings.length - 1) go(index.map.get(siblings[position + 1].id));
    });

    shell.addEventListener('click', event => {
        const target = event.target.closest('button');
        if (!target || !shell.contains(target) || target.disabled) return;
        if (target.matches('[data-kt-direction]')) {
            prefs.direction = target.dataset.ktDirection;
            writePrefs(prefs);
            shell.dataset.ktDirection = prefs.direction;
            syncDirectionButtons();
            sizes = null;
            applyLayout({ center: true, fit: true });
        } else if (target.matches('[data-kt-zoom]')) {
            const mode = target.dataset.ktZoom;
            if (mode === 'fit') { setZoom(fitZoom(), { keepCenter: false }); centerOn('__root'); }
            else setZoom(zoom * (mode === 'in' ? 1.2 : 1 / 1.2));
        } else if (target.matches('[data-kt-fold-all]')) {
            if (!index) return;
            setFolds(target.dataset.ktFoldAll === 'open' ? new Set()
                : new Set([...index.map.values()].filter(info => info.level === 1 && info.children.length).map(info => info.node.id)));
            centerOn('__root');
        } else if (target.matches('[data-kt-bulk]')) void bulk(target.dataset.ktBulk === 'done');
        else if (target.matches('[data-kt-generate]')) void generate();
        else if (target.matches('[data-kt-side-close]')) { const previous = selectedId; select(''); nodeEls.get(previous)?.focus(); }
        else if (target.matches('[data-kt-goto]')) select(target.dataset.ktGoto, { reveal: true });
        else if (target.matches('[data-kt-toggle]')) toggleLeaf(target.dataset.ktToggle);
        else if (target.matches('[data-kt-move]')) moveSelected(Number(target.dataset.ktMove));
    });
    generateBtn.addEventListener('click', () => void generate());
    foldLayer.addEventListener('click', event => {
        const fold = event.target.closest('.kt-fold');
        if (fold) toggleFold(fold.dataset.foldId);
    });
    // Plain wheel scrolls natively; Ctrl+wheel (and trackpad pinch) zooms around the cursor.
    viewport.addEventListener('wheel', event => {
        if (!event.ctrlKey || !layout) return;
        event.preventDefault();
        const rect = viewport.getBoundingClientRect();
        const delta = Math.max(-120, Math.min(120, event.deltaMode === 1 ? event.deltaY * 40 : event.deltaY));
        setZoom(zoom * Math.exp(-delta * 0.0018), { anchor: { x: event.clientX - rect.left, y: event.clientY - rect.top } });
    }, { passive: false });
    viewport.addEventListener('keydown', event => {
        if (event.target !== viewport || !layout || event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key === '+' || event.key === '=') { event.preventDefault(); setZoom(zoom * 1.2); }
        else if (event.key === '-' || event.key === '_') { event.preventDefault(); setZoom(zoom / 1.2); }
        else if (event.key === '0') { event.preventDefault(); setZoom(fitZoom(), { keepCenter: false }); centerOn('__root'); }
    });
    viewport.addEventListener('dblclick', event => {
        if (!layout || event.target.closest('.kt-node, .kt-fold')) return;
        setZoom(fitZoom(), { keepCenter: false });
        centerOn('__root');
    });

    // Press on empty canvas and drag to pan (mouse/pen; touch already pans natively).
    let pan = null;
    viewport.addEventListener('pointerdown', event => {
        if (!layout || event.pointerType === 'touch' || ![0, 1].includes(event.button)) return;
        if (event.target.closest('.kt-node, .kt-fold')) return;
        // Leave the native scrollbars alone.
        const rect = viewport.getBoundingClientRect();
        if (event.clientX - rect.left >= viewport.clientWidth || event.clientY - rect.top >= viewport.clientHeight) return;
        event.preventDefault();
        viewport.focus({ preventScroll: true });
        pan = { id: event.pointerId, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
        viewport.classList.add('is-panning');
        try { viewport.setPointerCapture(event.pointerId); } catch { /* pointer already gone */ }
    });
    viewport.addEventListener('pointermove', event => {
        if (!pan || event.pointerId !== pan.id) return;
        viewport.scrollLeft = pan.left - (event.clientX - pan.x);
        viewport.scrollTop = pan.top - (event.clientY - pan.y);
    });
    const endPan = event => {
        if (!pan || event.pointerId !== pan.id) return;
        try { viewport.releasePointerCapture(pan.id); } catch { /* already released */ }
        pan = null;
        viewport.classList.remove('is-panning');
    };
    viewport.addEventListener('pointerup', endPan);
    viewport.addEventListener('pointercancel', endPan);
    viewport.addEventListener('lostpointercapture', endPan);
    themeSelect.addEventListener('change', () => {
        prefs.theme = themeSelect.value;
        writePrefs(prefs);
        shell.dataset.ktTheme = prefs.theme;
        sizes = null;
        applyLayout();
    });

    function syncDirectionButtons() {
        for (const button of directionButtons) {
            const active = button.dataset.ktDirection === prefs.direction;
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
            if (active) button.dataset.lqSelected = 'true'; else delete button.dataset.lqSelected;
        }
    }
    syncDirectionButtons();

    // The side panel and window size change the viewport; re-anchor the canvas once per frame.
    let resizeFrame = 0;
    const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
        window.cancelAnimationFrame(resizeFrame);
        resizeFrame = window.requestAnimationFrame(() => { if (layout && !viewport.hidden) setZoom(zoom, { keepCenter: false }); });
    }) : null;
    resizeObserver?.observe(viewport);

    let dropdowns = [];
    function cleanup() {
        if (closed) return;
        closed = true;
        resizeObserver?.disconnect();
        nodeResizeObserver?.disconnect();
        window.cancelAnimationFrame(resizeFrame);
        window.cancelAnimationFrame(measureFrame);
        window.clearTimeout(pollTimer);
        clearHold();
        dropdowns.forEach(binding => binding.destroy?.());
        dropdowns = [];
    }
    openDialog(root, {
        trigger,
        onClose: cleanup,
        onDestroy: cleanup,
    });
    dropdowns = enhanceDropdowns(shell);

    renderProgress();
    renderHeader();
    renderEmpty();
    void refresh().then(() => { if (isGenerating()) schedulePoll(); });
    return { refresh };
}
