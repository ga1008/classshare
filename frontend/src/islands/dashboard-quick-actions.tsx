// Keep SSR available while secondary controllers wait for the first paint.
const selectors = '[data-dw-open], [data-agenda-add-todo], [data-agenda-item], [data-agenda-calendar-feed], [data-agenda-sync], [data-group-mode], #ai-chat-fab';
let ready = false;
let loading: Promise<void> | null = null;
let pending: HTMLElement | null = null;
const notices = new Map<string, HTMLParagraphElement>();
const aiAssetPayload = document.getElementById('dw-deferred-ai-assets')?.textContent;
let aiReady = !aiAssetPayload;
let aiLoading: Promise<void> | null = null;
let aiRequested = false;
const islandReady = new Promise<void>(resolve => window.addEventListener('lanshare:dashboard-ready', () => resolve(), { once: true }));

function clearNotice(kind = 'workspace') { notices.get(kind)?.remove(); notices.delete(kind); }
function announce(message: string, retry = false, kind = 'workspace') {
  clearNotice(kind);
  const notice = document.createElement('p');
  notice.className = retry ? 'dw-error' : 'dw-empty-inline';
  notice.setAttribute('role', retry ? 'alert' : 'status');
  notice.append(message);
  if (retry) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'dw-link'; button.textContent = '刷新重试';
    button.addEventListener('click', () => window.location.reload());
    notice.append(button);
  }
  document.querySelector('.dw-page-head')?.after(notice);
  notices.set(kind, notice);
}
function detachWhenReady() { if (ready && aiReady) document.removeEventListener('click', capture, true); }
function afterPaint() { return new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))); }
function loadClassic(url: string) {
  return new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = url; script.async = false;
    script.addEventListener('load', () => resolve(), { once: true });
    script.addEventListener('error', () => { script.remove(); reject(new Error('AI dependency failed to load')); }, { once: true });
    document.head.append(script);
  });
}
function startAi() {
  if (!aiAssetPayload || aiLoading) return aiLoading;
  aiLoading = (async () => {
    await afterPaint();
    const assets = JSON.parse(aiAssetPayload) as { scripts: string[]; module: string; mermaid: string };
    const global = window as Window & { MARKDOWN_PREVIEW_ASSETS?: Record<string, unknown> };
    global.MARKDOWN_PREVIEW_ASSETS = { ...global.MARKDOWN_PREVIEW_ASSETS, mermaid: assets.mermaid };
    for (const url of assets.scripts) await loadClassic(url);
    await loadLegacy(assets.module);
    if (document.readyState === 'loading') await new Promise<void>(resolve => document.addEventListener('DOMContentLoaded', () => resolve(), { once: true }));
    aiReady = true; clearNotice('ai'); detachWhenReady();
    if (aiRequested) { aiRequested = false; document.getElementById('ai-chat-fab')?.click(); }
  })().catch(() => { aiLoading = null; announce('AI 工具暂时无法加载，请刷新重试。', true, 'ai'); });
  return aiLoading;
}

function replay(target: HTMLElement) {
  if (target.dataset.dwOpen) {
    window.dispatchEvent(new CustomEvent('lanshare:dashboard-open', { detail: {
      view: target.dataset.dwOpen, trigger: target.isConnected ? target : null,
      actionable: target.classList.contains('dw-overflow-link'),
    } }));
  } else if (target.hasAttribute('data-agenda-item')) {
    const anchor = target.isConnected ? target : document.querySelector<HTMLElement>('.dw-focus .dw-button');
    window.dispatchEvent(new CustomEvent('lanshare:agenda-detail', { detail: { data: { ...target.dataset }, anchor } }));
  } else if (target.isConnected) target.click();
}

function start(): Promise<void> {
  if (loading) return loading;
  loading = (async () => {
    await afterPaint();
    await Promise.all([
      import('./dashboard-workspace'),
      loadLegacy('/static/js/dashboard.js?v=workspace-20260905'),
      loadLegacy('/static/js/dashboard_agenda_widget.js?v=workspace-20260905'),
    ]);
    if (document.readyState === 'loading') await new Promise<void>(resolve => document.addEventListener('DOMContentLoaded', () => resolve(), { once: true }));
    await islandReady;
    ready = true;
    detachWhenReady();
    clearNotice();
    if (pending) { const target = pending; pending = null; replay(target); }
  })().catch(() => {
    loading = null;
    announce('首页交互工具暂时无法加载，请重试。', true);
  });
  return loading;
}

function capture(event: MouseEvent) {
  const target = event.target instanceof Element ? event.target.closest<HTMLElement>(selectors) : null;
  if (!target) return;
  if (target.id === 'ai-chat-fab') {
    if (aiReady) return;
    event.preventDefault(); event.stopImmediatePropagation(); aiRequested = true;
    announce('AI 工具正在准备…', false, 'ai'); void startAi(); return;
  }
  if (ready) return;
  event.preventDefault(); event.stopImmediatePropagation();
  pending = target;
  announce('正在准备操作…');
  void start();
}
document.addEventListener('click', capture, true);
void start();
void startAi();

function loadLegacy(url: string) { return import(/* @vite-ignore */ url); }
export {};
