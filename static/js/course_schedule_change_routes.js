/**
 * Orthogonal timetable change routes in untransformed, canvas-local pixels.
 * No DOM reads: the deck owns measurements, SVG rendering and resize scheduling.
 * Routes preserve the supplied source -> target identity; they never pair cards.
 */

const EPS = 0.01;
// Coordinates are quantized to 0.01px. Topology must use a much smaller
// tolerance: swallowing one grid step can turn the terminal stub diagonal.
const STRUCTURE_EPS = 1e-6;
const CLEARANCE = 2;
const BOUNDARY = 8;
const LANE_GAP = 6;
const ENDPOINT_STUB = 20;
const ARROW_STRAIGHT = 12;
const BOTTOM_MARGIN = 36;
const TURN_COST = 18;
const CROSSING_COST = 240;
const pixel = value => Math.round(value * 100) / 100;
const direction = (a, b) => Math.abs(a.y - b.y) < STRUCTURE_EPS ? (b.x > a.x ? 0 : 2) : (b.y > a.y ? 1 : 3);
const lengthOf = (a, b) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
const samePoint = (a, b) => Math.abs(a.x - b.x) < STRUCTURE_EPS && Math.abs(a.y - b.y) < STRUCTURE_EPS;

function between(value, low, high) { return value > low + EPS && value < high - EPS; }
function inside(point, rect) { return between(point.x, rect.left, rect.right) && between(point.y, rect.top, rect.bottom); }
function segmentHitsRect(a, b, rect) {
    if (Math.abs(a.y - b.y) < STRUCTURE_EPS) return between(a.y, rect.top, rect.bottom) && Math.max(a.x, b.x) > rect.left + EPS && Math.min(a.x, b.x) < rect.right - EPS;
    return between(a.x, rect.left, rect.right) && Math.max(a.y, b.y) > rect.top + EPS && Math.min(a.y, b.y) < rect.bottom - EPS;
}
function inflate(rect, amount) { return { ...rect, left: rect.left - amount, top: rect.top - amount, right: rect.right + amount, bottom: rect.bottom + amount }; }
function segments(points) { return points.slice(1).map((b, i) => [points[i], b]); }
function overlap(a, b, c, d) {
    const horizontal = Math.abs(a.y - b.y) < STRUCTURE_EPS;
    if (horizontal !== (Math.abs(c.y - d.y) < STRUCTURE_EPS)) return false;
    if (horizontal) return Math.abs(a.y - c.y) < EPS && Math.min(Math.max(a.x, b.x), Math.max(c.x, d.x)) > Math.max(Math.min(a.x, b.x), Math.min(c.x, d.x)) + EPS;
    return Math.abs(a.x - c.x) < EPS && Math.min(Math.max(a.y, b.y), Math.max(c.y, d.y)) > Math.max(Math.min(a.y, b.y), Math.min(c.y, d.y)) + EPS;
}
function crosses(a, b, c, d) {
    const horizontal = Math.abs(a.y - b.y) < STRUCTURE_EPS;
    if (horizontal === (Math.abs(c.y - d.y) < STRUCTURE_EPS)) return false;
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
        if (previous && samePoint(previous, point)) continue;
        while (result.length > 1) {
            const a = result[result.length - 2], b = result[result.length - 1];
            if (((Math.abs(a.x - b.x) < STRUCTURE_EPS && Math.abs(b.x - point.x) < STRUCTURE_EPS) || (Math.abs(a.y - b.y) < STRUCTURE_EPS && Math.abs(b.y - point.y) < STRUCTURE_EPS)) && direction(a, b) === direction(b, point)) result.pop();
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
function cardPorts(rect, height, corners = false) {
    const cx = (rect.left + rect.right) / 2, cy = (rect.top + rect.bottom) / 2;
    const ports = [];
    const nearBottom = height - rect.bottom < BOTTOM_MARGIN;
    function add(x, y, dx, dy, preference = 0) {
        if (dy > 0 && nearBottom) return;
        x = pixel(x); y = pixel(y);
        const anchor = { x, y }, point = { x: pixel(x + dx * ENDPOINT_STUB), y: pixel(y + dy * ENDPOINT_STUB) };
        ports.push({ anchor, point, outDirection: direction(anchor, point), preference: preference + (nearBottom && dy !== -1 ? 12 : 0) });
    }
    add(cx, rect.top, 0, -1);
    add(rect.left, cy, -1, 0);
    add(rect.right, cy, 1, 0);
    add(cx, rect.bottom, 0, 1);
    if (corners) {
        // Corners are a last-resort escape when expansion covers every midpoint;
        // they never compete with an already available midpoint route.
        for (const x of [rect.left, rect.right]) {
            for (const y of [rect.top, rect.bottom]) {
                add(x, y, x === rect.left ? -1 : 1, 0, 120);
                add(x, y, 0, y === rect.top ? -1 : 1, 120);
            }
        }
    }
    return ports;
}
function boundaryPorts(edge, width, height, rect, obstacles, occupied) {
    const cy = rect ? (rect.top + rect.bottom) / 2 : height / 2;
    const ys = channels([cy, cy - LANE_GAP, cy + LANE_GAP, ...(rect ? [rect.top - ENDPOINT_STUB, rect.bottom + ENDPOINT_STUB] : []), ...obstacles.flatMap(item => [item.top - CLEARANCE, item.bottom + CLEARANCE]), ...occupied.flatMap(([a, b]) => [a.y - LANE_GAP, b.y + LANE_GAP])], BOUNDARY, height - BOUNDARY);
    return unique(ys, BOUNDARY, height - BOUNDARY).map(y => ({ anchor: { x: edge === 'left' ? BOUNDARY : width - BOUNDARY, y }, point: { x: edge === 'left' ? BOUNDARY + ENDPOINT_STUB : width - BOUNDARY - ENDPOINT_STUB, y }, outDirection: edge === 'left' ? 0 : 2, preference: Math.abs(y - cy) * 0.04 }));
}

function legalPort(port, key, width, height, expanded, occupied = []) {
    return port.anchor.x >= 0 && port.anchor.x <= width && port.anchor.y >= 0 && port.anchor.y <= height && port.point.x >= BOUNDARY && port.point.x <= width - BOUNDARY && port.point.y >= BOUNDARY && port.point.y <= height - BOUNDARY && !expanded.some(rect => inside(port.anchor, rect) && rect.key !== key || inside(port.point, rect) || (rect.key !== key && segmentHitsRect(port.anchor, port.point, rect))) && Number.isFinite(trackCost(port.anchor, port.point, occupied));
}

function safePolyline(points, obstacles, width = Infinity, height = Infinity, occupied = []) {
    if (points.length < 2 || points.some(point => !Number.isFinite(point.x) || !Number.isFinite(point.y) || point.x < 0 || point.y < 0 || point.x > width || point.y > height)) return false;
    let previousDirection = -1;
    for (const [a, b] of segments(points)) {
        if (samePoint(a, b) || (Math.abs(a.x - b.x) > STRUCTURE_EPS && Math.abs(a.y - b.y) > STRUCTURE_EPS)) return false;
        const nextDirection = direction(a, b);
        if (previousDirection >= 0 && nextDirection === (previousDirection + 2) % 4) return false;
        if (obstacles.some(rect => segmentHitsRect(a, b, rect)) || !Number.isFinite(trackCost(a, b, occupied))) return false;
        previousDirection = nextDirection;
    }
    return true;
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
    const sources = sourcePorts.filter(port => legalPort(port, sourceKey, width, height, expanded, occupied));
    const targets = targetPorts.filter(port => legalPort(port, targetKey, width, height, expanded, occupied));
    if (!sources.length || !targets.length) return [];
    const xs = unique([...channels(expanded.flatMap(rect => [rect.left, rect.right]), BOUNDARY, width - BOUNDARY), ...sources.map(port => port.point.x), ...targets.map(port => port.point.x), ...occupied.flatMap(([a, b]) => [a.x - LANE_GAP, a.x + LANE_GAP, b.x - LANE_GAP, b.x + LANE_GAP])], BOUNDARY, width - BOUNDARY);
    const ys = unique([...channels(expanded.flatMap(rect => [rect.top, rect.bottom]), BOUNDARY, height - BOUNDARY), ...sources.map(port => port.point.y), ...targets.map(port => port.point.y), ...occupied.flatMap(([a, b]) => [a.y - LANE_GAP, a.y + LANE_GAP, b.y - LANE_GAP, b.y + LANE_GAP])], BOUNDARY, height - BOUNDARY);
    const xIndex = new Map(xs.map((x, i) => [x, i])), yIndex = new Map(ys.map((y, i) => [y, i]));
    const nodeFor = point => yIndex.get(Math.round(point.y * 100) / 100) * xs.length + xIndex.get(Math.round(point.x * 100) / 100);
    const pointFor = node => ({ x: xs[node % xs.length], y: ys[Math.floor(node / xs.length)] });
    const count = xs.length * ys.length;
    const distance = new Float64Array(count * 4).fill(Infinity), previous = new Int32Array(count * 4).fill(-1), origin = new Int16Array(count * 4).fill(-1);
    const straightRun = new Float64Array(count * 4);
    const turnCost = (from, to, run) => from === to ? 0 : TURN_COST + Math.max(0, ARROW_STRAIGHT - run) * 8;
    const targetsByNode = new Map();
    targets.forEach(port => {
        const node = nodeFor(port.point);
        if (!targetsByNode.has(node)) targetsByNode.set(node, []);
        targetsByNode.get(node).push(port);
    });
    const heuristic = point => Math.min(...targets.map(port => Math.abs(port.point.x - point.x) + Math.abs(port.point.y - point.y)));
    const heap = new MinHeap();
    sources.forEach((port, index) => {
        const state = nodeFor(port.point) * 4 + port.outDirection;
        const value = port.preference + Math.abs(port.point.x - port.anchor.x) + Math.abs(port.point.y - port.anchor.y) + trackCost(port.anchor, port.point, occupied);
        if (value < distance[state]) {
            distance[state] = value; origin[state] = index;
            straightRun[state] = Math.min(ARROW_STRAIGHT, lengthOf(port.anchor, port.point));
            heap.push({ state, value, priority: value + heuristic(port.point) });
        }
    });
    const edgeCache = new Map();
    let best = Infinity, bestState = -1, bestTarget = null;
    while (heap.items.length) {
        const item = heap.pop();
        if (item.value > distance[item.state] + STRUCTURE_EPS) continue;
        if (item.priority >= best) break;
        const node = Math.floor(item.state / 4), heading = item.state % 4, point = pointFor(node);
        for (const target of targetsByNode.get(node) || []) {
            // Entering the terminal stub in reverse would make a tiny U-turn
            // and flip the arrow. Arrival is perpendicular or toward the card.
            if (heading === target.outDirection) continue;
            const value = item.value + target.preference + lengthOf(target.anchor, target.point) + turnCost(heading, (target.outDirection + 2) % 4, straightRun[item.state]) + trackCost(target.point, target.anchor, occupied);
            if (value < best) { best = value; bestState = item.state; bestTarget = target; }
        }
        const x = node % xs.length, y = Math.floor(node / xs.length);
        const neighbors = [];
        if (x) neighbors.push([node - 1, 2]);
        if (x + 1 < xs.length) neighbors.push([node + 1, 0]);
        if (y) neighbors.push([node - xs.length, 3]);
        if (y + 1 < ys.length) neighbors.push([node + xs.length, 1]);
        for (const [nextNode, nextDirection] of neighbors) {
            if (nextDirection === (heading + 2) % 4) continue;
            const next = pointFor(nextNode), cacheKey = `${Math.min(node, nextNode)}:${Math.max(node, nextNode)}`;
            let edgeCost = edgeCache.get(cacheKey);
            if (edgeCost === undefined) {
                edgeCost = expanded.some(rect => segmentHitsRect(point, next, rect)) ? Infinity : Math.abs(point.x - next.x) + Math.abs(point.y - next.y) + trackCost(point, next, occupied);
                edgeCache.set(cacheKey, edgeCost);
            }
            const value = item.value + edgeCost + turnCost(heading, nextDirection, straightRun[item.state]), state = nextNode * 4 + nextDirection;
            const nextRun = Math.min(ARROW_STRAIGHT, (heading === nextDirection ? straightRun[item.state] : 0) + lengthOf(point, next));
            if (value + STRUCTURE_EPS < distance[state] || (Math.abs(value - distance[state]) < STRUCTURE_EPS && nextRun > straightRun[state] + STRUCTURE_EPS)) {
                distance[state] = value; previous[state] = item.state; origin[state] = origin[item.state];
                straightRun[state] = nextRun;
                heap.push({ state, value, priority: value + heuristic(next) });
            }
        }
    }
    if (bestState < 0) return [];
    const path = [bestTarget.anchor];
    for (let state = bestState; state >= 0; state = previous[state]) path.push(pointFor(Math.floor(state / 4)));
    path.push(sources[origin[bestState]].anchor);
    const result = simplify(path.reverse());
    return safePolyline(result, obstacles, width, height, occupied) ? result : [];
}

function rectanglesOverlap(a, b) { return a.left < b.right + EPS && a.right > b.left - EPS && a.top < b.bottom + EPS && a.bottom > b.top - EPS; }
function labelLength(label) { return Math.ceil([...String(label)].reduce((total, char) => total + (char.charCodeAt(0) > 255 ? 12 : 7), 12)); }
function labelFor(route, routes, obstacles, labels, width, height, excluded = new Set()) {
    if (!route.label || route.points.length < 2) return null;
    const length = labelLength(route.label);
    function place(x, y, vertical) {
        if (excluded.has(`${x}|${y}|${vertical}`)) return null;
        const boxWidth = vertical ? 22 : length, boxHeight = vertical ? length : 22;
        const box = { left: x - boxWidth / 2, right: x + boxWidth / 2, top: y - boxHeight / 2, bottom: y + boxHeight / 2 };
        if (box.left < 1 || box.right > width - 1 || box.top < 1 || box.bottom > height - 1 || obstacles.some(rect => rectanglesOverlap(box, inflate(rect, 2))) || labels.some(rect => rectanglesOverlap(box, inflate(rect, 3)))) return null;
        if (routes.some(other => other !== route && segments(other.points).some(([a, b]) => segmentHitsRect(a, b, inflate(box, 2))))) return null;
        labels.push(box);
        return { x, y, vertical, width: boxWidth, height: boxHeight };
    }
    const parts = segments(route.points).map(([a, b]) => ({ a, b, vertical: Math.abs(a.x - b.x) < STRUCTURE_EPS, length: lengthOf(a, b) }));
    const ordered = [...parts].sort((a, b) => Number(a.vertical) - Number(b.vertical) || b.length - a.length);
    const padding = point => samePoint(point, route.points.at(-1)) ? ARROW_STRAIGHT : samePoint(point, route.points[0]) ? 5 : 8;
    for (const segment of ordered) {
        const axis = segment.vertical ? 'y' : 'x';
        const low = segment.a[axis] < segment.b[axis] ? segment.a : segment.b;
        const high = low === segment.a ? segment.b : segment.a;
        const start = low[axis], end = high[axis];
        const min = start + length / 2 + padding(low), max = end - length / 2 - padding(high);
        if (max < min) continue;
        const positions = unique([(start + end) / 2, min, max, start + (end - start) * .25, start + (end - start) * .75], min, max);
        positions.sort((a, b) => Math.abs(a - (start + end) / 2) - Math.abs(b - (start + end) / 2));
        for (const position of positions) {
            const placement = place(segment.vertical ? segment.a.x : position, segment.vertical ? position : segment.a.y, segment.vertical);
            if (placement) return placement;
        }
    }
    // A 40px boundary line remains a straight 40px line. A narrow caption can
    // attach across it in the existing gutter; text never introduces waypoints.
    for (const segment of ordered) {
        const axis = segment.vertical ? 'y' : 'x', crossAxis = segment.vertical ? 'x' : 'y';
        const low = segment.a[axis] < segment.b[axis] ? segment.a : segment.b;
        const high = low === segment.a ? segment.b : segment.a;
        const min = low[axis] + 11 + padding(low), max = high[axis] - 11 - padding(high);
        if (max < min) continue;
        const anchors = unique([(min + max) / 2, min, max], min, max);
        const centre = segment.a[crossAxis], reach = length / 2 - 8;
        const shifts = unique([centre, centre - reach / 2, centre + reach / 2, centre - reach, centre + reach,
            ...labels.flatMap(box => segment.vertical ? [box.left - length / 2 - 4, box.right + length / 2 + 4] : [box.top - length / 2 - 4, box.bottom + length / 2 + 4])], centre - reach, centre + reach);
        shifts.sort((a, b) => Math.abs(a - centre) - Math.abs(b - centre));
        for (const anchor of anchors) for (const shift of shifts) {
            const placement = place(segment.vertical ? shift : anchor, segment.vertical ? anchor : shift, !segment.vertical);
            if (placement) return placement;
        }
    }
    return null;
}

function placeRouteLabels(routes, obstacles, width, height) {
    const labels = [];
    for (const route of routes) route.labelPlacement = labelFor(route, routes, obstacles, labels, width, height);
    const active = routes.filter(route => route.label && route.points.length > 1);
    if (active.every(route => route.labelPlacement)) return;
    // Nearby short boundary captions share one narrow gutter. If greedy
    // placement leaves one out, move an earlier caption rather than the line.
    // Hardest (shortest) paths go first; the bounded search never runs at idle.
    const ordered = [...active].sort((a, b) => Math.max(...segments(a.points).map(([x, y]) => lengthOf(x, y))) - Math.max(...segments(b.points).map(([x, y]) => lengthOf(x, y))));
    const trialLabels = [], chosen = new Map();
    let attempts = 256;
    function placeNext(index) {
        if (index === ordered.length) return true;
        if (attempts <= 0) return false;
        const route = ordered[index], excluded = new Set();
        for (let candidate = 0; candidate < 24 && attempts-- > 0; candidate++) {
            const placement = labelFor(route, routes, obstacles, trialLabels, width, height, excluded);
            if (!placement) return false;
            chosen.set(route.key, placement);
            if (placeNext(index + 1)) return true;
            trialLabels.pop();
            chosen.delete(route.key);
            excluded.add(`${placement.x}|${placement.y}|${placement.vertical}`);
        }
        return false;
    }
    if (placeNext(0)) for (const route of active) route.labelPlacement = chosen.get(route.key);
}

function routePorts(connection, width, height, byKey, obstacles, occupied, corners = false) {
    const source = byKey.get(connection.sourceKey), target = byKey.get(connection.targetKey);
    return {
        sources: connection.direction === 'incoming' ? boundaryPorts(connection.edge, width, height, target, obstacles, occupied) : cardPorts(source, height, corners),
        targets: connection.direction === 'outgoing' ? boundaryPorts(connection.edge, width, height, source, obstacles, occupied) : cardPorts(target, height, corners),
    };
}

/** Compare actual simplified paths, not the number of grid-search steps.
 * Avoid crossings first, then unnecessary bends, then excess distance. */
function routeQuality(points, occupied) {
    return [segments(points).reduce((sum, [a, b]) => sum + trackCost(a, b, occupied), 0),
        Math.max(0, points.length - 2), segments(points).reduce((sum, [a, b]) => sum + lengthOf(a, b), 0)];
}
function betterQuality(a, b) {
    return !b || a.some((value, index) => a.slice(0, index).every((item, i) => Math.abs(item - b[i]) < EPS) && value < b[index] - EPS);
}

/** Try straight, L and Z shapes before a full visibility-grid search. Every
 * candidate uses the same real ports, clearances and arrow stubs as A*. */
function simpleRoute(width, height, obstacles, occupied, sourcePorts, targetPorts, sourceKey, targetKey) {
    const expanded = obstacles.map(rect => inflate(rect, CLEARANCE));
    const sources = sourcePorts.filter(port => legalPort(port, sourceKey, width, height, expanded, occupied));
    const targets = targetPorts.filter(port => legalPort(port, targetKey, width, height, expanded, occupied));
    let best = [], quality = null;
    function consider(source, target, middle) {
        const points = simplify([source.anchor, source.point, ...middle, target.point, target.anchor]);
        if (points.length < 2 || !fitsEndpoint(points, [source], true) || !fitsEndpoint(points, [target], false)) return;
        const score = routeQuality(points, occupied);
        if (!betterQuality(score, quality) || !safePolyline(points, obstacles, width, height, occupied)) return;
        if (segments(points).some(([a, b], i) => expanded.some(rect => !((i === 0 && rect.key === sourceKey) || (i === points.length - 2 && rect.key === targetKey)) && segmentHitsRect(a, b, rect)))) return;
        // A tiny intermediate stair is never preferable to the A* fallback.
        if (segments(points).slice(1, -1).some(([a, b]) => lengthOf(a, b) < ARROW_STRAIGHT - EPS)) return;
        best = points; quality = score;
    }
    for (const source of sources) for (const target of targets) {
        const a = source.point, b = target.point;
        if (Math.abs(a.x - b.x) < STRUCTURE_EPS || Math.abs(a.y - b.y) < STRUCTURE_EPS) consider(source, target, []);
        consider(source, target, [{ x: a.x, y: b.y }]);
        consider(source, target, [{ x: b.x, y: a.y }]);
    }
    // With no crossing and at most one bend, another channel cannot improve it.
    if (quality && quality[0] === 0 && quality[1] <= 1) return best;
    const xs = unique(expanded.flatMap(rect => [rect.left, rect.right]), BOUNDARY, width - BOUNDARY);
    const ys = unique(expanded.flatMap(rect => [rect.top, rect.bottom]), BOUNDARY, height - BOUNDARY);
    for (const source of sources) for (const target of targets) {
        const a = source.point, b = target.point;
        for (const x of unique([pixel((a.x + b.x) / 2), ...xs], BOUNDARY, width - BOUNDARY)) consider(source, target, [{ x, y: a.y }, { x, y: b.y }]);
        for (const y of unique([pixel((a.y + b.y) / 2), ...ys], BOUNDARY, height - BOUNDARY)) consider(source, target, [{ x: a.x, y }, { x: b.x, y }]);
    }
    return best;
}

function fitsEndpoint(points, ports, atStart) {
    const anchor = atStart ? points[0] : points[points.length - 1];
    const next = atStart ? points[1] : points[points.length - 2];
    return ports.some(port => samePoint(anchor, port.anchor) && direction(anchor, next) === port.outDirection && lengthOf(anchor, next) >= ENDPOINT_STUB - EPS);
}

function reusableRoute(previous, connection, width, height, obstacles, occupied, sources, targets) {
    if (!previous || ['sourceKey', 'targetKey', 'direction', 'edge'].some(key => (previous[key] ?? null) !== (connection[key] ?? null))) return null;
    const points = previous.points;
    if (!Array.isArray(points) || !safePolyline(points, obstacles, width, height, occupied)) return null;
    // Boundary y may remain steady while an unrelated card animates. Its x and
    // inward/outward normal are still validated against the current canvas.
    function boundaryPort(point) {
        const x = connection.edge === 'left' ? BOUNDARY : width - BOUNDARY;
        return { anchor: { x, y: point.y }, point: { x: x + (connection.edge === 'left' ? ENDPOINT_STUB : -ENDPOINT_STUB), y: point.y }, outDirection: connection.edge === 'left' ? 0 : 2, preference: 0 };
    }
    const sourcePorts = connection.direction === 'incoming' ? [boundaryPort(points[0])] : sources;
    const targetPorts = connection.direction === 'outgoing' ? [boundaryPort(points[points.length - 1])] : targets;
    if (!fitsEndpoint(points, sourcePorts, true) || !fitsEndpoint(points, targetPorts, false)) return null;
    const expanded = obstacles.map(rect => inflate(rect, CLEARANCE));
    if (!sourcePorts.some(port => samePoint(port.anchor, points[0]) && legalPort(port, connection.sourceKey, width, height, expanded, occupied)) || !targetPorts.some(port => samePoint(port.anchor, points[points.length - 1]) && legalPort(port, connection.targetKey, width, height, expanded, occupied))) return null;
    // Keep the required stroke clearance everywhere except the deliberate
    // endpoint stubs that connect to their own card's boundary.
    if (segments(points).some(([a, b], index) => expanded.some(rect => !((index === 0 && rect.key === connection.sourceKey) || (index === points.length - 2 && rect.key === connection.targetKey)) && segmentHitsRect(a, b, rect)))) return null;
    return points.map(point => ({ ...point }));
}

function quadraticAt(a, control, b, t) {
    const s = 1 - t;
    return { x: s * s * a.x + 2 * s * t * control.x + t * t * b.x, y: s * s * a.y + 2 * s * t * control.y + t * t * b.y };
}

function curveHitsRect(a, control, b, rect) {
    // Evaluate intervals separated by exact boundary roots. A tiny obstacle
    // cannot slip between sampled points, including while a card animates.
    const bounds = [0, 1];
    for (const [axis, limits] of [['x', [rect.left, rect.right]], ['y', [rect.top, rect.bottom]]]) {
        const aa = a[axis] - 2 * control[axis] + b[axis], bb = 2 * (control[axis] - a[axis]);
        for (const limit of limits) {
            const cc = a[axis] - limit;
            if (Math.abs(aa) < 1e-9) {
                if (Math.abs(bb) > 1e-9) bounds.push(-cc / bb);
            } else {
                const discriminant = bb * bb - 4 * aa * cc;
                if (discriminant >= 0) {
                    const root = Math.sqrt(discriminant);
                    bounds.push((-bb - root) / (2 * aa), (-bb + root) / (2 * aa));
                }
            }
        }
    }
    const sorted = bounds.filter(t => t >= 0 && t <= 1).sort((x, y) => x - y);
    return sorted.some(t => inside(quadraticAt(a, control, b, t), rect)) || sorted.slice(1).some((t, index) => inside(quadraticAt(a, control, b, (t + sorted[index]) / 2), rect));
}

/** Actual quadratic SVG corners, not just rounded joins. Unsafe curves shrink
 * toward their original safe orthogonal corner. The first and final 12px remain
 * straight so arrowheads do not rotate into a card or disappear at a boundary.
 */
export function roundedScheduleRoute(points, obstacles = [], radius = 8) {
    if (!Array.isArray(points) || points.some(point => !point || !Number.isFinite(point.x) || !Number.isFinite(point.y))) return '';
    const route = simplify(points);
    if (!safePolyline(route, obstacles)) return '';
    const radii = [], clearanceObstacles = obstacles.map(rect => inflate(rect, 0.75));
    const curveFor = (index, amount) => {
        const a = route[index - 1], corner = route[index], b = route[index + 1];
        const incoming = lengthOf(a, corner), outgoing = lengthOf(corner, b);
        return {
            before: { x: corner.x - (corner.x - a.x) / incoming * amount, y: corner.y - (corner.y - a.y) / incoming * amount },
            after: { x: corner.x + (b.x - corner.x) / outgoing * amount, y: corner.y + (b.y - corner.y) / outgoing * amount },
        };
    };
    for (let i = 1; i < route.length - 1; i++) {
        const incoming = lengthOf(route[i - 1], route[i]), outgoing = lengthOf(route[i], route[i + 1]);
        let amount = Math.max(0, Math.min(Number.isFinite(radius) ? radius : 8, incoming / 2, outgoing / 2, i === 1 ? incoming - ARROW_STRAIGHT : Infinity, i === route.length - 2 ? outgoing - ARROW_STRAIGHT : Infinity));
        const safe = value => {
            const curve = curveFor(i, value);
            return !clearanceObstacles.some(rect => curveHitsRect(curve.before, route[i], curve.after, rect));
        };
        if (amount && !safe(amount)) {
            let low = 0, high = amount;
            for (let attempt = 0; attempt < 10; attempt++) {
                const middle = (low + high) / 2;
                if (safe(middle)) low = middle; else high = middle;
            }
            amount = Math.floor(low * 100) / 100;
        }
        radii[i] = amount >= 0.05 ? amount : 0;
    }
    const number = value => Number(value.toFixed(4));
    const position = point => `${number(point.x)} ${number(point.y)}`;
    let d = `M ${position(route[0])}`, current = route[0];
    const lineTo = point => {
        if (!samePoint(current, point)) d += ` L ${position(point)}`;
        current = point;
    };
    for (let i = 1; i < route.length - 1; i++) {
        if (!radii[i]) { lineTo(route[i]); continue; }
        const curve = curveFor(i, radii[i]);
        lineTo(curve.before);
        d += ` Q ${position(route[i])} ${position(curve.after)}`;
        current = curve.after;
    }
    lineTo(route[route.length - 1]);
    return d;
}

/**
 * Invalid/missing endpoints or a completely blocked canvas return points: [].
 * Label placement is best effort; null tells the deck to use its accessible
 * card/boundary caption. Unavoidable perpendicular crossings are penalized;
 * collinear overlap and entering a lesson rectangle are never accepted.
 */
export function routeScheduleChanges({ width, height, obstacles = [], connections = [], previousRoutes = [] } = {}) {
    const validCanvas = Number.isFinite(width) && Number.isFinite(height) && width > BOUNDARY * 2 && height > BOUNDARY * 2;
    const validObstacles = obstacles.filter(rect => rect && [rect.left, rect.top, rect.right, rect.bottom].every(Number.isFinite) && rect.right > rect.left && rect.bottom > rect.top).map(rect => ({ ...rect, left: pixel(rect.left), top: pixel(rect.top), right: pixel(rect.right), bottom: pixel(rect.bottom) }));
    const byKey = new Map(validObstacles.map(rect => [rect.key, rect]));
    const priorByKey = new Map(previousRoutes.map(route => [route.key, route]));
    const occupied = [], routes = [];
    for (const connection of connections) {
        const route = { ...connection, points: [], labelPlacement: null, reason: null, reused: false };
        routes.push(route);
        if (!validCanvas) { route.reason = 'invalid_canvas'; continue; }
        const source = byKey.get(connection.sourceKey), target = byKey.get(connection.targetKey);
        const outgoing = connection.direction === 'outgoing', incoming = connection.direction === 'incoming';
        if ((outgoing && (!source || target)) || (incoming && (!target || source)) || (!outgoing && !incoming && (!source || !target)) || ((outgoing || incoming) && !['left', 'right'].includes(connection.edge))) { route.reason = 'missing_endpoint'; continue; }
        if (source && target && source.key === target.key) { route.reason = 'same_time'; continue; }
        const { sources, targets } = routePorts(connection, width, height, byKey, validObstacles, occupied);
        const reusable = reusableRoute(priorByKey.get(route.key), connection, width, height, validObstacles, occupied, sources, targets);
        const simple = simpleRoute(width, height, validObstacles, occupied, sources, targets, source?.key, target?.key);
        // Recheck cheap shapes even when the old detour is still safe: shrinking
        // an obstacle must restore a straight/L route, not preserve old knots.
        const previousQuality = reusable ? routeQuality(reusable, occupied) : null;
        const simpleQuality = simple.length ? routeQuality(simple, occupied) : null;
        const materiallySimpler = simpleQuality && (!previousQuality || simpleQuality[0] < previousQuality[0] || (simpleQuality[0] === previousQuality[0] && (simpleQuality[1] < previousQuality[1] || (simpleQuality[1] === previousQuality[1] && simpleQuality[2] < previousQuality[2] - 16))));
        route.reused = Boolean(reusable && !materiallySimpler);
        route.points = route.reused ? reusable : simple;
        if (!route.points.length || routeQuality(route.points, occupied)[0] > 0) {
            const searched = findRoute(width, height, validObstacles, occupied, sources, targets, source?.key, target?.key);
            if (searched.length && (!route.points.length || betterQuality(routeQuality(searched, occupied), routeQuality(route.points, occupied)))) {
                route.points = searched; route.reused = false;
            }
        }
        if (!route.points.length) {
            const fallback = routePorts(connection, width, height, byKey, validObstacles, occupied, true);
            route.points = findRoute(width, height, validObstacles, occupied, fallback.sources, fallback.targets, source?.key, target?.key);
            if (!route.points.length) {
                const expanded = validObstacles.map(rect => inflate(rect, CLEARANCE));
                route.reason = !fallback.sources.some(port => legalPort(port, source?.key, width, height, expanded)) || !fallback.targets.some(port => legalPort(port, target?.key, width, height, expanded)) ? 'endpoint_occluded' : 'no_safe_path';
            }
        }
        occupied.push(...segments(route.points));
    }
    placeRouteLabels(routes, validObstacles, width, height);
    return routes;
}
