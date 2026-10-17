// 知识树结构命令：新增（下级/同级）、删除（确认）、移动、比重修改与归一化。
// 先用 model.js 做与服务端同规则的乐观预演，再经控制器的串行写队列提交；
// 状态与渲染仍归 session_knowledge_tree.js，所有访问都通过 ctx。
import { showToast } from '/static/js/ui.js';
import { confirm as lqConfirm } from '../lq/dialogs.js';
import {
    progressFor, leafWeight, moveInTree, removeFromTree, removalSummary, siblingIds, canAddChild, addBlocker, patchTree,
} from './model.js';

const withTree = (tree, completedIds) => ({ tree, ...progressFor(tree, completedIds) });
const nodePath = id => `/nodes/${encodeURIComponent(id)}`;
const apiParent = id => (id === '__root' ? '' : id);

/**
 * ctx: {
 *   data(), index(), editingId(), closed(), editable(), currentTotal(),
 *   mutate(path, options, optimistic, after) -> Promise<state|null>,
 *   saveEdit({quiet}), startEdit(id), refreshSide(), focusNode(id),
 *   unfold(id), setSelected(id), clearEditing(), editLater(id)
 * }
 */
export function createStructureCommands(ctx) {
    function moveNode(id, parentId, position, tree = null) {
        const next = tree || moveInTree(ctx.data().tree, id, parentId, position);
        if (!next) return;
        ctx.mutate(`${nodePath(id)}/move`, { method: 'POST', body: { parent_id: apiParent(parentId), index: position } },
            state => withTree(next, state.completed_ids || []));
    }

    function moveBy(id, delta) {
        const index = ctx.index();
        const info = index?.map.get(id);
        if (!info?.parent || !ctx.editable()) return;
        const ids = siblingIds(index, id);
        const to = ids.indexOf(id) + delta;
        if (to < 0 || to >= ids.length) return;
        moveNode(id, info.parent.node.id, to);
        window.requestAnimationFrame(() => ctx.focusNode(id));
    }

    async function addNode(parentId, position = null) {
        if (!ctx.editable()) return;
        const index = ctx.index();
        const parent = index?.map.get(parentId);
        if (!parent) return;
        if (!canAddChild(parent)) { showToast('知识树最多三层，已改为添加同级知识点。', 'info'); await addSibling(parentId); return; }
        const blocked = addBlocker(index, parent);
        if (blocked) { showToast(blocked, 'warning'); return; }
        if (ctx.editingId()) await ctx.saveEdit({ quiet: true });
        ctx.unfold(parentId);
        const body = { parent_id: apiParent(parentId) };
        if (position !== null) body.index = position;
        const subdivided = parent.isLeaf ? parent.node.label : '';
        await ctx.mutate('/nodes', { method: 'POST', body }, null, next => {
            if (!next?.created_id) return;
            if (subdivided) showToast(`「${subdivided}」已细分，新知识点继承了它的比重与完成状态。`, 'info');
            if (ctx.index()?.map.has(next.created_id)) ctx.startEdit(next.created_id);
            else ctx.editLater(next.created_id); // a later request's response will bring it
        });
    }

    function addSibling(id) {
        const index = ctx.index();
        const info = index?.map.get(id);
        if (!info?.parent) return Promise.resolve();
        return addNode(info.parent.node.id, siblingIds(index, id).indexOf(id) + 1);
    }

    function deleteMessage(summary) {
        const label = summary.info.node.label;
        const lines = [summary.info.isLeaf
            ? `将删除基础单元「${label}」（比重 ${summary.weight}%${summary.doneLeaves ? '，已完成' : ''}）。`
            : `将删除「${label}」及其下 ${summary.descendants} 个知识点（含 ${summary.leaves} 个基础单元，比重合计 ${summary.weight}%${summary.doneLeaves ? `，其中 ${summary.doneLeaves} 个已完成` : ''}）。`];
        if (summary.parentBecomesUnit) lines.push(`「${summary.info.parent.node.label}」将重新成为基础单元，并承接这 ${summary.weight}% 比重。`);
        else if (summary.weight) lines.push(`删除后比重合计为 ${ctx.currentTotal() - summary.weight}%，可在侧栏一键按比例调整为 100%。`);
        return lines.join('');
    }

    async function requestDelete(id) {
        if (!ctx.editable()) return;
        const data = ctx.data();
        const summary = removalSummary(ctx.index(), id, new Set(data.completed_ids || []));
        if (!summary || summary.info.level === 0) return;
        if (summary.info.level === 1 && summary.info.parent.children.length <= 1) {
            showToast('至少保留一个大知识点；如需整体替换请使用“重新生成”。', 'warning');
            return;
        }
        const ok = await lqConfirm({ title: summary.info.isLeaf ? '删除基础单元？' : '删除知识点及其下级？', message: deleteMessage(summary), confirmLabel: '删除', danger: true });
        if (!ok || ctx.closed() || !ctx.index()?.map.has(id) || !ctx.editable()) return;
        const editing = ctx.editingId();
        if (editing && editing !== id) await ctx.saveEdit({ quiet: true });
        if (!ctx.index()?.map.has(id)) return;
        const latest = ctx.data();
        const result = removeFromTree(latest.tree, id, latest.completed_ids || []);
        if (!result) return;
        if (ctx.editingId() === id) ctx.clearEditing();
        ctx.setSelected(result.parentId);
        ctx.mutate(nodePath(id), { method: 'DELETE' }, () => withTree(result.tree, result.completed));
        window.requestAnimationFrame(() => ctx.focusNode(result.parentId));
    }

    function saveWeight(id, raw) {
        const info = ctx.index()?.map.get(id);
        if (!info?.isLeaf) return;
        const weight = Number(raw);
        if (String(raw).trim() === '' || !Number.isInteger(weight) || weight < 0 || weight > 100) {
            showToast('比重必须是 0–100 的整数。', 'warning');
            ctx.refreshSide();
            return;
        }
        if (weight === leafWeight(info.node)) return;
        ctx.mutate(nodePath(id), { method: 'PATCH', body: { weight } },
            state => withTree(patchTree(state.tree, id, { weight }), state.completed_ids || []));
    }

    function normalizeWeights() {
        ctx.mutate('/weights/normalize', { method: 'POST' }, null, () => showToast('已按原有比例把比重合计调整为 100%（比重为 0 的单元保持 0）。', 'success'));
    }

    return { moveNode, moveBy, addNode, addSibling, requestDelete, saveWeight, normalizeWeights };
}
