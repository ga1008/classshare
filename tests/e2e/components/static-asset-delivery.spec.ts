import { test, expect } from '@playwright/test';
import { createServer, type Server } from 'node:http';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import type { Socket } from 'node:net';
import { init, parse } from 'es-module-lexer';

test('immutable graph keeps native auth/UI singletons, browser cache and previous lazy imports', async ({ page }) => {
  const temporary = mkdtempSync(join(tmpdir(), 'lanshare-static-test-'));
  const root = join(temporary, 'static');
  const write = (name: string, text: string) => {
    mkdirSync(join(root, name, '..'), { recursive: true });
    writeFileSync(join(root, name), text);
  };
  const build = () => {
    execFileSync(process.execPath, ['tools/build_static_assets.mjs', root], { cwd: process.cwd() });
    return JSON.parse(readFileSync(join(root, 'assets/manifest.json'), 'utf8')) as { revision: string };
  };
  await init;
  const copied = new Set<string>();
  const copyModule = (name: string) => {
    if (copied.has(name)) return;
    copied.add(name);
    const source = readFileSync(join('static', name), 'utf8');
    write(name, source);
    for (const item of parse(source)[0]) {
      if (item.n == null) continue;
      const dependency = item.n.split(/[?#]/)[0];
      if (dependency.startsWith('.')) copyModule(join(dirname(name), dependency));
      else if (dependency.startsWith('/static/')) copyModule(dependency.slice(8));
    }
  };
  // Exercise the real native dependencies (including print-only LQ modules),
  // without copying unrelated application code or building the live graph.
  copyModule('js/auth.js');
  copyModule('js/ui.js');
  write('js/first.js', "import './auth.js?v=one'; export { showToast } from './ui.js?v=one';");
  write('js/second.js', "import './auth.js?v=two'; export { showToast } from './ui.js?v=two';");
  write('js/lazy.js', "export { showToast } from './ui.js?v=old'; export const release = 'old';");
  write('css/app.css', 'body { color: rgb(12, 34, 56); }');
  const initial = build();
  let current = initial;
  const requests: string[] = [];
  const sockets = new Set<Socket>();
  let server: Server | undefined;
  try {
    server = createServer((request, response) => {
      const path = new URL(request.url || '/', 'http://fixture').pathname;
      requests.push(path);
      if (path.startsWith('/static/')) {
        const file = resolve(root, path.slice('/static/'.length));
        if (!file.startsWith(resolve(root) + '\\') && !file.startsWith(resolve(root) + '/')) {
          response.writeHead(400).end(); return;
        }
        try {
          response.writeHead(200, {
            'Content-Type': path.endsWith('.css') ? 'text/css' : 'text/javascript',
            'Cache-Control': 'public, max-age=31536000, immutable',
          });
          response.end(readFileSync(file));
        } catch { response.writeHead(404).end(); }
        return;
      }
      const base = `/static/assets/${current.revision}/`;
      response.writeHead(200, { 'Content-Type': 'text/html', 'Cache-Control': 'no-store' });
      response.end(`<!doctype html><link rel="stylesheet" href="${base}css/app.css">
        <script type="module" src="${base}js/auth.js"></script><body><button>fixture</button>
        <script type="module">
        import * as first from '${base}js/first.js';
        import * as second from '${base}js/second.js';
        import { showToast } from '${base}js/ui.js';
        window.graphProbe = { same: first.showToast === second.showToast && first.showToast === showToast, showToast };
        </script></body>`);
    });
    server.on('connection', socket => {
      sockets.add(socket);
      socket.on('close', () => sockets.delete(socket));
    });
    await new Promise<void>(done => server!.listen(0, '127.0.0.1', done));
    const address = server.address() as { port: number };
    const origin = `http://127.0.0.1:${address.port}`;
    await page.addInitScript(() => {
      const original = document.addEventListener.bind(document);
      (window as any).uiControllerListeners = 0;
      document.addEventListener = ((type: string, ...args: any[]) => {
        if (type === 'click' && new Error().stack?.includes('/js/ui.js')) (window as any).uiControllerListeners++;
        return (original as any)(type, ...args);
      }) as typeof document.addEventListener;
      let fetchValue = window.fetch;
      (window as any).authFetchPatches = 0;
      Object.defineProperty(window, 'fetch', {
        configurable: true,
        get: () => fetchValue,
        set: value => { (window as any).authFetchPatches++; fetchValue = value; },
      });
    });
    await page.goto(`${origin}/first`);
    await expect.poll(() => page.evaluate(() => (window as any).graphProbe?.same)).toBe(true);
    expect(await page.evaluate(() => [(window as any).authFetchPatches, (window as any).uiControllerListeners])).toEqual([1, 1]);
    await page.goto(`${origin}/second`);
    await expect.poll(() => page.evaluate(() => (window as any).graphProbe?.same)).toBe(true);
    expect(requests.filter(url => url.endsWith('/css/app.css'))).toHaveLength(1);

    // A deployment changes a child while this document is still open.
    write('js/ui.js', readFileSync('static/js/ui.js', 'utf8') + '\nexport const revision = 2;\n');
    write('js/lazy.js', "export { showToast } from './ui.js'; export const release = 'new';");
    current = build();
    expect(current.revision).not.toBe(initial.revision);
    expect(await page.evaluate(async url => {
      const lazy = await import(url);
      return lazy.release === 'old' && lazy.showToast === (window as any).graphProbe.showToast;
    }, `${origin}/static/assets/${initial.revision}/js/lazy.js`)).toBe(true);
    expect(await page.evaluate(() => [(window as any).authFetchPatches, (window as any).uiControllerListeners])).toEqual([1, 1]);
  } finally {
    if (server) {
      const closed = new Promise<void>((done, fail) => server!.close(error => error ? fail(error) : done()));
      // Chrome may keep unused preconnect sockets open after every assertion
      // passes. Close those test-owned sockets rather than waiting for timeout.
      for (const socket of sockets) socket.destroy();
      await closed;
    }
    rmSync(temporary, { recursive: true, force: true });
  }
});
