import { test, expect, type Page, type Route } from '@playwright/test';
import { mountMessageCenter, summary } from '../fixtures/message-center-controller';

const assistants = [
  { identity: 'assistant:1', class_offering_id: 10, display_name: '甲助教', role: 'assistant', can_send: true },
  { identity: 'assistant:2', class_offering_id: 20, display_name: '乙助教', role: 'assistant', can_send: true },
];
const body = '#message-center-conversation-body';
const input = '#message-center-compose-input';
const key = (index: number) => `student:99|${assistants[index].identity}|${assistants[index].class_offering_id}`;
function job(index: number, status = 'pending') {
  return { id: 101 + index, conversation_key: key(index), status, created_at: '2026-09-21T08:00:00' };
}
function conversation(index: number, activeJob: ReturnType<typeof job> | null = job(index)) {
  return { summary, conversation: { contact: assistants[index], conversation_key: key(index),
    class_offering_id: assistants[index].class_offering_id, ai_reply_job: activeJob,
    messages: [{ id: `message-${index}`, content: `${index ? 'B' : 'A'} current conversation`,
      sender_display_name: assistants[index].display_name, created_at: '2026-09-21T08:00:00', attachments: [] }] } };
}

// The real controller and API adapter run unchanged. The clock advances browser
// time; this observation-only wrapper counts just the existing 2200ms poll timers.
async function mount(page: Page) {
  await page.clock.install({ time: new Date('2026-09-21T00:00:00Z') });
  await page.clock.pauseAt(new Date('2026-09-21T00:00:01Z'));
  await page.addInitScript(() => {
    const probe = (window as any).__aiPollProbe = { timers: new Map<number, number>(), maximum: 0, successes: [] as string[] };
    const set = window.setTimeout.bind(window), clear = window.clearTimeout.bind(window);
    window.setTimeout = ((callback: TimerHandler, delay?: number, ...args: unknown[]) => {
      if (Number(delay) !== 2200 || typeof callback !== 'function') return set(callback, delay, ...args);
      const id = set(function (this: unknown) { probe.timers.delete(id); callback.apply(this, args); }, delay);
      probe.timers.set(id, Date.now() + Number(delay));
      probe.maximum = Math.max(probe.maximum, probe.timers.size);
      return id;
    }) as typeof window.setTimeout;
    window.clearTimeout = id => { probe.timers.delete(Number(id)); Reflect.apply(clear, window, [id]); };
    document.addEventListener('DOMContentLoaded', () => {
      new MutationObserver(() => {
        document.querySelectorAll('.lq-toast__message').forEach(node => {
          if (node.textContent?.includes('AI 助教已回复')) probe.successes.push(node.textContent);
        });
      }).observe(document.body, { childList: true, subtree: true, characterData: true });
    }, { once: true });
  });
  const h = await mountMessageCenter(page, 'private', false, { summary, private_contacts: assistants, private_blocks: [] });
  // The shared reply helper waits real animation frames. Here the paused clock
  // owns those frames, so settle the actual response then explicitly run two.
  const reply = async (route: Route, data: unknown, status = 200) => {
    const received = page.waitForResponse(response => response.url() === route.request().url());
    await route.fulfill({ status, json: data });
    await (await received).finished();
    await page.clock.runFor(34);
  };
  const abort = async (route: Route) => {
    const failed = page.waitForEvent('requestfailed', request => request.url() === route.request().url());
    await route.abort('failed'); await failed;
    await page.clock.runFor(34);
  };
  const ready = async (index: number, activeJob: ReturnType<typeof job> | null = job(index)) => {
    await reply(await h.take('/private/conversation', { contact: assistants[index].identity,
      scope: String(assistants[index].class_offering_id) }), conversation(index, activeJob));
    await expect(page.locator(body)).toContainText(`${index ? 'B' : 'A'} current conversation`);
  };
  const pollRequests = () => h.requests.filter(request => request.includes('/private/ai-jobs/'));
  const timers = () => page.evaluate(() => (window as any).__aiPollProbe.timers.size as number);
  const next = async (index: number) => {
    await expect.poll(timers).toBe(1);
    const remaining = await page.evaluate(() => Math.max(0, Math.min(...(window as any).__aiPollProbe.timers.values()) - Date.now()));
    await page.clock.runFor(remaining);
    return h.take(`/ai-jobs/${101 + index}`);
  };
  const checkClean = async () => {
    expect(h.errors).toEqual([]);
    expect(await page.evaluate(() => (window as any).__aiPollProbe.maximum)).toBeLessThanOrEqual(1);
    expect(h.requests.filter(request => !request.startsWith('GET '))).toEqual([]);
  };
  await ready(0);
  return { ...h, reply, abort, ready, pollRequests, timers, next, checkClean };
}

for (const stale of ['pending', 'failed', 'completed', '404', 'network']) {
  test(`LQ AI poll late A ${stale} cannot alter B or release its pending lease`, async ({ page }) => {
    const h = await mount(page);
    const oldA = await h.next(0);
    await h.select(assistants[1].identity, 20); await h.ready(1);
    await page.locator(input).fill('B draft remains');
    const pendingB = await h.next(1);
    if (stale === 'network') await h.abort(oldA);
    else await h.reply(oldA, stale === '404' ? { detail: 'Old A missing' } : { job: job(0, stale) }, stale === '404' ? 404 : 200);
    await expect(page.locator(body)).toContainText('B current conversation');
    await expect(page.locator('[data-ai-poll-error]')).toHaveCount(0);
    await expect(page.locator(input)).toHaveValue('B draft remains');
    await expect(page.locator(input)).toBeFocused();
    await expect(page).toHaveURL(/contact=assistant%3A2&scope=20/);
    expect(await h.timers()).toBe(0);
    await page.clock.runFor(6600);
    expect(h.pollRequests()).toHaveLength(2); // B still owns its in-flight lease.
    await h.reply(pendingB, { job: job(1, 'running') });
    await expect(page.locator(body)).toContainText('AI 助教正在整理回复');
    expect(await h.timers()).toBe(1);
    await h.reply(await h.next(1), { job: job(1, 'pending') });
    expect(h.pollRequests()).toEqual([
      'GET /api/message-center/private/ai-jobs/101',
      'GET /api/message-center/private/ai-jobs/102',
      'GET /api/message-center/private/ai-jobs/102',
    ]);
    expect(h.pending.filter(route => new URL(route.request().url()).pathname.endsWith('/conversation'))).toHaveLength(0);
    expect(await page.evaluate(() => (window as any).__aiPollProbe.successes)).toEqual([]);
    await h.checkClean();
  });
}

for (const failure of ['404', '403', 'missing-job', 'null-response', 'wrong-id', 'wrong-key', 'wrong-status']) {
  test(`LQ AI poll current ${failure} stops visibly and manual refresh starts one new chain`, async ({ page }) => {
    const h = await mount(page), active = await h.next(0);
    const payload = failure === 'null-response' ? null : failure === 'missing-job' ? {} : { job: { ...job(0),
      ...(failure === 'wrong-id' ? { id: 999 } : {}),
      ...(failure === 'wrong-key' ? { conversation_key: key(1) } : {}),
      ...(failure === 'wrong-status' ? { status: 'unknown' } : {}),
    } };
    await h.reply(active, payload, failure === '404' ? 404 : failure === '403' ? 403 : 200);
    await expect(page.locator('[data-ai-poll-error]')).toBeVisible();
    await expect(page.locator(body)).toContainText('A current conversation');
    await expect(page.locator(body)).toContainText('AI 助教正在整理回复');
    expect(await h.timers()).toBe(0);
    await page.clock.runFor(8800);
    expect(h.pollRequests()).toHaveLength(1);
    await page.locator('[data-ai-poll-retry]').click();
    await h.ready(0);
    await expect(page.locator('[data-ai-poll-error]')).toHaveCount(0);
    await h.reply(await h.next(0), { job: job(0, 'running') });
    expect(h.pollRequests()).toHaveLength(2);
    expect(await h.timers()).toBe(1);
    await h.checkClean();
  });
}

test('LQ AI poll 5xx keeps current content and retries only once after 2200ms', async ({ page }) => {
  const h = await mount(page);
  for (let attempt = 1; attempt <= 3; attempt++) {
    await h.reply(await h.next(0), { detail: 'Temporary server failure' }, 503);
    await expect(page.locator('[data-ai-poll-error]')).toContainText('正在重试');
    await expect(page.locator(body)).toContainText('A current conversation');
    await expect(page.locator(body)).toContainText('AI 助教正在整理回复');
    expect(await h.timers()).toBe(1);
    expect(h.pollRequests()).toHaveLength(attempt);
    const remaining = await page.evaluate(() => Math.min(...(window as any).__aiPollProbe.timers.values()) - Date.now());
    expect(remaining).toBeGreaterThan(2100);
    await page.clock.runFor(remaining - 1);
    expect(h.pollRequests()).toHaveLength(attempt);
  }
  await h.reply(await h.next(0), { job: job(0, 'running') });
  await expect(page.locator('[data-ai-poll-error]')).toHaveCount(0);
  expect(await h.timers()).toBe(1);
  await h.checkClean();
});

test('LQ AI completed job with failed conversation refresh cannot announce success', async ({ page }) => {
  const h = await mount(page);
  await h.reply(await h.next(0), { job: job(0, 'completed') });
  await h.reply(await h.take('/private/conversation'), { detail: 'Refresh failed' }, 503);
  await expect(page.locator('[data-message-load-error]')).toBeVisible();
  await expect(page.locator('[data-send-button]')).toBeDisabled();
  expect(await h.timers()).toBe(0);
  await page.clock.runFor(8800);
  expect(h.pollRequests()).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).__aiPollProbe.successes)).toEqual([]);
  await h.checkClean();
});

test('LQ AI completed job announces success only after the actual conversation refresh succeeds', async ({ page }) => {
  const h = await mount(page);
  await h.reply(await h.next(0), { job: job(0, 'completed') });
  const refresh = await h.take('/private/conversation');
  expect(await page.evaluate(() => (window as any).__aiPollProbe.successes)).toEqual([]);
  await h.reply(refresh, conversation(0, null));
  await expect(page.locator('.lq-toast__message')).toContainText('AI 助教已回复');
  await expect(page.locator(body)).not.toContainText('正在整理回复');
  expect(await h.timers()).toBe(0);
  await h.checkClean();
});

for (const response of ['completed', 'network']) {
  test(`LQ AI poll ${response} after non-persisted pagehide cannot write or reschedule`, async ({ page }) => {
    const h = await mount(page), pending = await h.next(0);
    const before = await page.locator(body).innerHTML();
    const snapshot = await page.evaluate(() => JSON.stringify((window as any).__LANSHARE_MESSAGE_CENTER_WORKSPACE__));
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false })));
    if (response === 'network') await h.abort(pending);
    else await h.reply(pending, { job: job(0, 'completed') });
    await page.clock.runFor(8800);
    expect(await page.locator(body).innerHTML()).toBe(before);
    expect(await page.evaluate(() => JSON.stringify((window as any).__LANSHARE_MESSAGE_CENTER_WORKSPACE__))).toBe(snapshot);
    expect(await h.timers()).toBe(0);
    expect(h.pollRequests()).toHaveLength(1);
    expect(h.pending.filter(route => new URL(route.request().url()).pathname.endsWith('/conversation'))).toHaveLength(0);
    expect(await page.evaluate(() => (window as any).__aiPollProbe.successes)).toEqual([]);
    await h.checkClean();
  });
}
