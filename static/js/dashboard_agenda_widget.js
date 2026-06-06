// Agenda reminder widget: clicking an item opens a detail popover anchored to
// it, with a button to jump to the related page. Keyboard + outside-click close.

const GO_LABELS = {
  invigilation: '前往监考安排',
  exam: '前往查看',
  assignment: '前往提交',
  todo: '前往处理',
  class: '前往课堂',
};

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
    <p class="agenda-popover__subtitle" data-pop-subtitle></p>
    <div class="agenda-popover__meta">
      <span class="agenda-popover__when" data-pop-when></span>
      <span class="agenda-popover__relative" data-pop-relative></span>
    </div>
    <a class="agenda-popover__go" data-pop-go href="#">前往查看</a>
  `;
  document.body.appendChild(pop);
  return pop;
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
  const subtitleEl = pop.querySelector('[data-pop-subtitle]');
  const whenEl = pop.querySelector('[data-pop-when]');
  const relEl = pop.querySelector('[data-pop-relative]');
  const goEl = pop.querySelector('[data-pop-go]');
  let activeItem = null;

  const close = () => {
    if (pop.hidden) return;
    pop.classList.remove('is-open');
    pop.hidden = true;
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
    kindEl.textContent = data.kindLabel || '日程';
    kindEl.className = `agenda-popover__kind kind-${kind}`;
    titleEl.textContent = data.title || '待办事项';
    subtitleEl.textContent = data.subtitle || '';
    subtitleEl.hidden = !data.subtitle;
    whenEl.textContent = (data.when || '').trim();
    relEl.textContent = data.relative || '';
    relEl.hidden = !data.relative;
    const href = data.href || '#';
    goEl.setAttribute('href', href);
    goEl.textContent = GO_LABELS[kind] || '前往查看';
    goEl.classList.toggle('is-disabled', !href || href === '#');

    if (activeItem) activeItem.classList.remove('is-active');
    activeItem = item;
    item.classList.add('is-active');
    positionPopover(pop, item);
    pop.classList.add('is-open');
    goEl.focus({ preventScroll: true });
  };

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

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAgendaWidget);
} else {
  initAgendaWidget();
}
