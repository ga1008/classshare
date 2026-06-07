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

    collapseForm();
    setStatus('', '');
    remindCancel.hidden = true;
    remindBtn.hidden = !canRemind;
    // For invigilation/exam the reminder is the primary action; the old "前往学期日历"
    // link led nowhere useful, so it is replaced by the email-reminder button.
    const href = data.href || '#';
    goEl.hidden = canRemind;
    goEl.setAttribute('href', href);
    goEl.textContent = GO_LABELS[kind] || '前往查看';
    goEl.classList.toggle('is-disabled', !href || href === '#');

    if (activeItem) activeItem.classList.remove('is-active');
    activeItem = item;
    item.classList.add('is-active');
    positionPopover(pop, item);
    pop.classList.add('is-open');
    (canRemind ? remindBtn : goEl).focus({ preventScroll: true });
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

  remindBtn.addEventListener('click', () => {
    const expanded = !remindForm.hidden;
    if (expanded) {
      collapseForm();
      return;
    }
    remindForm.hidden = false;
    remindBtn.setAttribute('aria-expanded', 'true');
    positionPopover(pop, activeItem);
    remindValue.focus({ preventScroll: true });
    fetchReminderState();
  });

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

function initAgendaReminderWidget() {
  initAgendaWidget();
  initAgendaReminderSync();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAgendaReminderWidget);
} else {
  initAgendaReminderWidget();
}
