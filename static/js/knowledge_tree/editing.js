// 知识树结构命令：新增（下级/同级）、删除（确认）、移动、比重修改与归一化。
// 在 model.js 中更新本地草稿，每步记录历史；控制器统一保存并处理版本冲突。
// 状态与渲染仍归 session_knowledge_tree.js，所有访问都通过 ctx。
import { showToast } from '/static/js/ui.js';
import { confirm as lqConfirm } from '../lq/dialogs.js';
import {
    progressFor, leafWeight, moveInTree, removeFromTree, removalSummary, siblingIds, canAddChild, addBlocker, patchTree,
    addToTree, normalizeTreeWeights,
} from './model.js';

const withTree = (tree, completedIds) => ({ tree, ...progressFor(tree, completedIds) });

/**
 * ctx: {
 *   data(), index(), editingId(), closed(), editable(), currentTotal(),
 *   commit(patch, label) -> bool,
 *   saveEdit({quiet}), startEdit(id), refreshSide(), focusNode(id),
 *   unfold(id), setSelected(id), clearEditing(), editLater(id)
 * }
 */
export function createStructureCommands(ctx) {
    function moveNode(id, parentId, position, tree = null) {
        if (!ctx.editable()) return;
        const next = tree || moveInTree(ctx.data().tree, id, parentId, position);
        if (!next) return;
        ctx.commit(withTree(next, ctx.data().completed_ids || []), '移动知识点');
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
        if (ctx.editingId() && !await ctx.saveEdit({ quiet: true })) return;
        if (!ctx.editable()) return;
        ctx.unfold(parentId);
        const subdivided = parent.isLeaf ? parent.node.label : '';
        const next = addToTree(ctx.data().tree, ctx.data().completed_ids || [], parentId, position);
        if (!next) return;
        ctx.commit(withTree(next.tree, next.completed_ids), '新增知识点');
        if (subdivided) showToast(`「${subdivided}」已细分，新知识点继承了它的比重与完成状态。`, 'info');
        ctx.startEdit(next.created_id);
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
        if (editing && editing !== id && !await ctx.saveEdit({ quiet: true })) return;
        if (!ctx.index()?.map.has(id)) return;
        const latest = ctx.data();
        const result = removeFromTree(latest.tree, id, latest.completed_ids || []);
        if (!result) return;
        if (ctx.editingId() === id) ctx.clearEditing();
        ctx.setSelected(result.parentId);
        ctx.commit(withTree(result.tree, result.completed), '删除知识点');
        window.requestAnimationFrame(() => ctx.focusNode(result.parentId));
    }

    function saveWeight(id, raw) {
        if (!ctx.editable()) return;
        const info = ctx.index()?.map.get(id);
        if (!info?.isLeaf) return;
        const weight = Number(raw);
        if (String(raw).trim() === '' || !Number.isInteger(weight) || weight < 0 || weight > 100) {
            showToast('比重必须是 0–100 的整数。', 'warning');
            ctx.refreshSide();
            return;
        }
        if (weight === leafWeight(info.node)) return;
        ctx.commit(withTree(patchTree(ctx.data().tree, id, { weight }), ctx.data().completed_ids || []), '修改比重');
    }

    function normalizeWeights() {
        if (!ctx.editable()) return;
        const tree = normalizeTreeWeights(ctx.data().tree);
        if (tree) ctx.commit(withTree(tree, ctx.data().completed_ids || []), '调整比重合计');
    }

    return { moveNode, moveBy, addNode, addSibling, requestDelete, saveWeight, normalizeWeights };
}
