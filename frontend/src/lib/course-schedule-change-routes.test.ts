import { describe, expect, it } from 'vitest';
import { routeScheduleChanges, type ScheduleRouteObstacle, type ScheduleRoutePoint } from '../../../static/js/course_schedule_change_routes.js';

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
  });
}
function shareSegment(a: Point[], b: Point[]) {
  return a.slice(1).some((a2, i) => b.slice(1).some((b2, j) => {
    const a1 = a[i], b1 = b[j];
    if (a1.y === a2.y && b1.y === b2.y && a1.y === b1.y) return Math.min(Math.max(a1.x, a2.x), Math.max(b1.x, b2.x)) > Math.max(Math.min(a1.x, a2.x), Math.min(b1.x, b2.x));
    if (a1.x === a2.x && b1.x === b2.x && a1.x === b1.x) return Math.min(Math.max(a1.y, a2.y), Math.max(b1.y, b2.y)) > Math.max(Math.min(a1.y, a2.y), Math.min(b1.y, b2.y));
    return false;
  }));
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
  it('uses an exterior upper-to-lower U route for a room change on one time card', () => {
    const a = rect('same-time', 220, 100, 350, 280);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a], connections: [{ key: 'room', sourceKey: a.key, targetKey: a.key, label: '教室更改' }] });
    expectSafe(route.points, [a]);
    expect(route.points.length).toBeGreaterThanOrEqual(4);
    expect(onBoundary(route.points[0], a)).toBe(true);
    expect(onBoundary(route.points.at(-1)!, a)).toBe(true);
    expect(route.points[0].y).toBeLessThan(route.points.at(-1)!.y);
    expect(route.sourceKey).toBe(route.targetKey);
    expect(route.labelPlacement).toMatchObject({ vertical: true, width: 22 });
  });
  it('keeps the room-change caption on the exterior loop of a compact two-period card', () => {
    const a = rect('room', 80, 150, 210, 245);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a], connections: [{ key: 'room', sourceKey: a.key, targetKey: a.key, label: '教室更改' }] });
    expectSafe(route.points, [a]);
    expect(route.labelPlacement).not.toBeNull();
    expect(route.points[0].y).toBeLessThan(route.points.at(-1)!.y);
  });
  it.each(['outgoing', 'incoming'] as const)('reserves an exterior caption detour for a short %s boundary connection', direction => {
    const a = rect('near-right', 420, 80, 552, 175);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a], connections: [{ key: 'edge', sourceKey: direction === 'outgoing' ? a.key : null, targetKey: direction === 'incoming' ? a.key : null, edge: 'right', direction, label: '时间更改 · 教室更改 · 至第5周' }] });
    expectSafe(route.points, [a]);
    expect(route.labelPlacement).not.toBeNull();
    expect(route.points.length).toBeGreaterThan(2);
    expect((direction === 'outgoing' ? route.points.at(-1)! : route.points[0]).x).toBe(592);
    const label = route.labelPlacement!;
    expect(label.x + label.width / 2).toBeLessThan(600);
    const box = rect('caption', label.x - label.width / 2, label.y - label.height / 2, label.x + label.width / 2, label.y + label.height / 2);
    expect(box.left < a.right && box.right > a.left && box.top < a.bottom && box.bottom > a.top).toBe(false);
  });
  it('returns an empty route when a wall leaves no legal passage, never cutting through the lesson', () => {
    const a = rect('a', 60, 60, 180, 140), b = rect('b', 380, 260, 500, 340), wall = rect('wall', 0, 180, 600, 220);
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [a, b, wall], connections: [{ key: 'blocked', sourceKey: 'a', targetKey: 'b', label: '时间更改' }] });
    expect(route.points).toEqual([]);
    expect(route.labelPlacement).toBeNull();
  });
  it('never guesses a missing endpoint, even when another card has the same coordinates', () => {
    const [route] = routeScheduleChanges({ width: 600, height: 400, obstacles: [rect('a', 80, 80, 180, 180), rect('unrelated', 360, 80, 460, 180)], connections: [{ key: 'missing', sourceKey: 'a', targetKey: 'missing', label: '时间更改' }] });
    expect(route.points).toEqual([]);
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
});
