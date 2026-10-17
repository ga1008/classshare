// 知识树纯数据模型：索引、比重、进度，以及与服务端同规则的乐观预演操作。
// 规则真源是 classroom_app/services/session_knowledge_tree_edit_service.py；
// 这里的结果只用于在请求返回前先动起来，最终一律以服务端返回为准。

export const MAX_DEPTH = 3;
export const BRANCH_TONES = 8;
// Same limits as session_knowledge_tree_edit_service.py.
export const MAX_TOP = 8;
export const MAX_CHILDREN = 12;
export const MAX_LEAVES = 100;
export const MAX_NODES = 160;

const clone = value => JSON.parse(JSON.stringify(value));
export const leafWeight = node => Math.max(0, Math.trunc(Number(node?.weight) || 0));

/** Colour slot of a major point: stored ``tone`` (stable across reordering) or its position. */
export function toneOf(node, index) {
    const tone = Number.isInteger(node?.tone) ? node.tone : index;
    return ((tone % BRANCH_TONES) + BRANCH_TONES) % BRANCH_TONES;
}

/** Root + flattened index (parent links, level, top-level branch tone, leaf flags). */
export function indexTree(tree) {
    const root = { id: '__root', label: tree?.title || '本次课', summary: tree?.summary || '', children: tree?.nodes || [] };
    const map = new Map();
    const walk = (node, parent, level, branch) => {
        const children = Array.isArray(node.children) ? node.children : [];
        const info = { node, parent, level, branch, isLeaf: level > 0 && !children.length, children };
        map.set(node.id, info);
        children.forEach((child, index) => walk(child, info, level + 1, level === 0 ? toneOf(child, index) : branch));
    };
    walk(root, null, 0, -1);
    return { root, map };
}

/** Weight and unit totals for every node in one post-order pass. */
export function computeShares(index, done) {
    const shares = new Map();
    const visit = info => {
        let share;
        if (info.isLeaf) {
            const weight = leafWeight(info.node);
            const finished = done.has(info.node.id);
            share = { total: weight, done: finished ? weight : 0, leaves: 1, doneLeaves: finished ? 1 : 0 };
        } else {
            share = info.children.reduce((sum, child) => {
                const part = visit(index.map.get(child.id));
                return { total: sum.total + part.total, done: sum.done + part.done, leaves: sum.leaves + part.leaves, doneLeaves: sum.doneLeaves + part.doneLeaves };
            }, { total: 0, done: 0, leaves: 0, doneLeaves: 0 });
        }
        shares.set(info.node.id, share);
        return share;
    };
    visit(index.map.get('__root'));
    return shares;
}

export const isShareComplete = share => Boolean(share && share.leaves > 0 && share.doneLeaves >= share.leaves);
export const sharePercent = share => {
    if (!share?.leaves) return 0;
    return share.total > 0 ? Math.round((share.done / share.total) * 100) : Math.round((share.doneLeaves / share.leaves) * 100);
};

export function descendantCount(info, index) {
    return info.children.reduce((sum, child) => sum + 1 + descendantCount(index.map.get(child.id), index), 0);
}

export function leavesOf(nodes) {
    const out = [];
    const walk = list => list.forEach(node => (node.children?.length ? walk(node.children) : out.push(node)));
    walk(nodes || []);
    return out;
}

export const weightTotal = tree => leavesOf(tree?.nodes).reduce((sum, leaf) => sum + leafWeight(leaf), 0);

/** Progress fields exactly as the server computes them, for optimistic display. */
export function progressFor(tree, completedIds) {
    const leaves = leavesOf(tree?.nodes);
    const set = new Set(completedIds);
    const done = leaves.filter(leaf => set.has(leaf.id));
    const total = leaves.reduce((sum, leaf) => sum + leafWeight(leaf), 0);
    const doneWeight = done.reduce((sum, leaf) => sum + leafWeight(leaf), 0);
    let progress;
    if (total > 0) progress = total === 100 ? doneWeight : Math.floor(doneWeight * 100 / total + 0.5);
    else progress = leaves.length ? Math.floor(done.length * 100 / leaves.length + 0.5) : 0;
    progress = Math.max(0, Math.min(100, progress));
    if (leaves.length && done.length === leaves.length) progress = 100;
    else if (progress >= 100) progress = 99;
    return {
        completed_ids: done.map(leaf => leaf.id),
        completed_count: done.length,
        leaf_count: leaves.length,
        weight_total: total,
        progress,
        lifecycle: progress >= 100 ? 'completed' : (done.length ? 'in_progress' : 'not_started'),
    };
}

function locate(tree, id) {
    const walk = (nodes, parent, level) => {
        for (const node of nodes) {
            if (node.id === id) return { node, parent, siblings: nodes, level };
            const found = walk(node.children || [], node, level + 1);
            if (found) return found;
        }
        return null;
    };
    return walk(tree.nodes || [], null, 1);
}

function childrenOf(tree, parentId) {
    if (!parentId) return { list: tree.nodes, level: 1, parent: null };
    const found = locate(tree, parentId);
    if (!found) return null;
    if (!found.node.children) found.node.children = [];
    return { list: found.node.children, level: found.level + 1, parent: found.node };
}

/** Pin every major point's colour before positions change (mirrors the server). */
export function pinTones(tree) {
    (tree.nodes || []).forEach((node, index) => { if (!Number.isInteger(node.tone)) node.tone = index % BRANCH_TONES; });
    return tree;
}

function tidy(nodes) {
    for (const node of nodes || []) {
        if (Array.isArray(node.children) && !node.children.length) delete node.children;
        tidy(node.children);
    }
}

/** Ids a dragged node may be dropped under: one level up, a branch (or the root for majors). */
export function dropParents(index, id) {
    const info = index.map.get(id);
    if (!info?.parent) return [];
    const parentLevel = info.level - 1;
    return [...index.map.values()]
        .filter(item => item.level === parentLevel && (item.level === 0 || item.children.length))
        // A full parent only accepts reordering of its own children.
        .filter(item => item === info.parent || item.children.length < (item.level === 0 ? MAX_TOP : MAX_CHILDREN))
        .map(item => item.node.id);
}

/** Copy of ``tree`` with ``id`` moved under ``parentId`` (``__root`` = top level) at ``position``. */
export function moveInTree(tree, id, parentId, position) {
    const next = pinTones(clone(tree));
    const found = locate(next, id);
    const target = childrenOf(next, parentId === '__root' ? '' : parentId);
    if (!found || !target || target.level !== found.level) return null;
    found.siblings.splice(found.siblings.indexOf(found.node), 1);
    target.list.splice(Math.max(0, Math.min(position, target.list.length)), 0, found.node);
    if (found.parent && !found.parent.children.length) { delete found.parent.children; found.parent.weight = 0; }
    tidy(next.nodes);
    return next;
}

/** Copy of ``tree`` without ``id``; a parent left childless becomes a unit keeping the removed share. */
export function removeFromTree(tree, id, completedIds) {
    const next = pinTones(clone(tree));
    const found = locate(next, id);
    if (!found) return null;
    const removedLeaves = leavesOf([found.node]);
    const removed = new Set();
    const collect = node => { removed.add(node.id); (node.children || []).forEach(collect); };
    collect(found.node);
    found.siblings.splice(found.siblings.indexOf(found.node), 1);
    let completed = [...completedIds];
    if (found.parent && !found.parent.children.length) {
        delete found.parent.children;
        found.parent.weight = removedLeaves.reduce((sum, leaf) => sum + leafWeight(leaf), 0);
        if (removedLeaves.length && removedLeaves.every(leaf => completed.includes(leaf.id))) completed.push(found.parent.id);
    }
    completed = completed.filter(item => !removed.has(item));
    tidy(next.nodes);
    return { tree: next, completed, removed, parentId: found.parent?.id || '__root' };
}

/** Facts the delete confirmation states before anything is removed. */
export function removalSummary(index, id, done) {
    const info = index.map.get(id);
    if (!info) return null;
    const leaves = [];
    let descendants = 0;
    const walk = item => item.children.forEach(child => {
        const sub = index.map.get(child.id);
        descendants += 1;
        if (sub.isLeaf) leaves.push(sub);
        walk(sub);
    });
    walk(info);
    if (info.isLeaf) leaves.push(info);
    return {
        info,
        descendants,
        leaves: leaves.length,
        weight: leaves.reduce((sum, item) => sum + leafWeight(item.node), 0),
        doneLeaves: leaves.filter(item => done.has(item.node.id)).length,
        parentBecomesUnit: Boolean(info.parent && info.parent.level > 0 && info.parent.children.length === 1),
    };
}

export function siblingIds(index, id) {
    const info = index.map.get(id);
    return info?.parent ? info.parent.children.map(child => child.id) : [];
}

export const canAddChild = info => Boolean(info) && info.level < MAX_DEPTH;

/** Why a new node cannot go under ``parentInfo`` (empty string = allowed). */
export function addBlocker(index, parentInfo) {
    if (!parentInfo) return '找不到上级知识点。';
    if (index.map.size - 1 >= MAX_NODES) return `一棵知识树最多 ${MAX_NODES} 个知识点。`;
    const limit = parentInfo.level === 0 ? MAX_TOP : MAX_CHILDREN;
    if (parentInfo.children.length >= limit) return `同一层最多 ${limit} 个知识点。`;
    const units = [...index.map.values()].filter(item => item.isLeaf).length;
    if (!parentInfo.isLeaf && units >= MAX_LEAVES) return `一棵知识树最多 ${MAX_LEAVES} 个基础单元。`;
    return '';
}

/** Patch one node of a tree copy (optimistic preview of PATCH /nodes/{id}). */
export function patchTree(tree, id, changes) {
    const next = clone(tree);
    if (id === '__root') {
        if (changes.label !== undefined) next.title = changes.label;
        if (changes.detail !== undefined) next.summary = changes.detail;
        return next;
    }
    const walk = nodes => nodes.some(node => {
        if (node.id === id) {
            for (const [key, value] of Object.entries(changes)) {
                if (key === 'weight') node.weight = value;
                else if (value) node[key] = value;
                else delete node[key];
            }
            return true;
        }
        return walk(node.children || []);
    });
    walk(next.nodes || []);
    return next;
}
