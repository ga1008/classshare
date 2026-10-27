const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('@playwright/test');
// Isolated Chrome + immutable production assets, no application server or DB.
// --cold-data revalidates each old implementation's HTTP page marker; the
// default measures immediately usable retained content on warm reopen.
const freshEachOpen = process.argv.includes('--cold-data');
const output = process.env.SOCIAL_PERF_OUTPUT || '.codex-temp/social-chat-performance-after.json';
const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const asset = name => '/static/' + manifest.entries[name];
const DM = 'dm:student:1|student:2';
const avatar = '/api/profile/avatar?role=student&user_id=1';

(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const results = [];
  for (const count of [1, 200]) for (const motion of ['standard', 'off']) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 980 } });
    const page = await context.newPage();
    const requests = [];
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {
      class Socket extends EventTarget { static OPEN = 1; readyState = 1; constructor() { super(); queueMicrotask(() => this.dispatchEvent(new Event('open'))); } send() {} close() { this.readyState = 3; } }
      window.WebSocket = Socket;
      window.__longTasks = [];
      new PerformanceObserver(list => window.__longTasks.push(...list.getEntries().map(e => ({ at: e.startTime, duration: e.duration })))).observe({ type: 'longtask', buffered: true });
      window.__observe = (epoch = null) => {
        const sample = window.__sample = { start: performance.now(), epoch, visible: null, ready: null, open: null, hidden: null, added: 0, removed: 0, frames: [], stop: false };
        const observer = new MutationObserver(records => {
          for (const r of records) { sample.added += r.addedNodes.length; sample.removed += r.removedNodes.length; }
          const root = document.querySelector('#social-chat');
          if (!root) return;
          const elapsed = performance.now() - sample.start;
          if (!root.hidden && sample.visible === null) sample.visible = elapsed;
          if (root.dataset.lqLayerState === 'open' && sample.open === null) sample.open = elapsed;
          if (root.hidden && sample.hidden === null) sample.hidden = elapsed;
          if (!root.hidden && root.querySelector('[data-message-id="50"]')?.textContent.includes('批次' + epoch) && root.querySelector('textarea:not(:disabled)') && sample.ready === null) sample.ready = elapsed;
        });
        observer.observe(document.body, { attributes: true, childList: true, subtree: true });
        let previous = performance.now();
        const frame = now => { sample.frames.push(now - previous); previous = now; if (!sample.stop) requestAnimationFrame(frame); };
        requestAnimationFrame(frame);
        window.__endObserve = () => { sample.stop = true; observer.disconnect(); return { ...sample, longTasks: window.__longTasks.filter(e => e.at >= sample.start), maxFrame: Math.max(0,...sample.frames) }; };
      };
    });
    const friends = Array.from({ length: count }, (_, i) => ({ identity: `student:${i + 2}`, name: `合成好友${i + 2}`, online: true, avatar_url: avatar, conversation_key: `dm:student:1|student:${i + 2}` }));
    const conversations = friends.map(friend => ({ ...friend, conversation_key: friend.conversation_key, type: 'dm', peer_identity: friend.identity, title: friend.name, unread: 0, muted: false, last_message: { preview: '合成消息', at: '2026-10-28T01:00:00Z' } }));
    const messages = Array.from({ length: 50 }, (_, i) => ({ id: i + 1, conversation_key: DM, sender: { identity: i % 2 ? 'student:1' : 'student:2', name: i % 2 ? '我' : '合成好友2', avatar_url: avatar }, body: `第 ${i + 1} 条合成消息，包含 **知识交流** 和上下文。`, body_format: 'md', message_type: 'text', media: [], created_at: `2026-10-28T01:${String(i).padStart(2,'0')}:00Z` }));
    let messageEpoch = 0;
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'https://chat-perf.test') return route.abort();
      if (url.pathname.startsWith('/static/')) {
        const file = path.resolve('.' + url.pathname);
        if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) return route.abort();
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/javascript', body: fs.readFileSync(file) });
      }
      if (url.pathname === '/api/profile/avatar') return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><circle cx="20" cy="20" r="20" fill="teal"/></svg>' });
      if (url.pathname.startsWith('/api/')) {
        requests.push({ path: decodeURIComponent(url.pathname), at: Date.now() });
        const respond = json => route.fulfill({ json });
        if (url.pathname === '/api/social/friends') return respond({ items: friends, counts: { pending_in: 0 } });
        if (url.pathname === '/api/social/conversations') return respond({ items: conversations });
        if (url.pathname.endsWith('/messages')) { messageEpoch++; return respond({ items: messages.map(message => ({ ...message, body: message.body + ' 批次' + messageEpoch })), has_more: false, peer_read_message_id: 50, muted: false, mention_quota: { limit: 3, used: 0, remaining: 3 } }); }
        if (url.pathname.endsWith('/profile')) return respond(friends[0]);
        if (url.pathname.endsWith('/read')) return respond({ ok: true });
        if (url.pathname.endsWith('/quota')) return respond({ daily_limit: 200000000, used_today: 0, file_limit: 100000000 });
        return respond({});
      }
      return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html data-theme="lanshare" data-appearance="light" data-lq-motion="${motion}" data-lq-glass="tinted" data-lq-tier="A" data-cultivation-emblems="off"><head><meta charset="utf-8"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"></head><body><main><button class="lq-btn lq-btn--soft" data-social-entry data-social-me="student:1" data-social-css="${asset('css/lq/pages/social.css')}" data-social-polyfills="${asset('vendor/runtime/es2022-polyfills.js')}" data-social-marked="${asset('js/marked.min.js')}" data-social-markdown="${asset('js/markdown_runtime.js')}">好友</button>${Array.from({length:200},(_,i)=>`<article><h2>背景合成课程${i}</h2><p>课堂说明与学习记录</p></article>`).join('')}</main><script type="module">import{getSocialClient}from'${asset('js/social/social_client.js')}';window.client=getSocialClient();document.body.dataset.ready='true';</script></body></html>` });
    });
    await page.goto('https://chat-perf.test/');
    await page.waitForSelector('body[data-ready="true"]');
    const cdp = await context.newCDPSession(page);
    await cdp.send('Performance.enable');
    const metrics = async () => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m => [m.name,m.value]));
    for (let cycle = 0; cycle < 3; cycle++) {
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const startRequests = requests.length;
      const before = await metrics();
      await page.evaluate(({key,epoch}) => { window.__observe(epoch); window.client.openChat(key,document.querySelector('[data-social-entry]')); }, {key:DM,epoch:cycle === 0 || freshEachOpen ? messageEpoch + 1 : messageEpoch});
      await page.waitForFunction(() => window.__sample.ready !== null && window.__sample.open !== null);
      const open = await page.evaluate(() => window.__endObserve());
      const after = await metrics();
      const dom = await page.evaluate(() => ({ nodes: document.querySelector('#social-chat').querySelectorAll('*').length, messages: document.querySelectorAll('.social-msg').length, mineAvatars: document.querySelectorAll('.social-msg--out .lq-avatar').length, otherAvatars: document.querySelectorAll('.social-msg--in .lq-avatar').length, blurHosts: [...document.querySelectorAll('#social-chat *')].filter(el => getComputedStyle(el).backdropFilter !== 'none').length }));
      await page.evaluate(() => { window.__observe(); document.querySelector('#social-chat [data-lq-dialog-close]').click(); });
      await page.waitForFunction(() => document.querySelector('#social-chat').hidden);
      const close = await page.evaluate(() => window.__endObserve());
      const closed = await metrics();
      const delta = (a,b) => Object.fromEntries(['TaskDuration','ScriptDuration','LayoutDuration','RecalcStyleDuration','LayoutCount','RecalcStyleCount'].map(k => [k,b[k]-a[k]]));
      results.push({ count, motion, cycle, dom, open, close, openCpu:delta(before,after), closeCpu:delta(after,closed), requests: requests.slice(startRequests).map(r=>r.path), errors });
    }
    await context.close();
  }
  await browser.close();
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify({revision:manifest.revision,freshEachOpen,results},null,2));
  console.log(JSON.stringify(results.map(r=>({count:r.count,motion:r.motion,cycle:r.cycle,visible:r.open.visible,ready:r.open.ready,open:r.open.open,close:r.close.hidden,openTasks:r.open.longTasks,closeTasks:r.close.longTasks,openFrame:r.open.maxFrame,closeFrame:r.close.maxFrame,cpuMs:Math.round(r.openCpu.TaskDuration*1000),layoutMs:Math.round(r.openCpu.LayoutDuration*1000),mutations:[r.open.added,r.open.removed],requests:r.requests,dom:r.dom})),null,2));
})().catch(error => { console.error(error); process.exitCode=1; });
