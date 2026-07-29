/**
 * 结课（end-of-term closeout）浮窗。
 *
 * 教师点课堂顶栏的“结课”后，拉 /api/classroom/{id}/closeout/summary，把课堂里
 * 所有还没结束的过程性任务渲染成卡片：作业/测验带默认分滑块 + 输入框，其它类别
 * 只提供“本次跳过”开关。底部“确认结课”把选择打包 POST 给 .../closeout/execute。
 *
 * 状态只存在本模块的 `state` 里；每次打开都重新拉取，避免用陈旧计数做破坏性操作。
 */

import { showToast, openModal, closeModal, escapeHtml } from './ui.js';

const MODAL_ID = 'classroom-closeout-modal';
const SCORABLE_KINDS = new Set(['assignment', 'exam']);

const KIND_LABELS = {
  assignment: '作业',
  exam: '测验',
  poll: '投票',
  group_scheme: '分组方案',
  live_activity: '课堂互动',
  help_signal: '举手求助',
  question: '课堂提问',
};

const state = {
  classOfferingId: null,
  cards: [],
  // cardKey -> { skip: boolean, score: number, includeUngraded: boolean }
  plans: new Map(),
  loading: false,
  submitting: false,
};

const cardKey = (card) => `${card.kind}:${card.id}`;

function clampScore(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 0;
  return Math.max(0, Math.min(100, Math.round(num)));
}

function planFor(card) {
  const key = cardKey(card);
  if (!state.plans.has(key)) {
    state.plans.set(key, { skip: false, score: 0, includeUngraded: false });
  }
  return state.plans.get(key);
}

function kindTone(kind) {
  switch (kind) {
    case 'assignment': return 'primary';
    case 'exam': return 'rose';
    case 'poll': return 'blue';
    case 'group_scheme': return 'teal';
    case 'live_activity': return 'cyan';
    default: return 'slate';
  }
}

function renderCardMeta(card) {
  const chips = [];
  if (SCORABLE_KINDS.has(card.kind)) {
    const unsubmitted = Number(card.unsubmitted_count || 0);
    const ungraded = Number(card.ungraded_count || 0);
    chips.push(`<span class="closeout-chip">共 ${Number(card.total_students || 0)} 人</span>`);
    chips.push(
      `<span class="closeout-chip${unsubmitted > 0 ? ' closeout-chip--danger' : ''}">未提交 ${unsubmitted}</span>`
    );
    if (ungraded > 0) {
      chips.push(`<span class="closeout-chip closeout-chip--warn">待批改 ${ungraded}</span>`);
    }
    if (card.due_at) {
      chips.push(`<span class="closeout-chip">截止 ${escapeHtml(String(card.due_at).replace('T', ' '))}</span>`);
    }
  } else if (card.kind === 'poll') {
    chips.push(`<span class="closeout-chip">已投票 ${Number(card.voted_count || 0)} 人</span>`);
    if (card.status === 'draft') chips.push('<span class="closeout-chip">草稿</span>');
  } else if (card.kind === 'group_scheme') {
    chips.push(`<span class="closeout-chip">${Number(card.group_count || 0)} 个小组</span>`);
  } else if (card.pending_count) {
    chips.push(`<span class="closeout-chip closeout-chip--warn">待处理 ${Number(card.pending_count)}</span>`);
  }
  return chips.join('');
}

function renderScoreControls(card, plan) {
  const unsubmitted = Number(card.unsubmitted_count || 0);
  const ungraded = Number(card.ungraded_count || 0);
  if (unsubmitted === 0 && ungraded === 0) {
    return '<p class="closeout-card__hint">全部已提交并批改，将直接截止。</p>';
  }

  const key = cardKey(card);
  let html = '';
  if (unsubmitted > 0) {
    html += `
      <div class="closeout-card__score">
        <label class="closeout-card__score-label" for="closeout-score-input-${escapeHtml(key)}">
          未提交者默认分
        </label>
        <input type="range" class="closeout-card__slider" data-closeout-score-range="${escapeHtml(key)}"
               min="0" max="100" step="1" value="${plan.score}" aria-label="未提交者默认分滑块">
        <input type="number" class="form-control closeout-card__number" id="closeout-score-input-${escapeHtml(key)}"
               data-closeout-score-input="${escapeHtml(key)}" min="0" max="100" step="1" value="${plan.score}">
      </div>`;
  }
  if (ungraded > 0) {
    html += `
      <label class="closeout-card__toggle">
        <input type="checkbox" data-closeout-ungraded="${escapeHtml(key)}" ${plan.includeUngraded ? 'checked' : ''}>
        <span>把 ${ungraded} 份“已提交未批改”也按默认分记（会顶掉真实批改）</span>
      </label>`;
  }
  return html;
}

function renderCards() {
  const list = document.getElementById('closeout-card-list');
  const empty = document.getElementById('closeout-empty');
  const footer = document.getElementById('closeout-footer');
  if (!list) return;

  if (!state.cards.length) {
    list.innerHTML = '';
    if (empty) empty.hidden = false;
    if (footer) footer.hidden = true;
    return;
  }
  if (empty) empty.hidden = true;
  if (footer) footer.hidden = false;

  list.innerHTML = state.cards
    .map((card) => {
      const plan = planFor(card);
      const key = cardKey(card);
      const titleHtml = card.detail_url
        ? `<a href="${escapeHtml(card.detail_url)}" target="_blank" rel="noopener">${escapeHtml(card.title || '')}</a>`
        : escapeHtml(card.title || '');
      const body = SCORABLE_KINDS.has(card.kind)
        ? renderScoreControls(card, plan)
        : '<p class="closeout-card__hint">确认结课后将停止并归档。</p>';
      return `
        <article class="closeout-card${plan.skip ? ' closeout-card--skipped' : ''}" data-closeout-card="${escapeHtml(key)}">
          <header class="closeout-card__head">
            <span class="closeout-card__kind closeout-card__kind--${kindTone(card.kind)}">${escapeHtml(card.kind_label || '')}</span>
            <h4 class="closeout-card__title">${titleHtml}</h4>
            <label class="closeout-card__skip">
              <input type="checkbox" data-closeout-skip="${escapeHtml(key)}" ${plan.skip ? 'checked' : ''}>
              <span>本次跳过</span>
            </label>
          </header>
          <div class="closeout-card__meta">${renderCardMeta(card)}</div>
          <div class="closeout-card__body">${body}</div>
        </article>`;
    })
    .join('');
}

function updateHeadline(summary) {
  const headline = document.getElementById('closeout-headline');
  if (!headline) return;
  const total = Number(summary.total || 0);
  if (total === 0) {
    headline.textContent = '本课堂没有未结束的过程性任务，可以放心结课。';
    return;
  }
  const bits = [`${total} 项待收尾`];
  if (summary.pending_absence_score_count) bits.push(`${summary.pending_absence_score_count} 人次未提交`);
  if (summary.pending_grading_count) bits.push(`${summary.pending_grading_count} 份待批改`);
  headline.textContent = bits.join(' · ');
}

function bindCardEvents() {
  const list = document.getElementById('closeout-card-list');
  if (!list || list.dataset.bound === '1') return;
  list.dataset.bound = '1';

  list.addEventListener('input', (event) => {
    const target = event.target;
    const key = target.getAttribute?.('data-closeout-score-range')
      || target.getAttribute?.('data-closeout-score-input');
    if (!key) return;

    const plan = state.plans.get(key);
    if (!plan) return;
    plan.score = clampScore(target.value);
    // 滑块与输入框互相同步，不整块重绘（会打断正在拖动的滑块）。
    const card = list.querySelector(`[data-closeout-card="${CSS.escape(key)}"]`);
    const range = card?.querySelector('[data-closeout-score-range]');
    const number = card?.querySelector('[data-closeout-score-input]');
    if (range && range !== target) range.value = String(plan.score);
    if (number && number !== target) number.value = String(plan.score);
  });

  list.addEventListener('change', (event) => {
    const target = event.target;

    // 输入提交（失焦/回车）时把越界值写回输入框本身。'input' 阶段不回写，否则
    // 会打断正在输入的数字；但若不在这里纠正，教师会看到 "250" 却实际记 100。
    const scoreKey = target.getAttribute?.('data-closeout-score-input')
      || target.getAttribute?.('data-closeout-score-range');
    if (scoreKey) {
      const plan = state.plans.get(scoreKey);
      if (plan) target.value = String(plan.score);
      return;
    }

    const skipKey = target.getAttribute?.('data-closeout-skip');
    if (skipKey) {
      const plan = state.plans.get(skipKey);
      if (plan) plan.skip = target.checked === true;
      const card = list.querySelector(`[data-closeout-card="${CSS.escape(skipKey)}"]`);
      card?.classList.toggle('closeout-card--skipped', target.checked === true);
      return;
    }
    const ungradedKey = target.getAttribute?.('data-closeout-ungraded');
    if (ungradedKey) {
      const plan = state.plans.get(ungradedKey);
      if (plan) plan.includeUngraded = target.checked === true;
    }
  });
}

async function loadSummary() {
  if (state.loading) return;
  state.loading = true;
  const headline = document.getElementById('closeout-headline');
  if (headline) headline.textContent = '正在统计…';

  try {
    const response = await fetch(`/api/classroom/${state.classOfferingId}/closeout/summary`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.message || '结课统计加载失败');

    state.cards = Array.isArray(data.cards) ? data.cards : [];
    state.plans = new Map();
    state.cards.forEach((card) => planFor(card));
    updateHeadline(data);
    renderCards();
    bindCardEvents();
  } catch (error) {
    showToast(error.message || '结课统计加载失败', 'error');
    if (headline) headline.textContent = '加载失败，请关闭后重试。';
  } finally {
    state.loading = false;
  }
}

function buildPayload() {
  const payload = { default_score: 0, include_ungraded: false };
  state.cards.forEach((card) => {
    const plan = planFor(card);
    const entry = {};
    if (plan.skip) entry.action = 'skip';
    if (SCORABLE_KINDS.has(card.kind)) {
      entry.default_score = plan.score;
      entry.include_ungraded = plan.includeUngraded;
    }
    if (!Object.keys(entry).length) return;
    if (!payload[card.kind]) payload[card.kind] = {};
    payload[card.kind][String(card.id)] = entry;
  });
  return payload;
}

function describeResult(result) {
  const processed = result.processed || {};
  const parts = Object.entries(processed).map(([kind, count]) => `${KIND_LABELS[kind] || kind} ${count}`);
  return parts.length ? parts.join('，') : '没有需要处理的任务';
}

async function confirmCloseout() {
  if (state.submitting) return;

  const pending = state.cards.filter((card) => !planFor(card).skip);
  if (!pending.length) {
    showToast('所有任务都已勾选跳过，没有需要结课的内容', 'info');
    return;
  }
  const scoring = pending.filter(
    (card) => SCORABLE_KINDS.has(card.kind) && Number(card.unsubmitted_count || 0) > 0
  );
  const scoreNote = scoring.length ? `\n其中 ${scoring.length} 项作业/测验会给未提交者写默认分。` : '';
  const confirmed = window.confirm(
    `确认结课？将收尾 ${pending.length} 项任务，学生此后无法再提交或参与。${scoreNote}\n\n此操作不可批量撤销。`
  );
  if (!confirmed) return;

  const btn = document.getElementById('closeout-confirm-btn');
  const original = btn?.innerHTML;
  state.submitting = true;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<div class="spinner spinner-sm mr-2"></div> 结课中...';
  }

  try {
    const response = await fetch(`/api/classroom/${state.classOfferingId}/closeout/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildPayload()),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.message || '结课失败');

    const failures = Array.isArray(data.failures) ? data.failures : [];
    if (failures.length) {
      const names = failures.map((f) => `${f.kind_label}「${f.title || f.id}」`).join('、');
      showToast(`已结课 ${data.processed_total || 0} 项，但 ${failures.length} 项失败：${names}`, 'warning', 8000);
      await loadSummary();
      return;
    }

    showToast(`结课完成：${describeResult(data)}`, 'success');
    closeModal(MODAL_ID);
    setTimeout(() => window.location.reload(), 900);
  } catch (error) {
    showToast(error.message || '结课失败', 'error');
  } finally {
    state.submitting = false;
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }
}

function init() {
  const trigger = document.querySelector('[data-classroom-closeout-open]');
  if (!trigger) return;

  state.classOfferingId = Number(window.APP_CONFIG?.classOfferingId || 0);
  if (!state.classOfferingId) return;

  trigger.addEventListener('click', () => {
    trigger.closest('details')?.removeAttribute('open');
    openModal(MODAL_ID);
    loadSummary();
  });

  document.getElementById('closeout-confirm-btn')?.addEventListener('click', confirmCloseout);
  document.getElementById('closeout-refresh-btn')?.addEventListener('click', loadSummary);
  document.querySelectorAll('[data-closeout-close]').forEach((el) => {
    el.addEventListener('click', () => closeModal(MODAL_ID));
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
