import { test, expect, type Page } from '@playwright/test';
import { serveScheduleModule } from './schedule-fixture-modules';

type Position = { date: string; sections: number[]; room: string };
const oldPosition = (date: string, sections: number[], room = 'B416-1'): Position => ({ date, sections, room });

function fixture() {
  const weeks = [3, 4, 5].map(week_index => ({ week_index, label: `第${week_index}周`, is_current: week_index === 4, lessons: [] as any[] }));
  const lesson = (key: string, course: string, position: Position, session: number, adjustment: any = null) => ({
    event_key: key, course_name: course, date: position.date,
    weekday: new Date(`${position.date}T12:00:00Z`).getUTCDay() || 7,
    sections: position.sections, hours: position.sections.length,
    classroom: position.room, classroom_short: position.room,
    class_label: '人工智能2601班（专升本） · 人工智能2602班（专升本）',
    class_offering_id: 11, session_id: session, session_no: session - 100, session_total: 32,
    classroom_url: `/classroom/11?session_id=${session}`, adjustment,
    counts_towards_total: adjustment?.endpoint !== 'proposed',
  });
  const add = (week: number, item: any) => weeks.find(w => w.week_index === week)!.lessons.push(item);
  const move = (id: string, course: string, fromWeek: number, toWeek: number, from: Position, to: Position, session: number) => {
    const shared = { request_id: id, kind: 'move', phase: 'pending', original: from, proposed: to };
    add(fromWeek, lesson(`${id}-old`, course, from, session, { ...shared, endpoint: 'original', counterpart_event_key: `${id}-new`, counterpart_week_index: toWeek }));
    add(toWeek, lesson(`${id}-new`, course, to, session, { ...shared, endpoint: 'proposed', counterpart_event_key: `${id}-old`, counterpart_week_index: fromWeek }));
  };
  move('same', '同周向前调整', 4, 4, oldPosition('2026-09-25', [2, 3]), oldPosition('2026-09-24', [6, 7]), 101);
  move('later', '调至下一周', 4, 5, oldPosition('2026-09-27', [2, 3]), oldPosition('2026-09-29', [4, 5]), 102);
  move('earlier', '从下一周提前并换教室', 5, 4, oldPosition('2026-09-29', [6, 7]), oldPosition('2026-09-21', [10, 11], 'B210'), 103);
  move('bottom-a', '底部网络课程', 3, 4, oldPosition('2026-09-19', [8, 9]), oldPosition('2026-09-22', [10, 11]), 108);
  move('bottom-b', '底部网络课程', 3, 4, oldPosition('2026-09-20', [2, 3]), oldPosition('2026-09-23', [10, 11]), 109);
  const roomFrom = oldPosition('2026-09-21', [6, 7], 'B310');
  add(4, lesson('room-only', '只换教室', roomFrom, 104, { request_id: 'room', kind: 'room', phase: 'pending', endpoint: 'original', original: roomFrom, proposed: { ...roomFrom, room: 'B312' }, counterpart_event_key: null, counterpart_week_index: null }));
  const cancelFrom = oldPosition('2026-09-23', [2, 3]);
  add(4, lesson('cancel-only', '停课', cancelFrom, 105, { request_id: 'cancel', kind: 'cancel', phase: 'pending', endpoint: 'original', original: cancelFrom, proposed: null, counterpart_event_key: null }));
  add(4, lesson('unaffected-one', '原有正式课程', oldPosition('2026-09-24', [4, 5]), 106));
  add(4, lesson('unaffected-two', '原有周末课程', oldPosition('2026-09-26', [6, 7]), 107));
  return { selected_term: { year: '2026-2027', term: '1', label: '2026-2027第一学期' },
    section_range: { min: 1, max: 11 }, filters: { course_options: [...new Set(weeks.flatMap(w => w.lessons.map(l => l.course_name)))] }, weeks };
}

async function mount(page: Page) {
  await page.route('http://schedule-lines.test/**', async route => {
    if (await serveScheduleModule(route)) return;
    await route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{box-sizing:border-box}body{margin:16px;font-family:Arial,sans-serif}</style><div id="deck"></div><script type="module">
      import { createScheduleDeck } from '/static/js/course_schedule_deck.js?v=fixture';
      window.navigations=[]; window.fixture=${JSON.stringify(fixture())};
      window.deck=createScheduleDeck(document.getElementById('deck'),{onNavigate:url=>window.navigations.push(url)});
      window.deck.setOverview(window.fixture); window.deck.goToWeek(1); window.deck.openExpanded();
    </script></html>` });
  });
  await page.goto('http://schedule-lines.test/');
  await expect(page.getByRole('dialog')).toBeVisible();
  await settle(page);
  await expect(page.locator('.cs-expand .cs-change-line')).not.toHaveCount(0);
}

async function settle(page: Page) {
  await page.locator('.cs-expand__card').evaluate(async node => {
    await Promise.allSettled(node.getAnimations({ subtree: true }).map(animation => animation.finished));
    await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
  });
}

function change(page: Page, source: string) {
  const request = source === 'room-only' ? 'room' : source === 'cancel-only' ? 'cancel' : source.replace(/-old$/, '');
  return page.locator(`.cs-expand .cs-change-lines > g[data-change-key='${JSON.stringify([request, source])}']`);
}

async function geometry(page: Page) {
  return page.locator('.cs-expand .cs-change-map').evaluate(map => {
    const cards = [...map.querySelectorAll<HTMLElement>('.cs-lesson-slot > [data-event-key]')].map(card => ({ key: card.dataset.eventKey, box: card.getBoundingClientRect() }));
    const inside = (point: { x: number; y: number }, box: DOMRect) => point.x > box.left + 1 && point.x < box.right - 1 && point.y > box.top + 1 && point.y < box.bottom - 1;
    const collisions: string[] = [], paths: any[] = [];
    const routeShape = (d: string) => {
      const points: { x: number; y: number }[] = [];
      for (const command of d.matchAll(/([MLQ])([^MLQ]*)/g)) {
        const values = (command[2].match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi) || []).map(Number);
        for (let index = 0; index < values.length; index += 2) {
          const point = { x: values[index], y: values[index + 1] };
          if (points.length && Math.hypot(points.at(-1)!.x - point.x, points.at(-1)!.y - point.y) < .01) continue;
          while (points.length >= 2) {
            const a = points.at(-2)!, b = points.at(-1)!;
            const cross = (b.x - a.x) * (point.y - b.y) - (b.y - a.y) * (point.x - b.x);
            const dot = (b.x - a.x) * (point.x - b.x) + (b.y - a.y) * (point.y - b.y);
            if (Math.abs(cross) > .05 || dot < 0) break;
            points.pop();
          }
          points.push(point);
        }
      }
      const segments = points.slice(1).map((point, index) => ({ dx: point.x - points[index].x, dy: point.y - points[index].y }));
      let backtracks = 0;
      for (const axis of ['dx', 'dy'] as const) {
        const directions = segments.map(segment => Math.sign(segment[axis])).filter(Boolean);
        backtracks += directions.slice(1).filter((direction, index) => direction !== directions[index]).length;
      }
      return { bends: Math.max(0, points.length - 2), backtracks, orthogonalLength: segments.reduce((sum, segment) => sum + Math.abs(segment.dx) + Math.abs(segment.dy), 0), directLength: Math.abs(points.at(-1)!.x - points[0].x) + Math.abs(points.at(-1)!.y - points[0].y) };
    };
    for (const line of map.querySelectorAll<SVGPathElement>('.cs-change-line')) {
      const length = line.getTotalLength(), matrix = line.getScreenCTM()!;
      const samples = [];
      for (let step = 0; step <= Math.ceil(length); step += 1) {
        const p = line.getPointAtLength(Math.min(step, length));
        const point = new DOMPoint(p.x, p.y).matrixTransform(matrix);
        samples.push({ x: point.x, y: point.y });
        for (const card of cards) if (inside(point, card.box)) collisions.push(`${line.parentElement?.getAttribute('data-source-key')} crosses ${card.key}`);
      }
      const pointAt = (distance: number) => { const point = line.getPointAtLength(distance); const screen = new DOMPoint(point.x, point.y).matrixTransform(matrix); return { x: screen.x, y: screen.y }; };
      paths.push({ source: JSON.parse(line.parentElement!.getAttribute('data-change-key')!)[1], sourceKey: line.parentElement!.getAttribute('data-source-key'), targetKey: line.parentElement!.getAttribute('data-target-key'), start: pointAt(0), startStub: pointAt(Math.min(10, length)), end: pointAt(length), endStub: pointAt(Math.max(0, length - 10)), dash: getComputedStyle(line).strokeDasharray, color: getComputedStyle(line).stroke, d: line.getAttribute('d'), marker: line.getAttribute('marker-end'), ...routeShape(line.getAttribute('d')!) });
    }
    for (const label of map.querySelectorAll<SVGGElement>('.cs-change-line-label')) {
      const box = label.getBoundingClientRect();
      for (const card of cards) if (box.left < card.box.right - 1 && box.right > card.box.left + 1 && box.top < card.box.bottom - 1 && box.bottom > card.box.top + 1) collisions.push(`label ${label.textContent} covers ${card.key}`);
    }
    return { collisions: [...new Set(collisions)], paths, bounds: { left: map.getBoundingClientRect().left, right: map.getBoundingClientRect().right, top: map.getBoundingClientRect().top, bottom: map.getBoundingClientRect().bottom }, cards: cards.map(card => ({ key: card.key, x: card.box.x, y: card.box.y, width: card.box.width, height: card.box.height })) };
  });
}

function expectVisiblePorts(metrics: Awaited<ReturnType<typeof geometry>>) {
  for (const route of metrics.paths) for (const [key, point, stub] of [[route.sourceKey, route.start, route.startStub], [route.targetKey, route.end, route.endStub]] as const) {
    if (!key) continue;
    const card = metrics.cards.find(item => item.key === key)!;
    expect(card, `visible endpoint ${key}`).toBeTruthy();
    const { x, y, width, height } = card;
    const ports = [[x + width / 2, y], [x + width, y + height / 2], [x + width / 2, y + height], [x, y + height / 2], [x, y], [x + width, y], [x, y + height], [x + width, y + height]];
    expect(Math.min(...ports.map(([px, py]) => Math.hypot(point.x - px, point.y - py))), `endpoint ${key} attaches to its actual card midpoint or corner`).toBeLessThan(2);
    expect(Math.hypot(point.x - stub.x, point.y - stub.y), `endpoint ${key} retains a visible straight arrow shaft`).toBeGreaterThan(9.5);
    expect(Math.min(Math.abs(point.x - stub.x), Math.abs(point.y - stub.y)), `endpoint ${key} has no turn inside its first 10px`).toBeLessThan(.5);
  }
}

test('same-week arrows point from the original time to the earlier new time with the exact pair', async ({ page }, testInfo) => {
  await mount(page);
  const line = change(page, 'same-old');
  await expect(line).toHaveCount(1);
  await expect(line).toHaveAttribute('data-target-key', 'same-new');
  await expect(line).toHaveAttribute('data-boundary', '');
  await expect(line).toContainText('时间更改');
  await expect(line).not.toContainText('教室更改');
  const measured = await geometry(page);
  const route = measured.paths.find(p => p.source === 'same-old');
  const distanceToCard = (p: {x: number; y: number}, key: string) => {
    const card = measured.cards.find(c => c.key === key)!;
    return Math.hypot(Math.max(card.x - p.x, 0, p.x - card.x - card.width), Math.max(card.y - p.y, 0, p.y - card.y - card.height));
  };
  expect(distanceToCard(route.start, 'same-old')).toBeLessThan(12);
  expect(distanceToCard(route.end, 'same-new')).toBeLessThan(12);
  expect(route.marker).toMatch(/url\(/);
  expect(route.dash).toBe('none');
  expect(route.d).toContain('Q');
  expectVisiblePorts(measured);
  await page.screenshot({ path: testInfo.outputPath('change-lines-desktop.png') });
});

test('solid connections have distinct colors even for the same course and retain that color across pages', async ({ page }) => {
  await mount(page);
  const before = await geometry(page);
  expect(before.paths.length).toBeGreaterThanOrEqual(5);
  expect(new Set(before.paths.map(route => route.color)).size).toBe(before.paths.length);
  expect(before.paths.every(route => route.dash === 'none')).toBe(true);
  const colors = new Map(before.paths.map(route => [route.source, route.color]));
  await page.evaluate(() => (window as any).deck.goToWeek(0));
  await settle(page);
  const previous = await geometry(page);
  expect(previous.paths).toHaveLength(2);
  for (const route of previous.paths) expect(route.color).toBe(colors.get(route.source));
  await page.evaluate(() => (window as any).deck.goToWeek(2));
  await settle(page);
  for (const route of (await geometry(page)).paths) expect(route.color).toBe(colors.get(route.source));
});

test('opening and closing previews keep the endpoint attached throughout the visible animation', async ({ page }, testInfo) => {
  await mount(page);
  const result = await page.evaluate(async () => {
    const cell = document.querySelector<HTMLElement>('.cs-expand [data-event-key="same-new"]')!;
    const beforeWidth = cell.getBoundingClientRect().width;
    const takeSamples = async () => {
      const samples: { width: number; error: number; pathPresent: boolean; animated: boolean; collisionCount: number }[] = [];
      for (let frame = 0; frame < 40; frame++) {
        await new Promise(requestAnimationFrame);
        const card = cell.getBoundingClientRect();
        const group = [...document.querySelectorAll('.cs-expand .cs-change-lines > g')].find(node => node.getAttribute('data-change-key') === '["same","same-old"]');
        const line = group?.querySelector<SVGPathElement>('.cs-change-line');
        let error = Infinity;
        if (line) {
          const point = line.getPointAtLength(line.getTotalLength());
          const screen = new DOMPoint(point.x, point.y).matrixTransform(line.getScreenCTM()!);
          const ports = [[card.left + card.width / 2, card.top], [card.right, card.top + card.height / 2], [card.left + card.width / 2, card.bottom], [card.left, card.top + card.height / 2], [card.left, card.top], [card.right, card.top], [card.left, card.bottom], [card.right, card.bottom]];
          error = Math.min(...ports.map(([x, y]) => Math.hypot(screen.x - x, screen.y - y)));
        }
        const animated = cell.getAnimations().some(animation => animation.playState === 'running');
        const boxes = [...document.querySelectorAll('.cs-expand .cs-lesson-slot > [data-event-key]')].map(node => node.getBoundingClientRect());
        let collisionCount = 0;
        for (const path of document.querySelectorAll<SVGPathElement>('.cs-expand .cs-change-line')) {
          const matrix = path.getScreenCTM()!, length = path.getTotalLength();
          for (let step = 0; step <= length; step += 3) {
            const p = path.getPointAtLength(step), point = new DOMPoint(p.x, p.y).matrixTransform(matrix);
            if (boxes.some(box => point.x > box.left + 1 && point.x < box.right - 1 && point.y > box.top + 1 && point.y < box.bottom - 1)) collisionCount++;
          }
        }
        samples.push({ width: card.width, error, pathPresent: !!line, animated, collisionCount });
        if (!animated && frame > 2) break;
      }
      return samples;
    };
    cell.querySelector<HTMLElement>('.cs-lesson__main')!.focus();
    const opening = await takeSamples();
    const expandedWidth = cell.getBoundingClientRect().width;
    document.querySelector<HTMLElement>('[data-csd-expand-close]')!.focus();
    const closing = await takeSamples();
    return { beforeWidth, expandedWidth, opening, closing };
  });
  await testInfo.attach('live-endpoint-animation', { body: JSON.stringify(result, null, 2), contentType: 'application/json' });
  expect(result.expandedWidth).toBeGreaterThan(result.beforeWidth + 30);
  for (const [name, samples] of [['opening', result.opening], ['closing', result.closing]] as const) {
    const middle = samples.filter(sample => sample.width > result.beforeWidth + 4 && sample.width < result.expandedWidth - 4);
    expect(middle.length, `${name} includes actual intermediate animation frames`).toBeGreaterThan(0);
    expect(samples.every(sample => sample.pathPresent), `${name} does not lose an unobstructed connection`).toBe(true);
    expect(Math.max(...samples.map(sample => sample.error)), `${name} follows the currently visible rectangle instead of the old slot`).toBeLessThan(2);
    expect(samples.every(sample => sample.collisionCount === 0), `${name} avoids visible cards at intermediate animation frames`).toBe(true);
  }
});

test('an unrelated enlarged lesson becomes an obstacle and any covered endpoint never receives a line through it', async ({ page }) => {
  await mount(page);
  const other = page.locator('.cs-expand [data-event-key="unaffected-one"]');
  await other.focus();
  await expect(other).toHaveClass(/is-preview/);
  await settle(page);
  const measured = await geometry(page);
  expect(measured.collisions).toEqual([]);
  expectVisiblePorts(measured);
  await page.locator('[data-csd-expand-close]').focus();
  await settle(page);
  await expect(change(page, 'same-old')).toHaveCount(1);
  expectVisiblePorts(await geometry(page));
});

test('the two bottom incoming cards keep visible arrow shafts and avoid routing under a cramped lower edge', async ({ page }) => {
  await mount(page);
  await page.addStyleTag({ content: '.cs-change-map > .cs-grid { bottom: 4px !important; }' });
  await page.setViewportSize({ width: 1420, height: 900 });
  await settle(page);
  const measured = await geometry(page);
  expect(measured.collisions).toEqual([]);
  expectVisiblePorts(measured);
  for (const key of ['bottom-a-old', 'bottom-b-old']) {
    const route = measured.paths.find(item => item.source === key);
    expect(route, `incoming bottom route ${key} remains available`).toBeTruthy();
    const card = measured.cards.find(item => item.key === route.targetKey)!;
    expect(route.end.y).toBeLessThan(card.y + card.height - 4);
    expect(route.endStub.y).toBeLessThan(measured.bounds.bottom - 8);
  }
});

test('mobile internal scrolling and reduced motion retain geometry, keyboard jumps and destruction cleanup', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  await mount(page);
  await page.locator('.cs-expand__body').evaluate(body => { body.scrollLeft = body.scrollWidth - body.clientWidth; body.scrollTop = body.scrollHeight - body.clientHeight; });
  await settle(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect((await geometry(page)).collisions).toEqual([]);
  expectVisiblePorts(await geometry(page));
  const label = change(page, 'later-old').locator('.cs-change-line-label');
  await label.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('[data-csd-expand-title]')).toContainText('第5周');
  await settle(page);
  expect((await geometry(page)).collisions).toEqual([]);
  expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
  await page.evaluate(() => (window as any).deck.destroy());
  await page.setViewportSize({ width: 700, height: 800 });
  await expect(page.locator('.cs-change-lines')).toHaveCount(0);
});

test('cross-week arrows reach the boundary and emerge from it on the correct destination page', async ({ page }) => {
  await mount(page);
  const outgoing = change(page, 'later-old');
  await expect(outgoing).toHaveAttribute('data-source-key', 'later-old');
  await expect(outgoing).toHaveAttribute('data-target-key', '');
  await expect(outgoing).toHaveAttribute('data-boundary', 'outgoing');
  const outgoingGeometry = await geometry(page);
  expect(Math.abs(outgoingGeometry.paths.find(p => p.source === 'later-old').end.x - outgoingGeometry.bounds.right)).toBeLessThan(20);
  const label = outgoing.locator('[data-csd-line-jump="later-new"]');
  await expect(label).toContainText('第5周');
  await label.click();
  await expect(page.locator('[data-csd-expand-title]')).toContainText('第5周');
  await expect(page.locator('.cs-expand [data-event-key="later-new"]')).toHaveClass(/is-counterpart-focus/);
  await settle(page);
  const incoming = change(page, 'later-old');
  await expect(incoming).toHaveAttribute('data-source-key', '');
  await expect(incoming).toHaveAttribute('data-target-key', 'later-new');
  await expect(incoming).toHaveAttribute('data-boundary', 'incoming');
  await expect(incoming).toContainText('第4周');
  const incomingGeometry = await geometry(page);
  expect(Math.abs(incomingGeometry.paths.find(p => p.source === 'later-old').start.x - incomingGeometry.bounds.left)).toBeLessThan(20);
  await incoming.locator('[data-csd-line-jump="later-old"]').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('[data-csd-expand-title]')).toContainText('第4周');
  expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
});

test('moving from a later week to an earlier week retains original-to-new direction and both change words', async ({ page }) => {
  await mount(page);
  const incoming = change(page, 'earlier-old');
  await expect(incoming).toHaveAttribute('data-boundary', 'incoming');
  await expect(incoming).toHaveAttribute('data-target-key', 'earlier-new');
  await expect(incoming).toContainText('时间更改');
  await expect(incoming).toContainText('教室更改');
  const incomingGeometry = await geometry(page);
  expect(Math.abs(incomingGeometry.paths.find(p => p.source === 'earlier-old').start.x - incomingGeometry.bounds.right)).toBeLessThan(20);
  await incoming.locator('[data-csd-line-jump="earlier-old"]').click();
  await expect(page.locator('[data-csd-expand-title]')).toContainText('第5周');
  await expect(change(page, 'earlier-old')).toHaveAttribute('data-boundary', 'outgoing');
  await expect(change(page, 'earlier-old')).toHaveAttribute('data-source-key', 'earlier-old');
  await expect(change(page, 'earlier-old')).toHaveAttribute('data-target-key', '');
  await settle(page);
  const outgoingGeometry = await geometry(page);
  expect(Math.abs(outgoingGeometry.paths.find(p => p.source === 'earlier-old').end.x - outgoingGeometry.bounds.left)).toBeLessThan(20);
  expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
});

test('room-only changes retain the comparison button without drawing a false time route', async ({ page }) => {
  await mount(page);
  await expect(page.locator('.cs-expand [data-event-key="room-only"]')).toHaveCount(1);
  const room = change(page, 'room-only');
  await expect(room).toHaveCount(0);
  await page.locator('.cs-expand [data-event-key="room-only"] .cs-adjustment-label').click();
  await expect(page.locator('.cs-expand [data-event-key="room-only"] .cs-adjustment-details')).toContainText('B312');
  await expect(change(page, 'cancel-only')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
});

test('routes and inline labels avoid every course block, including unrelated official lessons', async ({ page }) => {
  await mount(page);
  expect((await geometry(page)).collisions).toEqual([]);
  await page.locator('[data-csd-expand-next]').click();
  await settle(page);
  expect((await geometry(page)).collisions).toEqual([]);
});

test('counterparts missing from the visible data do not produce guessed arrows or edge destinations', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    const w = window as any;
    w.fixture.weeks.forEach((week: any) => { week.lessons = week.lessons.filter((l: any) => !['same-new', 'later-new', 'earlier-old'].includes(l.event_key)); });
    w.deck.setOverview(w.fixture, { keepWeek: true });
  });
  await settle(page);
  for (const key of ['same-old', 'later-old', 'earlier-old']) await expect(change(page, key)).toHaveCount(0);
  await expect(change(page, 'room-only')).toHaveCount(0);
});

test('viewport resize recalculates routes while narrow screens keep scrolling inside the timetable', async ({ page }, testInfo) => {
  await mount(page);
  const focusedLabel = change(page, 'same-old').locator('.cs-change-line-label');
  await focusedLabel.focus();
  const before = await change(page, 'same-old').locator('.cs-change-line').getAttribute('d');
  await page.setViewportSize({ width: 1000, height: 820 });
  await expect.poll(() => change(page, 'same-old').locator('.cs-change-line').getAttribute('d')).not.toBe(before);
  await expect(focusedLabel).toBeFocused();
  for (const width of [1000, 390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await settle(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect((await geometry(page)).collisions).toEqual([]);
    await expect(change(page, 'same-old').locator('.cs-change-line')).toHaveCount(1);
    if (width === 390) await page.screenshot({ path: testInfo.outputPath('change-lines-mobile.png') });
  }
});

test('hover previews retain readable interactive text while paths avoid the actual enlarged card', async ({ page }) => {
  await mount(page);
  const target = page.locator('.cs-expand [data-event-key="same-new"]');
  await target.locator('.cs-lesson__main').hover();
  await expect(target).toHaveClass(/is-preview/);
  await settle(page);
  const visibleText = await target.evaluate(card => {
    const text = card.querySelector('strong')!;
    const box = text.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    const foreground = getComputedStyle(text);
    const surface = getComputedStyle(card.querySelector('.cs-lesson__surface')!);
    return { targetOwnsHit: !!hit && card.contains(hit), opacity: foreground.opacity, color: foreground.color, background: surface.backgroundColor };
  });
  expect(visibleText.targetOwnsHit).toBe(true);
  expect(visibleText.opacity).toBe('1');
  expect(visibleText.color).not.toBe('rgb(255, 255, 255)');
  expect(visibleText.background).not.toContain('0.5');
  expect((await geometry(page)).collisions).toEqual([]);
});

test('approved replacement data and destruction clear obsolete lines and observers', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    const w = window as any;
    for (const week of w.fixture.weeks) {
      week.lessons = week.lessons.filter((lesson: any) => !lesson.adjustment || lesson.adjustment.endpoint === 'proposed');
      week.lessons.forEach((lesson: any) => { lesson.adjustment = null; lesson.counts_towards_total = true; });
    }
    w.deck.setOverview(w.fixture, { keepWeek: true });
  });
  await settle(page);
  await expect(page.locator('.cs-expand .cs-change-line')).toHaveCount(0);
  await page.evaluate(() => (window as any).deck.destroy());
  await page.setViewportSize({ width: 900, height: 700 });
  await expect(page.locator('.cs-change-lines')).toHaveCount(0);
  await expect(page.locator('.cs-expand')).toHaveCount(0);
});

test('an empty overview closes and clears the expanded map, then a restored overview opens fresh routes', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await mount(page);
  await page.evaluate(() => {
    const w = window as any;
    w.deck.setOverview({ ...w.fixture, weeks: [] });
  });
  await expect(page.locator('.cs-expand')).toHaveAttribute('hidden', '');
  await expect(page.locator('.cs-expand .cs-change-line')).toHaveCount(0);
  await expect(page.locator('.cs-expand [data-event-key]')).toHaveCount(0);
  await page.evaluate(() => {
    const w = window as any;
    w.deck.setOverview(w.fixture); w.deck.goToWeek(1); w.deck.openExpanded();
  });
  await expect(page.getByRole('dialog')).toBeVisible();
  await settle(page);
  const measured = await geometry(page);
  expect(measured.paths).toHaveLength(5);
  expect(measured.collisions).toEqual([]);
  expectVisiblePorts(measured);
  expect(errors).toEqual([]);
});

test('a clear course-to-boundary corridor stays straight even when its caption is longer than the corridor', async ({ page }) => {
  await mount(page);
  for (const [width, height] of [[1182, 850], [1182, 650], [1440, 900]]) {
    await page.setViewportSize({ width, height });
    await settle(page);
    const measured = await geometry(page);
    const route = measured.paths.find(item => item.source === 'later-old');
    expect(route).toBeTruthy();
    expect(route.bends, `${width}x${height}: a caption must not turn the clear right-hand corridor into a loop`).toBe(0);
    expect(route.backtracks, `${width}x${height}: the exit arrow must not curl back before reaching the edge`).toBe(0);
    expect(route.orthogonalLength).toBeCloseTo(route.directLength, 1);
    await expect(change(page, 'later-old').locator('.cs-change-line-label')).toContainText('第5周');
    expect(measured.collisions).toEqual([]);
    expectVisiblePorts(measured);
  }
});

test('an unobstructed same-week L or Z route remains simple without caption-driven backtracking', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    const w = window as any;
    w.fixture.weeks.forEach((week: any) => { week.lessons = week.lessons.filter((lesson: any) => ['same-old', 'same-new'].includes(lesson.event_key)); });
    w.deck.setOverview(w.fixture, { keepWeek: true });
  });
  for (const width of [1182, 1440, 390]) {
    await page.setViewportSize({ width, height: 850 });
    await settle(page);
    const measured = await geometry(page);
    const route = measured.paths.find(item => item.source === 'same-old');
    expect(route.bends).toBeLessThanOrEqual(2);
    expect(route.backtracks).toBe(0);
    expect(route.orthogonalLength).toBeCloseTo(route.directLength, 1);
    expect(measured.collisions).toEqual([]);
    expectVisiblePorts(measured);
  }
});

test('adjacent short exits keep both readable captions without overlap or added route bends', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    const w = window as any;
    const original = structuredClone(w.fixture.weeks[1].lessons.find((lesson: any) => lesson.event_key === 'later-old'));
    const proposed = structuredClone(w.fixture.weeks[2].lessons.find((lesson: any) => lesson.event_key === 'later-new'));
    const oldSlot = { date: '2026-09-27', sections: [4, 5], room: 'B416-1' };
    const newSlot = { date: '2026-09-30', sections: [4, 5], room: 'B416-1' };
    Object.assign(original, { event_key: 'adjacent-old', sections: [4, 5], session_id: 110, classroom_url: '/classroom/11?session_id=110' });
    Object.assign(proposed, { event_key: 'adjacent-new', weekday: 3, date: newSlot.date, sections: [4, 5], session_id: 110, classroom_url: '/classroom/11?session_id=110' });
    Object.assign(original.adjustment, { request_id: 'adjacent', original: oldSlot, proposed: newSlot, counterpart_event_key: 'adjacent-new' });
    Object.assign(proposed.adjustment, { request_id: 'adjacent', original: oldSlot, proposed: newSlot, counterpart_event_key: 'adjacent-old' });
    w.fixture.weeks[1].lessons.push(original); w.fixture.weeks[2].lessons.push(proposed);
    const side = w.fixture.weeks[1].lessons.find((lesson: any) => lesson.event_key === 'unaffected-two');
    const sideOld = { date: '2026-09-26', sections: [6, 7], room: 'B416-1' };
    const sideNew = { date: '2026-10-01', sections: [6, 7], room: 'B416-1' };
    Object.assign(side, { event_key: 'side-old', adjustment: { request_id: 'side', kind: 'move', phase: 'pending', endpoint: 'original', original: sideOld, proposed: sideNew, counterpart_event_key: 'side-new', counterpart_week_index: 5 } });
    const sideTarget = structuredClone(side);
    Object.assign(sideTarget, { event_key: 'side-new', weekday: 4, date: sideNew.date, counts_towards_total: false });
    Object.assign(sideTarget.adjustment, { endpoint: 'proposed', counterpart_event_key: 'side-old', counterpart_week_index: 4 });
    w.fixture.weeks[2].lessons.push(sideTarget);
    w.deck.setOverview(w.fixture, { keepWeek: true });
  });
  for (const [width, height] of [[1182, 850], [1366, 768], [390, 844]]) {
    await page.setViewportSize({ width, height });
    await settle(page);
    const measured = await geometry(page);
    for (const key of ['later-old', 'adjacent-old']) {
      await expect(change(page, key).locator('.cs-change-line-label')).toHaveCount(1);
      expect(measured.paths.find(route => route.source === key).bends).toBe(0);
    }
    expect(measured.collisions).toEqual([]);
    const labelOverlaps = await page.locator('.cs-expand .cs-change-line-label').evaluateAll(labels => {
      const rectangles = labels.map(label => label.getBoundingClientRect());
      let overlaps = 0;
      rectangles.forEach((a, index) => rectangles.slice(index + 1).forEach(b => {
        if (a.left < b.right - 1 && a.right > b.left + 1 && a.top < b.bottom - 1 && a.bottom > b.top + 1) overlaps++;
      }));
      return overlaps;
    });
    expect(labelOverlaps).toBe(0);
  }
});
