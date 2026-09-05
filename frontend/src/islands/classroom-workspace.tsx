import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { classroomReadiness } from '@/lib/classroom-bootstrap-ready';
import { classroomMaterialUrl, materialScope, parseClassroomDate, taskConstraintLabel, taskDeadlineLabel, taskMatchesFilter, taskPresentation, taskPreview, type ClassroomSession, type ClassroomTask } from '@/lib/classroom-workspace';

type Panel = 'tasks' | 'materials' | 'timeline' | 'session-materials' | 'session-detail' | 'material-detail';
type Material = { material_id: number; name: string; type_label?: string; open_url?: string; ai_blurb?: string };
type SavedState = { panel?: Panel; filter?: string; query?: string; scroll?: number; restore?: boolean; sessionOrder?: string | number };
const labels: Record<Panel, string> = { tasks: '全部课堂任务', materials: '全部课堂材料', timeline: '全部课次', 'session-materials': '课次材料', 'session-detail': '课次详情', 'material-detail': '材料详情' };
const sources: Partial<Record<Panel, string>> = {
  tasks: '[data-cw-source="tasks"]', materials: '[data-cw-source="materials"]', timeline: '[data-cw-source="timeline"]',
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
  const storageKey = `classroom-workspace:${classroomId}:${(config.userInfo as { id?: number })?.id || ''}`;
  const saved = useRef<SavedState>({});
  const [tasks, setTasks] = useState<ClassroomTask[]>((config.assignmentWorkspaceItems || []) as ClassroomTask[]);
  const [session, setSession] = useState<ClassroomSession | null>(plan?.anchor_session || sessions.find(item => item.is_anchor) || sessions[0] || null);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [materials, setMaterials] = useState<Material[]>([]);
  const [materialState, setMaterialState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [revision, setRevision] = useState(0);
  const [pendingEditor, setPendingEditor] = useState<string | null>(null);
  const materialCache = useRef(new Map<number, Material[]>());
  const opener = useRef<HTMLElement | null>(null);
  const returnPanel = useRef<Panel | null>(null);
  const scrollPositions = useRef<Partial<Record<Panel, number>>>({});
  const suppressRestoreFocus = useRef(false);
  const externalReturn = useRef<Panel | null>(null);
  const [restoreScroll, setRestoreScroll] = useState(0);
  const scope = materialScope(session);
  const preview = taskPreview(tasks, teacher);

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
    if (saved.current.restore) {
      config.workspaceSelectedOrder = saved.current.sessionOrder;
      setFilter(saved.current.filter || 'all'); setQuery(saved.current.query || '');
      setRestoreScroll(saved.current.scroll || 0); setPanel(saved.current.panel || 'tasks');
      saved.current.restore = false;
      try { sessionStorage.setItem(storageKey, JSON.stringify(saved.current)); } catch { /* optional */ }
    }
    const selected = (event: Event) => setSession((event as CustomEvent<ClassroomSession>).detail);
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
    const changed = () => { materialCache.current.clear(); setRevision(value => value + 1); };
    document.addEventListener('classroom:session-selected', selected);
    document.addEventListener('classroom:assignment-time-states', times);
    document.addEventListener('classroom:materials-changed', changed);
    const pageShown = (event: PageTransitionEvent) => { if (event.persisted) consumeReturnState(); };
    window.addEventListener('pageshow', pageShown);
    return () => {
      document.removeEventListener('classroom:session-selected', selected);
      document.removeEventListener('classroom:assignment-time-states', times);
      document.removeEventListener('classroom:materials-changed', changed);
      window.removeEventListener('pageshow', pageShown);
    };
  }, [storageKey]);

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
      if (target?.closest('[data-assignment-link], a[href^="/assignment/"]')) {
        const state: SavedState = { panel: 'tasks', filter, query, scroll: document.querySelector('.cw-dialog-scroll')?.scrollTop || 0, restore: true, sessionOrder: session?.order_index };
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
      const next = (event as CustomEvent<{ panel: Panel | null; back?: boolean }>).detail;
      if (next.back) { setRestoreScroll(returnPanel.current ? scrollPositions.current[returnPanel.current] || 0 : 0); setPanel(returnPanel.current); returnPanel.current = null; return; }
      if (next.panel === 'material-detail' || next.panel === 'session-detail') returnPanel.current = panel;
      open(next.panel as Panel);
    };
    document.addEventListener('click', click, true);
    document.addEventListener('classroom:workspace-panel', request);
    const externalClosed = () => {
      if (!externalReturn.current) return;
      const previous = externalReturn.current; externalReturn.current = null;
      setRestoreScroll(scrollPositions.current[previous] || 0); setPanel(previous);
    };
    document.addEventListener('classroom:group-config-closed', externalClosed);
    return () => { document.removeEventListener('click', click, true); document.removeEventListener('classroom:workspace-panel', request); document.removeEventListener('classroom:group-config-closed', externalClosed); };
  }, [panel, filter, query, session, storageKey]);

  useEffect(() => {
    if (scope === null) { setMaterials([]); setMaterialState('ready'); return; }
    const cached = materialCache.current.get(scope);
    if (cached) { setMaterials(cached); setMaterialState('ready'); return; }
    const controller = new AbortController();
    setMaterials([]); setMaterialState('loading');
    fetch(`/api/classrooms/${classroomId}/learning-materials?session_id=${scope}&generate_blurbs=false`, { credentials: 'same-origin', signal: controller.signal })
      .then(response => { if (!response.ok) throw new Error(String(response.status)); return response.json(); })
      .then((data: { materials?: Material[] }) => {
        if (controller.signal.aborted) return;
        const entries = data.materials || [];
        materialCache.current.set(scope, entries); setMaterials(entries); setMaterialState('ready');
      }).catch(() => { if (!controller.signal.aborted) setMaterialState('error'); });
    return () => controller.abort();
  }, [classroomId, scope, revision]);

  const renderMaterials = (all = false) => {
    if (materialState === 'loading') return <p className="cw-empty" role="status">正在读取材料…</p>;
    if (materialState === 'error') return <p className="cw-empty" role="alert">材料读取失败。<button className="cw-text-button" onClick={() => setRevision(value => value + 1)}>重试</button></p>;
    if (!materials.length) return <p className="cw-empty">{scope === null ? session?.is_academic_exam || session?.entry_type === 'academic_exam' ? '教务考试安排不关联课次材料。' : '尚未安排课次，可浏览全部课堂材料。' : teacher ? '尚未绑定材料，可通过“管理课次”添加。' : '本课次尚未配置学习材料。'}</p>;
    return <ul className="cw-rows">{(all ? materials : materials.slice(0, 3)).map(material => {
      const href = classroomMaterialUrl(material.open_url || '', classroomId, scope);
      return <li className="cw-row" key={material.material_id}><div className="cw-row-copy"><strong>{material.name}</strong>{all && material.ai_blurb && <span>{material.ai_blurb}</span>}</div><span className="cw-meta">{material.type_label || '文档'}</span>{href ? <a className="cw-text-button" href={href} target="_blank" rel="noopener">阅读<span className="cw-sr-only"> {material.name}（新窗口）</span> ↗</a> : <span className="cw-meta">暂不可打开</span>}</li>;
    })}</ul>;
  };
  const taskTarget = document.getElementById('cw-tasks-preview');
  const materialTarget = document.getElementById('cw-materials-preview');
  return <>
    {taskTarget && createPortal(<><div className="cw-section-head"><div className="cw-section-title"><h2>{teacher ? '待处理任务' : '课堂任务'}{preview.actionableCount > 0 && <span className="cw-count">{preview.actionableCount}</span>}</h2><WorkspaceExplanation title="课堂任务" text="这里汇总整个课堂的待处理任务，不随所选课次筛选。已提交、已关闭与历史记录均在全部任务中。" /></div><button className="cw-text-button" onClick={() => open('tasks')}>全部任务（{preview.totalCount}） →</button></div>
      {!preview.actionableCount ? <p className="cw-empty">{preview.totalCount ? '当前没有待处理任务，已提交与历史任务可在全部任务中查看。' : teacher ? '尚未布置课堂任务。' : '老师尚未发布课堂任务。'}</p>
        : <ul className="cw-rows">{preview.rows.map(task => { const state = taskPresentation(task, teacher); const deadline = taskDeadlineLabel(task); return <li className="cw-row" key={task.id}><span className={`cw-task-status${state.rank < 2 ? ' is-attention' : ''}`}>{state.status}</span><div className="cw-row-copy"><strong>{task.title}</strong><span>{task.kind === 'exam' ? '考试' : '作业'}{deadline ? ` · ${deadline} ${task.canResubmit ? '重交截止' : '截止'}` : ''}</span>{taskConstraintLabel(task) && <span className="cw-constraint">{taskConstraintLabel(task)}</span>}</div><a className="cw-text-button" data-assignment-link={`/assignment/${task.id}`} href={`/assignment/${task.id}`}>{state.action} →</a></li>; })}</ul>}
      {preview.urgentOverflow > 0 && <button className="cw-overflow" onClick={() => { setFilter('urgent'); setQuery(''); open('tasks'); }}>还有 {preview.urgentOverflow} 项将在 24 小时内截止，查看紧急任务</button>}
      {teacher && <div className="cw-secondary-actions"><button className="cw-text-button" disabled={pendingEditor !== null} aria-busy={pendingEditor === '[data-cw-create-assignment]'} onClick={() => void openEditor('[data-cw-create-assignment]')}>新建作业</button><button className="cw-text-button" disabled={pendingEditor !== null} aria-busy={pendingEditor === '[data-cw-assign-exam]'} onClick={() => void openEditor('[data-cw-assign-exam]')}>从试卷库添加</button>{pendingEditor && <span className="cw-meta" role="status">正在准备编辑工具…</span>}</div>}
    </>, taskTarget)}
    {materialTarget && createPortal(<><div className="cw-section-head"><div className="cw-section-title"><h2>{scope === 0 ? '课程首页材料' : '课次材料'}</h2><WorkspaceExplanation title="材料范围" text="当前展示所选课次绑定的材料；全部课堂材料包含教师为本课堂分配的目录与文件。" /></div><button className="cw-text-button" onClick={() => open('materials')}>全部课堂材料 →</button></div>
      {scope !== null && <p className="cw-scope">{session?.session_number_label || session?.detail_title || session?.title}</p>}{renderMaterials()}
      {scope !== null && <div className="cw-secondary-actions"><button className="cw-text-button" onClick={() => open('session-materials')}>本{scope === 0 ? '首页' : '课次'}全部材料（{materialState === 'ready' ? materials.length : '…'}） →</button></div>}
    </>, materialTarget)}
    <Dialog open={panel !== null} onOpenChange={value => { if (!value) close(); }}>
      <DialogContent className="cw-dialog classroom-page classroom-workspace-v2" onOpenAutoFocus={event => {
        if (panel === 'tasks') {
          const filterControl = document.querySelector<HTMLElement>('.cw-dialog .cw-filterbar select');
          if (filterControl) { event.preventDefault(); filterControl.focus({ preventScroll: true }); }
        }
        if (panel === 'timeline') {
          const selected = document.querySelector<HTMLElement>('.cw-dialog [data-session-select][aria-pressed="true"]');
          if (selected) { event.preventDefault(); selected.focus({ preventScroll: true }); selected.scrollIntoView({ block: 'nearest' }); }
        }
      }} onCloseAutoFocus={event => { event.preventDefault(); if (!suppressRestoreFocus.current) opener.current?.focus({ preventScroll: true }); suppressRestoreFocus.current = false; }}>
        <div className="cw-dialog-heading"><DialogTitle>{panel ? labels[panel] : '课堂工作区'}</DialogTitle><DialogDescription>{panel === 'tasks' ? '本课堂全部已授权任务，包含已提交、已截止和历史记录。' : panel === 'materials' ? '课堂材料目录，保留目录导航、预览和下载权限。' : panel === 'timeline' ? '选择课次后返回课堂；课程首页与教务考试分别标识。' : session?.detail_title || session?.title || '查看详细信息'}</DialogDescription></div>
        {(panel === 'material-detail' || panel === 'session-detail') && returnPanel.current && <button className="cw-text-button cw-back" onClick={() => { setRestoreScroll(scrollPositions.current[returnPanel.current!] || 0); setPanel(returnPanel.current); returnPanel.current = null; }}>← 返回列表</button>}
        {panel === 'tasks' && <div className="cw-filterbar"><label>任务状态<select value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部（{tasks.length}）</option><option value="actionable">待处理（{preview.actionableCount}）</option><option value="urgent">24 小时内截止（{preview.urgentCount}）</option>{teacher ? <option value="draft">草稿</option> : <option value="submitted">已提交 / 已批改</option>}<option value="closed">已关闭</option></select></label><label className="cw-search-label">查找任务<input value={query} onChange={event => setQuery(event.target.value)} type="search" placeholder="输入任务名称" /></label></div>}
        <div className="cw-dialog-scroll">{panel === 'tasks' && !tasks.some(task => taskMatchesFilter(task, teacher, filter, query)) && <p className="cw-empty" role="status">没有符合当前筛选条件的任务。</p>}{panel === 'session-materials' ? <>{renderMaterials(true)}{teacher && scope !== null && <button className="cw-text-button" onClick={() => { const returnFocus = opener.current; close(); window.setTimeout(() => document.dispatchEvent(new CustomEvent('classroom:manage-session-materials', { detail: { returnFocus } })), 0); }}>管理本课次材料</button>}</> : panel && <ExistingSurface panel={panel} filter={filter} query={query} tasks={tasks} teacher={teacher} restoreScroll={restoreScroll} />}</div>
      </DialogContent>
    </Dialog>
  </>;
}
