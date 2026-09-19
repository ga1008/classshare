import { describe, expect, it } from 'vitest';
import { roundedScheduleRoute, routeScheduleChanges, type ScheduleRouteObstacle, type ScheduleRoutePoint } from '../../../static/js/course_schedule_change_routes.js';

type Point = ScheduleRoutePoint;
type Rect = ScheduleRouteObstacle;
const rect = (key: string, left: number, top: number, right: number, bottom: number): Rect => ({ key, left, top, right, bottom });
function onBoundary(point: Point, box: Rect) {
  return ((point.x === box.left || point.x === box.right) && point.y >= box.top && point.y <= box.bottom) || ((point.y === box.top || point.y === box.bottom) && point.x >= box.left && point.x <= box.right);
}
function hits(a: Point, b: Point, box: Rect) {
  if (a.y === b.y) return a.y > box.top && a.y < box.bottom && Math.max(a.x, b.x) > box.left && Math.min(a.x, b.x) < box.right;
  return a.x > box.left && a.x < box.right && Math.max(a.y, b.y) > box.top && Math.min(a.y, b.y) < box.bottom;
}
function expectSafe(points: Point[], obstacles: Rect[], width = 600, height = 400) {
  expect(points.length).toBeGreaterThan(1);
  points.forEach(point => { expect(point.x).toBeGreaterThanOrEqual(0); expect(point.x).toBeLessThanOrEqual(width); expect(point.y).toBeGreaterThanOrEqual(0); expect(point.y).toBeLessThanOrEqual(height); });
  points.slice(1).forEach((b, i) => {
    const a = points[i];
    expect(a.x === b.x || a.y === b.y).toBe(true);
    obstacles.forEach(box => expect(hits(a, b, box), `segment ${JSON.stringify([a, b])} crosses ${box.key}`).toBe(false));
    expect(Math.abs(a.x - b.x) + Math.abs(a.y - b.y)).toBeGreaterThan(0.01);
    if (i > 0) {
      const before = points[i - 1];
      expect((a.x - before.x) * (b.x - a.x) + (a.y - before.y) * (b.y - a.y)).toBeGreaterThanOrEqual(0);
    }
  });
  expect(Math.abs(points[0].x - points[1].x) + Math.abs(points[0].y - points[1].y)).toBeGreaterThanOrEqual(19.99);
  expect(Math.abs(points.at(-1)!.x - points.at(-2)!.x) + Math.abs(points.at(-1)!.y - points.at(-2)!.y)).toBeGreaterThanOrEqual(19.99);
}
function shareSegment(a: Point[], b: Point[]) {
  return a.slice(1).some((a2, i) => b.slice(1).some((b2, j) => {
    const a1 = a[i], b1 = b[j];
    if (a1.y === a2.y && b1.y === b2.y && a1.y === b1.y) return Math.min(Math.max(a1.x, a2.x), Math.max(b1.x, b2.x)) > Math.max(Math.min(a1.x, a2.x), Math.min(b1.x, b2.x));
    if (a1.x === a2.x && b1.x === b2.x && a1.x === b1.x) return Math.min(Math.max(a1.y, a2.y), Math.max(b1.y, b2.y)) > Math.max(Math.min(a1.y, a2.y), Math.min(b1.y, b2.y));
    return false;
  }));
}
function bendCount(points: Point[]) {
  return points.slice(2).filter((point, index) => (points[index].x === points[index + 1].x) !== (points[index + 1].x === point.x)).length;
}

describe('orthogonal academic change routes', () => {
  it('points from the original time to the proposed time around a blocking lesson', () => {
    const a = rect('original', 50, 120, 150, 220), b = rect('proposed', 420, 120, 520, 220), blocker = rect('other-lesson', 240, 70, 330, 290);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a, b, blocker], connections: [{ key: 'change', sourceKey: a.key, targetKey: b.key, label: '时间更改' }] });
    expectSafe(route.points, [a, b, blocker]);
    expect(onBoundary(route.points[0], a)).toBe(true);
    expect(onBoundary(route.points.at(-1)!, b)).toBe(true);
    expect(route.points.some(point => point.y < blocker.top || point.y > blocker.bottom)).toBe(true);
  });
  it.each(['left', 'right'] as const)('keeps outgoing and incoming arrows correctly directed at the %s page boundary', edge => {
    const a = rect('original', 230, 100, 340, 190), b = rect('proposed', 240, 240, 350, 330);
    const routes = routeScheduleChanges({ width: 600, height: 400, obstacles: [a, b], connections: [
      { key: 'out', sourceKey: a.key, targetKey: null, direction: 'outgoing', edge, label: '时间更改' },
      { key: 'in', sourceKey: null, targetKey: b.key, direction: 'incoming', edge, label: '时间更改' },
    ] });
    routes.forEach(route => expectSafe(route.points, [a, b]));
    expect(onBoundary(routes[0].points[0], a)).toBe(true);
    expect(routes[0].points.at(-1)!.x).toBe(edge === 'left' ? 8 : 592);
    expect(routes[1].points[0].x).toBe(edge === 'left' ? 8 : 592);
    expect(onBoundary(routes[1].points.at(-1)!, b)).toBe(true);
    expect(shareSegment(routes[0].points, routes[1].points)).toBe(false);
  });
  it('routes two changes from the same lesson separately instead of concealing their lines', () => {
    const a = rect('a', 80, 150, 180, 250), b = rect('b', 420, 150, 520, 250);
    const routes = routeScheduleChanges({ width: 600, height: 400, obstacles: [a, b], connections: [
      { key: 'one', sourceKey: 'a', targetKey: 'b', label: '时间更改' },
      { key: 'two', sourceKey: 'a', targetKey: 'b', label: '时间更改' },
    ] });
    routes.forEach(route => expectSafe(route.points, [a, b]));
    expect(shareSegment(routes[0].points, routes[1].points)).toBe(false);
    expect(routes.map(route => route.key)).toEqual(['one', 'two']);
  });
  it('does not invent a time destination for room-only changes on one time card', () => {
    const a = rect('same-time', 220, 100, 350, 280);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a], connections: [{ key: 'room', sourceKey: a.key, targetKey: a.key, label: '教室更改' }] });
    expect(route.points).toEqual([]);
    expect(route.reason).toBe('same_time');
    expect(route.labelPlacement).toBeNull();
  });
  it.each(['outgoing', 'incoming'] as const)('keeps a 40px %s boundary connection straight while placing its long caption clear of the card and arrow', direction => {
    const a = rect('near-right', 420, 80, 552, 175);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a], connections: [{ key: 'edge', sourceKey: direction === 'outgoing' ? a.key : null, targetKey: direction === 'incoming' ? a.key : null, edge: 'right', direction, label: '时间更改 · 教室更改 · 至第5周' }] });
    expectSafe(route.points, [a]);
    expect(route.labelPlacement).not.toBeNull();
    expect(route.points).toEqual(direction === 'outgoing' ? [{ x: 552, y: 127.5 }, { x: 592, y: 127.5 }] : [{ x: 592, y: 127.5 }, { x: 552, y: 127.5 }]);
    expect(bendCount(route.points)).toBe(0);
    expect((direction === 'outgoing' ? route.points.at(-1)! : route.points[0]).x).toBe(592);
    const label = route.labelPlacement!;
    expect(label.x + label.width / 2).toBeLessThan(600);
    const box = rect('caption', label.x - label.width / 2, label.y - label.height / 2, label.x + label.width / 2, label.y + label.height / 2);
    expect(box.left < a.right && box.right > a.left && box.top < a.bottom && box.bottom > a.top).toBe(false);
    const end = route.points.at(-1)!, previous = route.points.at(-2)!;
    const segmentLength = Math.abs(end.x - previous.x) + Math.abs(end.y - previous.y);
    const arrowStart = { x: end.x + (previous.x - end.x) * 12 / segmentLength, y: end.y + (previous.y - end.y) * 12 / segmentLength };
    expect(hits(arrowStart, end, box), 'caption must leave the final 12px arrow shaft uncovered').toBe(false);
  });
  it.each(['outgoing', 'incoming'] as const)('does not change the %s geometry when the caption is absent, short, or long', direction => {
    const obstacle = rect('edge-card', 420, 80, 552, 175);
    const connection = { key: 'caption-independent', sourceKey: direction === 'outgoing' ? obstacle.key : null, targetKey: direction === 'incoming' ? obstacle.key : null, direction, edge: 'right' as const };
    const routes = ['', '时间更改', '时间更改 · 教室更改 · 来自第3周 · 至第15周'].map(label => routeScheduleChanges({ width: 600, height: 400, obstacles: [obstacle], connections: [{ ...connection, label }] })[0]);
    routes.forEach(route => expectSafe(route.points, [obstacle]));
    expect(routes[1].points).toEqual(routes[0].points);
    expect(routes[2].points).toEqual(routes[0].points);
    expect(bendCount(routes[0].points)).toBe(0);
    expect(routes[0].labelPlacement).toBeNull();
  });
  it('uses a legal one-bend L for a same-week move instead of adding unnecessary turns for the label', () => {
    const a = rect('a', 80, 80, 180, 180), b = rect('b', 410, 230, 510, 330);
    const options = { width: 600, height: 400, obstacles: [a, b] };
    const captions = ['', '时间更改', '时间更改 · 教室更改 · 同周调整'];
    const routes = captions.map(label => routeScheduleChanges({ ...options, connections: [{ key: 'local-L', sourceKey: 'a', targetKey: 'b', label }] })[0]);
    for (const route of routes) {
      expectSafe(route.points, [a, b]);
      expect(bendCount(route.points)).toBe(1);
      expect(onBoundary(route.points[0], a)).toBe(true);
      expect(onBoundary(route.points.at(-1)!, b)).toBe(true);
      expect(route.points).toEqual(routes[0].points);
    }
  });
  it('replaces a still-safe cached detour with the available straight route', () => {
    const a = rect('a', 60, 80, 160, 160), b = rect('b', 420, 80, 520, 160);
    const connection = { key: 'cached-route', sourceKey: 'a', targetKey: 'b', label: '时间更改' };
    const oldPoints = [{ x: 160, y: 120 }, { x: 200, y: 120 }, { x: 200, y: 260 }, { x: 360, y: 260 }, { x: 360, y: 120 }, { x: 420, y: 120 }];
    expectSafe(oldPoints, [a, b]);
    const previous = { ...connection, points: oldPoints, labelPlacement: null, reason: null, reused: false };
    const oldSnapshot = JSON.stringify(previous);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a, b], connections: [connection], previousRoutes: [previous] });
    expectSafe(route.points, [a, b]);
    expect(route.points).toEqual([{ x: 160, y: 120 }, { x: 420, y: 120 }]);
    expect(bendCount(route.points)).toBe(0);
    expect(route.reused).toBe(false);
    expect(JSON.stringify(previous)).toBe(oldSnapshot);
  });
  it('returns an empty route when a wall leaves no legal passage, never cutting through the lesson', () => {
    const a = rect('a', 60, 60, 180, 140), b = rect('b', 380, 260, 500, 340), wall = rect('wall', 0, 180, 600, 220);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a, b, wall], connections: [{ key: 'blocked', sourceKey: 'a', targetKey: 'b', label: '时间更改' }] });
    expect(route.points).toEqual([]);
    expect(route.labelPlacement).toBeNull();
    expect(route.reason).toBe('no_safe_path');
  });
  it('never guesses a missing endpoint, even when another card has the same coordinates', () => {
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [rect('a', 80, 80, 180, 180), rect('unrelated', 360, 80, 460, 180)], connections: [{ key: 'missing', sourceKey: 'a', targetKey: 'missing', label: '时间更改' }] });
    expect(route.points).toEqual([]);
    expect(route.reason).toBe('missing_endpoint');
  });
  it('places a full label in open space without covering a lesson, another label or another connection', () => {
    const obstacles = [rect('a', 60, 80, 150, 170), rect('b', 450, 80, 540, 170), rect('c', 60, 250, 150, 340), rect('d', 450, 250, 540, 340)];
    const routes = routeScheduleChanges({ width: 600, height: 400, obstacles, connections: [{ key: 'one', sourceKey: 'a', targetKey: 'b', label: '时间更改 · 教室更改' }, { key: 'two', sourceKey: 'c', targetKey: 'd', label: '时间更改' }] });
    for (const route of routes) {
      expectSafe(route.points, obstacles);
      expect(route.labelPlacement).not.toBeNull();
      const label = route.labelPlacement!;
      const box = rect('label', label.x - label.width / 2, label.y - label.height / 2, label.x + label.width / 2, label.y + label.height / 2);
      obstacles.forEach(other => expect(box.left < other.right && box.right > other.left && box.top < other.bottom && box.bottom > other.top).toBe(false));
      routes.filter(other => other !== route).forEach(other => other.points.slice(1).forEach((b, i) => expect(hits(other.points[i], b, box)).toBe(false)));
    }
  });
  it('keeps dense 4px gutters traversable without cutting either neighboring card', () => {
    const a = rect('a', 50, 40, 200, 130), b = rect('b', 210, 280, 370, 370), neighbors = [rect('left', 0, 150, 280, 250), rect('right', 284, 150, 600, 250)];
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a, b, ...neighbors], connections: [{ key: 'narrow', sourceKey: 'a', targetKey: 'b', label: '时间更改' }] });
    expectSafe(route.points, [a, b, ...neighbors]);
    expect(route.points.some(point => point.x === 282)).toBe(true);
  });
  it('remains orthogonal with browser subpixel measurements and does not mutate the input', () => {
    const obstacles = [rect('a', 70.23456, 40.6789, 185.1234, 155.5678), rect('b', 367.5678, 255.2345, 492.1234, 355.6543)];
    const options = { width: 600, height: 400, obstacles, connections: [{ key: 'fractional', sourceKey: 'a', targetKey: 'b', label: '时间更改' }] };
    const snapshot = JSON.stringify(options);
    const [route] = routeScheduleChanges(options);
    expect(route.points.length).toBeGreaterThan(1);
    route.points.slice(1).forEach((b, i) => expect(route.points[i].x === b.x || route.points[i].y === b.y).toBe(true));
    expect(JSON.stringify(options)).toBe(snapshot);
    expect(routeScheduleChanges(options)).toEqual([route]);
  });
  it('prefers four edge midpoints and keeps enough straight space for arrowheads', () => {
    const a = rect('a', 80, 80, 180, 180), b = rect('b', 410, 230, 510, 330);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a, b], connections: [{ key: 'midpoints', sourceKey: 'a', targetKey: 'b', label: '时间更改' }] });
    expectSafe(route.points, [a, b]);
    function midpoint(point: Point, box: Rect) {
      return ((point.x === box.left || point.x === box.right) && point.y === (box.top + box.bottom) / 2) || ((point.y === box.top || point.y === box.bottom) && point.x === (box.left + box.right) / 2);
    }
    expect(midpoint(route.points[0], a)).toBe(true);
    expect(midpoint(route.points.at(-1)!, b)).toBe(true);
  });
  it('enters a bottom-edge destination from its top instead of hiding a reversed arrow below it', () => {
    const a = rect('a', 390, 50, 510, 150), b = rect('b', 390, 280, 510, 396);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a, b], connections: [{ key: 'bottom', sourceKey: 'a', targetKey: 'b', label: '时间更改' }] });
    expectSafe(route.points, [a, b]);
    expect(route.points.at(-1)).toEqual({ x: 450, y: b.top });
    expect(route.points.at(-2)!.y).toBeLessThan(b.top);
    expect(route.points.some(point => point.y > b.bottom)).toBe(false);
  });
  it('aligns a boundary entry directly with the top stub instead of adding a short reverse stair', () => {
    const target = rect('bottom', 380, 280, 520, 396), leftNeighbor = rect('left-neighbor', 0, 280, 375, 400), rightNeighbor = rect('right-neighbor', 525, 280, 600, 400);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [target, leftNeighbor, rightNeighbor], connections: [{ key: 'incoming', sourceKey: null, targetKey: 'bottom', direction: 'incoming', edge: 'left', label: '时间更改 · 来自第3周' }] });
    expectSafe(route.points, [target, leftNeighbor, rightNeighbor]);
    expect(route.points).toEqual([{ x: 8, y: 260 }, { x: 450, y: 260 }, { x: 450, y: 280 }]);
  });
  it('safely reuses unchanged paths but replans around a growing obstacle and follows changing endpoint bounds', () => {
    const a = rect('a', 60, 80, 160, 160), b = rect('b', 420, 80, 520, 160), small = rect('animated', 250, 240, 330, 330);
    const connection = { key: 'live', sourceKey: 'a', targetKey: 'b', label: '时间更改' };
    const options = { width: 600, height: 400, obstacles: [a, b, small], connections: [connection] };
    const original = routeScheduleChanges(options);
    const stable = routeScheduleChanges({ ...options, previousRoutes: original });
    expect(stable[0].reused).toBe(true);
    expect(stable[0].points).toEqual(original[0].points);
    const expanded = rect('animated', 210, 90, 390, 250);
    const moved = routeScheduleChanges({ ...options, obstacles: [a, b, expanded], previousRoutes: stable });
    expect(moved[0].reused).toBe(false);
    expectSafe(moved[0].points, [a, b, expanded]);
    expect(moved[0].points).not.toEqual(original[0].points);
    const largerTarget = rect('b', 350, 40, 550, 260);
    const targetGrows = routeScheduleChanges({ ...options, obstacles: [a, largerTarget, small], previousRoutes: original });
    expect(targetGrows[0].reused).toBe(false);
    expectSafe(targetGrows[0].points, [a, largerTarget, small]);
    expect(onBoundary(targetGrows[0].points.at(-1)!, largerTarget)).toBe(true);
    const shrunk = routeScheduleChanges({ ...options, previousRoutes: targetGrows });
    expect(shrunk[0].reused).toBe(false);
    expectSafe(shrunk[0].points, options.obstacles);
    expect(onBoundary(shrunk[0].points.at(-1)!, b)).toBe(true);
  });
  it('reports a completely covered endpoint instead of drawing through the expanded foreground card', () => {
    const a = rect('a', 60, 80, 160, 160), b = rect('b', 420, 80, 520, 160), cover = rect('expanded-other-card', 390, 50, 560, 220);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a, b, cover], connections: [{ key: 'hidden', sourceKey: 'a', targetKey: 'b', label: '时间更改' }] });
    expect(route.points).toEqual([]);
    expect(route.reason).toBe('endpoint_occluded');
  });
  it.each([
    ['opening', 534.953125, 796.859375, 403.1514601089842, 546.1104213656959],
    ['closing', 535.5, 796.296875, 403.1827182179285, 546.0791632567516],
  ] as const)('retains the %s animation-frame route when neighboring channels differ by 0.01px', (_phase, left, right, top, bottom) => {
    // Minimal captured geometry only: the old 0.01px topology epsilon removed
    // an actual grid step and made the final arrow stub diagonal, hiding it.
    const obstacles = [
      rect('original', 745.703125, 110.02854348390187, 881.140625, 246.251382263119),
      rect('moving-target', left, top, right, bottom),
      rect('neighbor-1', 1040.5625, 110.02854348390187, 1176, 246.251382263119),
      rect('neighbor-2', 156, 618.7542665522266, 291.421875, 754.9927343859159),
      rect('neighbor-3', 303.421875, 618.7542665522266, 438.84375, 754.9927343859159),
      rect('neighbor-4', 450.84375, 618.7542665522266, 586.28125, 754.9927343859159),
      rect('neighbor-5', 156, 406.4960777660233, 291.421875, 542.7345455997125),
      rect('neighbor-6', 450.84375, 110.02854348390187, 586.28125, 246.251382263119),
      rect('neighbor-7', 598.28125, 258.2544960977265, 733.703125, 394.4929639314158),
      rect('neighbor-8', 893.140625, 406.4960777660233, 1028.5625, 542.7345455997125),
    ];
    const [route] = routeScheduleChanges({ width: 1224, height: 783, obstacles, connections: [{ key: 'live-time-change', sourceKey: 'original', targetKey: 'moving-target', direction: 'local', edge: null, label: '时间更改' }] });
    const quantized = obstacles.map(box => Object.fromEntries(Object.entries(box).map(([key, value]) => [key, typeof value === 'number' ? Math.round(value * 100) / 100 : value])) as Rect);
    expect(route.reason).toBeNull();
    expectSafe(route.points, quantized, 1224, 783);
    expect(roundedScheduleRoute(route.points, obstacles)).toContain(' Q ');
    route.points.slice(1).forEach((point, index) => expect(Math.abs(point.x - route.points[index].x) + Math.abs(point.y - route.points[index].y)).toBeGreaterThanOrEqual(12));
    expect(onBoundary(route.points.at(-1)!, quantized[1])).toBe(true);
  });
});

function parsePath(d: string) {
  const commands = [...d.matchAll(/([MLQ])\s+([^MLQ]+)/g)];
  return commands.map(match => ({ command: match[1], values: match[2].trim().split(/\s+/).map(Number) }));
}

describe('real rounded timetable paths', () => {
  it('emits safe Q curves while preserving endpoints, arrow direction and 12px terminal straights', () => {
    const obstacles = [rect('corner-obstacle', 130, 80, 190, 140)];
    const points = [{ x: 60, y: 78 }, { x: 192, y: 78 }, { x: 192, y: 170 }, { x: 260, y: 170 }];
    const d = roundedScheduleRoute(points, obstacles, 8);
    expect(d).toContain(' Q ');
    const commands = parsePath(d);
    expect(commands[0]).toEqual({ command: 'M', values: [60, 78] });
    expect(commands.at(-1)).toEqual({ command: 'L', values: [260, 170] });
    let current = points[0];
    for (const item of commands.slice(1)) {
      const end = item.command === 'Q' ? { x: item.values[2], y: item.values[3] } : { x: item.values[0], y: item.values[1] };
      expect(Math.abs(end.x - current.x) + Math.abs(end.y - current.y)).toBeGreaterThan(0.01);
      for (let step = 0; step <= 200; step++) {
        const t = step / 200, s = 1 - t;
        const p = item.command === 'Q' ? { x: s * s * current.x + 2 * s * t * item.values[0] + t * t * end.x, y: s * s * current.y + 2 * s * t * item.values[1] + t * t * end.y } : { x: current.x + (end.x - current.x) * t, y: current.y + (end.y - current.y) * t };
        for (const box of obstacles) expect(p.x > box.left + 0.01 && p.x < box.right - 0.01 && p.y > box.top + 0.01 && p.y < box.bottom - 0.01).toBe(false);
      }
      current = end;
    }
    const firstLine = commands[1].values;
    expect(Math.abs(firstLine[0] - points[0].x) + Math.abs(firstLine[1] - points[0].y)).toBeGreaterThanOrEqual(12);
    const previous = commands.at(-2)!.values;
    expect(260 - previous.at(-2)!).toBeGreaterThanOrEqual(12);
  });
  it('keeps a full arrow shaft when a corner follows a 20px endpoint stub', () => {
    const d = roundedScheduleRoute([{ x: 80, y: 80 }, { x: 100, y: 80 }, { x: 100, y: 180 }, { x: 120, y: 180 }]);
    const commands = parsePath(d);
    expect(commands[1]).toEqual({ command: 'L', values: [92, 80] });
    expect(commands.at(-2)).toEqual({ command: 'Q', values: [100, 180, 108, 180] });
    expect(commands.at(-1)).toEqual({ command: 'L', values: [120, 180] });
  });
  it('rejects stale obstructed or backtracking polylines instead of rounding them into a misleading path', () => {
    expect(roundedScheduleRoute([{ x: 20, y: 100 }, { x: 180, y: 100 }], [rect('moving', 80, 80, 130, 130)])).toBe('');
    expect(roundedScheduleRoute([{ x: 20, y: 100 }, { x: 50, y: 100 }, { x: 30, y: 100 }])).toBe('');
    expect(roundedScheduleRoute([])).toBe('');
  });
});
