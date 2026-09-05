// Agenda reminder widget: clicking an item opens a detail popover anchored to
// it, with a button to jump to the related page. Keyboard + outside-click close.

const GO_LABELS = {
  invigilation: '前往监考安排',
  exam: '前往查看',
  assignment: '前往提交',
  todo: '前往处理',
  class: '前往课堂',
};

// Labelled facts shown for invigilation/exam reminders, in display order.
const FACT_FIELDS = [
  { key: 'subject', label: '科目' },
  { key: 'date', label: '日期' },
  { key: 'time', label: '时间' },
  { key: 'campus', label: '校区' },
  { key: 'classroom', label: '教室' },
  { key: 'teachingClass', label: '教学班' },
  { key: 'invigilators', label: '监考分工' },
  { key: 'role', label: '我的角色' },
];

const STRUCTURED_KINDS = new Set(['invigilation', 'exam']);

function buildPopover() {
  const pop = document.createElement('div');
  pop.className = 'agenda-popover';
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', '待办详情');
  pop.hidden = true;
  pop.innerHTML = `
    <button type="button" class="agenda-popover__close" data-pop-close aria-label="关闭">
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
    </button>
    <span class="agenda-popover__kind" data-pop-kind></span>
    <strong class="agenda-popover__title" data-pop-title></strong>
    <dl class="agenda-popover__facts" data-pop-facts hidden></dl>
    <p class="agenda-popover__subtitle" data-pop-subtitle></p>
    <div class="agenda-popover__meta">
      <span class="agenda-popover__when" data-pop-when></span>
      <span class="agenda-popover__relative" data-pop-relative></span>
    </div>
    <div class="agenda-popover__actions">
      <a class="agenda-popover__go" data-pop-go href="#">前往查看</a>
      <button type="button" class="agenda-popover__remind" data-pop-remind hidden>
        <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
        设置邮件提醒
      </button>
    </div>
    <form class="agenda-popover__remind-form" data-pop-remind-form hidden>
      <div class="agenda-popover__remind-row">
        <span>提前</span>
        <input type="number" min="1" max="999" value="30" inputmode="numeric" data-remind-value aria-label="提前时间数值" />
        <select data-remind-unit aria-label="提前时间单位">
          <option value="minute">分钟</option>
          <option value="hour">小时</option>
          <option value="day">天</option>
        </select>
        <span>发送邮件</span>
      </div>
      <div class="agenda-popover__remind-actions">
        <button type="submit" class="agenda-popover__remind-submit" data-remind-submit>确认</button>
        <button type="button" class="agenda-popover__remind-cancel" data-remind-cancel hidden>取消提醒</button>
      </div>
      <p class="agenda-popover__remind-status" data-remind-status role="status"></p>
    </form>
    <div class="agenda-popover__manage" data-pop-manage hidden>
      <button type="button" class="agenda-popover__manage-btn is-complete" data-pop-complete>
        <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>
        完成
      </button>
      <button type="button" class="agenda-popover__manage-btn" data-pop-edit>
        <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
        编辑
      </button>
      <button type="button" class="agenda-popover__manage-btn is-danger" data-pop-delete>
        <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="m19 6-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
        删除
      </button>
      <p class="agenda-popover__manage-status" data-manage-status role="status"></p>
    </div>
  `;
  document.body.appendChild(pop);
  return pop;
}

function renderFacts(factsEl, data) {
  const rows = FACT_FIELDS
    .map(({ key, label }) => ({ label, value: (data[key] || '').trim() }))
    .filter((row) => row.value);
  if (!rows.length) {
    factsEl.hidden = true;
    factsEl.innerHTML = '';
    return false;
  }
  factsEl.innerHTML = rows
    .map(
      (row) =>
        `<div class="agenda-popover__fact"><dt>${row.label}</dt><dd>${escapeHtml(row.value)}</dd></div>`,
    )
    .join('');
  factsEl.hidden = false;
  return true;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function trapModalTab(event, card) {
  if (event.key !== 'Tab') return;
  // The shared date picker is portalled to body; keep its controls reachable.
  const picker = document.querySelector('.ls-dp-pop:not(.is-closing)');
  const boundary = picker?.getClientRects().length ? picker : card;
  const controls = Array.from(boundary.querySelectorAll('a[href], button, input, select, textarea, summary, [tabindex="0"]'))
    .filter((node) => !node.disabled && node.getClientRects().length);
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (!first) return;
  if (event.shiftKey && (document.activeElement === first || !boundary.contains(document.activeElement))) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !boundary.contains(document.activeElement))) {
    event.preventDefault(); first.focus();
  }
}

function positionPopover(pop, anchor) {
  const margin = 8;
  const rect = anchor.getBoundingClientRect();
  pop.style.visibility = 'hidden';
  pop.hidden = false;
  const pw = pop.offsetWidth;
  const ph = pop.offsetHeight;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  if (vw <= 560) {
    // Bottom sheet on small screens.
    pop.style.left = `${Math.max(12, (vw - pw) / 2)}px`;
    pop.style.top = `${Math.max(12, vh - ph - 16)}px`;
  } else {
    let left = rect.left;
    if (left + pw > vw - margin) left = vw - pw - margin;
    if (left < margin) left = margin;
    let top = rect.bottom + margin;
    if (top + ph > vh - margin) top = Math.max(margin, rect.top - ph - margin);
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
  }
  pop.style.visibility = '';
}

function initAgendaWidget() {
  const pop = buildPopover();
  const kindEl = pop.querySelector('[data-pop-kind]');
  const titleEl = pop.querySelector('[data-pop-title]');
  const factsEl = pop.querySelector('[data-pop-facts]');
  const subtitleEl = pop.querySelector('[data-pop-subtitle]');
  const whenEl = pop.querySelector('[data-pop-when]');
  const relEl = pop.querySelector('[data-pop-relative]');
  const goEl = pop.querySelector('[data-pop-go]');
  const remindBtn = pop.querySelector('[data-pop-remind]');
  const remindForm = pop.querySelector('[data-pop-remind-form]');
  const remindValue = pop.querySelector('[data-remind-value]');
  const remindUnit = pop.querySelector('[data-remind-unit]');
  const remindRow = pop.querySelector('.agenda-popover__remind-row');
  const remindSubmit = pop.querySelector('[data-remind-submit]');
  const remindCancel = pop.querySelector('[data-remind-cancel]');
  const remindStatus = pop.querySelector('[data-remind-status]');
  const manageEl = pop.querySelector('[data-pop-manage]');
  const completeBtn = pop.querySelector('[data-pop-complete]');
  const editBtn = pop.querySelector('[data-pop-edit]');
  const deleteBtn = pop.querySelector('[data-pop-delete]');
  const manageStatus = pop.querySelector('[data-manage-status]');
  let activeItem = null;
  let activeData = null;
  let activeEndpoint = '';
  let activeEventId = '';

  const setStatus = (message, tone) => {
    remindStatus.textContent = message || '';
    remindStatus.dataset.tone = tone || '';
  };

  const formatReminderRunAt = (value) => {
    const text = String(value || '').trim();
    if (!text) return '';
    const date = new Date(text.replace(' ', 'T'));
    if (!Number.isNaN(date.getTime())) {
      const month = date.getMonth() + 1;
      const day = date.getDate();
      const hour = String(date.getHours()).padStart(2, '0');
      const minute = String(date.getMinutes()).padStart(2, '0');
      return `${month}月${day}日 ${hour}:${minute}`;
    }
    return text.replace('T', ' ').slice(0, 16);
  };

  const reminderSummaryText = (payload = {}) => {
    const parts = [];
    if (payload.lead_label) parts.push(`开始前 ${payload.lead_label}`);
    const runAt = formatReminderRunAt(payload.run_at);
    if (runAt) parts.push(`预计 ${runAt} 发送`);
    return parts.join('，') || '已设置邮件提醒';
  };

  const showReminderEditor = (message = '', tone = '') => {
    if (remindRow) remindRow.hidden = false;
    if (remindSubmit) remindSubmit.hidden = false;
    remindCancel.hidden = true;
    setStatus(message, tone);
  };

  const showReminderSummary = (payload = {}) => {
    if (remindRow) remindRow.hidden = true;
    if (remindSubmit) remindSubmit.hidden = true;
    remindCancel.hidden = false;
    setStatus(`已设置邮件提醒：${reminderSummaryText(payload)}。取消后可重新设置。`, 'success');
  };

  const collapseForm = () => {
    remindForm.hidden = true;
    remindBtn.setAttribute('aria-expanded', 'false');
  };

  const close = (restoreFocus = false) => {
    if (pop.hidden) return;
    pop.classList.remove('is-open');
    pop.hidden = true;
    collapseForm();
    if (activeItem) {
      activeItem.classList.remove('is-active');
      if (restoreFocus && activeItem.isConnected) activeItem.focus({ preventScroll: true });
    }
    activeItem = null;
    activeData = null;
  };

  const open = (item, suppliedData = null) => {
    if (activeItem === item && !suppliedData) {
      close();
      return;
    }
    const data = suppliedData || item.dataset;
    activeData = data;
    const kind = data.kind || 'todo';
    const structured = STRUCTURED_KINDS.has(kind);
    kindEl.textContent = data.kindLabel || '日程';
    kindEl.className = `agenda-popover__kind kind-${kind}`;
    titleEl.textContent = data.subject || data.title || '待办事项';

    const hasFacts = structured && renderFacts(factsEl, data);
    if (!hasFacts) factsEl.hidden = true;
    subtitleEl.textContent = data.subtitle || '';
    subtitleEl.hidden = hasFacts || !data.subtitle;

    whenEl.textContent = (data.when || '').trim();
    relEl.textContent = data.relative || '';
    relEl.hidden = !data.relative;

    activeEndpoint = item.closest('[data-agenda-reminder]')?.dataset.reminderEndpoint || '';
    activeEventId = data.eventId || '';
    const canRemind = Boolean(data.canReminder === '1' && activeEventId && activeEndpoint);

    showReminderEditor('', '');
    // Invigilation/exam: show the email-reminder form inline (the only action) —
    // no extra toggle button, no dead-end "前往学期日历" link. Other kinds keep
    // their jump link.
    remindBtn.hidden = true;
    remindForm.hidden = !canRemind;
    const isManual = data.manual === '1';
    if (manageEl) manageEl.hidden = !isManual;
    if (manageStatus) { manageStatus.textContent = ''; manageStatus.dataset.tone = ''; }
    if (completeBtn) completeBtn.disabled = false;
    if (completeBtn) completeBtn.hidden = data.status === 'completed';
    if (deleteBtn) deleteBtn.disabled = false;
    const href = data.href || '#';
    goEl.hidden = canRemind || isManual;
    goEl.setAttribute('href', href);
    goEl.textContent = GO_LABELS[kind] || '前往查看';
    goEl.classList.toggle('is-disabled', !href || href === '#');

    if (activeItem) activeItem.classList.remove('is-active');
    activeItem = item;
    item.classList.add('is-active');
    positionPopover(pop, item);
    pop.classList.add('is-open');
    if (canRemind) {
      fetchReminderState();
      remindValue.focus({ preventScroll: true });
    } else if (isManual) {
      editBtn?.focus({ preventScroll: true });
    } else if (!goEl.hidden) {
      goEl.focus({ preventScroll: true });
    } else {
      pop.querySelector('[data-pop-close]')?.focus({ preventScroll: true });
    }
  };

  const fetchReminderState = async () => {
    if (!activeEndpoint || !activeEventId) return;
    try {
      const response = await fetch(`${activeEndpoint}?event_id=${encodeURIComponent(activeEventId)}`, {
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
      });
      const payload = await response.json().catch(() => ({}));
      if (payload.has_reminder) {
        showReminderSummary(payload);
        return;
      }
      showReminderEditor('', '');
    } catch {
      /* prefill is best-effort */
    }
  };

  remindForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!activeEndpoint || !activeEventId) return;
    const value = parseInt(remindValue.value, 10);
    if (!Number.isFinite(value) || value <= 0) {
      setStatus('请输入大于 0 的提前时间。', 'error');
      return;
    }
    setStatus('正在设置…', 'info');
    remindForm.classList.add('is-busy');
    try {
      const response = await fetch(activeEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ event_id: Number(activeEventId), lead_value: value, lead_unit: remindUnit.value }),
      });
      const payload = await response.json().catch(() => ({}));
      if (response.ok && payload.status === 'success') {
        setStatus(payload.message || '邮件提醒已设置。', 'success');
        remindCancel.hidden = false;
        showReminderSummary(payload);
        notify(payload.message || '邮件提醒已设置。', 'success');
      } else {
        setStatus(payload.message || '设置失败，请稍后重试。', 'error');
      }
    } catch {
      setStatus('网络异常，设置失败。', 'error');
    } finally {
      remindForm.classList.remove('is-busy');
    }
  });

  remindCancel.addEventListener('click', async () => {
    if (!activeEndpoint || !activeEventId) return;
    setStatus('正在取消…', 'info');
    try {
      const response = await fetch(`${activeEndpoint}?event_id=${encodeURIComponent(activeEventId)}`, {
        method: 'DELETE',
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
      });
      const payload = await response.json().catch(() => ({}));
      setStatus(payload.message || '已取消提醒。', payload.cancelled_count ? 'success' : 'info');
      if (payload.cancelled_count) remindCancel.hidden = true;
      if (payload.cancelled_count) showReminderEditor(payload.message || '已取消提醒，可以重新设置。', 'success');
    } catch {
      setStatus('网络异常，取消失败。', 'error');
    }
  });

  document.addEventListener('click', (event) => {
    const item = event.target instanceof Element ? event.target.closest('[data-agenda-item]') : null;
    if (!item || event.defaultPrevented) return;
    event.preventDefault();
    open(item);
  });
  // The full-list Dialog closes before handing off to the existing detail and
  // editor. Its durable trigger anchors the popover, avoiding nested modals.
  window.addEventListener('lanshare:agenda-detail', (event) => {
    const { data, anchor } = event.detail || {};
    if (anchor instanceof HTMLElement && data) open(anchor, data);
  });

  pop.querySelector('[data-pop-close]').addEventListener('click', () => close(true));

  const setManageStatus = (message, tone) => {
    if (!manageStatus) return;
    manageStatus.textContent = message || '';
    manageStatus.dataset.tone = tone || '';
  };

  const activeTodoIds = () => ({
    classOfferingId: Number(activeData?.classOfferingId || 0),
    todoId: Number(activeData?.todoId || 0),
  });

  completeBtn?.addEventListener('click', async () => {
    const { classOfferingId, todoId } = activeTodoIds();
    if (!todoId || (!isTeacherTodoActor() && !classOfferingId)) return;
    completeBtn.disabled = true;
    setManageStatus('正在更新…', 'info');
    const { ok, payload } = await todoLifecycleRequest('PATCH', classOfferingId, todoId, { completed: true });
    if (ok) {
      setManageStatus('已完成', 'success');
      notify(payload.message || '待办已完成。', 'success');
      reloadSoon(500);
    } else {
      setManageStatus(payload.message || '操作失败，请稍后重试。', 'error');
      completeBtn.disabled = false;
    }
  });

  deleteBtn?.addEventListener('click', async () => {
    const { classOfferingId, todoId } = activeTodoIds();
    if (!todoId || (!isTeacherTodoActor() && !classOfferingId)) return;
    if (!window.confirm('确定删除这条待办吗？删除后不可恢复。')) return;
    deleteBtn.disabled = true;
    setManageStatus('正在删除…', 'info');
    const { ok, payload } = await todoLifecycleRequest('DELETE', classOfferingId, todoId);
    if (ok) {
      setManageStatus('已删除', 'success');
      notify(payload.message || '待办已删除。', 'success');
      reloadSoon(500);
    } else {
      setManageStatus(payload.message || '删除失败，请稍后重试。', 'error');
      deleteBtn.disabled = false;
    }
  });

  editBtn?.addEventListener('click', () => {
    if (!activeItem) return;
    const controller = getTodoModalController();
    if (!controller) { setManageStatus('暂时无法编辑。', 'error'); return; }
    const data = activeData || activeItem.dataset;
    const payload = {
      todoId: data.todoId,
      classOfferingId: data.classOfferingId,
      title: data.title || '',
      notes: data.notes || '',
      priority: data.priority || 'normal',
      dueAt: data.dueAt || '',
      startAt: data.startAt || '',
      reminderEnabled: data.reminderEnabled || '0',
      emailReminderEnabled: data.emailReminderEnabled || '0',
      reminderLead: data.reminderLead || '1440',
    };
    const triggerEl = activeItem;
    close();
    controller.openEdit(payload, triggerEl);
  });

  document.addEventListener('click', (event) => {
    if (pop.hidden) return;
    if (event.target.closest('.agenda-popover') || event.target.closest('[data-agenda-item]')) return;
    close();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') close(true);
  });
  window.addEventListener('resize', close, { passive: true });
  document.querySelectorAll('[data-agenda-list]').forEach((list) => {
    list.addEventListener('scroll', () => {
      // A body-level popover must not remain anchored to a scrolled-away row.
      if (activeItem && list.contains(activeItem)) close();
    }, { passive: true });
  });
  window.addEventListener('scroll', () => { if (activeItem) positionPopover(pop, activeItem); }, { passive: true });
}

function initAgendaScrollHints() {
  document.querySelectorAll('[data-agenda-reminder]').forEach((widget) => {
    const list = widget.querySelector('[data-agenda-list]');
    const hint = widget.querySelector('[data-agenda-scroll-hint]');
    if (!list || !hint) return;
    const update = () => {
      const atEnd = list.scrollTop + list.clientHeight >= list.scrollHeight - 2;
      hint.textContent = atEnd ? '已到底，向上查看 ↑' : '向下滚动查看更多 ↓';
    };
    list.addEventListener('scroll', update, { passive: true });
    update();
  });
}

// Bell → sync affordance: hovering the bell reveals a sync icon; clicking it
// resyncs the teacher's invigilation + course-exam reminders from the academic
// system in the background, spins while running, flashes on completion, then
// reloads so the freshly synced reminders render.
const SYNC_FLASH_MS = 1100;

function notify(message, type) {
  const toast = window.showToast || window.UI?.showToast;
  if (typeof toast === 'function') {
    toast(message, type);
  }
}

function initAgendaReminderSync() {
  const buttons = Array.from(document.querySelectorAll('[data-agenda-sync]'));
  if (!buttons.length) return;

  buttons.forEach((button) => {
    button.addEventListener('click', async () => {
      if (button.classList.contains('is-syncing')) return;
      const endpoint = button.dataset.syncEndpoint;
      if (!endpoint) return;

      button.classList.remove('is-synced');
      button.classList.add('is-syncing');
      button.disabled = true;

      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
        });
        let payload = {};
        try {
          payload = await response.json();
        } catch {
          payload = {};
        }
        if (!response.ok || (payload.status && payload.status === 'failed')) {
          throw new Error(payload.message || '教务提醒刷新未完成，请稍后重试。');
        }

        button.classList.remove('is-syncing');
        button.classList.add('is-synced');
        notify(payload.message || '教务提醒已刷新。', 'success');
        // Hold the completion flash briefly, then reload so the new reminders show.
        window.setTimeout(() => window.location.reload(), SYNC_FLASH_MS);
      } catch (error) {
        button.classList.remove('is-syncing');
        button.disabled = false;
        notify(error instanceof Error ? error.message : '教务提醒刷新失败。', 'error');
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Manual to-do lifecycle from the agenda widget: a + button opens a medium
// modal to CREATE a private-by-default to-do, and clicking an existing manual to-do
// opens a popover with 完成 / 编辑 / 删除. Everything reuses the existing
// classroom REST API for students and account-level /api/todos API for teachers;
// the new/changed item flows into the
// agenda feed, cockpit next-steps, classroom overview and semester calendar —
// so on success we reload to reflect it everywhere.
// ---------------------------------------------------------------------------
const TODO_ENDPOINT_BASE = '/api/classrooms';
const ACCOUNT_TODO_ENDPOINT_BASE = '/api/todos';

function readTodoOptions() {
  const holder = document.querySelector('[data-agenda-todo-options]');
  if (!holder) return { options: [], defaultOfferingId: 0 };
  try {
    const parsed = JSON.parse(holder.textContent || '{}');
    const options = Array.isArray(parsed.options) ? parsed.options : [];
    return {
      options,
      defaultOfferingId: Number(parsed.default_offering_id || 0),
      actorRole: String(parsed.actor_role || 'student'),
      emailReminder: parsed.email_reminder && typeof parsed.email_reminder === 'object'
        ? parsed.email_reminder
        : { available: false, reason: '', settings_url: '/profile?section=email' },
    };
  } catch {
    return { options: [], defaultOfferingId: 0, actorRole: 'student', emailReminder: {} };
  }
}

function reloadSoon(delay = 600) {
  window.setTimeout(() => window.location.reload(), delay);
}

// Split an ISO-ish datetime ("YYYY-MM-DDTHH:MM" / "YYYY-MM-DD HH:MM" / date)
// into { date, time } for the native pickers.
function splitTodoDateTime(value) {
  const text = String(value || '').trim();
  if (!text) return { date: '', time: '' };
  const normalized = text.replace('T', ' ');
  const [datePart, timePart = ''] = normalized.split(' ');
  return { date: (datePart || '').slice(0, 10), time: (timePart || '').slice(0, 5) };
}

async function todoLifecycleRequest(method, classOfferingId, todoId, body) {
  const opts = { method, headers: { Accept: 'application/json' }, credentials: 'same-origin' };
  if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const teacherAccountTodo = isTeacherTodoActor();
  const endpoint = teacherAccountTodo
    ? `${ACCOUNT_TODO_ENDPOINT_BASE}/${todoId}`
    : `${TODO_ENDPOINT_BASE}/${classOfferingId}/todos/${todoId}`;
  const response = await fetch(endpoint, opts);
  const payload = await response.json().catch(() => ({}));
  return { ok: response.ok && payload.status === 'success', payload };
}

function isTeacherTodoActor() {
  return readTodoOptions().actorRole === 'teacher';
}

function buildTodoModalDom({ actorRole = 'student' } = {}) {
  const isTeacher = actorRole === 'teacher';
  const classroomField = isTeacher
    ? `
        <details class="agenda-todo-scope" data-todo-scope>
          <summary>
            <span class="agenda-todo-scope__icon" aria-hidden="true">
              <svg xmlns="http://www.w3.org/2000/svg" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
            </span>
            <span class="agenda-todo-scope__copy">
              <strong data-todo-scope-title>私人待办</strong>
              <small data-todo-scope-copy>不关联课堂 · 仅自己可见</small>
            </span>
            <span class="agenda-todo-scope__action">设置归属</span>
          </summary>
          <div class="agenda-todo-scope__panel">
            <label class="agenda-todo-field">
              <span>关联课堂（可选）</span>
              <select name="class_offering_id" data-todo-course aria-label="关联课堂（可选）"></select>
            </label>
            <p>关联只用于分类和快速回到课堂，不会把这条待办展示给学生。</p>
          </div>
        </details>`
    : `
        <label class="agenda-todo-field agenda-todo-field--classroom">
          <span>所属课堂</span>
          <select name="class_offering_id" data-todo-course required></select>
        </label>`;
  const modal = document.createElement('div');
  modal.className = 'agenda-todo-modal';
  modal.hidden = true;
  modal.innerHTML = `
    <div class="agenda-todo-modal__backdrop" data-todo-close></div>
    <div class="agenda-todo-modal__card" role="dialog" aria-modal="true" aria-labelledby="agendaTodoTitle">
      <div class="agenda-todo-modal__head">
        <div>
          <span class="agenda-todo-modal__eyebrow" data-todo-eyebrow>我的待办</span>
          <h3 id="agendaTodoTitle" data-todo-heading>新增待办</h3>
          <p class="agenda-todo-modal__intro" data-todo-intro ${isTeacher ? '' : 'hidden'}>默认不关联课堂，只保存在你的个人待办中。</p>
        </div>
        <button type="button" class="agenda-todo-modal__close" data-todo-close aria-label="关闭">
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        </button>
      </div>
      <form id="agendaTodoForm" class="agenda-todo-modal__form" data-todo-form novalidate>
        ${isTeacher ? '' : classroomField}
        <label class="agenda-todo-field agenda-todo-field--title">
          <span>待办名称</span>
          <input type="text" name="title" maxlength="120" required placeholder="${isTeacher ? '例如：准备下周的教研分享' : '例如：完成第二章实验报告'}" autocomplete="off">
        </label>
        <div class="agenda-todo-field">
          <span>优先级</span>
          <div class="agenda-todo-priority" role="group" aria-label="优先级" data-todo-priority>
            <button type="button" data-priority-value="low">低</button>
            <button type="button" data-priority-value="normal" class="is-active" aria-pressed="true">中</button>
            <button type="button" data-priority-value="high">高</button>
          </div>
        </div>
        <div class="agenda-todo-grid">
          <label class="agenda-todo-field">
            <span>截止日期</span>
            <input type="date" name="due_date">
          </label>
          <label class="agenda-todo-field">
            <span>截止时间</span>
            <input type="time" name="due_time" value="23:59" step="60">
          </label>
        </div>
        <p class="agenda-todo-hint">不填截止日期则记为“无截止”，会一直留在待办里。</p>
        <div class="agenda-todo-reminder" data-todo-reminder>
          <label class="agenda-todo-reminder__toggle">
            <input type="checkbox" name="reminder_enabled" data-reminder-toggle>
            <span>到期前在站内提醒我</span>
          </label>
          <label class="agenda-todo-reminder__toggle agenda-todo-reminder__toggle--email" data-email-reminder-channel>
            <input type="checkbox" name="email_reminder_enabled" data-email-reminder-toggle>
            <span>同时发送邮件提醒</span>
            <span class="agenda-todo-reminder__mail-badge">邮件</span>
          </label>
          <div class="agenda-todo-reminder__lead" data-reminder-lead>
            <span>提前</span>
            <input type="number" name="reminder_lead_value" min="1" max="60" value="1" inputmode="numeric" aria-label="提前数值">
            <select name="reminder_lead_unit" aria-label="提前单位">
              <option value="day">天</option>
              <option value="hour">小时</option>
              <option value="minute">分钟</option>
            </select>
          </div>
          <p class="agenda-todo-reminder__hint" data-reminder-hint>设置截止日期后可开启到期提醒。</p>
          <p class="agenda-todo-reminder__hint agenda-todo-reminder__email-hint" data-email-reminder-hint hidden></p>
        </div>
        <details class="agenda-todo-more">
          <summary>更多选项（开始时间、备注）</summary>
          <div class="agenda-todo-grid">
            <label class="agenda-todo-field"><span>开始日期</span><input type="date" name="start_date"></label>
            <label class="agenda-todo-field"><span>开始时间</span><input type="time" name="start_time" value="00:00" step="60"></label>
          </div>
          <label class="agenda-todo-field">
            <span>备注</span>
            <textarea name="notes" maxlength="1200" rows="3" placeholder="任务要求、材料位置，或提醒自己的话"></textarea>
          </label>
        </details>
        ${isTeacher ? classroomField : ''}
        <p class="agenda-todo-status" data-todo-status role="status"></p>
      </form>
      <div class="agenda-todo-actions">
        <button type="button" class="agenda-todo-btn agenda-todo-btn--ghost" data-todo-close>取消</button>
        <button type="submit" form="agendaTodoForm" class="agenda-todo-btn agenda-todo-btn--primary" data-todo-submit>保存待办</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  return modal;
}

let sharedTodoModal = null;

function getTodoModalController() {
  if (sharedTodoModal) return sharedTodoModal;
  const { options, defaultOfferingId, actorRole, emailReminder } = readTodoOptions();
  if (!options.length && actorRole !== 'teacher') return null;
  sharedTodoModal = createTodoModalController(options, defaultOfferingId, { actorRole, emailReminder });
  return sharedTodoModal;
}

function createTodoModalController(options, defaultOfferingId, settings = {}) {
  const actorRole = String(settings.actorRole || 'student');
  const isTeacher = actorRole === 'teacher';
  const modal = buildTodoModalDom({ actorRole });
  const card = modal.querySelector('.agenda-todo-modal__card');
  const form = modal.querySelector('[data-todo-form]');
  const courseSelect = modal.querySelector('[data-todo-course]');
  const titleInput = form.elements.title;
  const statusEl = modal.querySelector('[data-todo-status]');
  const submitBtn = modal.querySelector('[data-todo-submit]');
  const headingEl = modal.querySelector('[data-todo-heading]');
  const eyebrowEl = modal.querySelector('[data-todo-eyebrow]');
  const introEl = modal.querySelector('[data-todo-intro]');
  const scopeDetails = modal.querySelector('[data-todo-scope]');
  const scopeTitle = modal.querySelector('[data-todo-scope-title]');
  const scopeCopy = modal.querySelector('[data-todo-scope-copy]');
  const priorityGroup = modal.querySelector('[data-todo-priority]');
  const reminderWrap = modal.querySelector('[data-todo-reminder]');
  const reminderToggle = form.elements.reminder_enabled;
  const emailReminderToggle = form.elements.email_reminder_enabled;
  const emailReminderChannel = modal.querySelector('[data-email-reminder-channel]');
  const emailReminderHint = modal.querySelector('[data-email-reminder-hint]');
  const reminderLeadValue = form.elements.reminder_lead_value;
  const reminderLeadUnit = form.elements.reminder_lead_unit;
  const reminderHint = modal.querySelector('[data-reminder-hint]');
  let priority = 'normal';
  let mode = 'create';
  let editingId = 0;
  let lastFocus = null;
  let reminderTouched = false;
  const emailReminder = settings.emailReminder || {};
  const emailAvailable = actorRole === 'teacher' && Boolean(emailReminder.available);

  if (actorRole !== 'teacher') {
    emailReminderChannel.hidden = true;
  } else {
    emailReminderToggle.disabled = !emailAvailable;
    if (!emailAvailable) {
      const reason = String(emailReminder.reason || '邮件提醒暂不可用。');
      const settingsUrl = String(emailReminder.settings_url || '/profile?section=email');
      emailReminderHint.innerHTML = `${escapeHtml(reason)} <a href="${escapeHtml(settingsUrl)}">去完善设置</a>`;
      emailReminderHint.hidden = false;
      emailReminderChannel.classList.add('is-unavailable');
    }
  }

  courseSelect.innerHTML = [
    isTeacher ? '<option value="">不关联课堂（私人待办）</option>' : '',
    ...options.map((opt) => `<option value="${Number(opt.class_offering_id)}">${escapeHtml(opt.label || `${opt.course_name || ''} · ${opt.class_name || ''}`)}</option>`),
  ].join('');

  const syncScopeSummary = () => {
    if (!scopeTitle || !scopeCopy) return;
    const selectedId = Number(courseSelect.value || 0);
    const selected = options.find((opt) => Number(opt.class_offering_id) === selectedId);
    if (selectedId && selected) {
      scopeTitle.textContent = selected.label || `${selected.course_name || ''} · ${selected.class_name || ''}`;
      scopeCopy.textContent = '已关联课堂 · 仍仅你可见';
    } else {
      scopeTitle.textContent = '私人待办';
      scopeCopy.textContent = '不关联课堂 · 仅自己可见';
    }
  };

  const applyDefaultCourse = () => {
    if (isTeacher) {
      courseSelect.value = '';
    } else if (defaultOfferingId && options.some((opt) => Number(opt.class_offering_id) === defaultOfferingId)) {
      courseSelect.value = String(defaultOfferingId);
    }
    syncScopeSummary();
  };

  const setStatus = (message, tone) => {
    statusEl.textContent = message || '';
    statusEl.dataset.tone = tone || '';
  };

  const setPriority = (value) => {
    priority = ['low', 'normal', 'high'].includes(value) ? value : 'normal';
    priorityGroup.querySelectorAll('[data-priority-value]').forEach((btn) => {
      const active = btn.dataset.priorityValue === priority;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  };

  const close = () => {
    if (modal.hidden) return;
    modal.classList.remove('is-open');
    modal.hidden = true;
    document.body.classList.remove('agenda-todo-open');
    if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus({ preventScroll: true });
  };

  // The backdrop, header close button and footer cancel button all share the
  // same dismiss contract. Bind them directly because clicks inside the card
  // are intentionally stopped from bubbling to the modal shell.
  modal.querySelectorAll('[data-todo-close]').forEach((control) => {
    control.addEventListener('click', (event) => {
      event.preventDefault();
      close();
    });
  });

  const reveal = () => {
    modal.hidden = false;
    document.body.classList.add('agenda-todo-open');
    window.requestAnimationFrame(() => {
      modal.classList.add('is-open');
      titleInput?.focus({ preventScroll: true });
    });
  };

  const hasDeadline = () => Boolean(form.elements.due_date?.value);

  // A reminder only makes sense with a deadline; the lead inputs only matter
  // when the toggle is on. Keep the control's enabled/visual state in sync.
  const syncReminderAvailability = () => {
    const due = hasDeadline();
    reminderToggle.disabled = !due;
    if (!due) reminderToggle.checked = false;
    emailReminderToggle.disabled = !due || !emailAvailable;
    if (!due || !emailAvailable) emailReminderToggle.checked = false;
    const leadOn = due && (reminderToggle.checked || emailReminderToggle.checked);
    reminderLeadValue.disabled = !leadOn;
    reminderLeadUnit.disabled = !leadOn;
    reminderWrap.classList.toggle('is-disabled', !due);
    reminderWrap.classList.toggle('is-active', leadOn);
    if (reminderHint) reminderHint.hidden = due;
  };

  const setReminderLeadFromMinutes = (minutes) => {
    let value = Math.max(1, parseInt(minutes, 10) || 1440);
    let unit = 'minute';
    if (value % 1440 === 0) { unit = 'day'; value /= 1440; }
    else if (value % 60 === 0) { unit = 'hour'; value /= 60; }
    reminderLeadValue.value = String(Math.min(value, 60));
    reminderLeadUnit.value = unit;
  };

  const reminderLeadMinutes = () => {
    const value = Math.max(1, parseInt(reminderLeadValue.value, 10) || 1);
    const factor = reminderLeadUnit.value === 'day' ? 1440 : reminderLeadUnit.value === 'hour' ? 60 : 1;
    return Math.min(value * factor, 30 * 24 * 60);
  };

  const openCreate = (trigger) => {
    lastFocus = trigger || null;
    mode = 'create';
    editingId = 0;
    form.reset();
    setPriority('normal');
    setStatus('', '');
    eyebrowEl.textContent = actorRole === 'teacher' ? '教师待办' : '我的待办';
    headingEl.textContent = isTeacher ? '记一件待办' : '新增待办';
    if (introEl) introEl.textContent = '默认不关联课堂，只保存在你的个人待办中。';
    submitBtn.textContent = '保存待办';
    courseSelect.disabled = false;
    if (scopeDetails) scopeDetails.open = false;
    applyDefaultCourse();
    reminderTouched = false;
    emailReminderToggle.checked = false;
    setReminderLeadFromMinutes(1440);
    syncReminderAvailability();
    reveal();
  };

  const openEdit = (data, trigger) => {
    lastFocus = trigger || null;
    mode = 'edit';
    editingId = Number(data.todoId || 0);
    form.reset();
    setStatus('', '');
    eyebrowEl.textContent = '编辑待办';
    headingEl.textContent = '编辑待办';
    if (introEl) introEl.textContent = '课堂归属只用于整理，这条待办仍然仅你可见。';
    submitBtn.textContent = '保存修改';
    courseSelect.value = String(Number(data.classOfferingId || 0) || '');
    courseSelect.disabled = !isTeacher;
    if (scopeDetails) scopeDetails.open = false;
    syncScopeSummary();
    titleInput.value = data.title || '';
    if (form.elements.notes) form.elements.notes.value = data.notes || '';
    setPriority(data.priority || 'normal');
    const due = splitTodoDateTime(data.dueAt);
    const start = splitTodoDateTime(data.startAt);
    if (form.elements.due_date) form.elements.due_date.value = due.date;
    if (form.elements.due_time) form.elements.due_time.value = due.time || '23:59';
    if (form.elements.start_date) form.elements.start_date.value = start.date;
    if (form.elements.start_time) form.elements.start_time.value = start.time || '00:00';
    if (start.date || (form.elements.notes && form.elements.notes.value)) {
      const more = modal.querySelector('.agenda-todo-more');
      if (more) more.open = true;
    }
    reminderTouched = true; // respect the stored setting; don't auto-flip
    setReminderLeadFromMinutes(data.reminderLead || 1440);
    reminderToggle.checked = data.reminderEnabled === '1';
    emailReminderToggle.checked = emailAvailable && data.emailReminderEnabled === '1';
    syncReminderAvailability();
    reveal();
  };

  const dateTime = (dateValue, timeValue, fallbackTime) => (
    dateValue ? `${dateValue}T${timeValue || fallbackTime}` : null
  );

  const submit = async (event) => {
    event.preventDefault();
    const classOfferingId = Number(courseSelect.value || 0);
    const title = (titleInput.value || '').trim();
    if (!isTeacher && !classOfferingId) { setStatus('请选择所属课堂。', 'error'); return; }
    if (!title) { setStatus('请填写待办名称。', 'error'); titleInput.focus(); return; }

    const body = {
      title,
      notes: form.elements.notes?.value || '',
      priority,
      start_at: dateTime(form.elements.start_date?.value, form.elements.start_time?.value, '00:00'),
      due_at: dateTime(form.elements.due_date?.value, form.elements.due_time?.value, '23:59'),
      reminder_enabled: Boolean(hasDeadline() && reminderToggle.checked),
      email_reminder_enabled: Boolean(hasDeadline() && emailAvailable && emailReminderToggle.checked),
      reminder_lead_minutes: reminderLeadMinutes(),
    };
    if (isTeacher) body.class_offering_id = classOfferingId || null;
    if (body.start_at && body.due_at && body.due_at < body.start_at) {
      setStatus('截止时间不能早于开始时间。', 'error');
      return;
    }

    submitBtn.disabled = true;
    setStatus('正在保存…', 'info');
    try {
      const isEdit = mode === 'edit' && editingId;
      const url = isTeacher
        ? (isEdit ? `${ACCOUNT_TODO_ENDPOINT_BASE}/${editingId}` : ACCOUNT_TODO_ENDPOINT_BASE)
        : (isEdit
          ? `${TODO_ENDPOINT_BASE}/${classOfferingId}/todos/${editingId}`
          : `${TODO_ENDPOINT_BASE}/${classOfferingId}/todos`);
      const response = await fetch(url, {
        method: isEdit ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(body),
      });
      const payload = await response.json().catch(() => ({}));
      if (response.ok && payload.status === 'success') {
        const message = payload.message || (isEdit ? '待办已更新。' : '待办已添加。');
        setStatus(message, 'success');
        notify(message, 'success');
        reloadSoon(650);
      } else {
        setStatus(payload.message || '保存失败，请稍后重试。', 'error');
        submitBtn.disabled = false;
      }
    } catch {
      setStatus('网络异常，保存失败。', 'error');
      submitBtn.disabled = false;
    }
  };

  priorityGroup.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-priority-value]');
    if (btn) setPriority(btn.dataset.priorityValue);
  });
  courseSelect.addEventListener('change', syncScopeSummary);
  scopeDetails?.addEventListener('toggle', () => {
    if (!scopeDetails.open) return;
    window.requestAnimationFrame(() => {
      form.scrollTo({
        top: form.scrollHeight,
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      });
    });
  });
  form.elements.due_date?.addEventListener('change', () => {
    // First time a deadline is set (and the user hasn't touched the toggle),
    // default the reminder on — the helpful, expected behaviour.
    if (form.elements.due_date.value && !reminderTouched && mode === 'create') {
      reminderToggle.checked = true;
    }
    syncReminderAvailability();
  });
  reminderToggle.addEventListener('change', () => {
    reminderTouched = true;
    syncReminderAvailability();
  });
  emailReminderToggle.addEventListener('change', () => {
    reminderTouched = true;
    syncReminderAvailability();
  });
  form.addEventListener('submit', submit);
  document.addEventListener('keydown', (event) => {
    if (modal.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    else trapModalTab(event, card);
  });
  card.addEventListener('click', (event) => event.stopPropagation());

  return { openCreate, openEdit };
}

function initAgendaTodoCreator() {
  const triggers = Array.from(document.querySelectorAll('[data-agenda-add-todo]'));
  if (!triggers.length) return;
  const controller = getTodoModalController();
  if (!controller) {
    triggers.forEach((btn) => { btn.hidden = true; });
    return;
  }
  triggers.forEach((trigger) => trigger.addEventListener('click', () => controller.openCreate(trigger)));
}

// ---------------------------------------------------------------------------
// 订阅到手机日历：拉取本人 iCal 订阅链接，弹窗提供 打开订阅 / 复制链接 / 重置。
// 复用 agenda-todo-modal 的外观类，保持视觉一致。
// ---------------------------------------------------------------------------
let calendarFeedModal = null;

function buildCalendarFeedModal() {
  const modal = document.createElement('div');
  modal.className = 'agenda-todo-modal';
  modal.hidden = true;
  modal.innerHTML = `
    <div class="agenda-todo-modal__backdrop" data-feed-close></div>
    <div class="agenda-todo-modal__card" role="dialog" aria-modal="true" aria-labelledby="agendaCalendarFeedTitle">
      <div class="agenda-todo-modal__head">
        <div>
          <span class="agenda-todo-modal__eyebrow">日历订阅</span>
          <h3 id="agendaCalendarFeedTitle">订阅到手机日历</h3>
        </div>
        <button type="button" class="agenda-todo-modal__close" data-feed-close aria-label="关闭">
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        </button>
      </div>
      <div class="agenda-todo-modal__form">
        <p class="agenda-todo-hint" style="margin-top:0;">
          用系统日历 App 订阅这个链接后，上课安排、作业/考试截止和你的待办会自动出现在手机日历里，并保持更新。
          iPhone 可直接点“打开订阅”；安卓/电脑请复制链接后在日历 App 里选择“订阅日历 / 从 URL 添加”。
        </p>
        <label class="agenda-todo-field">
          <span>我的专属订阅链接（请勿分享给他人）</span>
          <input type="text" readonly data-feed-url value="加载中…" onclick="this.select()">
        </label>
        <div class="agenda-todo-modal__actions" style="display:flex;gap:10px;flex-wrap:wrap;">
          <a class="btn btn-primary btn-sm" data-feed-open href="#" target="_blank" rel="noopener">打开订阅（iPhone/Mac）</a>
          <button type="button" class="btn btn-secondary btn-sm" data-feed-copy>复制链接</button>
          <button type="button" class="btn btn-ghost btn-sm" data-feed-reset title="旧链接立即失效并生成新链接">重置链接</button>
        </div>
        <p class="agenda-todo-hint" data-feed-status aria-live="polite"></p>
      </div>
    </div>`;
  document.body.appendChild(modal);
  return modal;
}

async function fetchCalendarFeed(endpoint, method = 'GET') {
  const response = await fetch(endpoint, {
    method,
    headers: { Accept: 'application/json' },
    credentials: 'same-origin',
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok || payload.status !== 'success') {
    throw new Error(payload.detail || payload.message || '获取订阅链接失败，请稍后重试。');
  }
  return payload;
}

function initAgendaCalendarFeed() {
  const triggers = Array.from(document.querySelectorAll('[data-agenda-calendar-feed]'));
  if (!triggers.length) return;

  const applyPayload = (modal, payload) => {
    modal.querySelector('[data-feed-url]').value = payload.feed_url || '';
    modal.querySelector('[data-feed-open]').href = payload.webcal_url || payload.feed_url || '#';
  };

  let returnFocus = null;
  const closeModal = () => {
    calendarFeedModal.classList.remove('is-open');
    calendarFeedModal.hidden = true;
    document.body.classList.remove('agenda-todo-open');
    returnFocus?.focus({ preventScroll: true });
  };
  const openModal = async (event) => {
    returnFocus = event?.currentTarget;
    if (!returnFocus?.getClientRects().length) returnFocus = document.activeElement;
    if (!calendarFeedModal) {
      calendarFeedModal = buildCalendarFeedModal();
      const modal = calendarFeedModal;
      const statusEl = modal.querySelector('[data-feed-status]');
      const setStatus = (message) => { statusEl.textContent = message || ''; };
      modal.querySelectorAll('[data-feed-close]').forEach((el) => {
        el.addEventListener('click', closeModal);
      });
      document.addEventListener('keydown', (keyEvent) => {
        if (modal.hidden) return;
        if (keyEvent.key === 'Escape') { keyEvent.preventDefault(); closeModal(); }
        else trapModalTab(keyEvent, modal.querySelector('[role="dialog"]'));
      });
      modal.querySelector('[data-feed-copy]').addEventListener('click', async () => {
        const url = modal.querySelector('[data-feed-url]').value;
        try {
          await navigator.clipboard.writeText(url);
          setStatus('链接已复制，去日历 App 里“订阅日历 / 从 URL 添加”即可。');
        } catch {
          modal.querySelector('[data-feed-url]').select();
          setStatus('自动复制失败，链接已选中，请手动复制（Ctrl/Cmd+C）。');
        }
      });
      modal.querySelector('[data-feed-reset]').addEventListener('click', async () => {
        if (!window.confirm('重置后旧链接立即失效，所有已订阅的日历需要用新链接重新订阅。确定重置吗？')) return;
        try {
          const payload = await fetchCalendarFeed('/api/calendar-feed/reset', 'POST');
          applyPayload(calendarFeedModal, payload);
          setStatus('已生成新链接，旧链接已失效。');
        } catch (error) {
          setStatus(error instanceof Error ? error.message : '重置失败，请稍后重试。');
        }
      });
    }
    calendarFeedModal.hidden = false;
    document.body.classList.add('agenda-todo-open');
    window.requestAnimationFrame(() => {
      calendarFeedModal.classList.add('is-open');
      calendarFeedModal.querySelector('[data-feed-url]').focus({ preventScroll: true });
    });
    const statusEl = calendarFeedModal.querySelector('[data-feed-status]');
    statusEl.textContent = '';
    try {
      const payload = await fetchCalendarFeed('/api/calendar-feed');
      applyPayload(calendarFeedModal, payload);
    } catch (error) {
      statusEl.textContent = error instanceof Error ? error.message : '获取订阅链接失败。';
    }
  };

  triggers.forEach((trigger) => trigger.addEventListener('click', openModal));
}

function initAgendaReminderWidget() {
  initAgendaScrollHints();
  initAgendaWidget();
  initAgendaReminderSync();
  initAgendaTodoCreator();
  initAgendaCalendarFeed();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAgendaReminderWidget);
} else {
  initAgendaReminderWidget();
}
