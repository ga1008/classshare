// 知识树拖动排序：除根节点外任意节点都可拖动，层级保持不变，可换到同层任意中间节点之下。
// 鼠标/笔移动超过阈值即拿起；触屏需长按（短按滑动仍是原生平移）。拿起的节点跟随指针、
// 带轻微倾斜与投影；其余节点按"如果在这里放下"的预览布局用弹簧让位。大节点拿起时
// 同层节点先收起子树，放下后由控制器重新布局并再展开。
import { dropParents, moveInTree } from './model.js';

const HOLD_MS = 320;
const MOUSE_SLOP = 5;
const TOUCH_SLOP = 8;
const EDGE_ZONE = 48;

/**
 * ctx: {
 *   viewport, nodesLayer, canvasPoint(event) -> {x,y}, allowed(id) -> bool,
 *   tree() -> current tree, index() -> current index, layout() -> displayed layout,
 *   direction() -> 'lr'|'bt', bodyOf(id) -> {x,y},
 *   begin(id)                       controller collapses peers and lifts the node
 *   preview(tree|null) -> index     controller relayouts everything but the dragged node
 *   follow(id, x, y, vx, vy)        pin the dragged node under the pointer
 *   end({id, changed, parentId, index, tree})
 * }
 */
export function createDragController(ctx) {
    let press = null;
    let drag = null;
    let frame = 0;
    let suppressClick = false;

    const lr = () => ctx.direction() !== 'bt';
    const crossRange = box => (lr() ? [box.y, box.y + box.h] : [box.x, box.x + box.w]);
    const crossCenter = box => (lr() ? box.y + box.h / 2 : box.x + box.w / 2);

    function origin(id) {
        const info = ctx.index().map.get(id);
        return { parentId: info.parent.node.id, index: info.parent.children.findIndex(child => child.id === id) };
    }

    /** Nearest allowed parent to the pointer and the insertion index among its other children. */
    function slotFor(point) {
        const layout = ctx.layout();
        const index = drag.previewIndex || ctx.index();
        const p = lr() ? point.y : point.x;
        let best = null;
        for (const parentId of drag.parents) {
            const parentInfo = index.map.get(parentId);
            const parentBox = layout.boxes.get(parentId);
            if (!parentInfo || !parentBox || !layout.visible.has(parentId)) continue;
            const kids = parentInfo.children.map(child => child.id).filter(item => item !== drag.id);
            const shownKids = kids.filter(item => layout.visible.has(item));
            let [lo, hi] = crossRange(parentBox);
            for (const kid of shownKids) {
                const [a, b] = crossRange(layout.boxes.get(kid));
                lo = Math.min(lo, a);
                hi = Math.max(hi, b);
            }
            const distance = p < lo ? lo - p : p > hi ? p - hi : 0;
            const tie = Math.abs(p - crossCenter(parentBox));
            if (!best || distance < best.distance || (distance === best.distance && tie < best.tie)) {
                const position = shownKids.length === kids.length
                    ? kids.filter(item => crossCenter(layout.boxes.get(item)) < p).length
                    : kids.length;
                best = { parentId, index: position, distance, tie };
            }
        }
        return best ? { parentId: best.parentId, index: best.index } : drag.origin;
    }

    function autoscroll(event) {
        const rect = ctx.viewport.getBoundingClientRect();
        const step = (low, high, value) => (value < low + EDGE_ZONE ? -(low + EDGE_ZONE - value) / 3
            : value > high - EDGE_ZONE ? (value - (high - EDGE_ZONE)) / 3 : 0);
        const dx = step(rect.left, rect.right, event.clientX);
        const dy = step(rect.top, rect.bottom, event.clientY);
        if (dx) ctx.viewport.scrollLeft += dx;
        if (dy) ctx.viewport.scrollTop += dy;
        return Boolean(dx || dy);
    }

    function update() {
        frame = 0;
        if (!drag) return;
        const event = drag.last;
        const scrolled = autoscroll(event);
        const point = ctx.canvasPoint(event);
        const now = performance.now();
        const x = point.x - drag.grab.x, y = point.y - drag.grab.y;
        const dt = Math.max(16, now - drag.lastTime) / 1000;
        ctx.follow(drag.id, x, y, (x - drag.lastPos.x) / dt, (y - drag.lastPos.y) / dt);
        drag.lastPos = { x, y };
        drag.lastTime = now;
        const slot = slotFor(point);
        if (slot.parentId !== drag.slot.parentId || slot.index !== drag.slot.index) {
            drag.slot = slot;
            const same = slot.parentId === drag.origin.parentId && slot.index === drag.origin.index;
            drag.previewTree = same ? null : moveInTree(ctx.tree(), drag.id, slot.parentId, slot.index);
            drag.previewIndex = ctx.preview(drag.previewTree);
        }
        if (scrolled) frame = window.requestAnimationFrame(update);
    }

    function begin(event) {
        const { id, node, pointerId } = press;
        press = null;
        const info = ctx.index().map.get(id);
        if (!info?.parent) return;
        suppressClick = true;
        ctx.begin(id);
        const body = ctx.bodyOf(id);
        const point = ctx.canvasPoint(event);
        drag = {
            id, node, pointerId,
            grab: { x: point.x - body.x, y: point.y - body.y },
            origin: origin(id), parents: dropParents(ctx.index(), id),
            last: event, lastPos: { x: body.x, y: body.y }, lastTime: performance.now(),
            previewTree: null, previewIndex: null,
        };
        drag.slot = drag.origin;
        try { node.setPointerCapture(pointerId); } catch { /* pointer already gone */ }
        update();
    }

    function finish(commit) {
        window.clearTimeout(press?.timer);
        press = null;
        if (!drag) return;
        window.cancelAnimationFrame(frame);
        frame = 0;
        const current = drag;
        drag = null;
        try { current.node.releasePointerCapture(current.pointerId); } catch { /* already released */ }
        const changed = commit && Boolean(current.previewTree);
        ctx.end({ id: current.id, changed, parentId: current.slot.parentId, index: current.slot.index, tree: changed ? current.previewTree : null });
        // The click that follows a drag's pointerup is swallowed once; never longer.
        window.setTimeout(() => { suppressClick = false; }, 0);
    }

    ctx.nodesLayer.addEventListener('pointerdown', event => {
        const node = event.target.closest('.kt-node');
        if (!node || event.button !== 0 || drag || !ctx.allowed(node.dataset.nodeId)) return;
        window.clearTimeout(press?.timer);
        press = { id: node.dataset.nodeId, node, pointerId: event.pointerId, type: event.pointerType, x: event.clientX, y: event.clientY, lastEvent: event };
        if (event.pointerType === 'touch') {
            press.timer = window.setTimeout(() => { if (press?.node === node) begin(press.lastEvent); }, HOLD_MS);
        }
    });
    ctx.nodesLayer.addEventListener('pointermove', event => {
        if (press && event.pointerId === press.pointerId) {
            press.lastEvent = event;
            const moved = Math.hypot(event.clientX - press.x, event.clientY - press.y);
            if (press.type === 'touch') {
                if (moved > TOUCH_SLOP) { window.clearTimeout(press.timer); press = null; }
            } else if (moved > MOUSE_SLOP) begin(event);
            return;
        }
        if (!drag || event.pointerId !== drag.pointerId) return;
        event.preventDefault();
        drag.last = event;
        if (!frame) frame = window.requestAnimationFrame(update);
    });
    ctx.nodesLayer.addEventListener('pointerup', event => {
        if (press && event.pointerId === press.pointerId) { window.clearTimeout(press.timer); press = null; return; }
        if (drag && event.pointerId === drag.pointerId) { drag.last = event; update(); finish(true); }
    });
    ctx.nodesLayer.addEventListener('pointercancel', event => {
        if (press?.pointerId === event.pointerId || drag?.pointerId === event.pointerId) finish(false);
    });
    ctx.nodesLayer.addEventListener('contextmenu', event => { if (press || drag) event.preventDefault(); });
    // After a press-and-hold the finger drags the node instead of panning the canvas.
    ctx.nodesLayer.addEventListener('touchmove', event => { if (drag) event.preventDefault(); }, { passive: false });

    return {
        get active() { return Boolean(drag); },
        get draggedId() { return drag?.id || ''; },
        /** True once for the click a drag produced. */
        consumeClick() { if (!suppressClick) return false; suppressClick = false; return true; },
        cancel() { finish(false); },
    };
}
