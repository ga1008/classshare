// 知识树节点与折叠圆点的 DOM 内容和状态（纯函数，无布局）：控制器按 id 复用元素，
// 只在 nodeKey 变化时重填内容，状态（完成/选中/折叠/无障碍名称）每次重绘。
import { isShareComplete, leafWeight, sharePercent } from './model.js';

const el = (tag, className = '', text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};

/** Content identity of a node: refill only when this changes. */
export const nodeKey = info => [info.level, info.isLeaf ? 1 : 0, info.branch, info.node.label, info.isLeaf ? leafWeight(info.node) : ''].join('|');

export function fillNode(node, info, canEdit) {
    node.dataset.level = String(info.level);
    node.dataset.branch = String(Math.max(0, info.branch));
    node.setAttribute('aria-level', String(info.level + 1));
    const label = el('span', 'kt-node__label', info.node.label || '未命名知识点');
    if (info.isLeaf) {
        node.dataset.leaf = 'true';
        const check = el('span', 'kt-node__check');
        check.setAttribute('aria-hidden', 'true');
        if (canEdit) check.title = '标记完成/未完成';
        node.replaceChildren(check, label, el('span', 'kt-node__weight', `${leafWeight(info.node)}%`));
        return;
    }
    delete node.dataset.leaf;
    if (info.level === 0) {
        node.replaceChildren(el('span', 'kt-node__kicker', '本次课'), label);
        return;
    }
    const meter = el('span', 'kt-node__meter');
    meter.setAttribute('aria-hidden', 'true');
    node.replaceChildren(label, meter);
}

export function paintNode(node, info, share, { selected, hidden, folded, canEdit }) {
    const complete = isShareComplete(share);
    node.dataset.done = complete ? 'true' : 'false';
    node.dataset.selected = selected ? 'true' : 'false';
    node.classList.toggle('is-folded', hidden);
    node.tabIndex = hidden ? -1 : 0;
    if (hidden) node.setAttribute('aria-hidden', 'true'); else node.removeAttribute('aria-hidden');
    if (info.children.length) node.setAttribute('aria-expanded', folded ? 'false' : 'true');
    else node.removeAttribute('aria-expanded');
    if (info.isLeaf) {
        node.setAttribute('aria-pressed', complete ? 'true' : 'false');
        node.setAttribute('aria-label', `${info.node.label}，比重 ${leafWeight(info.node)}%，${complete ? '已完成' : '未完成'}${canEdit ? '，空格键切换完成，回车查看详情' : ''}`);
        return;
    }
    node.removeAttribute('aria-pressed');
    node.style.setProperty('--kt-share', `${sharePercent(share)}%`);
    node.setAttribute('aria-label', `${info.node.label}${info.level ? `，已完成 ${sharePercent(share)}%` : ''}，查看详情`);
}

export function paintFold(fold, info, { folded, hidden, count }) {
    fold.setAttribute('aria-expanded', folded ? 'false' : 'true');
    fold.textContent = folded ? `+${count}` : '';
    fold.setAttribute('aria-label', `${folded ? '展开' : '收起'}「${info.node.label}」的下级知识点`);
    fold.title = folded ? '展开下级知识点' : '收起下级知识点';
    fold.classList.toggle('is-folded', hidden || !info.children.length);
    fold.tabIndex = hidden ? -1 : 0;
}
