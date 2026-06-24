/**
 * Teacher: configure an assignment/exam to be completed by study group.
 *
 * A "分组配置" button on each assignment card (data-group-config-btn) opens a
 * modal where the teacher can:
 *   - mark the assignment as completed by an existing group scheme, or
 *   - create a new scheme (reusing the collaboration 新建分组 flow) and bind it,
 *   - or cancel the group requirement.
 */
import { showToast, escapeHtml } from '/static/js/ui.js';

const API = {
  get: (id) => `/api/assignments/${encodeURIComponent(id)}/group-config`,
  post: (id) => `/api/assignments/${encodeURIComponent(id)}/group-config`,
};

let overlayEl = null;

function closeModal() {
  if (overlayEl) {
    overlayEl.remove();
    overlayEl = null;
    document.removeEventListener('keydown', onKeydown);
  }
}

function onKeydown(event) {
  if (event.key === 'Escape') closeModal();
}

async function postConfig(assignmentId, body) {
  const response = await fetch(API.post(assignmentId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.message || '操作失败，请稍后重试');
  }
  return payload;
}

function schemeRow(scheme, boundSchemeId) {
  const checked = Number(boundSchemeId) === Number(scheme.id) ? 'checked' : '';
  const assigned = scheme.assigned_count || 0;
  return `
    <label class="ga-scheme-option">
      <input type="radio" name="ga-scheme" value="${scheme.id}" ${checked} />
      <span class="ga-scheme-option__body">
        <span class="ga-scheme-option__name">${escapeHtml(scheme.name || '随机分组')}</span>
        <span class="ga-scheme-option__meta">${scheme.group_count || 0} 个小组 · 已分配 ${assigned} 人 · 每组 ${scheme.min_members || 0}-${scheme.max_members || 0} 人</span>
      </span>
    </label>`;
}

function render(assignmentId, title, data) {
  const binding = data.binding;
  const schemes = data.schemes || [];
  const boundSchemeId = binding ? binding.scheme_id : null;
  const schemeListHtml = schemes.length
    ? schemes.map((s) => schemeRow(s, boundSchemeId)).join('')
    : '<p class="ga-empty">该课堂还没有分组方案，请在下方新建一个分组方案。</p>';

  overlayEl = document.createElement('div');
  overlayEl.className = 'ga-modal-overlay';
  overlayEl.innerHTML = `
    <div class="ga-modal" role="dialog" aria-modal="true" aria-label="分组配置">
      <header class="ga-modal__header">
        <div>
          <h3 class="ga-modal__title">按小组完成</h3>
          <p class="ga-modal__subtitle">${escapeHtml(title || '作业')}</p>
        </div>
        <button type="button" class="ga-modal__close" data-ga-close aria-label="关闭">&times;</button>
      </header>
      <div class="ga-modal__body">
        ${binding ? `<div class="ga-current"><span class="ga-current__dot"></span>当前已按「${escapeHtml(binding.scheme_name || '分组方案')}」完成</div>` : ''}
        <section class="ga-section">
          <h4 class="ga-section__title">选择已有分组方案</h4>
          <div class="ga-scheme-list">${schemeListHtml}</div>
        </section>
        <section class="ga-section ga-newscheme" data-ga-newscheme>
          <button type="button" class="ga-newscheme__toggle" data-ga-newscheme-toggle>
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            新建分组方案
          </button>
          <div class="ga-newscheme__form" data-ga-newscheme-form hidden>
            <label class="ga-field">
              <span>方案名称</span>
              <input type="text" data-ga-new-name placeholder="如：项目小组" maxlength="60" />
            </label>
            <div class="ga-field-row">
              <label class="ga-field">
                <span>每组最少</span>
                <input type="number" data-ga-new-min min="1" max="12" value="2" />
              </label>
              <label class="ga-field">
                <span>每组最多</span>
                <input type="number" data-ga-new-max min="1" max="12" value="5" />
              </label>
              <label class="ga-field">
                <span>组数(可选)</span>
                <input type="number" data-ga-new-count min="1" max="60" placeholder="自动" />
              </label>
            </div>
            <p class="ga-hint">新建后学生可在课堂互动区随机加入小组；也可由你在大屏拖拽分配。</p>
          </div>
        </section>
      </div>
      <footer class="ga-modal__footer">
        ${binding ? '<button type="button" class="btn btn-outline ga-btn-danger" data-ga-unbind>取消按小组</button>' : '<span></span>'}
        <div class="ga-modal__footer-right">
          <button type="button" class="btn btn-ghost" data-ga-close>关闭</button>
          <button type="button" class="btn btn-primary" data-ga-save>保存配置</button>
        </div>
      </footer>
    </div>`;
  document.body.appendChild(overlayEl);
  document.addEventListener('keydown', onKeydown);

  overlayEl.addEventListener('click', (event) => {
    if (event.target === overlayEl) closeModal();
  });
  overlayEl.querySelectorAll('[data-ga-close]').forEach((el) => el.addEventListener('click', closeModal));

  const newForm = overlayEl.querySelector('[data-ga-newscheme-form]');
  overlayEl.querySelector('[data-ga-newscheme-toggle]').addEventListener('click', () => {
    newForm.hidden = !newForm.hidden;
  });

  const unbindBtn = overlayEl.querySelector('[data-ga-unbind]');
  if (unbindBtn) {
    unbindBtn.addEventListener('click', async () => {
      unbindBtn.disabled = true;
      try {
        await postConfig(assignmentId, { action: 'unbind' });
        showToast('已取消按小组完成', 'success');
        closeModal();
      } catch (err) {
        showToast(err.message, 'error');
        unbindBtn.disabled = false;
      }
    });
  }

  const saveBtn = overlayEl.querySelector('[data-ga-save]');
  saveBtn.addEventListener('click', async () => {
    const newNameVisible = !newForm.hidden;
    let body = null;
    if (newNameVisible) {
      const min = parseInt(overlayEl.querySelector('[data-ga-new-min]').value, 10) || 2;
      const max = parseInt(overlayEl.querySelector('[data-ga-new-max]').value, 10) || 5;
      const countRaw = overlayEl.querySelector('[data-ga-new-count]').value;
      const newScheme = {
        name: overlayEl.querySelector('[data-ga-new-name]').value.trim() || '随机分组',
        min_members: min,
        max_members: max,
      };
      if (countRaw) newScheme.group_count = parseInt(countRaw, 10);
      body = { new_scheme: newScheme };
    } else {
      const selected = overlayEl.querySelector('input[name="ga-scheme"]:checked');
      if (!selected) {
        showToast('请选择一个分组方案，或新建一个', 'warning');
        return;
      }
      body = { scheme_id: Number(selected.value) };
    }
    saveBtn.disabled = true;
    try {
      await postConfig(assignmentId, body);
      showToast('已设置为按小组完成', 'success');
      closeModal();
    } catch (err) {
      showToast(err.message, 'error');
      saveBtn.disabled = false;
    }
  });
}

async function openModal(btn) {
  const assignmentId = btn.getAttribute('data-assignment-id');
  const title = btn.getAttribute('data-assignment-title') || '';
  if (!assignmentId) return;
  closeModal();
  try {
    const response = await fetch(API.get(assignmentId));
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.message || '加载失败');
    if (data.supported === false) {
      showToast(data.message || '该作业未关联教学班，无法按小组完成', 'warning');
      return;
    }
    render(assignmentId, title, data);
  } catch (err) {
    showToast(err.message || '加载分组配置失败', 'error');
  }
}

document.addEventListener('click', (event) => {
  const btn = event.target.closest ? event.target.closest('[data-group-config-btn]') : null;
  if (btn) {
    event.preventDefault();
    event.stopPropagation();
    openModal(btn);
  }
});
