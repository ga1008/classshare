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
  move('earlier', '从下一周提前并换教室', 5, 4, oldPosition('2026-09-29', [6, 7]), oldPosition('2026-09-22', [10, 11], 'B210'), 103);
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
    const cards = [...map.querySelectorAll<HTMLElement>('.cs-lesson-slot')].map(card => ({ key: card.querySelector<HTMLElement>('[data-event-key]')!.dataset.eventKey, box: card.getBoundingClientRect() }));
    const inside = (point: { x: number; y: number }, box: DOMRect) => point.x > box.left + 1 && point.x < box.right - 1 && point.y > box.top + 1 && point.y < box.bottom - 1;
    const collisions: string[] = [], paths: any[] = [];
    for (const line of map.querySelectorAll<SVGPathElement>('.cs-change-line')) {
      const length = line.getTotalLength(), matrix = line.getScreenCTM()!;
      const samples = [];
      for (let step = 0; step <= Math.ceil(length); step += 1) {
        const p = line.getPointAtLength(Math.min(step, length));
        const point = new DOMPoint(p.x, p.y).matrixTransform(matrix);
        samples.push({ x: point.x, y: point.y });
        for (const card of cards) if (inside(point, card.box)) collisions.push(`${line.parentElement?.getAttribute('data-source-key')} crosses ${card.key}`);
      }
      paths.push({ source: JSON.parse(line.parentElement!.getAttribute('data-change-key')!)[1], start: samples[0], end: samples.at(-1), dash: getComputedStyle(line).strokeDasharray, marker: line.getAttribute('marker-end') });
    }
    for (const label of map.querySelectorAll<SVGGElement>('.cs-change-line-label')) {
      const box = label.getBoundingClientRect();
      for (const card of cards) if (box.left < card.box.right - 1 && box.right > card.box.left + 1 && box.top < card.box.bottom - 1 && box.bottom > card.box.top + 1) collisions.push(`label ${label.textContent} covers ${card.key}`);
    }
    return { collisions: [...new Set(collisions)], paths, bounds: { left: map.getBoundingClientRect().left, right: map.getBoundingClientRect().right }, cards: cards.map(card => ({ key: card.key, x: card.box.x, y: card.box.y, width: card.box.width, height: card.box.height })) };
  });
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
  expect(route.dash).not.toBe('none');
  await page.screenshot({ path: testInfo.outputPath('change-lines-desktop.png') });
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

test('room-only changes use a single course card and a room comparison; cancellations have no false destination', async ({ page }) => {
  await mount(page);
  await expect(page.locator('.cs-expand [data-event-key="room-only"]')).toHaveCount(1);
  const room = change(page, 'room-only');
  await expect(room).toContainText('教室更改');
  await expect(room).not.toContainText('时间更改');
  await room.locator('[data-csd-line-detail="room-only"]').click();
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
  await expect(change(page, 'room-only')).toHaveCount(1);
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

test('hover previews stay above the dashed lines and retain readable interactive text', async ({ page }) => {
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
