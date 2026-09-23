import { test, expect, type Page, type Locator } from '@playwright/test';
import { serveScheduleModule, settleScheduleMotion } from './schedule-fixture-modules';

function densityFixture() {
  const original = { date: '2026-09-25', sections: [2, 3], room: '知新楼B416-1' };
  const proposed = { date: '2026-09-24', sections: [6, 7], room: '大成楼C108' };
  const classes = '人工智能2601班（专升本） · 人工智能2602班（专升本） · 软件工程2601班（专升本） · 软件工程2602班（专升本）';
  const move = { request_id: 'density-move', kind: 'move', phase: 'pending', original, proposed };
  const timeMove = { request_id: 'density-time', kind: 'move', phase: 'pending', original: { date: '2026-09-22', sections: [2, 3], room: original.room }, proposed: { date: '2026-09-22', sections: [6, 7], room: original.room } };
  const lesson = (key: string, name: string, date: string, weekday: number, sections: number[], adjustment: any = null) => ({
    event_key: key, course_name: name, date, actual_date: date, weekday, sections,
    section_label: `第${sections[0]}-${sections.at(-1)}节`, hours: sections.length,
    classroom: adjustment ? adjustment[adjustment.endpoint].room : original.room,
    classroom_short: (adjustment ? adjustment[adjustment.endpoint].room : original.room).match(/[A-Z]\d[\w-]*/)?.[0], class_label: classes, student_count: 128,
    class_offering_id: 11, session_id: 701, session_no: 8, session_total: 32,
    classroom_url: '/classroom/11?session_id=701', adjustment, counts_towards_total: adjustment?.endpoint !== 'proposed',
  });
  return { selected_term: { label: '2026-2027第一学期' }, section_range: { min: 1, max: 11 }, weeks: [{ week_index: 4, label: '第4周', is_current: true, lessons: [
    lesson('move-old', '计算机网络原理', original.date, 5, [2, 3], { ...move, endpoint: 'original', counterpart_event_key: 'move-new', counterpart_week_index: 4 }),
    lesson('move-new', '计算机网络原理', proposed.date, 4, [6, 7], { ...move, endpoint: 'proposed', counterpart_event_key: 'move-old', counterpart_week_index: 4 }),
    lesson('time-old', 'Python程序设计', timeMove.original.date, 2, [2, 3], { ...timeMove, endpoint: 'original', counterpart_event_key: 'time-new', counterpart_week_index: 4 }),
    lesson('time-new', 'Python程序设计', timeMove.proposed.date, 2, [6, 7], { ...timeMove, endpoint: 'proposed', counterpart_event_key: 'time-old', counterpart_week_index: 4 }),
    lesson('room', 'Python程序设计', '2026-09-21', 1, [4, 5], { request_id: 'density-room', kind: 'room', phase: 'pending', endpoint: 'original', original: { date: '2026-09-21', sections: [4, 5], room: 'B310' }, proposed: { date: '2026-09-21', sections: [4, 5], room: 'B312' } }),
    lesson('cancel', '计算机网络原理', '2026-09-23', 3, [8, 9], { request_id: 'density-cancel', kind: 'cancel', phase: 'pending', endpoint: 'original', original: { date: '2026-09-23', sections: [8, 9], room: 'B310' }, proposed: null }),
    lesson('ordinary', 'Python程序设计', '2026-09-24', 4, [4, 5]),
    lesson('weekend', '计算机网络原理', '2026-09-26', 6, [10, 11]),
  ] }] };
}

async function mount(page: Page, options: { long?: boolean } = {}) {
  const fixture = densityFixture();
  if (options.long) fixture.weeks[0].lessons.find(lesson => lesson.event_key === 'move-new')!.class_label = '人工智能2601班、软件工程2602班、计算机科学2603班；'.repeat(60);
  await page.route('http://schedule-density.test/**', async route => {
    if (await serveScheduleModule(route)) return;
    await route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><link rel="stylesheet" href="/schedule-tokens.css"><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{box-sizing:border-box}body{margin:16px;font-family:Arial,sans-serif}</style><div id="deck"></div><script type="module">
      import {createScheduleDeck} from '/static/js/course_schedule_deck.js';
      window.navigations=[];window.deck=createScheduleDeck(document.getElementById('deck'),{onNavigate:url=>window.navigations.push(url)});
      window.deck.setOverview(${JSON.stringify(fixture)});window.deck.openExpanded();
    </script></html>` });
  });
  await page.goto('http://schedule-density.test/');
  await expect(page.getByRole('dialog')).toBeVisible();
  await settled(page);
}

async function settled(page: Page) {
  await settleScheduleMotion(page);
}

function card(page: Page, key: string) { return page.locator(`.cs-expand [data-event-key="${key}"]`); }

async function readCard(card: Locator) {
  return card.evaluate(node => {
    const title = node.querySelector<HTMLElement>('.cs-lesson__title')!;
    const button = node.querySelector<HTMLButtonElement>('.cs-adjustment-label');
    const details = node.querySelector<HTMLElement>('.cs-lesson__details')!;
    const room = node.querySelector<HTMLElement>('.cs-lesson__room')!;
    const surface = node.querySelector<HTMLElement>('.cs-lesson__surface')!;
    const titleBox = title.getBoundingClientRect(), box = node.getBoundingClientRect();
    const container = title.parentElement!.getBoundingClientRect();
    const buttonBox = button?.getBoundingClientRect(), buttonContainer = button?.parentElement!.getBoundingClientRect();
    const roomBox = room.getBoundingClientRect(), surfaceBox = surface.getBoundingClientRect();
    const detailsStyle = getComputedStyle(details);
    let detailOpacity = 1;
    for (let ancestor: HTMLElement | null = details; ancestor && ancestor !== node; ancestor = ancestor.parentElement) detailOpacity *= Number(getComputedStyle(ancestor).opacity);
    const metadata = [...details.querySelectorAll<HTMLElement>('.cs-lesson__meta')];
    const visibleMetadata = metadata.filter(span => { const rect = span.getBoundingClientRect(); return detailOpacity > .01 && detailsStyle.visibility !== 'hidden' && rect.width > 0 && rect.height > 0 && getComputedStyle(span).visibility !== 'hidden'; });
    const buttonHit = buttonBox && document.elementFromPoint(buttonBox.left + buttonBox.width / 2, buttonBox.top + buttonBox.height / 2);
    const buttonOnscreen = buttonBox && buttonBox.left >= 0 && buttonBox.right <= innerWidth && buttonBox.top >= 0 && buttonBox.bottom <= innerHeight;
    return { width: box.width, height: box.height, title: title.textContent, titleWidth: titleBox.width, titleHeight: title.offsetHeight, lineHeight: parseFloat(getComputedStyle(title).lineHeight),
      titleFits: titleBox.top >= container.top - 1 && titleBox.bottom <= container.bottom + 1 && titleBox.top >= box.top - 1 && titleBox.bottom <= box.bottom + 1,
      titleAtTop: titleBox.top >= surfaceBox.top - 1 && titleBox.top <= surfaceBox.top + 18,
      detailOpacity, metadataCount: metadata.length, visibleMetadata: visibleMetadata.map(span => span.textContent),
      roomText: room.innerText.trim(), roomShortText: room.querySelector('.cs-lesson__room-short')?.textContent, roomVisible: roomBox.width > 0 && roomBox.height > 0 && getComputedStyle(room).visibility !== 'hidden', roomWidth: roomBox.width, roomFont: parseFloat(getComputedStyle(room).fontSize), titleFont: parseFloat(getComputedStyle(title).fontSize),
      roomAtLeftBottom: roomBox.left >= surfaceBox.left - 1 && roomBox.left <= surfaceBox.left + 18 && roomBox.bottom <= surfaceBox.bottom + 1 && roomBox.bottom >= surfaceBox.bottom - 18,
      roomFits: roomBox.left >= surfaceBox.left - 1 && roomBox.right <= surfaceBox.right + 1 && roomBox.top >= surfaceBox.top - 1 && roomBox.bottom <= surfaceBox.bottom + 1,
      roomComplete: room.scrollWidth <= room.clientWidth + 1 && room.scrollHeight <= room.clientHeight + 1,
      buttonText: button?.innerText.trim() || '', buttonLabel: button?.getAttribute('aria-label') || '',
      buttonAtRightBottom: !button || (buttonBox!.right <= surfaceBox.right + 1 && buttonBox!.right >= surfaceBox.right - 18 && buttonBox!.bottom <= surfaceBox.bottom + 1 && buttonBox!.bottom >= surfaceBox.bottom - 18),
      buttonWinsHit: !buttonOnscreen || !buttonHit || button === buttonHit || button!.contains(buttonHit),
      buttonFits: !button || (buttonBox!.top >= box.top - 1 && buttonBox!.bottom <= box.bottom + 1 && buttonBox!.top >= buttonContainer!.top - 1 && buttonBox!.bottom <= buttonContainer!.bottom + 1 && buttonBox!.left >= buttonContainer!.left - 1 && buttonBox!.right <= buttonContainer!.right + 1),
      titleButtonOverlap: !!button && titleBox.left < button.getBoundingClientRect().right - 1 && titleBox.right > button.getBoundingClientRect().left + 1 && titleBox.top < button.getBoundingClientRect().bottom - 1 && titleBox.bottom > button.getBoundingClientRect().top + 1 };
  });
}

function expectCompact(metrics: Awaited<ReturnType<typeof readCard>>, hasButton: boolean) {
  expect(metrics.titleWidth, 'a narrow overlap lane must still allocate width to the course title').toBeGreaterThan(0);
  expect(metrics.titleHeight, 'course title must retain complete readable lines').toBeGreaterThanOrEqual(metrics.lineHeight - 1);
  expect(metrics.titleHeight, 'collapsed titles have at most two complete lines').toBeLessThanOrEqual(2 * metrics.lineHeight + 1);
  expect(metrics.titleFits).toBe(true);
  expect(metrics.metadataCount).toBeGreaterThan(0);
  expect(metrics.visibleMetadata).toEqual([]);
  expect(metrics.roomShortText).not.toBe('');
  if (!hasButton || (metrics.width > 100 && metrics.height > 52)) {
    expect(metrics.roomVisible).toBe(true);
    expect(metrics.roomWidth).toBeGreaterThan(0);
    expect(metrics.roomAtLeftBottom).toBe(true);
  }
  expect(metrics.roomFont).toBeLessThan(metrics.titleFont);
  if (hasButton) {
    expect(Array.from(metrics.buttonText).length).toBeGreaterThan(0);
    expect(Array.from(metrics.buttonText).length).toBeLessThanOrEqual(5);
    expect(metrics.buttonText).not.toContain('\n');
    expect(metrics.buttonLabel.length).toBeGreaterThan(4);
    expect(metrics.buttonFits).toBe(true);
    expect(metrics.buttonAtRightBottom).toBe(true);
    expect(metrics.titleButtonOverlap, `title/button overlap in ${metrics.width} × ${metrics.height} card`).toBe(false);
  }
}

test('compact cards keep two-line titles, lower-left room abbreviations and semantic glass buttons', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1182, height: 650 });
  await mount(page);
  for (const key of ['move-old', 'move-new', 'time-old', 'time-new', 'room', 'cancel', 'ordinary']) {
    const metrics = await readCard(card(page, key));
    expectCompact(metrics, key !== 'ordinary');
    const expected = key.startsWith('move-') ? '教室+时间' : key.startsWith('time-') ? '改时间' : key === 'room' ? '改教室' : key === 'cancel' ? '停课' : '';
    if (expected) expect(metrics.buttonText).toBe(expected);
    expect(metrics.roomShortText).toBe(`${key === 'move-new' ? 'C108' : ['room', 'cancel'].includes(key) ? 'B310' : 'B416-1'}教室`);
  }
  await page.screenshot({ path: testInfo.outputPath('density-small-cards.png') });
  await page.locator('[data-csd-expand-close]').click();
  await expect(page.locator('.cs-expand')).toHaveAttribute('hidden', '');
  for (const key of ['move-old', 'move-new', 'time-old', 'time-new', 'room', 'cancel', 'ordinary']) {
    const mini = page.locator(`.cs-card.is-active .cs-lesson--mini[data-event-key="${key}"]`);
    const metrics = await readCard(mini);
    expectCompact(metrics, key !== 'ordinary');
    expect(metrics.titleWidth, 'mini titles must retain room for more than one character').toBeGreaterThanOrEqual(40);
  }
});

test('preview restores all lesson information and complete button wording on the same classroom link', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1182, height: 650 });
  await mount(page);
  const target = card(page, 'move-new');
  const originalLink = await target.locator('.cs-lesson__main').elementHandle();
  const stableSlot = await target.locator('..').boundingBox();
  await target.locator('.cs-lesson__main').focus();
  await expect(target).toHaveClass(/is-preview/);
  await settled(page);
  const metrics = await readCard(target);
  expect(metrics.roomText).toContain('大成楼C108');
  expect(metrics.visibleMetadata.join(' ')).toContain('人工智能2601班');
  expect(metrics.visibleMetadata.join(' ')).toContain('2026-09-24');
  expect(metrics.visibleMetadata.join(' ')).toContain('第8次课');
  expect(metrics.buttonText).toContain('正在申请变更');
  expect(metrics.buttonText).toContain('原位置');
  expect(metrics.titleFits).toBe(true);
  expect(metrics.titleAtTop).toBe(true);
  expect(metrics.roomFits).toBe(true);
  expect(metrics.roomComplete).toBe(true);
  expect(metrics.buttonFits).toBe(true);
  expect(await originalLink!.evaluate(link => link.isConnected)).toBe(true);
  expect(await target.locator('..').boundingBox()).toEqual(stableSlot);
  await page.screenshot({ path: testInfo.outputPath('density-expanded-details.png') });
  await page.locator('[data-csd-expand-close]').focus();
  await target.evaluate(async node => {
    for (const animation of node.getAnimations({ subtree: true })) {
      animation.pause(); animation.currentTime = Number(animation.effect!.getComputedTiming().duration) * .35;
    }
    await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
  });
  await expect(target).toHaveClass(/is-preview-closing/);
  const closing = await readCard(target);
  expect(closing.detailOpacity).toBeLessThan(metrics.detailOpacity);
  await target.evaluate(node => node.getAnimations({ subtree: true }).forEach(animation => animation.finish()));
  await settled(page);
  expectCompact(await readCard(target), true);
  await expect(target.locator('.cs-lesson__main')).toHaveAttribute('href', '/classroom/11?session_id=701');
});

test('slot-size changes retain title and footer without leaking details or replacing the classroom link', async ({ page }) => {
  await mount(page);
  for (const key of ['move-old', 'ordinary']) {
    const target = card(page, key);
    await target.locator('..').evaluate(slot => { (slot as HTMLElement).style.width = '300px'; (slot as HTMLElement).style.height = '240px'; });
    await settled(page);
    const spacious = await readCard(target);
    expectCompact(spacious, key !== 'ordinary');
    await target.locator('..').evaluate(slot => { (slot as HTMLElement).style.width = '130px'; (slot as HTMLElement).style.height = '90px'; });
    await settled(page);
    expectCompact(await readCard(target), key !== 'ordinary');
    await target.locator('..').evaluate(slot => { (slot as HTMLElement).style.width = '300px'; (slot as HTMLElement).style.height = '240px'; });
    await settled(page);
    expectCompact(await readCard(target), key !== 'ordinary');
  }
});

test('height boundaries and narrow overlap lanes reserve complete title lines and concise controls', async ({ page }) => {
  await mount(page);
  for (const key of ['move-old', 'move-new', 'room', 'cancel']) {
    const target = card(page, key);
    for (const height of [58, 68, 69, 79, 80, 92, 80, 79, 69, 68]) {
      await target.locator('..').evaluate((slot, height) => { (slot as HTMLElement).style.width = '116px'; (slot as HTMLElement).style.height = `${height}px`; }, height);
      await settled(page);
      const metrics = await readCard(target);
      expectCompact(metrics, true);
    }
    for (const height of [53, 58, 68, 92]) {
      await target.locator('..').evaluate((slot, height) => { (slot as HTMLElement).style.width = '64px'; (slot as HTMLElement).style.height = `${height}px`; }, height);
      await settled(page);
      expectCompact(await readCard(target), true);
    }
    await target.locator('..').evaluate(slot => { (slot as HTMLElement).style.width = '180px'; (slot as HTMLElement).style.height = '34px'; });
    await settled(page);
    expectCompact(await readCard(target), true);
  }
  const ordinary = card(page, 'ordinary');
  await ordinary.locator('..').evaluate(slot => { (slot as HTMLElement).style.width = '180px'; (slot as HTMLElement).style.height = '34px'; });
  await settled(page);
  const singlePeriod = await readCard(ordinary);
  expect(singlePeriod.titleFits).toBe(true);
  expect(singlePeriod.roomVisible).toBe(true);
  expect(await ordinary.evaluate(node => {
    const title = node.querySelector('.cs-lesson__title')!.getBoundingClientRect(), room = node.querySelector('.cs-lesson__room')!.getBoundingClientRect();
    return title.right <= room.left + 1 || title.bottom <= room.top + 1;
  }), 'an ordinary single-period card retains a separate readable room abbreviation').toBe(true);
});

test('the rounded glass action owns the lower-right hit area when its room text runs underneath', async ({ page }) => {
  await mount(page);
  const target = card(page, 'move-old');
  await target.locator('..').evaluate(slot => { (slot as HTMLElement).style.width = '110px'; (slot as HTMLElement).style.height = '92px'; });
  await target.scrollIntoViewIfNeeded();
  await settled(page);
  const metrics = await readCard(target);
  expectCompact(metrics, true);
  expect(metrics.buttonWinsHit).toBe(true);
  const finish = await target.evaluate(node => {
    const button = getComputedStyle(node.querySelector('.cs-adjustment-label')!);
    const surface = getComputedStyle(node.querySelector('.cs-lesson__surface')!);
    const context = document.createElement('canvas').getContext('2d')!;
    context.fillStyle = button.backgroundColor; context.fillRect(0, 0, 1, 1);
    const alpha = context.getImageData(0, 0, 1, 1).data[3] / 255;
    return { radius: parseFloat(button.borderTopLeftRadius), blur: button.backdropFilter, highlight: button.backgroundImage,
      alpha, rim: button.boxShadow, ink: button.color, surfaceInk: surface.color,
      opacity: button.opacity, surfaceRadius: parseFloat(surface.borderTopLeftRadius) };
  });
  expect(finish.radius).toBeGreaterThanOrEqual(8);
  expect(finish.surfaceRadius).toBeGreaterThanOrEqual(8);
  expect(finish.alpha).toBeGreaterThan(0);
  expect(finish.alpha).toBeLessThan(1);
  expect(finish.rim).not.toBe('none');
  expect(finish.ink).toBe(finish.surfaceInk);
  expect(finish.opacity).toBe('1');
  await target.locator('.cs-adjustment-label').click();
  await expect(card(page, 'move-new')).toHaveClass(/is-counterpart-focus/);
  expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
});

test('preview grows proportionally with hidden text, reveals after arrival, then fades before shrinking', async ({ page }, testInfo) => {
  await mount(page);
  const result = await card(page, 'move-new').evaluate(async cell => {
    const main = cell.querySelector<HTMLElement>('.cs-lesson__main')!;
    const close = document.querySelector<HTMLElement>('[data-csd-expand-close]')!;
    const parts = [...cell.querySelector('.cs-lesson__surface')!.children];
    const snapshot = () => {
      const box = cell.getBoundingClientRect();
      return { rect: [box.x, box.y, box.width, box.height], opacity: parts.map(part => Number(getComputedStyle(part).opacity)), state: (cell as HTMLElement).dataset.previewState || '' };
    };
    const motion = () => cell.getAnimations({ subtree: true });
    const timing = () => motion().map(animation => ({ target: (animation.effect as KeyframeEffect).target === cell ? 'geometry' : 'text', ...animation.effect!.getTiming() }));
    const seek = async (time: number) => {
      for (const animation of motion()) { animation.pause(); animation.currentTime = time; }
      await new Promise(requestAnimationFrame);
      return snapshot();
    };
    const finish = async () => {
      motion().forEach(animation => animation.finish());
      await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
    };
    const compact = snapshot();
    main.focus();
    const openingTiming = timing();
    const opening = [await seek(0), await seek(95), await seek(190), await seek(230)];
    await finish(); const expanded = snapshot();
    close.focus(); const closingTiming = timing();
    const closing = [await seek(0), await seek(35), await seek(70), await seek(145)];
    const beforeReopen = snapshot(); main.focus(); const afterReopen = snapshot();
    await seek(80); const beforeReverse = snapshot(); close.focus(); const afterReverse = snapshot();
    const invisibleReverseTiming = timing();
    await finish();
    return { compact, expanded, openingTiming, opening, closingTiming, closing, beforeReopen, afterReopen,
      beforeReverse, afterReverse, invisibleReverseTiming, closed: snapshot(), remainingAnimations: motion().length };
  });
  await testInfo.attach('preview-phases', { body: JSON.stringify(result, null, 2), contentType: 'application/json' });
  expect(result.openingTiming.find(item => item.target === 'geometry')).toMatchObject({ duration: 190, delay: 0 });
  for (const text of result.openingTiming.filter(item => item.target === 'text')) expect(text).toMatchObject({ duration: 80, delay: 190 });
  expect(result.closingTiming.find(item => item.target === 'geometry')).toMatchObject({ duration: 150, delay: 70 });
  for (const text of result.closingTiming.filter(item => item.target === 'text')) expect(text).toMatchObject({ duration: 70, delay: 0 });
  for (const sample of result.opening.slice(0, 3)) expect(Math.max(...sample.opacity)).toBeLessThanOrEqual(.01);
  expect(Math.min(...result.opening[3].opacity)).toBeGreaterThan(.4);
  expect(Math.max(...result.opening[3].opacity)).toBeLessThan(.6);
  expect(result.opening[3].rect[2]).toBeCloseTo(result.expanded.rect[2], 0);
  expect(result.expanded.opacity.every(value => value === 1)).toBe(true);
  for (const sample of result.closing.slice(0, 3)) expect(sample.rect[2]).toBeCloseTo(result.expanded.rect[2], 0);
  expect(Math.min(...result.closing[1].opacity)).toBeGreaterThan(.4);
  expect(Math.max(...result.closing[1].opacity)).toBeLessThan(.6);
  for (const sample of result.closing.slice(2)) expect(Math.max(...sample.opacity)).toBeLessThanOrEqual(.01);
  expect(result.closing[3].rect[2]).toBeLessThan(result.expanded.rect[2] - 1);
  const ratio = result.compact.rect[2] / result.compact.rect[3];
  for (const sample of [...result.opening, result.expanded, ...result.closing, result.closed]) {
    expect(Math.abs(sample.rect[2] / sample.rect[3] - ratio)).toBeLessThan(.02);
  }
  for (const [before, after] of [[result.beforeReopen, result.afterReopen], [result.beforeReverse, result.afterReverse]]) {
    before.rect.forEach((value, axis) => expect(Math.abs(after.rect[axis] - value), 'reversal preserves the painted glass rectangle').toBeLessThan(1));
  }
  expect(result.invisibleReverseTiming.find(item => item.target === 'geometry')).toMatchObject({ duration: 150, delay: 0 });
  expect(result.closed.rect[2]).toBeCloseTo(result.compact.rect[2], 0);
  expect(result.closed.state).toBe('');
  expect(result.remainingAnimations).toBe(0);
});

test('a scrolled long preview preserves its reading position through hidden-text resize and reversal', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1182, height: 650 });
  await mount(page, { long: true });
  const target = card(page, 'move-new');
  await target.locator('.cs-lesson__main').focus();
  await settled(page);
  await target.evaluate(cell => {
    cell.scrollTop = cell.scrollHeight - cell.clientHeight;
    const parts = [cell, cell.querySelector('.cs-lesson__title')!, cell.querySelector('.cs-lesson__room')!, cell.querySelector('.cs-adjustment-label')!];
    const boxes = () => parts.map(node => { const box = node.getBoundingClientRect(); return [box.x, box.y, box.width, box.height]; });
    const probe = { initialScroll: cell.scrollTop, resizeFrames: [] as any[], capture: null as any, after: null as any };
    probe.capture = () => probe.resizeFrames.push({ before: boxes(), scrollBefore: cell.scrollTop });
    probe.after = () => Object.assign(probe.resizeFrames.at(-1), { after: boxes(), scrollAfter: cell.scrollTop });
    window.addEventListener('resize', probe.capture, true);
    window.addEventListener('resize', probe.after);
    (window as any).scrollPreviewProbe = probe;
  });
  await page.setViewportSize({ width: 1050, height: 650 });
  await settled(page);
  const result = await target.evaluate(async cell => {
    const probe = (window as any).scrollPreviewProbe;
    window.removeEventListener('resize', probe.capture, true); window.removeEventListener('resize', probe.after);
    const main = cell.querySelector<HTMLElement>('.cs-lesson__main')!, close = document.querySelector<HTMLElement>('[data-csd-expand-close]')!;
    const parts = [cell, cell.querySelector('.cs-lesson__title')!, cell.querySelector('.cs-lesson__room')!, cell.querySelector('.cs-adjustment-label')!];
    const boxes = () => parts.map(node => { const box = node.getBoundingClientRect(); return [box.x, box.y, box.width, box.height]; });
    const finish = async () => { cell.getAnimations({ subtree: true }).forEach(animation => animation.finish()); await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); };
    const scrollAfterResize = cell.scrollTop, maxScroll = cell.scrollHeight - cell.clientHeight;
    const beforeClose = boxes(); close.focus(); const afterClose = boxes();
    for (const animation of cell.getAnimations({ subtree: true })) { animation.pause(); animation.currentTime = Number(animation.effect!.getComputedTiming().duration) * .4; }
    await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
    const beforeReopen = boxes(); main.focus({ preventScroll: true }); const afterReopen = boxes();
    await finish();
    const scrollAfterReopen = cell.scrollTop;
    close.focus(); await finish();
    return { initialScroll: probe.initialScroll, resizeFrames: probe.resizeFrames, scrollAfterResize, maxScroll, beforeClose, afterClose,
      beforeReopen, afterReopen, scrollAfterReopen, finalScroll: cell.scrollTop, remainingAnimations: cell.getAnimations({ subtree: true }).length,
      remainingMotion: cell.matches('.is-preview,.is-preview-closing,.is-preview-moving') };
  });
  await testInfo.attach('scrolled-preview-motion', { body: JSON.stringify(result, null, 2), contentType: 'application/json' });
  expect(result.initialScroll).toBeGreaterThan(100);
  expect(result.resizeFrames.length).toBeGreaterThan(0);
  const transitions = [...result.resizeFrames, { before: result.beforeClose, after: result.afterClose }, { before: result.beforeReopen, after: result.afterReopen }];
  for (const transition of transitions) transition.before[0].forEach((value: number, axis: number) => expect(Math.abs(transition.after[0][axis] - value), 'the glass rectangle retains its painted position at the first frame').toBeLessThan(1));
  expect(result.scrollAfterResize).toBeCloseTo(Math.min(result.initialScroll, result.maxScroll), 0);
  expect(result.scrollAfterReopen).toBeCloseTo(result.scrollAfterResize, 0);
  expect(result.finalScroll).toBe(0);
  expect(result.remainingAnimations).toBe(0);
  expect(result.remainingMotion).toBe(false);
});

test('short controls preserve keyboard comparison, first-tap preview and classroom navigation on mobile', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  await mount(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const room = card(page, 'room');
  expectCompact(await readCard(room), true);
  await room.locator('.cs-adjustment-label').focus(); await page.keyboard.press('Enter');
  await expect(room.locator('.cs-adjustment-details')).toBeVisible();
  await expect(room.locator('.cs-adjustment-details')).toContainText('B312');
  await settled(page);
  expect(await room.evaluate(node => node.querySelector('.cs-adjustment-details')!.getBoundingClientRect().bottom <= node.querySelector('.cs-lesson__footer')!.getBoundingClientRect().top + 1), 'comparison content must precede the footer instead of pushing room and action away from the bottom').toBe(true);
  await page.keyboard.press('Escape');
  await settled(page);
  expectCompact(await readCard(room), true);
  await expect(room.locator('.cs-adjustment-details')).not.toBeVisible();
  const target = card(page, 'move-new');
  await target.locator('.cs-lesson__main').tap();
  await expect(target).toHaveClass(/is-preview/);
  await settled(page);
  expect((await readCard(target)).visibleMetadata.length).toBeGreaterThanOrEqual(3);
  expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
  await target.locator('.cs-lesson__main').tap();
  expect(await page.evaluate(() => (window as any).navigations)).toEqual(['/classroom/11?session_id=701']);
  await context.close();
});
