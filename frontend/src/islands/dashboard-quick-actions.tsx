import { legacyModuleUrl } from '@/lib/static-assets';

// Keep SSR available while secondary controllers wait for the first paint.
const selectors = '[data-ls-open], [data-agenda-add-todo], [data-agenda-item], [data-agenda-calendar-feed], [data-agenda-sync], [data-group-mode], [data-student-schedule-mode], [data-student-week-prev], [data-student-week-next], [data-student-week-today], [data-student-schedule-expand]';
let ready = false;
let loading: Promise<void> | null = null;
let pending: HTMLElement | null = null;
const notices = new Map<string, HTMLParagraphElement>();
const islandReady = new Promise<void>(resolve => window.addEventListener('lanshare:dashboard-ready', () => resolve(), { once: true }));

function clearNotice(kind = 'workspace') { notices.get(kind)?.remove(); notices.delete(kind); }
function announce(message: string, retry = false, kind = 'workspace') {
  clearNotice(kind);
  const notice = document.createElement('p');
  notice.className = retry ? 'ls-error' : 'ls-empty-inline';
  notice.setAttribute('role', retry ? 'alert' : 'status');
  notice.append(message);
  if (retry) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'ls-link'; button.textContent = '刷新重试';
    button.addEventListener('click', () => window.location.reload());
    notice.append(button);
  }
  document.querySelector('.ls-page-head')?.after(notice);
  notices.set(kind, notice);
}
function detachWhenReady() { if (ready) document.removeEventListener('click', capture, true); }
function afterPaint() { return new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))); }
function replay(target: HTMLElement) {
  if (target.dataset.lsOpen) {
    window.dispatchEvent(new CustomEvent('lanshare:dashboard-open', { detail: {
      view: target.dataset.lsOpen, trigger: target.isConnected ? target : null,
      actionable: target.classList.contains('ls-overflow-link'),
    } }));
  } else if (target.hasAttribute('data-agenda-item')) {
    const anchor = target.isConnected ? target : document.querySelector<HTMLElement>('.ls-focus .ls-button');
    window.dispatchEvent(new CustomEvent('lanshare:agenda-detail', { detail: { data: { ...target.dataset }, anchor } }));
  } else if (target.hasAttribute('data-agenda-add-todo')) {
    (target.isConnected ? target : document.querySelector<HTMLElement>('[data-dashboard-root] [data-agenda-add-todo]'))?.click();
  } else if (target.isConnected) target.click();
}

function start(): Promise<void> {
  if (loading) return loading;
  loading = (async () => {
    await afterPaint();
    await Promise.all([
      import('./dashboard-workspace'),
      loadLegacy(legacyModuleUrl('dashboard.js')),
      loadLegacy(legacyModuleUrl('dashboard_agenda_widget.js')),
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
  if (ready) return;
  event.preventDefault(); event.stopImmediatePropagation();
  pending = target;
  announce('正在准备操作…');
  void start();
}
document.addEventListener('click', capture, true);
void start();

function loadLegacy(url: string) { return import(/* @vite-ignore */ url); }
export {};
