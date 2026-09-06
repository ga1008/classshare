import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import { readIslandJsonPayload } from '@/lib/island-payload';
import {
  dashboardAgendaDataset, dashboardKindLabels, normalizeDashboardItems,
  type DashboardFilters, type DashboardItem,
} from '@/lib/dashboard-workspace';

type Workspace = {
  total: number; filtered_total: number; pending_total: number; actionable_total: number; has_more: boolean;
  focus_items: DashboardItem[]; all_items: DashboardItem[];
  attention_items: DashboardItem[]; action_summary: { total: number; today: number; overdue: number; undated: number };
  offering_options: { id: number; label: string }[];
  generated_at: string; next_transition_at: string;
  next_cursor: string | null;
};

function normalizeWorkspace(raw: unknown): Workspace {
  const value = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const options = Array.isArray(value.offering_options) ? value.offering_options : [];
  return {
    total: Number(value.total) || 0, filtered_total: Number(value.filtered_total ?? value.total) || 0, pending_total: Number(value.pending_total) || 0,
    actionable_total: Number(value.actionable_total) || 0, has_more: value.has_more === true,
    focus_items: normalizeDashboardItems(value.focus_items).slice(0, 3), all_items: normalizeDashboardItems(value.all_items),
    attention_items: normalizeDashboardItems(value.attention_items).slice(0, 3),
    action_summary: Object.fromEntries(['total', 'today', 'overdue', 'undated'].map(key => [key, Number((value.action_summary as Record<string, unknown>)?.[key]) || 0])) as Workspace['action_summary'],
    generated_at: String(value.generated_at || ''), next_transition_at: String(value.next_transition_at || ''),
    next_cursor: typeof value.next_cursor === 'string' && value.next_cursor ? value.next_cursor : null,
    offering_options: options.map((option) => ({ id: Number(option.id ?? option.offering_id) || 0, label: String(option.label || option.title || option.course_name || '课堂') })),
  };
}

const initialFilters: DashboardFilters = { query: '', offering: '', kind: '', date: '', state: '' };
const PAGE_SIZE = 20;

async function readWorkspace(query: URLSearchParams, signal?: AbortSignal): Promise<Workspace> {
  const response = await fetch(`/api/dashboard/workspace?${query}`, { credentials: 'same-origin', headers: { Accept: 'application/json' }, signal });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.status !== 'success' || !payload.workspace) {
    throw Object.assign(new Error(payload.detail || payload.message || (response.redirected ? '登录状态已变化，请刷新页面后重试。' : '事项暂时无法加载，请重试。')), { status: response.status });
  }
  return normalizeWorkspace(payload.workspace);
}

function agendaAttributes(item: DashboardItem) {
  return Object.fromEntries(Object.entries(dashboardAgendaDataset(item)).map(([key, value]) => [
    `data-${key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`, value,
  ]));
}

function needsAgendaDetail(item: DashboardItem) {
  return Boolean(item.agenda_data.is_manual || item.agenda_data.can_email_reminder || !item.href || item.href.startsWith('#') || item.href.endsWith('#dashboard-semester'));
}

function ItemCopy({ item }: { item: DashboardItem }) {
  return <>
    <span className="dw-item-kind">{item.type_label || dashboardKindLabels[item.kind] || '事项'}</span>
    <span className="dw-item-copy"><strong>{item.title}</strong>{item.subtitle ? <small>{item.offering_id ? <a href={`/classroom/${item.offering_id}`} aria-label={`进入课堂：${item.subtitle}`}>{item.subtitle}</a> : item.subtitle}</small> : null}</span>
    <span className="dw-item-time">{item.status_label ? <span className={item.date_bucket === 'overdue' ? 'dw-status-warn' : ''}>{item.status_label}</span> : null}<span>{item.date_label || '无日期'}{item.time_label ? ` · ${item.time_label}` : ''}</span></span>
  </>;
}

/** Move the one existing calendar node; preserve its controller and viewport state. */
function CalendarHost() {
  const host = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const storage = document.querySelector<HTMLElement>('[data-dw-calendar-storage]');
    const calendar = storage?.querySelector<HTMLElement>('[data-semester-calendar-root]');
    if (!host.current || !calendar) return;
    host.current.append(calendar);
    window.dispatchEvent(new CustomEvent('lanshare:dashboard-calendar-open', { detail: { root: calendar } }));
    requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    return () => { storage?.append(calendar); };
  }, []);
  return <div className="dw-calendar-host" ref={host} />;
}

function DashboardWorkspace({ initial }: { initial: Workspace }) {
  const isStudent = document.querySelector<HTMLElement>('[data-dashboard-root]')?.dataset.dashboardRole === 'student';
  const [workspace, setWorkspace] = useState(initial);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<'items' | 'calendar'>('items');
  const [filters, setFilters] = useState(initialFilters);
  const [scope, setScope] = useState<number[] | null>(null);
  const [page, setPage] = useState(0);
  const [result, setResult] = useState({ items: initial.all_items.slice(0, PAGE_SIZE), total: initial.total });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [retry, setRetry] = useState(0);
  const [itemKey, setItemKey] = useState('');
  const [todoNotice, setTodoNotice] = useState<{ todoId: number; classOfferingId: number; message: string; deleted?: boolean } | null>(null);
  const allButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const handoff = useRef(false);
  const handoffCallback = useRef<(() => void) | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const scrollPosition = useRef(0);
  const pageCursors = useRef<Record<number, string>>({});

  const show = (target: 'items' | 'calendar', trigger?: HTMLElement) => {
    returnFocus.current = trigger || allButton.current;
    handoff.current = false;
    setView(target); setOpen(true);
  };

  useEffect(() => {
    const handleOpen = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target.closest<HTMLElement>('[data-dw-open]') : null;
      if (!target) return;
      event.preventDefault(); setItemKey(''); show(target.dataset.dwOpen === 'calendar' ? 'calendar' : 'items', target);
    };
    const handleRequestedOpen = (event: Event) => { const detail = (event as CustomEvent<{ view: string; trigger?: HTMLElement; actionable?: boolean }>).detail; setItemKey(''); if (detail.actionable) setFilters(current => ({ ...current, state: isStudent ? 'attention' : 'actionable' })); show(detail.view === 'calendar' ? 'calendar' : 'items', detail.trigger || undefined); };
    const handleTodoChanged = (event: Event) => setTodoNotice((event as CustomEvent).detail);
    const handleHash = () => { if (window.location.hash === '#dashboard-semester') show('calendar'); };
    const handleScope = (event: Event) => {
      const detail = (event as CustomEvent<{ active: boolean; offeringIds: number[] }>).detail;
      setScope(detail.active ? detail.offeringIds : null); setPage(0);
    };
    document.addEventListener('click', handleOpen);
    window.addEventListener('hashchange', handleHash);
    window.addEventListener('lanshare:dashboard-open', handleRequestedOpen);
    window.addEventListener('lanshare:dashboard-scope', handleScope);
    window.addEventListener('lanshare:dashboard-todo-changed', handleTodoChanged);
    const savedScope = document.querySelector<HTMLElement>('[data-dashboard-root]')?.dataset.dashboardScope;
    if (savedScope) {
      try { handleScope(new CustomEvent('lanshare:dashboard-scope', { detail: JSON.parse(savedScope) })); } catch { /* default to all authorized classrooms */ }
    }
    handleHash();
    window.dispatchEvent(new Event('lanshare:dashboard-ready'));
    return () => {
      document.removeEventListener('click', handleOpen);
      window.removeEventListener('hashchange', handleHash);
      window.removeEventListener('lanshare:dashboard-open', handleRequestedOpen);
      window.removeEventListener('lanshare:dashboard-scope', handleScope);
      window.removeEventListener('lanshare:dashboard-todo-changed', handleTodoChanged);
    };
  }, []);

  useEffect(() => {
    const offeringIds = filters.offering ? [Number(filters.offering)].filter((id) => !scope || scope.includes(id)) : scope;
    window.dispatchEvent(new CustomEvent('lanshare:dashboard-calendar-scope', { detail: { offeringIds } }));
  }, [scope, filters.offering]);

  // One deadline timer and one foreground refresh, never a timer per item.
  useEffect(() => {
    let controller: AbortController | null = null;
    const refresh = async (calendarFresh = false) => {
      controller?.abort(); controller = new AbortController();
      try {
        const fresh = await readWorkspace(new URLSearchParams({ limit: '100' }), controller.signal);
        setWorkspace(fresh); setError('');
        if (!calendarFresh) window.dispatchEvent(new Event('lanshare:dashboard-calendar-invalidate'));
      } catch (failure) { if ((failure as Error).name !== 'AbortError') setError('事项状态暂时无法更新，请重试。'); }
    };
    const onVisible = () => { if (document.visibilityState === 'visible') void refresh(); };
    const onRetry = (event: Event) => { void refresh(Boolean((event as CustomEvent<{ calendarFresh?: boolean }>).detail?.calendarFresh)); };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('lanshare:dashboard-workspace-refresh', onRetry);
    const next = Date.parse(workspace.next_transition_at);
    const delay = Number.isFinite(next) ? Math.max(1000, Math.min(next - Date.now() + 1000, 2_147_000_000)) : 0;
    const timer = delay ? window.setTimeout(() => void refresh(), delay) : 0;
    return () => { document.removeEventListener('visibilitychange', onVisible); window.removeEventListener('lanshare:dashboard-workspace-refresh', onRetry); clearTimeout(timer); controller?.abort(); };
  }, [workspace.next_transition_at]);

  useEffect(() => {
    pageCursors.current = {};
    setPage(0);
  }, [filters, scope, itemKey]);
  useEffect(() => { pageCursors.current = {}; }, [workspace]);

  useEffect(() => {
    if (!open || view !== 'items') return;
    if (scope?.length === 0) { setResult({ items: [], total: 0 }); setError(''); setLoading(false); return; }
    const hasFilters = Object.values(filters).some(Boolean) || scope !== null || Boolean(itemKey);
    if (!hasFilters && !workspace.has_more) {
      setResult({ items: workspace.all_items.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE), total: workspace.total });
      setError(''); setLoading(false); return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true); setError('');
      try {
        const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
        const cursor = pageCursors.current[page];
        if (cursor) query.set('cursor', cursor);
        else if (page * PAGE_SIZE <= 10000) query.set('offset', String(page * PAGE_SIZE));
        else { setPage(0); setNotice('事项已更新，已返回第一页。'); return; }
        if (filters.query) query.set('q', filters.query);
        if (filters.offering) query.set('offering_id', filters.offering);
        if (filters.kind) query.set('kind', filters.kind);
        if (filters.date) query.set('date_scope', filters.date);
        if (filters.state) query.set('status', filters.state);
        if (itemKey) query.set('item_key', itemKey);
        if (scope) query.set('offering_ids', scope.join(','));
        const fresh = await readWorkspace(query, controller.signal);
        if (page > 0 && page * PAGE_SIZE >= fresh.filtered_total) { setPage(Math.max(0, Math.ceil(fresh.filtered_total / PAGE_SIZE) - 1)); return; }
        if (fresh.next_cursor) pageCursors.current[page + 1] = fresh.next_cursor;
        setResult({ items: fresh.all_items, total: fresh.filtered_total });
      } catch (failure) {
        if ((failure as Error & { status?: number }).status === 409) {
          pageCursors.current = {}; setPage(0); setNotice('事项状态或排序已更新，已返回第一页。');
        } else if ((failure as Error).name !== 'AbortError') setError((failure as Error).message);
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }, filters.query ? 200 : 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [open, view, filters, page, workspace, scope, retry, itemKey]);

  const setFilter = (key: keyof DashboardFilters, value: string) => { setFilters((current) => ({ ...current, [key]: value })); setPage(0); setNotice(''); scrollPosition.current = 0; };
  const closeAndRun = (callback: () => void) => {
    if (!open) { callback(); return; }
    handoff.current = true;
    handoffCallback.current = callback;
    scrollPosition.current = scroll.current?.scrollTop || 0;
    setOpen(false);
  };
  const showAgenda = (item: DashboardItem) => closeAndRun(() => {
    window.dispatchEvent(new CustomEvent('lanshare:agenda-detail', { detail: { data: dashboardAgendaDataset(item), anchor: allButton.current } }));
  });
  const launchTool = (attribute: string) => closeAndRun(() => {
    allButton.current?.focus({ preventScroll: true });
    document.querySelector<HTMLButtonElement>(`[data-dashboard-root] [${attribute}]`)?.click();
  });
  const hasFilters = Object.values(filters).some(Boolean) || Boolean(itemKey);
  const focusItems = isStudent ? workspace.attention_items : workspace.focus_items;
  const immediate = isStudent ? focusItems.find(item => ['today', 'overdue'].includes(item.date_bucket)) : undefined;
  const pending = isStudent ? workspace.action_summary.total : workspace.pending_total;
  const openAttention = (date = '') => { setItemKey(''); setFilters({ ...initialFilters, state: isStudent ? 'attention' : 'actionable', date }); show('items'); };
  const todoAction = (item: DashboardItem, action: 'edit' | 'toggle', anchor: HTMLButtonElement) => {
    const run = () => window.dispatchEvent(new CustomEvent(`lanshare:todo-${action}`, { detail: { data: dashboardAgendaDataset(item), anchor: action === 'edit' && open ? allButton.current : anchor, afterClose: action === 'edit' && open ? () => { handoff.current = false; setOpen(true); } : undefined } }));
    if (action === 'edit' && open) closeAndRun(run); else run();
  };
  const manualControls = (item: DashboardItem) => item.agenda_data.is_manual ? <span className="dw-manual-actions"><button type="button" className="dw-link" onClick={event => todoAction(item, 'edit', event.currentTarget)}>编辑</button><button type="button" className="dw-button" onClick={event => todoAction(item, 'toggle', event.currentTarget)}>{item.is_completed ? '恢复待办' : '完成'}</button></span> : null;

  return <>
    {immediate ? <div className="dw-mobile-urgent"><span><strong>现在有事需要留意</strong><small>{immediate.title}</small></span>{needsAgendaDetail(immediate) ? <button type="button" className="dw-button dw-button-primary" data-agenda-item {...agendaAttributes(immediate)} aria-haspopup="dialog">查看</button> : <a className="dw-button dw-button-primary" href={immediate.href}>去处理</a>}</div> : null}
    <section className={`dw-focus${isStudent ? ' dw-focus--student' : ''}`} aria-labelledby="dw-focus-title" data-agenda-reminder data-reminder-endpoint="/api/manage/system/exam-reminders/email">
      <div className="dw-section-head"><div><span className="dw-eyebrow">{isStudent ? '一步一步，完成今天' : '教学工作'}</span><h2 id="dw-focus-title">需要处理</h2></div><button type="button" className="dw-button" data-agenda-add-todo aria-haspopup="dialog">＋ 新增待办</button></div>
      {isStudent ? <div className="dw-action-summary" aria-label="待处理概况"><button type="button" onClick={() => openAttention('today')}><strong>{workspace.action_summary.today}</strong><span>今日截止</span></button><button type="button" onClick={() => openAttention('overdue')}><strong>{workspace.action_summary.overdue}</strong><span>逾期可处理</span></button><button type="button" onClick={() => openAttention()}><strong>{pending}</strong><span>全部待处理</span></button></div> : null}
      {todoNotice ? <div className="dw-todo-notice" role="status"><span>{todoNotice.message}</span>{!todoNotice.deleted && todoNotice.todoId ? <button type="button" className="dw-link" onClick={() => { setFilters(initialFilters); setScope(null); setItemKey(`manual:${todoNotice.todoId}:${todoNotice.classOfferingId}`); setPage(0); show('items'); }}>查看此待办</button> : null}<button type="button" className="dw-link" aria-label="关闭待办提示" onClick={() => setTodoNotice(null)}>×</button></div> : null}
      {focusItems.length ? <ul className="dw-focus-list">{focusItems.map((item, index) => <li key={item.key} className={`dw-focus-item${index === 0 ? ' is-primary' : ''}`}>
        <ItemCopy item={item} />
        <span className="dw-item-actions">{needsAgendaDetail(item) ? <button type="button" className={index === 0 ? 'dw-button dw-button-primary' : 'dw-button'} data-agenda-item {...agendaAttributes(item)} aria-haspopup="dialog">{item.action_label || '查看详情'}</button> : <a className={index === 0 ? 'dw-button dw-button-primary' : 'dw-button'} href={item.href}>{item.action_label || (item.kind === 'class' ? '进入课堂' : '查看任务')}</a>}{manualControls(item)}</span>
      </li>)}</ul> : <p className="dw-empty-inline">暂无需要处理的事项。</p>}
      <div className="dw-focus-footer"><button type="button" className="dw-link" onClick={() => openAttention()}>全部待处理（{pending}） →</button><button type="button" className="dw-link" ref={allButton} onClick={() => { setItemKey(''); setFilters(initialFilters); show('items', allButton.current || undefined); }}>全部事项与历史</button></div>
      {!open && error ? <p className="dw-error" role="status">{error}<button className="dw-link" type="button" onClick={() => window.dispatchEvent(new Event('lanshare:dashboard-workspace-refresh'))}>重试</button></p> : null}
    </section>
    <Dialog open={open} onOpenChange={(next) => { if (!next) scrollPosition.current = scroll.current?.scrollTop || 0; setOpen(next); }}>
      <DialogContent className={`dw-dialog${view === 'calendar' ? ' dw-dialog--calendar' : ''}`} aria-modal="true" aria-describedby={undefined} onCloseAutoFocus={(event) => { event.preventDefault(); const callback = handoffCallback.current; handoffCallback.current = null; if (callback) window.setTimeout(callback, 0); else if (!handoff.current) (returnFocus.current || allButton.current)?.focus({ preventScroll: true }); }} onClickCapture={(event) => {
        const trigger = event.target instanceof Element ? event.target.closest<HTMLButtonElement>('[data-semester-todo-add]') : null;
        if (!trigger) return;
        event.preventDefault(); event.stopPropagation();
        launchTool('data-agenda-add-todo');
      }}>
        <DialogHeader className="dw-dialog-head"><DialogTitle>日程与事项</DialogTitle></DialogHeader>
        <div className="dw-dialog-toolbar"><div className="dw-view-switch" role="group" aria-label="日程视图"><button type="button" aria-pressed={view === 'items'} onClick={() => setView('items')}>全部事项</button><button type="button" aria-pressed={view === 'calendar'} onClick={() => setView('calendar')}>学期日历</button></div><div className="dw-dialog-tools"><button type="button" className="dw-link" onClick={() => launchTool('data-agenda-add-todo')}>新增待办</button><button type="button" className="dw-link" onClick={() => launchTool('data-agenda-calendar-feed')}>订阅日历</button></div></div>
        <div className="dw-dialog-body" ref={(node) => { scroll.current = node; if (node) node.scrollTop = scrollPosition.current; }} onScroll={(event) => { scrollPosition.current = event.currentTarget.scrollTop; }}>
          {view === 'calendar' ? <CalendarHost /> : <>
            {scope ? <p className="dw-scope-note">沿用首页课堂筛选 · {scope.length} 个课堂<button type="button" className="dw-link" onClick={() => { setScope(null); setPage(0); }}>查看全部范围</button></p> : null}
            <div className="dw-item-filters">
              <label className="dw-search-filter"><span>搜索事项</span><input type="search" value={filters.query} placeholder="标题或课堂" onChange={(event) => setFilter('query', event.target.value)} /></label>
              <label><span>课堂</span><select value={filters.offering} onChange={(event) => setFilter('offering', event.target.value)}><option value="">全部课堂</option>{workspace.offering_options.filter((option) => !scope || scope.includes(option.id)).map((option) => <option value={option.id} key={option.id}>{option.label}</option>)}</select></label>
              <label><span>类型</span><select value={filters.kind} onChange={(event) => setFilter('kind', event.target.value)}><option value="">全部类型</option>{Object.entries(dashboardKindLabels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
              <label><span>日期</span><select value={filters.date} onChange={(event) => setFilter('date', event.target.value)}><option value="">全部日期</option><option value="today">今天</option><option value="this_week">本周</option><option value="next_seven_days">未来 7 天</option><option value="upcoming">未来</option><option value="overdue">逾期</option><option value="undated">无日期</option><option value="history">过去</option></select></label>
              <label><span>状态</span><select value={filters.state} onChange={(event) => setFilter('state', event.target.value)}><option value="">全部状态</option><option value={isStudent ? 'attention' : 'actionable'}>待处理</option><option value="completed">已完成</option></select></label>
            </div>
            <div className="dw-list-summary" role="status">{loading ? '正在加载…' : `${itemKey ? '已定位此待办 · ' : ''}共 ${result.total} 项${result.total ? ` · 显示 ${page * PAGE_SIZE + 1}–${Math.min(page * PAGE_SIZE + result.items.length, result.total)}` : ''}`}{hasFilters ? <button type="button" className="dw-link" onClick={() => { setFilters(initialFilters); setItemKey(''); setPage(0); }}>清除筛选</button> : null}</div>
            {notice ? <p className="dw-scope-note" role="status">{notice}</p> : null}
            {error ? <p className="dw-error" role="alert">{error}<button type="button" className="dw-link" onClick={() => setRetry((value) => value + 1)}>重试</button></p> : null}
            {!error && !loading && !result.items.length ? <p className="dw-empty-inline">没有符合条件的事项。</p> : null}
            {!error ? <ul className="dw-all-items" aria-busy={loading}>{result.items.map((item) => <li className={`dw-agenda-row${itemKey ? ' is-located' : ''}`} key={item.key}><ItemCopy item={item} /><span className="dw-item-actions">{needsAgendaDetail(item) ? <button type="button" className="dw-button" onClick={() => showAgenda(item)}>{item.agenda_data.is_manual ? '查看待办' : '查看详情'}</button> : <a href={item.href} className="dw-button">{item.action_label || '查看'}</a>}{manualControls(item)}</span></li>)}</ul> : null}
          </>}
        </div>
        {view === 'items' && result.total > PAGE_SIZE ? <nav className="dw-pagination" aria-label="事项分页"><button type="button" className="dw-button" disabled={loading || page === 0} onClick={() => { setPage((value) => value - 1); scrollPosition.current = 0; }}>上一页</button><span>第 {page + 1} / {Math.ceil(result.total / PAGE_SIZE)} 页</span><button type="button" className="dw-button" disabled={loading || (page + 1) * PAGE_SIZE >= result.total} onClick={() => { setPage((value) => value + 1); scrollPosition.current = 0; }}>下一页</button></nav> : null}
      </DialogContent>
    </Dialog>
  </>;
}

mountReactIslandsWhenReady({
  islandName: 'dashboard-workspace',
  getProps: (mountPoint) => ({ initial: normalizeWorkspace(readIslandJsonPayload(mountPoint, '[data-dashboard-workspace-payload]')) }),
  render: (props) => <DashboardWorkspace {...props} />,
});
