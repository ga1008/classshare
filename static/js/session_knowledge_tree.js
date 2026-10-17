// 课次知识树浮窗：AI 生成的 2-3 层思维导图，末梢勾选驱动课次进度，教师可增删改与拖动重排。
// 共享 LQ Dialog 持有层级/焦点/关闭；画布是数据内容槽（节点、连线与主题配色），
// 工具与操作一律使用 LQ 按钮、选择、状态、表单组件。后端合同见
// classroom_app/routers/session_knowledge_trees.py 与 session_knowledge_tree_edit_service.py。
// 纯数据与几何在 static/js/knowledge_tree/：model（规则预演）、layout（布局）、
// motion（弹簧位置动效）、drag（拖动）、side（侧栏）。
import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/ui.js';
import { openDialog } from './lq/dialogs.js';
import { createStatus } from './lq/status.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { enhanceForms } from './lq/forms.js';
import {
    indexTree, computeShares, isShareComplete, descendantCount, progressFor, weightTotal, patchTree,
} from './knowledge_tree/model.js';
import { layoutTree, foldPoint, edgePath, PAD } from './knowledge_tree/layout.js';
import { createSpringField } from './knowledge_tree/motion.js';
import { createDragController } from './knowledge_tree/drag.js';
import { renderSide, readForm } from './knowledge_tree/side.js';
import { createStructureCommands } from './knowledge_tree/editing.js';
import { bindViewportGestures } from './knowledge_tree/gestures.js';
import { createProgressCommands } from './knowledge_tree/progress.js';
import { buildChrome, paintChrome, el, readPrefs, writePrefs } from './knowledge_tree/chrome.js';
import { fillNode, nodeKey, paintNode, paintFold } from './knowledge_tree/nodes.js';

export { KNOWLEDGE_TREE_THEMES, lifecycleLabel } from './knowledge_tree/chrome.js';
export { indexTree, computeShares, progressFor, descendantCount } from './knowledge_tree/model.js';
export { layoutTree, foldPoint, edgePath } from './knowledge_tree/layout.js';

const FOLDS_KEY = 'lanshare:knowledge-tree:folds:';
const ZOOM_MIN = 0.4;
const ZOOM_MAX = 1.6;
const ZOOM_FIT_MIN = 0.75;
const LEAVE_MS = 260;
const REFOLD_DELAY_MS = 240; // let a dropped branch settle before its peers unfold again

const isTyping = target => Boolean(target?.closest?.('input, textarea, select, [contenteditable="true"]'));

/**
 * Open the knowledge tree dialog for one session.
 * @param {{classOfferingId: number|string, session: object, canEdit: boolean, trigger?: Element,
 *          onChange?: (sessionId: number, state: object) => void, onClose?: () => void}} options
 */
export function openKnowledgeTree({ classOfferingId, session, canEdit, trigger, onChange, onClose }) {
    const sessionId = Number(session?.id || 0);
    if (!sessionId) return null;
    const base = `/api/classrooms/${encodeURIComponent(classOfferingId)}/sessions/${sessionId}/knowledge-tree`;
    const uid = `kt${sessionId}x${Math.random().toString(36).slice(2, 7)}`;
    const prefs = readPrefs();
    let data = null;
    let index = null;           // index of the committed tree
    let displayIndex = null;    // what the canvas shows (a drop preview while dragging)
    let selectedId = '';
    let editingId = '';
    let closed = false;
    let mutationSeq = 0;
    let queue = Promise.resolve();
    let layout = null;
    let zoom = 1;
    let offset = null;          // canvas offset inside the sizer, kept stable across resizes
    const sizes = new Map();
    const dirtySizes = new Set();
    const nodeEls = new Map();
    const leavingEls = new Map();
    const edgeEls = new Map();
    const foldEls = new Map();
    const descendants = new Map();
    let shares = new Map();
    const foldsKey = `${FOLDS_KEY}${sessionId}`;
    let collapsed = new Set();
    try { collapsed = new Set(JSON.parse(window.localStorage.getItem(foldsKey) || '[]')); } catch { collapsed = new Set(); }
    let dragFolds = null;       // peers folded while a branch is being dragged
    let refoldTimer = 0;
    let deferredSync = false;
    let pendingEditId = '';     // a node created by a superseded request, edited once it appears

    const ui = buildChrome({ canEdit, prefs, session });
    const { root, shell, main, viewport, canvas, svg, nodesLayer, foldLayer, sizer, side, directionButtons, themeSelect, zoomValue, generateBtn } = ui;
    const reducedMotion = () => Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || shell.closest('[data-lq-motion="off"]'));

    // ---- helpers ----------------------------------------------------------
    const doneSet = () => new Set(data?.completed_ids || []);
    const isGenerating = () => data?.status === 'generating';
    const editable = () => canEdit && Boolean(data?.has_tree) && !isGenerating();
    const currentTotal = () => (data?.tree ? weightTotal(data.tree) : 0);
    const effectiveFolds = () => dragFolds || collapsed;
    const renderChrome = () => paintChrome(ui, { data, canEdit, total: currentTotal() });

    // ---- canvas: elements are reconciled by id, never rebuilt wholesale ----
    let measureFrame = 0;
    const nodeResizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(entries => {
        for (const entry of entries) if (entry.target.dataset.nodeId && nodeEls.has(entry.target.dataset.nodeId)) dirtySizes.add(entry.target.dataset.nodeId);
        if (drag.active || !dirtySizes.size) return;
        window.cancelAnimationFrame(measureFrame);
        measureFrame = window.requestAnimationFrame(() => { if (!closed && layout && !viewport.hidden && dirtySizes.size) applyLayout(); });
    }) : null;

    /** Create, update or retire elements so they match ``index``; ``previous`` locates leaving nodes. */
    function syncNodes(previous) {
        if (!index) return;
        for (const [id, info] of index.map) {
            let node = nodeEls.get(id);
            if (!node) {
                node = el('button', 'kt-node');
                node.type = 'button';
                node.dataset.nodeId = id;
                node.setAttribute('role', 'treeitem');
                if (layout) node.classList.add('is-entering');
                nodesLayer.append(node);
                nodeEls.set(id, node);
                nodeResizeObserver?.observe(node);
            }
            const key = nodeKey(info);
            if (node.ktKey !== key) { fillNode(node, info, canEdit); node.ktKey = key; dirtySizes.add(id); }
            if (info.parent && !edgeEls.has(id)) {
                const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                svg.append(path);
                edgeEls.set(id, path);
            }
            const path = edgeEls.get(id);
            if (path) {
                path.dataset.branch = String(Math.max(0, info.branch));
                path.setAttribute('class', `kt-edge kt-edge--l${info.level}`);
            }
            if (info.children.length && !foldEls.has(id)) {
                const fold = el('button', 'kt-fold');
                fold.type = 'button';
                fold.dataset.foldId = id;
                foldLayer.append(fold);
                foldEls.set(id, fold);
            }
            foldEls.get(id)?.setAttribute('data-branch', String(Math.max(0, info.branch)));
            descendants.set(id, descendantCount(info, index));
        }
        for (const [id, node] of [...nodeEls]) if (!index.map.has(id)) retireNode(id, node, previous);
        for (const [id, path] of [...edgeEls]) if (!index.map.get(id)?.parent) { path.remove(); edgeEls.delete(id); }
        for (const [id, fold] of [...foldEls]) if (!index.map.get(id)?.children.length) { fold.remove(); foldEls.delete(id); descendants.delete(id); }
        const pruned = new Set([...collapsed].filter(id => index.map.get(id)?.children.length));
        if (pruned.size !== collapsed.size) { collapsed = pruned; saveFolds(); }
    }

    /** A removed node shrinks into its nearest surviving ancestor before it leaves the DOM. */
    function retireNode(id, node, previous) {
        nodeEls.delete(id);
        leavingEls.set(id, node);
        nodeResizeObserver?.unobserve(node);
        node.classList.add('is-leaving');
        node.disabled = true;
        let anchor = previous?.map.get(id)?.parent;
        while (anchor && !index.map.has(anchor.node.id)) anchor = anchor.parent;
        const target = anchor && boxAt(anchor.node.id);
        const size = sizes.get(id) || { w: 0, h: 0 };
        if (springs.get(id) && target) {
            const point = foldPoint(target, prefs.direction);
            springs.set(id, point.x - size.w / 2, point.y - size.h / 2);
        }
        sizes.delete(id);
        window.setTimeout(() => {
            node.remove();
            // The id may be back (failed delete restored by refresh): never touch the live node.
            if (leavingEls.get(id) === node) leavingEls.delete(id);
            if (!nodeEls.has(id)) springs.delete(id);
        }, reducedMotion() ? 0 : LEAVE_MS);
    }

    function saveFolds() {
        try { window.localStorage.setItem(foldsKey, JSON.stringify([...collapsed])); } catch { /* storage unavailable */ }
    }

    function measure() {
        for (const id of nodeEls.keys()) if (!sizes.has(id)) dirtySizes.add(id);
        for (const id of dirtySizes) {
            const node = nodeEls.get(id);
            if (node) sizes.set(id, { w: node.offsetWidth, h: node.offsetHeight });
        }
        dirtySizes.clear();
    }

    // Spring positions drive nodes, edges and fold dots in the same frame.
    const springs = createSpringField({ reduced: reducedMotion, onFrame: drawFrame });

    function boxAt(id) {
        const body = springs.get(id);
        const size = sizes.get(id);
        if (!body || !size) return layout?.boxes.get(id) || null;
        return { x: body.x, y: body.y, w: size.w, h: size.h };
    }

    function drawFrame(bodies) {
        for (const [id, body] of bodies) {
            const node = nodeEls.get(id) || leavingEls.get(id);
            if (node) node.style.transform = `translate3d(${body.x.toFixed(2)}px, ${body.y.toFixed(2)}px, 0)`;
        }
        if (!displayIndex || !layout) return;
        for (const [id, path] of edgeEls) {
            const info = displayIndex.map.get(id);
            const from = info?.parent && boxAt(info.parent.node.id);
            const to = boxAt(id);
            if (from && to) path.setAttribute('d', edgePath(from, to, prefs.direction));
        }
        for (const [id, fold] of foldEls) {
            const box = boxAt(id);
            if (!box) continue;
            const point = foldPoint(box, prefs.direction);
            fold.style.transform = `translate3d(${point.x.toFixed(2)}px, ${point.y.toFixed(2)}px, 0) translate(-50%, -50%)`;
        }
    }

    function paintState() {
        if (!index) return;
        shares = computeShares(index, doneSet());
        const folds = effectiveFolds();
        const hiddenNow = id => (layout ? !layout.visible.has(id) : false);
        for (const [id, info] of index.map) {
            const node = nodeEls.get(id);
            if (node) paintNode(node, info, shares.get(id), { selected: id === selectedId, hidden: hiddenNow(id), folded: folds.has(id), canEdit });
        }
        for (const [id, path] of edgeEls) {
            path.classList.toggle('is-hidden', hiddenNow(id));
            path.classList.toggle('is-done', isShareComplete(shares.get(id)));
        }
        for (const [id, fold] of foldEls) {
            const info = displayIndex?.map.get(id) || index.map.get(id);
            if (info) paintFold(fold, info, { folded: folds.has(id), hidden: hiddenNow(id), count: descendants.get(id) || 0 });
        }
    }

    function fitZoom() {
        if (!layout) return 1;
        const scale = Math.min((viewport.clientWidth - 8) / layout.width, (viewport.clientHeight - 8) / layout.height);
        return Math.max(ZOOM_FIT_MIN, Math.min(1, scale));
    }

    /**
     * Size the canvas for ``zoom``. ``recenter`` centres a small tree; otherwise the current
     * offset is kept and only clamped, so opening the side panel or growing the tree never
     * slides the whole picture. ``anchor`` (viewport point) stays over the same canvas spot.
     */
    function setZoom(value, { keepCenter = true, anchor = null, recenter = true } = {}) {
        const next = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(value * 1000) / 1000));
        const ax = anchor ? anchor.x : viewport.clientWidth / 2;
        const ay = anchor ? anchor.y : viewport.clientHeight / 2;
        const cx = (viewport.scrollLeft + ax - canvas.offsetLeft) / zoom;
        const cy = (viewport.scrollTop + ay - canvas.offsetTop) / zoom;
        zoom = next;
        canvas.style.setProperty('--kt-zoom', String(zoom));
        if (layout) {
            const width = layout.width * zoom, height = layout.height * zoom;
            const roomX = Math.max(0, viewport.clientWidth - width);
            const roomY = Math.max(0, viewport.clientHeight - height);
            const bt = prefs.direction === 'bt';
            let x = roomX / 2;
            let y = bt ? roomY : roomY / 2;
            if (!recenter && offset) {
                // Keep the tree where it is: a narrower viewport (side panel) or a grown tree
                // scrolls instead of sliding; the next fit/zoom recentres.
                x = Math.max(0, offset.x);
                if (!bt) y = Math.max(0, offset.y);
            }
            offset = { x: Math.floor(x), y: Math.floor(y) };
            canvas.style.left = `${offset.x}px`;
            canvas.style.top = `${offset.y}px`;
            sizer.style.width = `${Math.ceil(width + offset.x)}px`;
            sizer.style.height = `${Math.ceil(height + offset.y)}px`;
        }
        zoomValue.textContent = `${Math.round(zoom * 100)}%`;
        if (keepCenter) {
            viewport.scrollLeft = Math.max(0, canvas.offsetLeft + cx * zoom - ax);
            viewport.scrollTop = Math.max(0, canvas.offsetTop + cy * zoom - ay);
        }
    }

    /**
     * Recompute target positions and let the springs carry every node there.
     * ``anchorId`` keeps that node's screen position; everything else moves relative to it.
     */
    function applyLayout({ center = false, fit = false, anchorId = '', snap = false } = {}) {
        if (!displayIndex || viewport.hidden) return;
        measure();
        const first = !layout;
        const before = layout && anchorId ? layout.boxes.get(anchorId) : null;
        const frame0 = { ox: canvas.offsetLeft, oy: canvas.offsetTop, sl: viewport.scrollLeft, st: viewport.scrollTop };
        const screen = before && { x: frame0.ox + before.x * zoom - frame0.sl, y: frame0.oy + before.y * zoom - frame0.st };
        layout = layoutTree(displayIndex, sizes, prefs.direction, effectiveFolds());
        canvas.style.width = `${layout.width}px`;
        canvas.style.height = `${layout.height}px`;
        svg.setAttribute('width', String(layout.width));
        svg.setAttribute('height', String(layout.height));
        svg.setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`);
        setZoom(fit ? fitZoom() : zoom, { keepCenter: false, recenter: first || fit });
        const after = anchorId ? layout.boxes.get(anchorId) : null;
        // Only re-anchor when the anchor really moved: rewriting an unchanged scroll position
        // rounds sub-pixels and nudges the whole picture.
        if (screen && after && (Math.abs(after.x - before.x) > 0.5 || Math.abs(after.y - before.y) > 0.5
            || canvas.offsetLeft !== frame0.ox || canvas.offsetTop !== frame0.oy)) {
            viewport.scrollLeft = canvas.offsetLeft + after.x * zoom - screen.x;
            viewport.scrollTop = canvas.offsetTop + after.y * zoom - screen.y;
        }
        // Keep the current picture still while the canvas frame moved underneath it.
        if (!first) {
            springs.shiftAll(((frame0.ox - frame0.sl) - (canvas.offsetLeft - viewport.scrollLeft)) / zoom,
                ((frame0.oy - frame0.st) - (canvas.offsetTop - viewport.scrollTop)) / zoom);
        }
        for (const [id, box] of layout.boxes) {
            if (!nodeEls.has(id) || id === drag.draggedId) continue;
            let from = null;
            if (!springs.get(id)) {
                // New nodes grow out of their parent's fold dot.
                const parentId = displayIndex.map.get(id)?.parent?.node.id;
                const parentBox = parentId && boxAt(parentId);
                if (parentBox) { const point = foldPoint(parentBox, prefs.direction); from = { x: point.x - box.w / 2, y: point.y - box.h / 2 }; }
            }
            springs.set(id, box.x, box.y, { from, snap: first || snap });
        }
        paintState();
        springs.draw();
        springs.start();
        window.requestAnimationFrame(() => { for (const node of nodesLayer.querySelectorAll('.kt-node.is-entering')) node.classList.remove('is-entering'); });
        if (center) centerOn('__root');
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
            viewport.scrollTo({ left: Math.max(0, x + w / 2 - clientWidth / 2), top: Math.max(0, y + h / 2 - clientHeight / 2), behavior: reducedMotion() ? 'auto' : 'smooth' });
        }
    }

    // ---- folding ----------------------------------------------------------
    function setFolds(next, anchorId = '__root') {
        collapsed = next;
        saveFolds();
        applyLayout({ anchorId });
        if (selectedId && layout && !layout.visible.has(selectedId)) select('');
    }
    function toggleFold(id) {
        const next = new Set(collapsed);
        if (next.has(id)) next.delete(id); else next.add(id);
        setFolds(next, id);
    }
    function expandTo(id) {
        let info = index?.map.get(id)?.parent;
        const next = new Set(collapsed);
        while (info) { next.delete(info.node.id); info = info.parent; }
        if (next.size !== collapsed.size) setFolds(next, id);
    }

    // ---- sidebar ----------------------------------------------------------
    let formLease = null;
    function refreshSide() {
        const info = index?.map.get(selectedId);
        side.hidden = !info;
        main.classList.toggle('has-side', Boolean(info));
        if (!info) { formLease?.dispose?.(); formLease = null; side.replaceChildren(); editingId = ''; return; }
        // Never rebuild an open form under the teacher's typing.
        const openForm = side.querySelector('[data-kt-edit-form]');
        if (openForm && openForm.dataset.ktEditForm === editingId && editingId === selectedId) return;
        formLease?.dispose?.();
        formLease = null;
        renderSide(side, info, {
            index, data, shares, done: doneSet(), canEdit, busy: !editable(),
            editing: Boolean(editingId) && editingId === selectedId, uid, total: currentTotal(),
        });
        if (side.querySelector('.lq-field')) formLease = enhanceForms(side);
    }

    function select(id, { reveal = false } = {}) {
        if (editingId && editingId !== id) void saveEdit({ quiet: true });
        if (id && reveal) expandTo(id);
        selectedId = id;
        paintState();
        refreshSide();
        if (reveal) revealNode(id);
    }

    // ---- state ------------------------------------------------------------
    function syncTree(previous) {
        if (data?.has_tree) {
            index = indexTree(data.tree);
            displayIndex = index;
            if (selectedId && !index.map.has(selectedId)) { selectedId = ''; editingId = ''; }
            const first = !layout;
            syncNodes(previous);
            applyLayout({ center: first, fit: first, anchorId: first ? '' : (selectedId || '__root') });
            return;
        }
        index = null;
        displayIndex = null;
        selectedId = '';
        editingId = '';
        for (const [id, node] of nodeEls) { nodeResizeObserver?.unobserve(node); node.remove(); springs.delete(id); }
        nodeEls.clear(); edgeEls.clear(); foldEls.clear(); sizes.clear(); descendants.clear();
        svg.replaceChildren();
        foldLayer.replaceChildren();
        layout = null;
    }

    function applyState(next) {
        const previous = index;
        data = next;
        renderChrome();
        if (drag.active) { deferredSync = true; onChange?.(sessionId, data); return; }
        syncTree(previous);
        paintState();
        refreshSide();
        onChange?.(sessionId, data);
        if (pendingEditId && index?.map.has(pendingEditId)) { const id = pendingEditId; pendingEditId = ''; startEdit(id); }
    }

    const request = (path = '', options = {}) => apiFetch(`${base}${path}`, { silent: true, ...options });

    async function refresh() {
        try {
            applyState(await request());
        } catch (error) {
            ui.notice.replaceChildren(createStatus('alert', { tone: 'danger', title: '知识树读取失败', body: error.message || '请稍后重试。' }));
            ui.notice.hidden = false;
        }
    }

    // Serialize writes; only the latest response is applied, a failure restores
    // the server truth instead of leaving an optimistic guess on screen.
    function mutate(path, options, optimistic, after) {
        if (!editable()) return Promise.resolve(null);
        const seq = ++mutationSeq;
        if (optimistic) applyState({ ...data, ...optimistic(data) });
        const run = queue.then(async () => {
            let next;
            try {
                next = await request(path, options);
            } catch (error) {
                showToast(error.message || '保存失败，请重试。', 'error');
                if (!closed && seq === mutationSeq) await refresh();
                return null;
            }
            if (closed) return next;
            // Only the latest response is painted; every success still runs its follow-up.
            if (seq === mutationSeq) applyState(next);
            after?.(next);
            return next;
        });
        // A rendering bug must surface, but never stall the write queue.
        queue = run.catch(error => { window.reportError?.(error); return null; });
        return queue;
    }

    function toggleLeaf(id) {
        const info = index?.map.get(id);
        if (!info?.isLeaf || !editable()) return;
        const current = doneSet();
        const done = !current.has(id);
        if (done) current.add(id); else current.delete(id);
        const node = nodeEls.get(id);
        if (node && !reducedMotion()) {
            node.classList.remove('is-ticked');
            void node.offsetWidth; // restart the tick animation on repeated toggles
            node.classList.add('is-ticked');
        }
        mutate(`/leaves/${encodeURIComponent(id)}`, { method: 'POST', body: { done } }, state => progressFor(state.tree, [...current]));
    }

    function startEdit(id) {
        if (!editable() || !index?.map.has(id)) return;
        if (editingId && editingId !== id) void saveEdit({ quiet: true });
        expandTo(id);
        selectedId = id;
        editingId = id;
        paintState();
        refreshSide();
        revealNode(id);
        const field = side.querySelector('[data-kt-field="label"]');
        field?.focus({ preventScroll: true });
        field?.select?.();
    }

    function stopEdit() {
        const id = editingId;
        editingId = '';
        refreshSide();
        if (id) nodeEls.get(id)?.focus({ preventScroll: true });
    }

    async function saveEdit({ quiet = false } = {}) {
        const form = side.querySelector('[data-kt-edit-form]');
        const id = editingId;
        const info = index?.map.get(id);
        if (!form || !info || form.dataset.ktEditForm !== id) { editingId = ''; return; }
        const values = readForm(form);
        const label = String(values.label || '').trim();
        if (!label && !quiet) { form.querySelector('[data-kt-field="label"]')?.reportValidity?.(); return; }
        const current = id === '__root' ? { label: info.node.label, detail: info.node.summary || '' } : info.node;
        const changes = {};
        for (const [key, value] of Object.entries(values)) {
            if (key === 'label' && !label) continue; // a blank name is never saved
            const text = String(value || '').trim();
            if (text !== String(current[key] || '').trim()) changes[key] = text;
        }
        if (quiet) editingId = ''; else stopEdit();
        if (!Object.keys(changes).length) return;
        const saved = await mutate(`/nodes/${encodeURIComponent(id)}`, { method: 'PATCH', body: changes }, state => ({ tree: patchTree(state.tree, id, changes) }));
        // A rejected save must not lose the teacher's text: reopen the form with the draft.
        if (!saved && !closed && index?.map.has(id) && !editingId) restoreDraft(id, values);
    }

    function restoreDraft(id, values) {
        startEdit(id);
        const form = side.querySelector(`[data-kt-edit-form="${CSS.escape(id)}"]`);
        if (!form) return;
        for (const control of form.querySelectorAll('[data-kt-field]')) {
            if (values[control.dataset.ktField] !== undefined) control.value = values[control.dataset.ktField];
        }
        formLease?.refresh?.();
    }

    const commands = createStructureCommands({
        data: () => data,
        index: () => index,
        editingId: () => editingId,
        closed: () => closed,
        editable,
        currentTotal,
        mutate,
        saveEdit,
        startEdit,
        refreshSide,
        focusNode: id => nodeEls.get(id)?.focus({ preventScroll: true }),
        unfold(id) { if (collapsed.has(id)) { const next = new Set(collapsed); next.delete(id); collapsed = next; saveFolds(); } },
        setSelected(id) { selectedId = id; },
        clearEditing() { editingId = ''; },
        editLater(id) { pendingEditId = id; },
    });
    const { moveNode, moveBy, addNode, addSibling, requestDelete, saveWeight, normalizeWeights } = commands;

    const lifecycle = createProgressCommands({
        data: () => data, index: () => index, canEdit, closed: () => closed, editingId: () => editingId,
        saveEdit, generateBtn, request, applyState, refresh, mutate,
    });
    const { generate, bulk } = lifecycle;

    // ---- drag to reorder (any node but the root) ---------------------------
    const canvasPoint = event => {
        const rect = canvas.getBoundingClientRect();
        return { x: (event.clientX - rect.left) / zoom, y: (event.clientY - rect.top) / zoom };
    };
    const drag = createDragController({
        viewport, nodesLayer, canvasPoint,
        allowed: id => editable() && id !== '__root' && Boolean(layout) && !editingId,
        tree: () => data.tree,
        index: () => index,
        layout: () => layout,
        direction: () => prefs.direction,
        bodyOf: id => springs.get(id) || layout.boxes.get(id),
        begin(id) {
            window.clearTimeout(refoldTimer);
            const info = index.map.get(id);
            nodeEls.get(id)?.classList.add('is-dragging');
            viewport.classList.add('is-dragging-node');
            if (!info.isLeaf) {
                // Peers fold into their dots so whole branches are easy to reorder.
                dragFolds = new Set(collapsed);
                for (const item of index.map.values()) if (item.level === info.level && item.children.length) dragFolds.add(item.node.id);
                applyLayout({ anchorId: id });
            }
        },
        preview(tree) {
            displayIndex = tree ? indexTree(tree) : index;
            applyLayout();
            return displayIndex;
        },
        follow(id, x, y, vx) {
            springs.pin(id, x, y);
            nodeEls.get(id)?.style.setProperty('--kt-tilt', `${Math.max(-4, Math.min(4, vx / 260)).toFixed(2)}deg`);
            springs.draw();
        },
        end({ id, changed, parentId, index: position, tree }) {
            const node = nodeEls.get(id);
            node?.classList.remove('is-dragging');
            node?.style.removeProperty('--kt-tilt');
            viewport.classList.remove('is-dragging-node');
            springs.release(id);
            displayIndex = index;
            const settleFolds = () => {
                if (!dragFolds) return;
                // Settle first, then let the folded peers unfold around the new order.
                refoldTimer = window.setTimeout(() => { dragFolds = null; if (!closed) applyLayout({ anchorId: id }); }, reducedMotion() ? 0 : REFOLD_DELAY_MS);
            };
            if (changed) {
                if (collapsed.has(parentId)) { const next = new Set(collapsed); next.delete(parentId); collapsed = next; saveFolds(); }
                deferredSync = false;
                selectedId = id;
                moveNode(id, parentId, position, tree);
            } else if (deferredSync) {
                deferredSync = false;
                syncTree(index);
                paintState();
            } else {
                applyLayout();
            }
            settleFolds();
            select(id);
        },
    });

    // ---- clicks and keys --------------------------------------------------
    nodesLayer.addEventListener('click', event => {
        const node = event.target.closest('.kt-node');
        if (!node || node.classList.contains('is-leaving')) return;
        if (drag.consumeClick()) return;
        const info = index?.map.get(node.dataset.nodeId);
        if (!info) return;
        // The round check marks the unit; anywhere else opens its details.
        if (info.isLeaf && canEdit && event.target.closest('.kt-node__check')) {
            toggleLeaf(info.node.id);
            if (selectedId === info.node.id) refreshSide();
            return;
        }
        select(info.node.id);
    });
    nodesLayer.addEventListener('dblclick', event => {
        const node = event.target.closest('.kt-node');
        if (!node || !editable() || event.target.closest('.kt-node__check')) return;
        startEdit(node.dataset.nodeId);
    });
    nodesLayer.addEventListener('keyup', event => {
        if (event.key === ' ' && canEdit && event.target.closest('.kt-node[data-leaf]')) event.preventDefault();
    });
    nodesLayer.addEventListener('keydown', event => {
        const node = event.target.closest('.kt-node');
        const info = node && index?.map.get(node.dataset.nodeId);
        if (!info || isTyping(event.target)) return;
        const id = info.node.id;
        const plain = !event.ctrlKey && !event.metaKey && !event.altKey;
        // Tab adds a child only under the selected node; otherwise it moves focus as usual.
        if (canEdit && plain && event.key === 'Tab' && !event.shiftKey && id === selectedId) { event.preventDefault(); void addNode(id); return; }
        if (canEdit && plain && (event.key === 'Delete' || event.key === 'Backspace')) { event.preventDefault(); void requestDelete(id); return; }
        if (canEdit && plain && event.key === 'F2') { event.preventDefault(); startEdit(id); return; }
        if (canEdit && plain && event.key === ' ' && info.isLeaf) {
            event.preventDefault();
            toggleLeaf(id);
            if (selectedId === id) refreshSide();
            return;
        }
        if (event.key === 'Escape' && selectedId) { event.preventDefault(); event.stopPropagation(); select(''); return; }
        const keys = prefs.direction !== 'bt'
            ? { parent: 'ArrowLeft', child: 'ArrowRight', prev: 'ArrowUp', next: 'ArrowDown' }
            : { parent: 'ArrowDown', child: 'ArrowUp', prev: 'ArrowLeft', next: 'ArrowRight' };
        if (event.altKey && canEdit && (event.key === keys.prev || event.key === keys.next)) {
            event.preventDefault();
            moveBy(id, event.key === keys.prev ? -1 : 1);
            return;
        }
        const go = target => { if (target) { event.preventDefault(); nodeEls.get(target.node.id)?.focus(); revealNode(target.node.id); } };
        const siblings = info.parent ? info.parent.children : [];
        const position = siblings.findIndex(item => item.id === id);
        if (event.key === keys.parent) go(info.parent);
        else if (event.key === keys.child) {
            if (collapsed.has(id)) { event.preventDefault(); toggleFold(id); }
            else go(info.children[0] ? index.map.get(info.children[0].id) : null);
        }
        else if (event.key === keys.prev && position > 0) go(index.map.get(siblings[position - 1].id));
        else if (event.key === keys.next && position >= 0 && position < siblings.length - 1) go(index.map.get(siblings[position + 1].id));
    });

    side.addEventListener('submit', event => {
        if (!event.target.matches('[data-kt-edit-form]')) return;
        event.preventDefault();
        void saveEdit();
    });
    side.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || event.isComposing || !event.target.closest('[data-kt-edit-form]')) return;
        event.preventDefault();
        event.stopPropagation();
        stopEdit();
    });
    side.addEventListener('change', event => {
        const weight = event.target.closest('[data-kt-weight]');
        if (weight) saveWeight(weight.dataset.ktWeight, weight.value);
    });

    shell.addEventListener('click', event => {
        const target = event.target.closest('button');
        if (!target || !shell.contains(target) || target.disabled) return;
        if (target.matches('[data-kt-direction]')) {
            prefs.direction = target.dataset.ktDirection;
            writePrefs(prefs);
            shell.dataset.ktDirection = prefs.direction;
            syncDirectionButtons();
            for (const id of nodeEls.keys()) dirtySizes.add(id);
            applyLayout({ center: true, fit: true });
        } else if (target.matches('[data-kt-zoom]')) {
            const mode = target.dataset.ktZoom;
            if (mode === 'fit') fitAndCenter();
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
        else if (target.matches('[data-kt-toggle]')) { toggleLeaf(target.dataset.ktToggle); refreshSide(); }
        else if (target.matches('[data-kt-move]')) moveBy(selectedId, Number(target.dataset.ktMove));
        else if (target.matches('[data-kt-edit]')) startEdit(target.dataset.ktEdit);
        else if (target.matches('[data-kt-edit-cancel]')) stopEdit();
        else if (target.matches('[data-kt-add-child]')) void addNode(target.dataset.ktAddChild);
        else if (target.matches('[data-kt-add-sibling]')) void addSibling(target.dataset.ktAddSibling);
        else if (target.matches('[data-kt-delete]')) void requestDelete(target.dataset.ktDelete);
        else if (target.matches('[data-kt-normalize]')) normalizeWeights();
    });
    generateBtn.addEventListener('click', () => void generate());
    foldLayer.addEventListener('click', event => {
        const fold = event.target.closest('.kt-fold');
        if (fold) toggleFold(fold.dataset.foldId);
    });
    const fitAndCenter = () => { setZoom(fitZoom(), { keepCenter: false }); centerOn('__root'); };
    const gestures = bindViewportGestures({ viewport, ready: () => Boolean(layout), zoomBy: (factor, anchor) => setZoom(zoom * factor, { anchor }), fit: fitAndCenter });
    // With a node selected, Tab / Delete / F2 also work while the canvas itself has focus.
    viewport.addEventListener('keydown', event => {
        if (event.target !== viewport || !canEdit || !selectedId || event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key === 'Tab' && !event.shiftKey) { event.preventDefault(); void addNode(selectedId); }
        else if (event.key === 'Delete') { event.preventDefault(); void requestDelete(selectedId); }
        else if (event.key === 'F2') { event.preventDefault(); startEdit(selectedId); }
    });
    themeSelect.addEventListener('change', () => {
        prefs.theme = themeSelect.value;
        writePrefs(prefs);
        shell.dataset.ktTheme = prefs.theme;
        for (const id of nodeEls.keys()) dirtySizes.add(id);
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

    // The side panel and window size change the viewport: re-clamp the canvas and
    // compensate by scrolling, so the tree stays where it is on screen.
    let resizeFrame = 0;
    const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
        window.cancelAnimationFrame(resizeFrame);
        resizeFrame = window.requestAnimationFrame(() => {
            if (!layout || viewport.hidden) return;
            const before = { ox: canvas.offsetLeft, oy: canvas.offsetTop, sl: viewport.scrollLeft, st: viewport.scrollTop };
            setZoom(zoom, { keepCenter: false, recenter: false });
            viewport.scrollLeft = before.sl + (canvas.offsetLeft - before.ox);
            viewport.scrollTop = before.st + (canvas.offsetTop - before.oy);
        });
    }) : null;
    resizeObserver?.observe(viewport);

    let dropdowns = [];
    function cleanup() {
        if (closed) return;
        if (editingId) void saveEdit({ quiet: true });
        closed = true;
        drag.cancel();
        springs.stop();
        resizeObserver?.disconnect();
        nodeResizeObserver?.disconnect();
        window.cancelAnimationFrame(resizeFrame);
        window.cancelAnimationFrame(measureFrame);
        gestures.dispose();
        lifecycle.stop();
        window.clearTimeout(refoldTimer);
        formLease?.dispose?.();
        dropdowns.forEach(binding => binding.destroy?.());
        dropdowns = [];
        onClose?.();
    }
    openDialog(root, {
        trigger,
        onClose: cleanup,
        onDestroy: cleanup,
    });
    dropdowns = enhanceDropdowns(shell);

    renderChrome();
    void refresh().then(() => { if (isGenerating()) lifecycle.schedulePoll(); });
    return { refresh };
}
