/**
 * Student: 20-point teammate peer evaluation for a group assignment.
 *
 * Opened automatically right after a group-assignment submission (the submit
 * handler calls window.openGroupPeerEval), and re-openable from the waiting
 * card via [data-open-peer-eval]. For each teammate the student picks 1-20.
 * If the student closes without rating, the server fills a fair default (16)
 * when the group is finalized — so a missed rating never blocks finalization.
 */
import { showToast, escapeHtml } from '/static/js/ui.js';

const DEFAULT_POINTS = 16;
const MAX_POINTS = 20;

let overlayEl = null;
let resolveOpen = null;

function cleanup(result) {
  if (overlayEl) {
    overlayEl.remove();
    overlayEl = null;
    document.removeEventListener('keydown', onKeydown);
  }
  if (resolveOpen) {
    const fn = resolveOpen;
    resolveOpen = null;
    fn(result);
  }
}

function onKeydown(event) {
  if (event.key === 'Escape') cleanup({ submitted: false });
}

function pointButtons(peerId) {
  let row1 = '';
  let row2 = '';
  for (let i = 1; i <= MAX_POINTS; i += 1) {
    const selected = i === DEFAULT_POINTS ? ' is-selected' : '';
    const btn = `<button type="button" class="peer-eval-pt${selected}" data-peer-id="${peerId}" data-points="${i}">${i}</button>`;
    if (i <= 10) row1 += btn; else row2 += btn;
  }
  return `<div class="peer-eval-scale"><div class="peer-eval-scale__row">${row1}</div><div class="peer-eval-scale__row">${row2}</div></div>`;
}

function peerBlock(peer) {
  return `
    <div class="peer-eval-item" data-peer-block="${peer.student_id}">
      <div class="peer-eval-item__head">
        <img class="peer-eval-item__avatar" src="${escapeHtml(peer.avatar_url || '/api/profile/avatar')}" alt="" />
        <div class="peer-eval-item__q">
          <strong>你觉得「${escapeHtml(peer.name || '组员')}」的贡献度高吗？</strong>
          <span>给 TA 在本次小组作业的表现打分（1-20 分，分数越高表示贡献越大）</span>
        </div>
        <span class="peer-eval-item__value" data-peer-value="${peer.student_id}">${DEFAULT_POINTS}</span>
      </div>
      ${pointButtons(peer.student_id)}
    </div>`;
}

function render(assignmentId, peers) {
  const selections = {};
  peers.forEach((p) => { selections[p.student_id] = DEFAULT_POINTS; });

  overlayEl = document.createElement('div');
  overlayEl.className = 'peer-eval-overlay';
  overlayEl.innerHTML = `
    <div class="peer-eval-modal" role="dialog" aria-modal="true" aria-label="小组互评">
      <header class="peer-eval-modal__header">
        <div>
          <h3 class="peer-eval-modal__title">小组互评</h3>
          <p class="peer-eval-modal__subtitle">请为每位组员的贡献度打分，评分仅教师与系统可见，组员之间互相保密。</p>
        </div>
        <button type="button" class="peer-eval-modal__close" data-peer-close aria-label="关闭">&times;</button>
      </header>
      <div class="peer-eval-modal__body">
        ${peers.map(peerBlock).join('')}
      </div>
      <footer class="peer-eval-modal__footer">
        <span class="peer-eval-modal__hint">未填写将自动按 ${DEFAULT_POINTS} 分计入</span>
        <button type="button" class="btn btn-primary" data-peer-submit>确认提交评分</button>
      </footer>
    </div>`;
  document.body.appendChild(overlayEl);
  document.addEventListener('keydown', onKeydown);

  overlayEl.addEventListener('click', (event) => {
    if (event.target === overlayEl) cleanup({ submitted: false });
    const ptBtn = event.target.closest ? event.target.closest('.peer-eval-pt') : null;
    if (ptBtn) {
      const peerId = ptBtn.getAttribute('data-peer-id');
      const points = Number(ptBtn.getAttribute('data-points'));
      selections[peerId] = points;
      overlayEl
        .querySelectorAll(`.peer-eval-pt[data-peer-id="${peerId}"]`)
        .forEach((el) => el.classList.toggle('is-selected', Number(el.getAttribute('data-points')) === points));
      const valueEl = overlayEl.querySelector(`[data-peer-value="${peerId}"]`);
      if (valueEl) valueEl.textContent = String(points);
    }
  });

  overlayEl.querySelector('[data-peer-close]').addEventListener('click', () => cleanup({ submitted: false }));

  const submitBtn = overlayEl.querySelector('[data-peer-submit]');
  submitBtn.addEventListener('click', async () => {
    const ratings = peers.map((p) => ({
      reviewee_student_id: Number(p.student_id),
      points: selections[p.student_id] || DEFAULT_POINTS,
    }));
    submitBtn.disabled = true;
    try {
      const response = await fetch(`/api/assignments/${encodeURIComponent(assignmentId)}/peer-eval`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ratings }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || payload.message || '提交失败');
      showToast('小组互评已提交', 'success');
      cleanup({ submitted: true });
    } catch (err) {
      showToast(err.message || '互评提交失败', 'error');
      submitBtn.disabled = false;
    }
  });
}

/**
 * Open the peer-eval modal for an assignment. Resolves immediately (no modal)
 * when the assignment is not a group assignment or the student has no teammates.
 * @param {string} assignmentId
 * @returns {Promise<{submitted: boolean}>}
 */
window.openGroupPeerEval = async function openGroupPeerEval(assignmentId) {
  try {
    const response = await fetch(`/api/assignments/${encodeURIComponent(assignmentId)}/peer-eval`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.is_group || !data.in_group) {
      return { submitted: false, skipped: true };
    }
    const peers = Array.isArray(data.peers) ? data.peers : [];
    if (!peers.length) {
      return { submitted: false, skipped: true };
    }
    return await new Promise((resolve) => {
      resolveOpen = resolve;
      render(assignmentId, peers);
    });
  } catch (err) {
    return { submitted: false, skipped: true };
  }
};

document.addEventListener('click', async (event) => {
  const btn = event.target.closest ? event.target.closest('[data-open-peer-eval]') : null;
  if (!btn) return;
  event.preventDefault();
  const assignmentId = btn.getAttribute('data-assignment-id');
  if (!assignmentId) return;
  const result = await window.openGroupPeerEval(assignmentId);
  if (result && result.submitted) {
    setTimeout(() => location.reload(), 600);
  }
});
