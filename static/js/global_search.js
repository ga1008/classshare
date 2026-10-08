// 全局搜索浮层：顶栏按钮或 "/" 快捷键唤起，一框搜 课堂/材料/作业考试/博客。
// 结果由 /api/global-search 按角色圈定范围返回，前端只做渲染与跳转。
import { getLayerSystem } from './lq/layer.js';

const DEBOUNCE_MS = 220;
const MIN_QUERY_LENGTH = 2;

let overlay = null;
let debounceTimer = 0;
let activeRequest = 0;
let layerHandle = null;
let opened = false;

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function buildOverlay() {
  const node = document.createElement('div');
  node.className = 'lq-domain-region global-search-overlay';
  node.dataset.lqComponent = 'layer';
  node.dataset.lqPresence = 'domain';
  node.hidden = true;
  node.innerHTML = `
    <style>
      .global-search-overlay {
        position: fixed; inset: 0; z-index: 6000;
        display: flex; justify-content: center; align-items: flex-start;
        padding: 9vh 16px 16px;
      }
      .global-search-panel {
        width: min(620px, 100%); max-height: 72vh; display: flex; flex-direction: column;
        overflow: hidden;
      }
      .global-search-input-row { display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid rgba(148,163,184,.25); }
      .global-search-input-row svg { flex-shrink: 0; }
      .global-search-input-row input {
        flex: 1; min-width: 0;
      }
      .global-search-results { overflow-y: auto; padding: 10px 10px 14px; }
      .global-search-group-title { padding: 10px 10px 4px; font-size: .72rem; font-weight: 800; color: #94a3b8; letter-spacing: .06em; }
      .global-search-item {
        display: block; padding: 10px 12px; text-decoration: none;
      }
      .global-search-item strong { display: block; font-size: .9rem; }
      .global-search-item small { color: #64748b; font-size: .74rem; }
      .global-search-hint { padding: 26px 16px; text-align: center; color: #94a3b8; font-size: .85rem; }
      .global-search-close { font-size: .78rem; }
    </style>
    <div data-lq-component="surface" data-lq-material="raised" class="lq-surface lq-domain-raised global-search-panel" role="dialog" aria-modal="true" aria-label="全局搜索">
      <div class="global-search-input-row">
        <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        <input data-lq-component="input" class="lq-input" type="search" placeholder="搜索课堂、材料、作业考试、博客…" data-global-search-input autocomplete="off">
        <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--ghost global-search-close" data-global-search-close>Esc 关闭</button>
      </div>
      <div class="global-search-results" data-global-search-results>
        <div class="global-search-hint">输入至少 ${MIN_QUERY_LENGTH} 个字开始搜索。</div>
      </div>
    </div>`;
  node.addEventListener('click', (event) => {
    if (event.target === node) closeOverlay();
  });
  node.querySelector('[data-global-search-close]').addEventListener('click', closeOverlay);
  const input = node.querySelector('[data-global-search-input]');
  input.addEventListener('input', () => {
    activeRequest += 1;
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => runSearch(input.value), DEBOUNCE_MS);
  });
  document.body.appendChild(node);
  return node;
}

function renderResults(payload) {
  const container = overlay.querySelector('[data-global-search-results]');
  const groups = payload.groups || [];
  if (!groups.length) {
    const message = (payload.query || '').length < MIN_QUERY_LENGTH
      ? `输入至少 ${MIN_QUERY_LENGTH} 个字开始搜索。`
      : '没有找到相关内容，换个关键词试试。';
    container.innerHTML = `<div class="global-search-hint">${escapeHtml(message)}</div>`;
    return;
  }
  container.innerHTML = groups.map((group) => `
    <div class="global-search-group">
      <div class="global-search-group-title">${escapeHtml(group.kind_label)}</div>
      ${(group.results || []).map((item) => `
        <a data-lq-component="choice" data-lq-shape="surface" class="lq-btn lq-btn--sm lq-btn--glass lq-domain-choice global-search-item" href="${escapeHtml(item.link_url)}">
          <strong>${escapeHtml(item.title)}</strong>
          <small>${escapeHtml(item.subtitle || '')}</small>
        </a>
      `).join('')}
    </div>
  `).join('');
}

async function runSearch(rawQuery) {
  const query = String(rawQuery || '').trim();
  const requestId = ++activeRequest;
  if (query.length < MIN_QUERY_LENGTH) {
    renderResults({ query, groups: [] });
    return;
  }
  try {
    const response = await fetch(`/api/global-search?q=${encodeURIComponent(query)}`, {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    });
    const payload = await response.json().catch(() => ({}));
    if (!opened || requestId !== activeRequest) return; // 过期响应丢弃
    if (!response.ok || payload.status !== 'success') {
      renderResults({ query, groups: [] });
      return;
    }
    renderResults(payload);
  } catch {
    if (opened && requestId === activeRequest) renderResults({ query, groups: [] });
  }
}

function openOverlay(event) {
  if (!overlay) overlay = buildOverlay();
  opened = true;
  activeRequest += 1;
  window.clearTimeout(debounceTimer);
  const input = overlay.querySelector('[data-global-search-input]');
  input.value = '';
  renderResults({ query: '', groups: [] });
  const layer = getLayerSystem(document);
  layer.getPortalHost({ trigger: event?.currentTarget instanceof Element ? event.currentTarget : document.activeElement }).append(overlay);
  layerHandle = layer.open(overlay, {
    type: 'modal', surface: overlay.querySelector('.global-search-panel'),
    trigger: event?.currentTarget instanceof Element ? event.currentTarget : document.activeElement,
    initialFocus: input,
    onCloseRequested: () => { opened = false; activeRequest += 1; window.clearTimeout(debounceTimer); },
    onClose: () => { layerHandle = null; },
    onDestroy: () => { opened = false; activeRequest += 1; window.clearTimeout(debounceTimer); layerHandle = null; },
  });
}

function closeOverlay() {
  if (layerHandle) return getLayerSystem(document).close(layerHandle, 'button');
}

function isTypingContext(target) {
  if (!(target instanceof Element)) return false;
  return Boolean(target.closest('input, textarea, select, [contenteditable="true"]'));
}

export function initGlobalSearch() {
  document.querySelectorAll('[data-global-search-open]').forEach((trigger) => {
    trigger.addEventListener('click', openOverlay);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === '/' && !isTypingContext(event.target)) {
      event.preventDefault();
      openOverlay();
    }
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initGlobalSearch);
} else {
  initGlobalSearch();
}
