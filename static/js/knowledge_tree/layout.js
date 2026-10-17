// 知识树整齐布局（纯函数）：沿主轴（层级）与交叉轴（同级）排布节点，并给出连线几何。

export const GAP_MAIN = 64;
export const GAP_CROSS = { 1: 28, 2: 14, 3: 8 };
export const PAD = 32;

/** Point where a node's outgoing edges start (its fold dot). */
export function foldPoint(box, direction) {
    return direction === 'bt' ? { x: box.x + box.w / 2, y: box.y } : { x: box.x + box.w, y: box.y + box.h / 2 };
}

/**
 * Tidy-tree layout. sizes: Map(id -> {w, h}); returns boxes Map(id -> {x, y, w, h}),
 * the visible id set and the canvas size. Folded descendants rest on the fold dot
 * of their nearest visible ancestor, so folding animates into and out of it.
 */
export function layoutTree(index, sizes, direction, collapsed = new Set()) {
    const lr = direction !== 'bt';
    const size = id => sizes.get(id) || { w: 160, h: 40 };
    const cross = id => (lr ? size(id).h : size(id).w);
    const mainSize = id => (lr ? size(id).w : size(id).h);
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
    depthMain.reduce((acc, value, level) => { offsets[level] = acc; return acc + (value || 0) + GAP_MAIN; }, 0);
    const lastLevel = depthMain.length - 1;
    const totalMain = offsets[lastLevel] + depthMain[lastLevel];
    const boxes = new Map();
    for (const id of visible) {
        const info = index.map.get(id);
        const { w, h } = size(id);
        const c = centers.get(id);
        if (lr) {
            boxes.set(id, { x: PAD + offsets[info.level], y: PAD + c - h / 2, w, h });
        } else {
            const rowBottom = totalMain - offsets[info.level];
            const rowSize = depthMain[info.level];
            boxes.set(id, { x: PAD + c - w / 2, y: PAD + rowBottom - rowSize + (rowSize - h) / 2, w, h });
        }
    }
    for (const [id, info] of index.map) {
        if (visible.has(id)) continue;
        let anchor = info.parent;
        while (anchor && !visible.has(anchor.node.id)) anchor = anchor.parent;
        const point = foldPoint(boxes.get(anchor.node.id), direction);
        const { w, h } = size(id);
        boxes.set(id, { x: point.x - w / 2, y: point.y - h / 2, w, h });
    }
    return { boxes, visible, width: (lr ? totalMain : totalCross) + PAD * 2, height: (lr ? totalCross : totalMain) + PAD * 2 };
}

const n = value => value.toFixed(1);

export function edgePath(parent, child, direction) {
    if (direction === 'bt') {
        const x1 = parent.x + parent.w / 2, y1 = parent.y, x2 = child.x + child.w / 2, y2 = child.y + child.h;
        const my = (y1 + y2) / 2;
        return `M${n(x1)} ${n(y1)}C${n(x1)} ${n(my)} ${n(x2)} ${n(my)} ${n(x2)} ${n(y2)}`;
    }
    const x1 = parent.x + parent.w, y1 = parent.y + parent.h / 2, x2 = child.x, y2 = child.y + child.h / 2;
    const mx = (x1 + x2) / 2;
    return `M${n(x1)} ${n(y1)}C${n(mx)} ${n(y1)} ${n(mx)} ${n(y2)} ${n(x2)} ${n(y2)}`;
}
