import { expect, type Page, type Route } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const partials = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_message_center.py'], { encoding: 'utf8' }));
export const summary = { unread_total: 3, tabs: [
  { category: 'all', label: '全部', unread_count: 3 },
  { category: 'system', label: '系统', unread_count: 1 },
  { category: 'private_message', label: '私信', unread_count: 2 },
], filters: [{ value: 'all', label: '全部' }, { value: 'unread', label: '未读' }] };
export const contacts = [
  { identity: 'teacher:1', class_offering_id: 10, display_name: '甲老师', role: 'teacher', can_send: true },
  { identity: 'teacher:2', class_offering_id: 10, display_name: '乙老师', role: 'teacher', can_send: true },
  { identity: 'teacher:1', class_offering_id: 20, display_name: '甲老师另一课堂', role: 'teacher', can_send: true },
];
export const bootstrap = { summary, private_contacts: contacts, private_blocks: [] };
export function conversation(identity: string, scope: number, content: string) {
  return { summary, conversation: { contact: contacts.find(item => item.identity === identity && item.class_offering_id === scope),
    conversation_key: `student:99|${identity}|${scope}`, class_offering_id: scope,
    messages: [{ id: content, content, sender_display_name: identity, created_at: '2026-09-21T08:00:00', attachments: [] }] } };
}

export async function mountMessageCenter(page: Page, mode = 'private', holdBootstrap = false, bootstrapData: unknown = bootstrap) {
  const pending: Route[] = [], requests: string[] = [], errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let bootstrapCount = 0;
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://message-races.test') return route.abort();
    if (url.pathname.startsWith('/api/')) {
      requests.push(`${route.request().method()} ${url.pathname}${url.search}`);
      if (url.pathname === '/api/message-center/bootstrap' && bootstrapCount++ === 0 && !holdBootstrap) return route.fulfill({ json: bootstrapData });
      pending.push(route); return;
    }
    if (url.pathname.startsWith('/static/js/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static/js') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Message request ownership</title>
      <style>[hidden]{display:none!important}body{font:16px sans-serif}.message-center-private-panel{display:grid;grid-template-columns:320px 1fr}.message-center-conversation__body{min-height:200px}button,input,select,textarea{min-height:36px}textarea{width:90%}</style></head>
      <body>${partials[mode]}<script type="module" src="/static/js/message_center.js"></script></body></html>` });
    return route.fulfill({ status: 404, body: 'isolated local fixture only' });
  });
  await page.goto('https://message-races.test/', { waitUntil: 'domcontentloaded' });
  const take = async (fragment: string, query: Record<string, string> = {}) => {
    const matches = (route: Route) => {
      const url = new URL(route.request().url());
      return url.pathname.endsWith(fragment) && Object.entries(query).every(([key, value]) => url.searchParams.get(key) === value);
    };
    await expect.poll(() => pending.some(matches)).toBe(true);
    return pending.splice(pending.findIndex(matches), 1)[0];
  };
  const reply = async (route: Route, data: unknown, status = 200) => {
    const received = page.waitForResponse(response => response.url() === route.request().url());
    await route.fulfill({ status, json: data });
    await (await received).finished();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  };
  const select = (identity: string, scope = 10) => page.locator('#message-center-contact-select').selectOption(`${identity}|scope:${scope}`);
  const readyA = async () => {
    await reply(await take('/private/conversation', { contact: 'teacher:1', scope: '10' }), conversation('teacher:1', 10, 'A initial'));
    await expect(page.locator('[data-send-button]')).toBeEnabled();
  };
  return { pending, requests, errors, take, reply, select, readyA };
}

