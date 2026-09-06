import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { classroomReadiness } from '@/lib/classroom-bootstrap-ready';
import { parseClassroomDate, taskConstraintLabel, taskDeadlineLabel, taskMatchesFilter, taskPresentation, taskPreview, taskHistory, type ClassroomSession, type ClassroomTask } from '@/lib/classroom-workspace';

type Panel = 'tasks' | 'materials' | 'timeline' | 'session-detail' | 'material-detail';
type SavedState = { panel?: Panel; filter?: string; query?: string; scroll?: number; restore?: boolean; sessionOrder?: string | number; previewFilter?: string; taskId?: string; openerKind?: 'history' | 'tasks'; returnPanel?: Panel; returnScroll?: number; timelineQuery?: string };
const labels: Record<Panel, string> = { tasks: '全部课堂任务', materials: '全部课堂材料', timeline: '全部课次', 'session-detail': '课次详情', 'material-detail': '材料详情' };
const sources: Partial<Record<Panel, string>> = {
  tasks: '[data-cw-source="tasks"]', materials: '[data-cw-source="materials"]',
  'session-detail': '#teachingSessionModal .teaching-session-modal-body',
  'material-detail': '#classroom-material-detail-modal .modal-content',
};

function WorkspaceExplanation({ title, text }: { title: string; text: string }) {
  return <button type="button" className="ui-explain-trigger cw-explain-button" aria-label={`${title}说明`} aria-haspopup="dialog"
    data-explain="" data-explain-toggle="" data-explain-title={title} data-explain-text={text}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4" /><path d="M12 17h.01" /></svg>
  </button>;
}

/** Move the actual business surface into one Radix shell, then restore it on close.
 * No cloned controls, duplicate IDs, or remounted legacy controllers. */
function ExistingSurface({ panel, filter, query, tasks, teacher, restoreScroll }: { panel: Panel; filter: string; query: string; tasks: ClassroomTask[]; teacher: boolean; restoreScroll: number }) {
  const host = useRef<HTMLDivElement>(null);
  const filterTasks = (node: HTMLElement) => {
    if (panel !== 'tasks') return;
    const byId = new Map(tasks.map(task => [String(task.id), task]));
    node.querySelectorAll<HTMLElement>('[data-assignment-task-card]').forEach(card => {
      const task = byId.get(card.dataset.assignmentId || '');
      if (!task) return;
      const hidden = !taskMatchesFilter(task, teacher, filter, query);
      if (card.hidden !== hidden) card.hidden = hidden;
      const status = card.querySelector<HTMLElement>('.assignment-card-tags .badge');
      const label = taskPresentation(task, teacher).status;
      if (status && task.submissionStatus !== 'graded' && status.textContent !== label) status.textContent = label;
    });
  };
  useLayoutEffect(() => {
    const selector = sources[panel];
    const node = selector ? document.querySelector<HTMLElement>(selector) : null;
    if (!host.current || !node) return;
    const parent = node.parentNode;
    const next = node.nextSibling;
    const hidden = node.hidden;
    // Filter while detached from layout, then reveal once in its final parent.
    // Writing scrollTop=0 here used to force layout of every business card
    // before Radix had finished applying its modal/scroll-lock styles.
    if (panel === 'tasks') {
      const cards = new Map([...node.querySelectorAll<HTMLElement>('[data-assignment-task-card]')].map(card => [String(card.dataset.assignmentId), card]));
      taskHistory(tasks).forEach(task => { const card = cards.get(String(task.id)); if (card?.parentElement) card.parentElement.appendChild(card); });
    }
    filterTasks(node);
    host.current.appendChild(node);
    node.classList.add('cw-secondary-content');
    node.hidden = false;
    const scroll = host.current.closest<HTMLElement>('.cw-dialog-scroll');
    if (scroll && restoreScroll > 0) scroll.scrollTop = restoreScroll;
    document.dispatchEvent(new CustomEvent('classroom:workspace-surface-visible', { detail: { panel } }));
    return () => {
      node.classList.remove('cw-secondary-content');
      node.hidden = hidden;
      if (parent) parent.insertBefore(node, next?.parentNode === parent ? next : null);
    };
  }, [panel, restoreScroll]);
  useLayoutEffect(() => {
    if (host.current) filterTasks(host.current);
  }, [panel, filter, query, tasks, teacher]);
  return <div ref={host} className="cw-existing-surface" />;
}

export function ClassroomWorkspace() {
  const config = window.APP_CONFIG || {};
  const teacher = config.userRole === 'teacher';
  const classroomId = String(config.classOfferingId || '');
  const plan = config.teachingPlan as { timeline_entries?: ClassroomSession[]; sessions?: ClassroomSession[]; anchor_session?: ClassroomSession } | undefined;
  const sessions = plan?.timeline_entries || plan?.sessions || [];
  const storageKey = `classroom-workspace:${teacher ? 'teacher' : 'student'}:${classroomId}:${(config.userInfo as { id?: number })?.id || ''}`;
  const saved = useRef<SavedState>({});
  const [tasks, setTasks] = useState<ClassroomTask[]>((config.assignmentWorkspaceItems || []) as ClassroomTask[]);
  const [session, setSession] = useState<ClassroomSession | null>(plan?.anchor_session || sessions.find(item => item.is_anchor) || sessions[0] || null);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [previewFilter, setPreviewFilter] = useState('actionable');
  const [timelineQuery, setTimelineQuery] = useState('');
  const [pendingEditor, setPendingEditor] = useState<string | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const returnPanel = useRef<Panel | null>(null);
  const scrollPositions = useRef<Partial<Record<Panel, number>>>({});
  const suppressRestoreFocus = useRef(false);
  const externalReturn = useRef<Panel | null>(null);
  const [restoreScroll, setRestoreScroll] = useState(0);
  const preview = taskPreview(tasks, teacher);
  const history = taskHistory(tasks);
  const previewTasks = previewFilter === 'actionable' ? preview.rows : history.filter(task => taskMatchesFilter(task, teacher, previewFilter, '')).slice(0, 4);
  const previewCount = tasks.filter(task => taskMatchesFilter(task, teacher, previewFilter, '')).length;
  const indexSessions = sessions.filter(item => [item.session_number_label, item.detail_title, item.session_date, item.detail_meta].join(' ').toLowerCase().includes(timelineQuery.trim().toLowerCase()));
  const openHistory = () => { setFilter('all'); setQuery(''); open('tasks'); };

  const openEditor = async (selector: string) => {
    if (pendingEditor) return;
    setPendingEditor(selector);
    try {
      await classroomReadiness.wait();
      document.querySelector<HTMLButtonElement>(selector)?.click();
    } catch {
      window.UI?.showToast?.('课堂编辑工具尚未就绪，请刷新重试。', 'error');
    } finally {
      setPendingEditor(null);
    }
  };

  useLayoutEffect(() => {
    document.querySelectorAll('[data-cw-loading]').forEach(node => node.remove());
  }, []);



  const open = (next: Panel, trigger?: HTMLElement | null) => {
    if (!panel) opener.current = trigger || (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    if (panel) scrollPositions.current[panel] = document.querySelector('.cw-dialog-scroll')?.scrollTop || 0;
    setRestoreScroll(scrollPositions.current[next] || 0);
    setPanel(next);
  };
  const consumeReturnState = () => {
    try {
      const state = JSON.parse(sessionStorage.getItem(storageKey) || '{}') as SavedState;
      state.restore = false; sessionStorage.setItem(storageKey, JSON.stringify(state));
    } catch { /* storage is optional */ }
  };
  const close = () => { setPanel(null); returnPanel.current = null; consumeReturnState(); };

  useEffect(() => {
    try { saved.current = JSON.parse(sessionStorage.getItem(storageKey) || '{}') as SavedState; } catch { /* storage is optional */ }
    config.workspaceSelectedOrder = saved.current.sessionOrder;
    if (saved.current.restore) {
      opener.current = document.querySelector<HTMLElement>(saved.current.openerKind === 'history' ? '[data-cw-history]' : '[data-cw-task-collection]');
      setFilter(saved.current.filter || 'all'); setQuery(saved.current.query || ''); setPreviewFilter(saved.current.previewFilter || 'actionable');
      setRestoreScroll(saved.current.scroll || 0); returnPanel.current = saved.current.returnPanel || null;
      setTimelineQuery(saved.current.timelineQuery || '');
      if (saved.current.panel) scrollPositions.current[saved.current.panel] = saved.current.scroll || 0;
      if (saved.current.returnPanel) scrollPositions.current[saved.current.returnPanel] = saved.current.returnScroll || 0;
      if (saved.current.panel === 'session-detail') {
        const order = saved.current.sessionOrder;
        void classroomReadiness.wait().then(() => document.dispatchEvent(new CustomEvent('classroom:select-session', { detail: { order, resume: true } }))).catch(() => window.UI?.showToast?.('课堂详情尚未就绪，请刷新重试。', 'error'));
      } else setPanel(saved.current.panel || 'tasks');
      saved.current.restore = false;
      try { sessionStorage.setItem(storageKey, JSON.stringify(saved.current)); } catch { /* optional */ }
    }
    const selected = (event: Event) => {
      const value = (event as CustomEvent<ClassroomSession>).detail; setSession(value);
      try { const previous = JSON.parse(sessionStorage.getItem(storageKey) || '{}'); sessionStorage.setItem(storageKey, JSON.stringify({ ...previous, sessionOrder: value?.order_index })); } catch { /* optional */ }
    };
    const times = (event: Event) => {
      const states = (event as CustomEvent<Map<string, Record<string, unknown>>>).detail;
      setTasks(previous => {
        let changed = false;
        const next = previous.map(task => {
          const state = states.get(String(task.id));
          if (!state) return task;
          const patch = { accepting: Boolean(state.is_accepting_submissions), lateOpen: Boolean(state.is_late_submission_open),
            latePolicyLabel: String(state.late_policy_label || ''), deadlinePhase: String(state.deadline_phase || ''), countdownAt: String(state.countdown_at || '') };
          if (task.accepting === patch.accepting && task.lateOpen === patch.lateOpen && task.deadlinePhase === patch.deadlinePhase && task.countdownAt === patch.countdownAt && task.latePolicyLabel === patch.latePolicyLabel) return task;
          changed = true; return { ...task, ...patch };
        });
        return changed ? next : previous;
      });
    };
    document.addEventListener('classroom:session-selected', selected);
    document.addEventListener('classroom:assignment-time-states', times);
    const pageShown = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      try { const previous = JSON.parse(sessionStorage.getItem(storageKey) || '{}') as SavedState;
        if (previous.restore && previous.panel === 'session-detail') document.dispatchEvent(new CustomEvent('classroom:select-session', { detail: { order: previous.sessionOrder, resume: true } }));
      } catch { /* optional */ }
      consumeReturnState();
    };
    window.addEventListener('pageshow', pageShown);
    return () => {
      document.removeEventListener('classroom:session-selected', selected);
      document.removeEventListener('classroom:assignment-time-states', times);
      window.removeEventListener('pageshow', pageShown);
    };
  }, [storageKey]);

  useLayoutEffect(() => {
    if (panel === 'timeline') {
      const scroll = document.querySelector<HTMLElement>('.cw-dialog-scroll');
      if (scroll) scroll.scrollTop = restoreScroll;
      document.querySelector<HTMLElement>('.cw-dialog [data-cw-session-order][aria-pressed="true"]')?.focus({ preventScroll: true });
    }
  }, [panel, restoreScroll]);

  useEffect(() => {
    // One lightweight clock for time-derived presentation, including individual
    // resubmission windows that do not share the assignment's ordinary deadline.
    const refreshTime = () => { if (!document.hidden) setTasks(previous => [...previous]); };
    const now = Date.now();
    const boundaries = tasks.flatMap(task => [
      parseClassroomDate(task.resubmissionDueAt),
      parseClassroomDate(task.countdownAt) - 86400000,
    ]).filter(value => Number.isFinite(value) && value > now);
    const nextBoundary = Math.min(now + 60000, ...boundaries);
    const timer = window.setTimeout(refreshTime, Math.max(25, nextBoundary - now + 25));
    document.addEventListener('visibilitychange', refreshTime);
    return () => { window.clearTimeout(timer); document.removeEventListener('visibilitychange', refreshTime); };
  }, [tasks]);

  useEffect(() => {
    const click = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      const trigger = target?.closest<HTMLElement>('[data-cw-open]');
      if (trigger && trigger.dataset.cwOpen && trigger.dataset.cwOpen in labels) open(trigger.dataset.cwOpen as Panel, trigger);
      if (!event.ctrlKey && !event.metaKey && !event.shiftKey && target?.closest('[data-assignment-link], a[href^="/assignment/"]')) {
        const state: SavedState = { panel: 'tasks', filter, query, scroll: document.querySelector('.cw-dialog-scroll')?.scrollTop || 0, restore: true, sessionOrder: session?.order_index, previewFilter, taskId: target?.closest<HTMLElement>('[data-assignment-id]')?.dataset.assignmentId, openerKind: opener.current?.hasAttribute('data-cw-history') ? 'history' : 'tasks' };
        try { sessionStorage.setItem(storageKey, JSON.stringify(state)); } catch { /* optional */ }
      }
      // Existing editors are independent modals. Relinquish the Radix trap before they open.
      if (panel && target?.closest('[data-cw-external-modal]')) {
        scrollPositions.current[panel] = document.querySelector('.cw-dialog-scroll')?.scrollTop || 0;
        if (target.closest('[data-group-config-btn]')) externalReturn.current = panel;
        suppressRestoreFocus.current = true; close();
      }
    };
    const request = (event: Event) => {
      const next = (event as CustomEvent<{ panel: Panel | null; back?: boolean; origin?: HTMLElement; handoff?: boolean; resume?: boolean }>).detail;
      if (next.handoff) { suppressRestoreFocus.current = true; setPanel(null); return; }
      if (next.back) { setRestoreScroll(returnPanel.current ? scrollPositions.current[returnPanel.current] || 0 : 0); setPanel(returnPanel.current); returnPanel.current = null; return; }
      if (!next.resume && next.panel !== panel && (next.panel === 'material-detail' || next.panel === 'session-detail')) returnPanel.current = panel;
      if (!next.panel) { close(); return; }
      open(next.panel, next.origin);
    };
    const navigate = (event: Event) => {
      const detail = (event as CustomEvent<{ panel: Panel; sessionOrder?: string | number }>).detail;
      const state: SavedState = { panel: detail.panel, sessionOrder: detail.sessionOrder, filter, query, previewFilter, timelineQuery, scroll: document.querySelector('.cw-dialog-scroll')?.scrollTop || 0, restore: true, returnPanel: returnPanel.current || undefined, returnScroll: returnPanel.current ? scrollPositions.current[returnPanel.current] || 0 : 0 };
      try { sessionStorage.setItem(storageKey, JSON.stringify(state)); } catch { /* optional */ }
    };
    document.addEventListener('classroom:workspace-navigate', navigate);
    document.addEventListener('click', click, true);
    document.addEventListener('classroom:workspace-panel', request);
    const externalClosed = () => {
      if (!externalReturn.current) return;
      const previous = externalReturn.current; externalReturn.current = null;
      setRestoreScroll(scrollPositions.current[previous] || 0); setPanel(previous);
    };
    document.addEventListener('classroom:group-config-closed', externalClosed);
    return () => { document.removeEventListener('classroom:workspace-navigate', navigate); document.removeEventListener('click', click, true); document.removeEventListener('classroom:workspace-panel', request); document.removeEventListener('classroom:group-config-closed', externalClosed); };
  }, [panel, filter, query, session, storageKey, previewFilter, timelineQuery]);

  const taskTarget = document.getElementById('cw-tasks-preview');
  return <>
    {taskTarget && createPortal(<>
      <div className="cw-section-head"><div className="cw-section-title"><h2>{teacher ? '待处理任务' : '作业与考试'}</h2><WorkspaceExplanation title="课堂任务" text="整个课堂的作业与考试，不随所选课次筛选。已提交、已关闭与历史记录均可回看。" /></div><button type="button" className="cw-button" data-cw-history="" onClick={openHistory}>历史作业与考试</button></div>
      <p className="cw-task-scope">整个课堂 · 共 {tasks.length} 项</p>
      <div className="cw-task-tabs" role="group" aria-label="任务筛选">{[['actionable', '待处理'], [teacher ? 'draft' : 'submitted', teacher ? '草稿' : '已提交与结果'], ['all', '全部']].map(([key, label]) => <button type="button" key={key} aria-pressed={previewFilter === key} onClick={() => setPreviewFilter(key)}>{label} <span>{tasks.filter(task => taskMatchesFilter(task, teacher, key, '')).length}</span></button>)}</div>
      {!previewTasks.length ? <p className="cw-empty">{!tasks.length ? teacher ? '尚未布置课堂任务。' : '老师尚未发布课堂任务。' : previewFilter === 'actionable' ? '目前没有待处理任务，已提交与历史记录仍可查看。' : '当前筛选没有任务，可切换全部或查看历史。'}</p>
        : <ul className="cw-task-cards" key={previewFilter}>{previewTasks.map(task => { const state = taskPresentation(task, teacher); const deadline = taskDeadlineLabel(task); const href = `/assignment/${task.id}`; return <li className="cw-task-card" key={task.id} data-assignment-id={task.id}><div className="cw-task-card-head"><span className="cw-task-kind">{task.kind === 'exam' ? '考试' : '作业'}</span><span className={`cw-task-status${state.actionable ? ' is-attention' : ''}`}>{state.status}</span></div><a className="cw-task-title" data-assignment-link={href} href={href}>{task.title}</a><div className="cw-task-card-foot"><div><span className="cw-meta">{deadline ? `${deadline} ${task.canResubmit ? '重交截止' : '截止'}` : '未设置截止时间'}</span>{taskConstraintLabel(task) && <p className="cw-constraint">{taskConstraintLabel(task)}</p>}</div><a className={`cw-button${state.actionable ? ' is-primary' : ''}`} data-assignment-link={href} href={href}>{state.action} →</a></div></li>; })}</ul>}
      {previewFilter === 'actionable' && preview.urgentOverflow > 0 && <button type="button" className="cw-overflow" onClick={() => { setFilter('urgent'); setQuery(''); open('tasks'); }}>还有 {preview.urgentOverflow} 项将在 24 小时内截止，查看紧急任务</button>}
      <div className="cw-secondary-actions"><button type="button" className="cw-button" data-cw-task-collection="" onClick={() => { setFilter(previewFilter); setQuery(''); open('tasks'); }}>全部任务（{previewCount}） →</button>
      {teacher && <><button type="button" className="cw-button is-primary" disabled={pendingEditor !== null} aria-busy={pendingEditor === '[data-cw-create-assignment]'} onClick={() => void openEditor('[data-cw-create-assignment]')}>新建作业</button><button type="button" className="cw-button" disabled={pendingEditor !== null} aria-busy={pendingEditor === '[data-cw-assign-exam]'} onClick={() => void openEditor('[data-cw-assign-exam]')}>从试卷库添加</button>{pendingEditor && <span className="cw-meta" role="status">正在准备编辑工具…</span>}</>}
      </div>
    </>, taskTarget)}
    <Dialog open={panel !== null} onOpenChange={value => { if (!value) close(); }}>
      <DialogContent className="cw-dialog classroom-page classroom-workspace-v2" onOpenAutoFocus={event => {
        if (panel === 'tasks') {
          if (saved.current.taskId) { const card = document.querySelector<HTMLElement>(`.cw-dialog [data-assignment-task-card][data-assignment-id="${saved.current.taskId}"]`); if (card && !card.hidden) { event.preventDefault(); card.focus({ preventScroll: true }); saved.current.taskId = undefined; return; } }
          const filterControl = document.querySelector<HTMLElement>('.cw-dialog .cw-filterbar select');
          if (filterControl) { event.preventDefault(); filterControl.focus({ preventScroll: true }); }
        }
        if (panel === 'timeline') {
          const selected = document.querySelector<HTMLElement>('.cw-dialog [data-cw-session-order][aria-pressed="true"]');
          if (selected) { event.preventDefault(); selected.focus({ preventScroll: true }); selected.scrollIntoView({ block: 'nearest' }); }
        }
      }} onCloseAutoFocus={event => { event.preventDefault(); if (!suppressRestoreFocus.current) opener.current?.focus({ preventScroll: true }); suppressRestoreFocus.current = false; document.dispatchEvent(new CustomEvent('classroom:workspace-closed')); }}>
        <div className="cw-dialog-heading"><DialogTitle>{panel ? labels[panel] : '课堂工作区'}</DialogTitle><DialogDescription>{panel === 'tasks' ? '本课堂全部已授权任务，包含已提交、已截止和历史记录。' : panel === 'materials' ? '课堂材料目录，保留目录导航、预览和下载权限。' : panel === 'timeline' ? '选择课次查看完整详情与材料；横向课次导航始终保留。' : session?.detail_title || session?.title || '查看详细信息'}</DialogDescription></div>
        {panel === 'material-detail' && returnPanel.current && <button type="button" className="cw-text-button cw-back" onClick={() => { setRestoreScroll(scrollPositions.current[returnPanel.current!] || 0); setPanel(returnPanel.current); returnPanel.current = null; }}>← 返回列表</button>}
        {panel === 'tasks' && <div className="cw-filterbar"><label>任务状态<select value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部（{tasks.length}）</option><option value="actionable">待处理（{preview.actionableCount}）</option><option value="urgent">24 小时内截止（{preview.urgentCount}）</option>{teacher ? <option value="draft">草稿</option> : <option value="submitted">已提交 / 已批改</option>}<option value="closed">已关闭</option></select></label><label className="cw-search-label">查找任务<input value={query} onChange={event => setQuery(event.target.value)} type="search" placeholder="输入任务名称" /></label></div>}
        {panel === 'timeline' && <label className="cw-filterbar">查找课次<input type="search" value={timelineQuery} onChange={event => setTimelineQuery(event.target.value)} placeholder="课次、标题或日期" /></label>}
        <div className="cw-dialog-scroll">
          {panel === 'tasks' && !tasks.some(task => taskMatchesFilter(task, teacher, filter, query)) && <p className="cw-empty" role="status">没有符合当前筛选条件的任务。</p>}
          {panel === 'timeline' && !indexSessions.length && <p className="cw-empty" role="status">没有匹配的课次，请更换搜索内容。</p>}
          {panel === 'timeline' ? <div className="cw-timeline-index">{indexSessions.map(item => <button type="button" key={String(item.order_index)} className="cw-timeline-index-item" data-cw-session-order={item.order_index} aria-pressed={String(item.order_index) === String(session?.order_index)} aria-haspopup="dialog" onClick={() => document.dispatchEvent(new CustomEvent('classroom:select-session', { detail: { order: item.order_index } }))}><span>{item.session_number_label}</span><strong>{item.segment_title || item.detail_title || item.title}</strong><small>{item.session_date || item.session_status_label}</small></button>)}</div> : panel && <ExistingSurface panel={panel} filter={filter} query={query} tasks={tasks} teacher={teacher} restoreScroll={restoreScroll} />}
        </div>
      </DialogContent>
    </Dialog>
  </>;
}
