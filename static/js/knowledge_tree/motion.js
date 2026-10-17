// 知识树画布位置动效：每个节点一个阻尼弹簧（带惯性与轻微回弹），节点、连线与折叠圆点
// 在同一帧读取同一组位置，所以连线始终"长"在节点上。重排、折叠、拖动预览随时改目标，
// 弹簧从当前位置与速度继续，不会跳变。减少动效时直接落位。

const STIFFNESS = 380;
const DAMPING = 34; // ζ ≈ 0.87: settles with a barely visible overshoot
const REST_DISTANCE = 0.3;
const REST_SPEED = 4;

/**
 * @param {{onFrame: (bodies: Map<string, {x:number,y:number}>) => void, reduced?: () => boolean}} options
 */
export function createSpringField({ onFrame, reduced = () => false }) {
    const bodies = new Map();
    let frame = 0;
    let last = 0;

    const settle = body => { body.x = body.tx; body.y = body.ty; body.vx = 0; body.vy = 0; };

    function tick(now) {
        frame = 0;
        const dt = last ? Math.min(1 / 30, Math.max(1 / 240, (now - last) / 1000)) : 1 / 60;
        last = now;
        let moving = false;
        const h = dt / 2;
        for (const body of bodies.values()) {
            if (body.pinned) continue;
            for (let step = 0; step < 2; step += 1) {
                body.vx += (-STIFFNESS * (body.x - body.tx) - DAMPING * body.vx) * h;
                body.vy += (-STIFFNESS * (body.y - body.ty) - DAMPING * body.vy) * h;
                body.x += body.vx * h;
                body.y += body.vy * h;
            }
            const near = Math.abs(body.x - body.tx) < REST_DISTANCE && Math.abs(body.y - body.ty) < REST_DISTANCE;
            if (near && Math.abs(body.vx) < REST_SPEED && Math.abs(body.vy) < REST_SPEED) settle(body);
            else moving = true;
        }
        onFrame(bodies);
        if (moving) frame = window.requestAnimationFrame(tick);
        else last = 0;
    }

    function start() {
        if (reduced()) {
            for (const body of bodies.values()) if (!body.pinned) settle(body);
            window.cancelAnimationFrame(frame);
            frame = 0;
            onFrame(bodies);
            return;
        }
        if (!frame) frame = window.requestAnimationFrame(tick);
    }

    return {
        /** Aim ``id`` at (x, y); ``from`` is the starting point of a new body, ``snap`` jumps. */
        set(id, x, y, { from = null, snap = false } = {}) {
            let body = bodies.get(id);
            if (!body) {
                const origin = from || { x, y };
                body = { x: origin.x, y: origin.y, vx: 0, vy: 0, tx: x, ty: y, pinned: false };
                bodies.set(id, body);
            }
            body.tx = x;
            body.ty = y;
            if (snap) settle(body);
        },
        /** Hold ``id`` under the pointer (drag); the spring resumes from here, with this velocity, on release. */
        pin(id, x, y, vx = 0, vy = 0) {
            const body = bodies.get(id) || { tx: x, ty: y };
            Object.assign(body, { x, y, vx, vy, pinned: true });
            bodies.set(id, body);
        },
        release(id) { const body = bodies.get(id); if (body) body.pinned = false; },
        /** Move every body by the same amount: keeps the picture still while the canvas re-anchors. */
        shiftAll(dx, dy) {
            if (!dx && !dy) return;
            for (const body of bodies.values()) { body.x += dx; body.y += dy; }
        },
        get: id => bodies.get(id),
        delete: id => bodies.delete(id),
        clear() { bodies.clear(); },
        start,
        draw() { onFrame(bodies); },
        stop() { window.cancelAnimationFrame(frame); frame = 0; last = 0; },
    };
}
