/**
 * Orthogonal timetable change routes in untransformed, canvas-local pixels.
 * No DOM reads: the deck owns measurements, SVG rendering and resize scheduling.
 * Routes preserve the supplied source -> target identity; they never pair cards.
 */

const EPS = 0.01;
const CLEARANCE = 2;
const BOUNDARY = 8;
const LANE_GAP = 6;
const TURN_COST = 18;
const CROSSING_COST = 240;
const pixel = value => Math.round(value * 100) / 100;

function between(value, low, high) { return value > low + EPS && value < high - EPS; }
function inside(point, rect) { return between(point.x, rect.left, rect.right) && between(point.y, rect.top, rect.bottom); }
function segmentHitsRect(a, b, rect) {
    if (Math.abs(a.y - b.y) < EPS) return between(a.y, rect.top, rect.bottom) && Math.max(a.x, b.x) > rect.left + EPS && Math.min(a.x, b.x) < rect.right - EPS;
    return between(a.x, rect.left, rect.right) && Math.max(a.y, b.y) > rect.top + EPS && Math.min(a.y, b.y) < rect.bottom - EPS;
}
function inflate(rect, amount) { return { ...rect, left: rect.left - amount, top: rect.top - amount, right: rect.right + amount, bottom: rect.bottom + amount }; }
function segments(points) { return points.slice(1).map((b, i) => [points[i], b]); }
function overlap(a, b, c, d) {
    const horizontal = Math.abs(a.y - b.y) < EPS;
    if (horizontal !== (Math.abs(c.y - d.y) < EPS)) return false;
    if (horizontal) return Math.abs(a.y - c.y) < EPS && Math.min(Math.max(a.x, b.x), Math.max(c.x, d.x)) > Math.max(Math.min(a.x, b.x), Math.min(c.x, d.x)) + EPS;
    return Math.abs(a.x - c.x) < EPS && Math.min(Math.max(a.y, b.y), Math.max(c.y, d.y)) > Math.max(Math.min(a.y, b.y), Math.min(c.y, d.y)) + EPS;
}
function crosses(a, b, c, d) {
    const horizontal = Math.abs(a.y - b.y) < EPS;
    if (horizontal === (Math.abs(c.y - d.y) < EPS)) return false;
    const h1 = horizontal ? a : c, h2 = horizontal ? b : d;
    const v1 = horizontal ? c : a, v2 = horizontal ? d : b;
    return v1.x >= Math.min(h1.x, h2.x) - EPS && v1.x <= Math.max(h1.x, h2.x) + EPS && h1.y >= Math.min(v1.y, v2.y) - EPS && h1.y <= Math.max(v1.y, v2.y) + EPS;
}
function trackCost(a, b, occupied) {
    let cost = 0;
    for (const [c, d] of occupied) {
        // Collinear sharing conceals distinct applications, so it is forbidden.
        if (overlap(a, b, c, d)) return Infinity;
        if (crosses(a, b, c, d)) cost += CROSSING_COST;
    }
    return cost;
}
function simplify(points) {
    const result = [];
    for (const point of points) {
        const previous = result[result.length - 1];
        if (previous && Math.abs(previous.x - point.x) < EPS && Math.abs(previous.y - point.y) < EPS) continue;
        while (result.length > 1) {
            const a = result[result.length - 2], b = result[result.length - 1];
            if ((Math.abs(a.x - b.x) < EPS && Math.abs(b.x - point.x) < EPS) || (Math.abs(a.y - b.y) < EPS && Math.abs(b.y - point.y) < EPS)) result.pop();
            else break;
        }
        result.push(point);
    }
    return result;
}
function unique(values, min, max) {
    return [...new Set(values.filter(value => Number.isFinite(value) && value >= min - EPS && value <= max + EPS).map(value => Math.round(value * 100) / 100))].sort((a, b) => a - b);
}
function channels(values, min, max) {
    const sorted = unique([min, max, ...values], min, max);
    return [...sorted, ...sorted.slice(1).flatMap((value, i) => value - sorted[i] >= 12 ? [(value + sorted[i]) / 2] : [])];
}
function cardPorts(rect, role, roomOnly = false) {
    const cx = (rect.left + rect.right) / 2, cy = (rect.top + rect.bottom) / 2;
    const ports = [];
    function add(x, y, dx, dy, preference = 0, clearance = CLEARANCE) {
        x = pixel(x); y = pixel(y);
        ports.push({ anchor: { x, y }, point: { x: pixel(x + dx * clearance), y: pixel(y + dy * clearance) }, axis: dx ? 0 : 1, preference });
    }
    if (roomOnly) {
        const y = rect.top + (rect.bottom - rect.top) * (role === 'source' ? 0.1 : 0.9);
        // Put the loop far enough outside the card for a 22px label. A compact
        // fallback remains available when another lesson blocks that margin.
        add(rect.right, y, 1, 0, 0, 16);
        add(rect.left, y, -1, 0, 8, 16);
        add(rect.right, y, 1, 0, 60);
        add(rect.left, y, -1, 0, 68);
        return ports;
    }
    const offsetsX = unique([cx, cx - LANE_GAP, cx + LANE_GAP, rect.left + (rect.right - rect.left) * 0.25, rect.left + (rect.right - rect.left) * 0.75], rect.left + 3, rect.right - 3);
    const offsetsY = unique([cy, cy - LANE_GAP, cy + LANE_GAP, rect.top + (rect.bottom - rect.top) * 0.25, rect.top + (rect.bottom - rect.top) * 0.75], rect.top + 3, rect.bottom - 3);
    for (const y of offsetsY) {
        add(rect.left, y, -1, 0, Math.abs(y - cy) * 0.05);
        add(rect.right, y, 1, 0, Math.abs(y - cy) * 0.05);
    }
    for (const x of offsetsX) {
        add(x, rect.top, 0, -1, Math.abs(x - cx) * 0.05);
        add(x, rect.bottom, 0, 1, Math.abs(x - cx) * 0.05);
    }
    return ports;
}
function boundaryPorts(edge, width, height, rect, obstacles, occupied) {
    const cy = rect ? (rect.top + rect.bottom) / 2 : height / 2;
    const ys = channels([cy, cy - LANE_GAP, cy + LANE_GAP, ...obstacles.flatMap(item => [item.top - CLEARANCE, item.bottom + CLEARANCE]), ...occupied.flatMap(([a, b]) => [a.y - LANE_GAP, b.y + LANE_GAP])], BOUNDARY, height - BOUNDARY);
    return unique(ys, BOUNDARY, height - BOUNDARY).map(y => ({ anchor: { x: edge === 'left' ? BOUNDARY : width - BOUNDARY, y }, point: { x: edge === 'left' ? BOUNDARY : width - BOUNDARY, y }, axis: 0, preference: Math.abs(y - cy) * 0.04 }));
}

class MinHeap {
    constructor() { this.items = []; }
    push(item) {
        let index = this.items.length;
        this.items.push(item);
        while (index) {
            const parent = (index - 1) >> 1;
            if (this.items[parent].priority <= item.priority) break;
            this.items[index] = this.items[parent]; index = parent;
        }
        this.items[index] = item;
    }
    pop() {
        if (!this.items.length) return null;
        const first = this.items[0], last = this.items.pop();
        if (this.items.length) {
            let index = 0;
            while (index * 2 + 1 < this.items.length) {
                let child = index * 2 + 1;
                if (child + 1 < this.items.length && this.items[child + 1].priority < this.items[child].priority) child++;
                if (this.items[child].priority >= last.priority) break;
                this.items[index] = this.items[child]; index = child;
            }
            this.items[index] = last;
        }
        return first;
    }
}

function findRoute(width, height, obstacles, occupied, sourcePorts, targetPorts, sourceKey, targetKey) {
    const expanded = obstacles.map(rect => inflate(rect, CLEARANCE));
    const legalPort = (port, key) => port.point.x >= BOUNDARY && port.point.x <= width - BOUNDARY && port.point.y >= BOUNDARY && port.point.y <= height - BOUNDARY && !expanded.some(rect => inside(port.point, rect) || (rect.key !== key && segmentHitsRect(port.anchor, port.point, rect))) && Number.isFinite(trackCost(port.anchor, port.point, occupied));
    const sources = sourcePorts.filter(port => legalPort(port, sourceKey));
    const targets = targetPorts.filter(port => legalPort(port, targetKey));
    if (!sources.length || !targets.length) return [];
    const xs = unique([...channels(expanded.flatMap(rect => [rect.left, rect.right]), BOUNDARY, width - BOUNDARY), ...sources.map(port => port.point.x), ...targets.map(port => port.point.x), ...occupied.flatMap(([a, b]) => [a.x - LANE_GAP, a.x + LANE_GAP, b.x - LANE_GAP, b.x + LANE_GAP])], BOUNDARY, width - BOUNDARY);
    const ys = unique([...channels(expanded.flatMap(rect => [rect.top, rect.bottom]), BOUNDARY, height - BOUNDARY), ...sources.map(port => port.point.y), ...targets.map(port => port.point.y), ...occupied.flatMap(([a, b]) => [a.y - LANE_GAP, a.y + LANE_GAP, b.y - LANE_GAP, b.y + LANE_GAP])], BOUNDARY, height - BOUNDARY);
    const xIndex = new Map(xs.map((x, i) => [x, i])), yIndex = new Map(ys.map((y, i) => [y, i]));
    const nodeFor = point => yIndex.get(Math.round(point.y * 100) / 100) * xs.length + xIndex.get(Math.round(point.x * 100) / 100);
    const pointFor = node => ({ x: xs[node % xs.length], y: ys[Math.floor(node / xs.length)] });
    const count = xs.length * ys.length;
    const distance = new Float64Array(count * 2).fill(Infinity), previous = new Int32Array(count * 2).fill(-1), origin = new Int16Array(count * 2).fill(-1);
    const targetsByNode = new Map();
    targets.forEach(port => {
        const node = nodeFor(port.point);
        if (!targetsByNode.has(node)) targetsByNode.set(node, []);
        targetsByNode.get(node).push(port);
    });
    const heuristic = point => Math.min(...targets.map(port => Math.abs(port.point.x - point.x) + Math.abs(port.point.y - point.y)));
    const heap = new MinHeap();
    sources.forEach((port, index) => {
        const state = nodeFor(port.point) * 2 + port.axis;
        const value = port.preference + Math.abs(port.point.x - port.anchor.x) + Math.abs(port.point.y - port.anchor.y) + trackCost(port.anchor, port.point, occupied);
        if (value < distance[state]) {
            distance[state] = value; origin[state] = index;
            heap.push({ state, value, priority: value + heuristic(port.point) });
        }
    });
    const edgeCache = new Map();
    let best = Infinity, bestState = -1, bestTarget = null;
    while (heap.items.length) {
        const item = heap.pop();
        if (item.value > distance[item.state] + EPS) continue;
        if (item.priority >= best) break;
        const node = Math.floor(item.state / 2), axis = item.state % 2, point = pointFor(node);
        for (const target of targetsByNode.get(node) || []) {
            const value = item.value + target.preference + Math.abs(target.anchor.x - target.point.x) + Math.abs(target.anchor.y - target.point.y) + (axis === target.axis ? 0 : TURN_COST) + trackCost(target.point, target.anchor, occupied);
            if (value < best) { best = value; bestState = item.state; bestTarget = target; }
        }
        const x = node % xs.length, y = Math.floor(node / xs.length);
        const neighbors = [];
        if (x) neighbors.push([node - 1, 0]);
        if (x + 1 < xs.length) neighbors.push([node + 1, 0]);
        if (y) neighbors.push([node - xs.length, 1]);
        if (y + 1 < ys.length) neighbors.push([node + xs.length, 1]);
        for (const [nextNode, nextAxis] of neighbors) {
            const next = pointFor(nextNode), cacheKey = `${Math.min(node, nextNode)}:${Math.max(node, nextNode)}`;
            let edgeCost = edgeCache.get(cacheKey);
            if (edgeCost === undefined) {
                edgeCost = expanded.some(rect => segmentHitsRect(point, next, rect)) ? Infinity : Math.abs(point.x - next.x) + Math.abs(point.y - next.y) + trackCost(point, next, occupied);
                edgeCache.set(cacheKey, edgeCost);
            }
            const value = item.value + edgeCost + (axis === nextAxis ? 0 : TURN_COST), state = nextNode * 2 + nextAxis;
            if (value + EPS < distance[state]) {
                distance[state] = value; previous[state] = item.state; origin[state] = origin[item.state];
                heap.push({ state, value, priority: value + heuristic(next) });
            }
        }
    }
    if (bestState < 0) return [];
    const path = [bestTarget.anchor];
    for (let state = bestState; state >= 0; state = previous[state]) path.push(pointFor(Math.floor(state / 2)));
    path.push(sources[origin[bestState]].anchor);
    return simplify(path.reverse());
}

function rectanglesOverlap(a, b) { return a.left < b.right + EPS && a.right > b.left - EPS && a.top < b.bottom + EPS && a.bottom > b.top - EPS; }
function labelLength(label) { return Math.ceil([...String(label)].reduce((total, char) => total + (char.charCodeAt(0) > 255 ? 12 : 7), 12)); }
function labelFor(route, routes, obstacles, labels, width, height) {
    if (!route.label || route.points.length < 2) return null;
    // A 12px label with an opaque 6px inset on each end. x/y are its centre.
    const length = labelLength(route.label);
    const candidates = segments(route.points).map(([a, b]) => ({ a, b, vertical: Math.abs(a.x - b.x) < EPS, length: Math.abs(a.x - b.x) + Math.abs(a.y - b.y) })).filter(item => item.length >= length + 10).sort((a, b) => Number(a.vertical) - Number(b.vertical) || b.length - a.length);
    for (const segment of candidates) {
        const boxWidth = segment.vertical ? 22 : length, boxHeight = segment.vertical ? length : 22;
        const start = segment.vertical ? Math.min(segment.a.y, segment.b.y) : Math.min(segment.a.x, segment.b.x);
        const end = segment.vertical ? Math.max(segment.a.y, segment.b.y) : Math.max(segment.a.x, segment.b.x);
        const inset = length / 2 + 5;
        const positions = unique([(start + end) / 2, start + inset, end - inset, start + (end - start) * 0.25, start + (end - start) * 0.75], start + inset, end - inset);
        positions.sort((a, b) => Math.abs(a - (start + end) / 2) - Math.abs(b - (start + end) / 2));
        for (const position of positions) {
            const x = segment.vertical ? segment.a.x : position, y = segment.vertical ? position : segment.a.y;
            const box = { left: x - boxWidth / 2, right: x + boxWidth / 2, top: y - boxHeight / 2, bottom: y + boxHeight / 2 };
            if (box.left < 1 || box.right > width - 1 || box.top < 1 || box.bottom > height - 1 || obstacles.some(rect => rectanglesOverlap(box, inflate(rect, 2))) || labels.some(rect => rectanglesOverlap(box, inflate(rect, 3)))) continue;
            if (routes.some(other => other !== route && segments(other.points).some(([a, b]) => segmentHitsRect(a, b, inflate(box, 2))))) continue;
            labels.push(box);
            return { x, y, vertical: segment.vertical, width: boxWidth, height: boxHeight };
        }
    }
    return null;
}

function routePorts(connection, width, height, byKey, obstacles, occupied) {
    const source = byKey.get(connection.sourceKey), target = byKey.get(connection.targetKey);
    const roomOnly = Boolean(source && target && source.key === target.key);
    return {
        sources: connection.direction === 'incoming' ? boundaryPorts(connection.edge, width, height, target, obstacles, occupied) : cardPorts(source, 'source', roomOnly),
        targets: connection.direction === 'outgoing' ? boundaryPorts(connection.edge, width, height, source, obstacles, occupied) : cardPorts(target, 'target', roomOnly),
    };
}

/** A short boundary connector may not fit its caption. Reserve a real clear
 * caption segment and route via it, rather than stamping text over a lesson.
 * Only used when the shortest safe route has no label; the bounded candidate
 * search favors nearby margins and keeps the normal routing path inexpensive.
 */
function routeWithCaption(route, routes, obstacles, labels, width, height, byKey) {
    const occupied = routes.filter(other => other !== route).flatMap(other => segments(other.points));
    const length = labelLength(route.label), span = length + 12;
    const routingObstacles = [...obstacles, ...labels.map((box, index) => ({ ...inflate(box, 3), key: `__caption-${index}` }))];
    const endpoints = [route.points[0], route.points[route.points.length - 1]];
    const candidates = [];
    for (const vertical of [true, false]) {
        const extent = vertical ? height : width;
        const tracks = unique([22, (vertical ? width : height) - 22, ...obstacles.flatMap(box => vertical ? [box.left - 16, box.right + 16] : [box.top - 16, box.bottom + 16]), ...endpoints.map(point => vertical ? point.x : point.y)], 14, (vertical ? width : height) - 14);
        for (const track of tracks) {
            // Project expanded lesson/label boxes onto this text-width channel.
            const blocked = routingObstacles.filter(box => vertical ? track + 13 > box.left && track - 13 < box.right : track + 13 > box.top && track - 13 < box.bottom).map(box => vertical ? [box.top - 4, box.bottom + 4] : [box.left - 4, box.right + 4]).sort((a, b) => a[0] - b[0]);
            let cursor = 14;
            const intervals = [];
            for (const [start, end] of blocked) {
                if (start > cursor) intervals.push([cursor, Math.min(start, extent - 14)]);
                cursor = Math.max(cursor, end);
            }
            if (cursor < extent - 14) intervals.push([cursor, extent - 14]);
            for (const [start, end] of intervals) {
                if (end - start < span) continue;
                const positions = unique([start, end - span, ...endpoints.flatMap(point => {
                    const axis = vertical ? point.y : point.x;
                    return [Math.max(start, Math.min(end - span, axis)), Math.max(start, Math.min(end - span, axis - span))];
                })], start, end - span);
                for (const position of positions) {
                    const a = vertical ? { x: track, y: position } : { x: position, y: track };
                    const b = vertical ? { x: track, y: position + span } : { x: position + span, y: track };
                    const box = { left: vertical ? track - 11 : position + 6, right: vertical ? track + 11 : position + span - 6, top: vertical ? position + 6 : track - 11, bottom: vertical ? position + span - 6 : track + 11, key: '__reserved-caption' };
                    if (occupied.some(([c, d]) => segmentHitsRect(c, d, inflate(box, 2))) || routingObstacles.some(obstacle => rectanglesOverlap(box, inflate(obstacle, 2)))) continue;
                    const distance = (p, q) => Math.abs(p.x - q.x) + Math.abs(p.y - q.y);
                    for (const [from, to] of [[a, b], [b, a]]) candidates.push({ from, to, box, axis: vertical ? 1 : 0, cost: distance(endpoints[0], from) + span + distance(to, endpoints[1]) });
                }
            }
        }
    }
    candidates.sort((a, b) => a.cost - b.cost);
    const { sources, targets } = routePorts(route, width, height, byKey, obstacles, occupied);
    // The eight nearest clear spans are enough to cover both sides of a card
    // and both edge directions without searching all timetable intersections.
    for (const candidate of candidates.slice(0, 8)) {
        const forced = [candidate.from, candidate.to];
        if (!Number.isFinite(trackCost(...forced, occupied))) continue;
        const reserved = [...routingObstacles, candidate.box];
        const makePort = point => ({ anchor: point, point, axis: candidate.axis, preference: 0 });
        const first = findRoute(width, height, reserved, [...occupied, forced], sources, [makePort(candidate.from)], route.sourceKey, undefined);
        if (!first.length) continue;
        const last = findRoute(width, height, reserved, [...occupied, forced, ...segments(first)], [makePort(candidate.to)], targets, undefined, route.targetKey);
        if (!last.length) continue;
        const points = simplify([...first, candidate.to, ...last]);
        const previous = route.points;
        route.points = points;
        const placement = labelFor(route, routes, obstacles, labels, width, height);
        if (placement) return placement;
        route.points = previous;
    }
    return null;
}

/**
 * Invalid/missing endpoints or a completely blocked canvas return points: [].
 * Label placement is best effort; null tells the deck to use its accessible
 * card/boundary caption. Unavoidable perpendicular crossings are penalized;
 * collinear overlap and entering a lesson rectangle are never accepted.
 */
export function routeScheduleChanges({ width, height, obstacles = [], connections = [] } = {}) {
    const validCanvas = Number.isFinite(width) && Number.isFinite(height) && width > BOUNDARY * 2 && height > BOUNDARY * 2;
    const validObstacles = obstacles.filter(rect => rect && [rect.left, rect.top, rect.right, rect.bottom].every(Number.isFinite) && rect.right > rect.left && rect.bottom > rect.top).map(rect => ({ ...rect, left: pixel(rect.left), top: pixel(rect.top), right: pixel(rect.right), bottom: pixel(rect.bottom) }));
    const byKey = new Map(validObstacles.map(rect => [rect.key, rect]));
    const occupied = [], routes = [];
    for (const connection of connections) {
        const route = { ...connection, points: [], labelPlacement: null };
        routes.push(route);
        if (!validCanvas) continue;
        const source = byKey.get(connection.sourceKey), target = byKey.get(connection.targetKey);
        const outgoing = connection.direction === 'outgoing', incoming = connection.direction === 'incoming';
        if ((outgoing && (!source || target)) || (incoming && (!target || source)) || (!outgoing && !incoming && (!source || !target)) || ((outgoing || incoming) && !['left', 'right'].includes(connection.edge))) continue;
        const { sources, targets } = routePorts(connection, width, height, byKey, validObstacles, occupied);
        route.points = findRoute(width, height, validObstacles, occupied, sources, targets, source?.key, target?.key);
        occupied.push(...segments(route.points));
    }
    const labels = [];
    for (const route of routes) {
        route.labelPlacement = labelFor(route, routes, validObstacles, labels, width, height);
        if (!route.labelPlacement && route.label && route.points.length > 1) route.labelPlacement = routeWithCaption(route, routes, validObstacles, labels, width, height, byKey);
    }
    return routes;
}
