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
  const items = Array.from(document.querySelectorAll('[data-agenda-item]'));
  if (!items.length) return;

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
  const remindCancel = pop.querySelector('[data-remind-cancel]');
  const remindStatus = pop.querySelector('[data-remind-status]');
  const manageEl = pop.querySelector('[data-pop-manage]');
  const completeBtn = pop.querySelector('[data-pop-complete]');
  const editBtn = pop.querySelector('[data-pop-edit]');
  const deleteBtn = pop.querySelector('[data-pop-delete]');
  const manageStatus = pop.querySelector('[data-manage-status]');
  let activeItem = null;
  let activeEndpoint = '';
  let activeEventId = '';

  const setStatus = (message, tone) => {
    remindStatus.textContent = message || '';
    remindStatus.dataset.tone = tone || '';
  };

  const collapseForm = () => {
    remindForm.hidden = true;
    remindBtn.setAttribute('aria-expanded', 'false');
  };

  const close = () => {
    if (pop.hidden) return;
    pop.classList.remove('is-open');
    pop.hidden = true;
    collapseForm();
    if (activeItem) activeItem.classList.remove('is-active');
    activeItem = null;
  };

  const open = (item) => {
    if (activeItem === item) {
      close();
      return;
    }
    const data = item.dataset;
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

    setStatus('', '');
    remindCancel.hidden = true;
    // Invigilation/exam: show the email-reminder form inline (the only action) —
    // no extra toggle button, no dead-end "前往学期日历" link. Other kinds keep
    // their jump link.
    remindBtn.hidden = true;
    remindForm.hidden = !canRemind;
    const isManual = data.manual === '1';
    if (manageEl) manageEl.hidden = !isManual;
    if (manageStatus) { manageStatus.textContent = ''; manageStatus.dataset.tone = ''; }
    if (completeBtn) completeBtn.disabled = false;
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
    } else {
      goEl.focus({ preventScroll: true });
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
        remindCancel.hidden = false;
        setStatus('已设置提醒，提交将更新提醒时间。', 'info');
      }
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
    } catch {
      setStatus('网络异常，取消失败。', 'error');
    }
  });

  items.forEach((item) => {
    item.addEventListener('click', () => open(item));
  });

  pop.querySelector('[data-pop-close]').addEventListener('click', close);

  const setManageStatus = (message, tone) => {
    if (!manageStatus) return;
    manageStatus.textContent = message || '';
    manageStatus.dataset.tone = tone || '';
  };

  const activeTodoIds = () => ({
    classOfferingId: Number(activeItem?.dataset.classOfferingId || 0),
    todoId: Number(activeItem?.dataset.todoId || 0),
  });

  completeBtn?.addEventListener('click', async () => {
    const { classOfferingId, todoId } = activeTodoIds();
    if (!classOfferingId || !todoId) return;
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
    if (!classOfferingId || !todoId) return;
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
    const data = activeItem.dataset;
    const payload = {
      todoId: data.todoId,
      classOfferingId: data.classOfferingId,
      title: data.title || '',
      notes: data.notes || '',
      priority: data.priority || 'normal',
      dueAt: data.dueAt || '',
      startAt: data.startAt || '',
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
    if (event.key === 'Escape') close();
  });
  window.addEventListener('resize', close, { passive: true });
  window.addEventListener('scroll', () => { if (activeItem) positionPopover(pop, activeItem); }, { passive: true });
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
// modal to CREATE a course-scoped to-do, and clicking an existing manual to-do
// opens a popover with 完成 / 编辑 / 删除. Everything reuses the existing
// /api/classrooms/{id}/todos REST API; the new/changed item flows into the
// agenda feed, cockpit next-steps, classroom overview and semester calendar —
// so on success we reload to reflect it everywhere.
// ---------------------------------------------------------------------------
const TODO_ENDPOINT_BASE = '/api/classrooms';

function readTodoOptions() {
  const holder = document.querySelector('[data-agenda-todo-options]');
  if (!holder) return { options: [], defaultOfferingId: 0 };
  try {
    const parsed = JSON.parse(holder.textContent || '{}');
    const options = Array.isArray(parsed.options) ? parsed.options : [];
    return { options, defaultOfferingId: Number(parsed.default_offering_id || 0) };
  } catch {
    return { options: [], defaultOfferingId: 0 };
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
  const response = await fetch(`${TODO_ENDPOINT_BASE}/${classOfferingId}/todos/${todoId}`, opts);
  const payload = await response.json().catch(() => ({}));
  return { ok: response.ok && payload.status === 'success', payload };
}

function buildTodoModalDom() {
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
        </div>
        <button type="button" class="agenda-todo-modal__close" data-todo-close aria-label="关闭">
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        </button>
      </div>
      <form class="agenda-todo-modal__form" data-todo-form novalidate>
        <label class="agenda-todo-field">
          <span>所属课堂</span>
          <select name="class_offering_id" data-todo-course required></select>
        </label>
        <label class="agenda-todo-field">
          <span>待办名称</span>
          <input type="text" name="title" maxlength="120" required placeholder="例如：完成第二章实验报告" autocomplete="off">
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
        <p class="agenda-todo-status" data-todo-status role="status"></p>
        <div class="agenda-todo-actions">
          <button type="button" class="agenda-todo-btn agenda-todo-btn--ghost" data-todo-close>取消</button>
          <button type="submit" class="agenda-todo-btn agenda-todo-btn--primary" data-todo-submit>保存待办</button>
        </div>
      </form>
    </div>
  `;
  document.body.appendChild(modal);
  return modal;
}

let sharedTodoModal = null;

function getTodoModalController() {
  if (sharedTodoModal) return sharedTodoModal;
  const { options, defaultOfferingId } = readTodoOptions();
  if (!options.length) return null;
  sharedTodoModal = createTodoModalController(options, defaultOfferingId);
  return sharedTodoModal;
}

function createTodoModalController(options, defaultOfferingId) {
  const modal = buildTodoModalDom();
  const card = modal.querySelector('.agenda-todo-modal__card');
  const form = modal.querySelector('[data-todo-form]');
  const courseSelect = modal.querySelector('[data-todo-course]');
  const titleInput = form.elements.title;
  const statusEl = modal.querySelector('[data-todo-status]');
  const submitBtn = modal.querySelector('[data-todo-submit]');
  const headingEl = modal.querySelector('[data-todo-heading]');
  const eyebrowEl = modal.querySelector('[data-todo-eyebrow]');
  const priorityGroup = modal.querySelector('[data-todo-priority]');
  const reminderWrap = modal.querySelector('[data-todo-reminder]');
  const reminderToggle = form.elements.reminder_enabled;
  const reminderLeadValue = form.elements.reminder_lead_value;
  const reminderLeadUnit = form.elements.reminder_lead_unit;
  const reminderHint = modal.querySelector('[data-reminder-hint]');
  let priority = 'normal';
  let mode = 'create';
  let editingId = 0;
  let lastFocus = null;
  let reminderTouched = false;

  courseSelect.innerHTML = options
    .map((opt) => `<option value="${Number(opt.class_offering_id)}">${escapeHtml(opt.label || `${opt.course_name || ''} · ${opt.class_name || ''}`)}</option>`)
    .join('');

  const applyDefaultCourse = () => {
    if (defaultOfferingId && options.some((opt) => Number(opt.class_offering_id) === defaultOfferingId)) {
      courseSelect.value = String(defaultOfferingId);
    }
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
    const leadOn = due && reminderToggle.checked;
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
    eyebrowEl.textContent = '我的待办';
    headingEl.textContent = '新增待办';
    submitBtn.textContent = '保存待办';
    courseSelect.disabled = false;
    applyDefaultCourse();
    reminderTouched = false;
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
    submitBtn.textContent = '保存修改';
    // The course can't be moved for an existing to-do — show it, locked.
    courseSelect.value = String(Number(data.classOfferingId || 0));
    courseSelect.disabled = true;
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
    if (!classOfferingId) { setStatus('请选择所属课堂。', 'error'); return; }
    if (!title) { setStatus('请填写待办名称。', 'error'); titleInput.focus(); return; }

    const body = {
      title,
      notes: form.elements.notes?.value || '',
      priority,
      start_at: dateTime(form.elements.start_date?.value, form.elements.start_time?.value, '00:00'),
      due_at: dateTime(form.elements.due_date?.value, form.elements.due_time?.value, '23:59'),
      reminder_enabled: Boolean(hasDeadline() && reminderToggle.checked),
      reminder_lead_minutes: reminderLeadMinutes(),
    };
    if (body.start_at && body.due_at && body.due_at < body.start_at) {
      setStatus('截止时间不能早于开始时间。', 'error');
      return;
    }

    submitBtn.disabled = true;
    setStatus('正在保存…', 'info');
    try {
      const isEdit = mode === 'edit' && editingId;
      const url = isEdit
        ? `${TODO_ENDPOINT_BASE}/${classOfferingId}/todos/${editingId}`
        : `${TODO_ENDPOINT_BASE}/${classOfferingId}/todos`;
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
  form.addEventListener('submit', submit);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) close();
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

function initAgendaReminderWidget() {
  initAgendaWidget();
  initAgendaReminderSync();
  initAgendaTodoCreator();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAgendaReminderWidget);
} else {
  initAgendaReminderWidget();
}
