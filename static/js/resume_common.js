/* Resume console shared helpers — window.RZ.
   Plain JS (no module), loaded before each page script. Provides fetch/toast/
   modal/markdown utilities so the per-page scripts stay focused. */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
    });
  }

  // Minimal, safe markdown → HTML (paragraphs, bullets, **bold**). Matches the
  // server-side render so the preview reads the same.
  function md(text) {
    var raw = String(text == null ? '' : text).trim();
    if (!raw) return '<p style="color:#9aa">（空）</p>';
    var blocks = raw.replace(/\r\n/g, '\n').split(/\n\n+/);
    return blocks.map(function (block) {
      block = block.trim();
      if (/^[-*]\s/.test(block)) {
        var items = block.split('\n').filter(Boolean).map(function (line) {
          return '<li>' + bold(esc(line.replace(/^[-*]\s+/, ''))) + '</li>';
        }).join('');
        return '<ul>' + items + '</ul>';
      }
      return '<p>' + bold(esc(block)).replace(/\n/g, '<br>') + '</p>';
    }).join('');
  }
  function bold(s) { return s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'); }

  function toast(message, type) {
    var box = document.getElementById('toast-container');
    if (!box) { if (type === 'error') alert(message); return; }
    var el = document.createElement('div');
    el.className = 'toast toast-' + (type || 'info');
    el.textContent = message;
    el.style.cssText = 'background:' + (type === 'error' ? '#dc2626' : type === 'success' ? '#16a34a' : '#334155') +
      ';color:#fff;padding:10px 16px;border-radius:10px;margin-top:8px;box-shadow:0 10px 30px -10px rgba(0,0,0,.4);font-weight:600;font-size:.86rem;max-width:340px';
    box.appendChild(el);
    setTimeout(function () { el.style.transition = 'opacity .3s'; el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 300); }, 2600);
  }

  async function api(url, opts) {
    opts = opts || {};
    var init = { credentials: 'same-origin', headers: {} };
    if (opts.method) init.method = opts.method;
    if (opts.body !== undefined && !(opts.body instanceof FormData)) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    } else if (opts.body instanceof FormData) {
      init.body = opts.body;
    }
    var resp = await fetch(url, init);
    var data = null;
    try { data = await resp.json(); } catch (e) { data = null; }
    if (!resp.ok) {
      var msg = (data && (data.detail || data.error || data.message)) || ('请求失败 (' + resp.status + ')');
      throw new Error(typeof msg === 'string' ? msg : '请求失败');
    }
    return data;
  }

  function track(eventName, context, surface) {
    var eventId = 'evt-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
    fetch('/api/career-tools/events', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        surface: surface || 'resume', event_name: eventName,
        context: context || {}, client_event_id: eventId
      })
    }).catch(function () {});
  }

  // Modal: builds an overlay, returns { root, body, foot, close }. The caller fills body.
  function openModal(opts) {
    opts = opts || {};
    var root = document.createElement('div');
    root.className = 'rz-modal';
    root.innerHTML =
      '<div class="rz-modal__panel ' + (opts.wide ? 'rz-modal__panel--wide' : '') + '">' +
      '<div class="rz-modal__head"><h3>' + esc(opts.title || '') + '</h3>' +
      '<button type="button" class="rz-modal__close" aria-label="关闭">&times;</button></div>' +
      '<div class="rz-modal__body"></div>' +
      '<div class="rz-modal__foot"></div></div>';
    document.body.appendChild(root);
    var panel = root.querySelector('.rz-modal__panel');
    function close() {
      root.classList.remove('show');
      setTimeout(function () { root.remove(); }, 200);
    }
    root.addEventListener('click', function (e) { if (e.target === root) close(); });
    root.querySelector('.rz-modal__close').addEventListener('click', close);
    document.addEventListener('keydown', function onEsc(e) {
      if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onEsc); }
    });
    requestAnimationFrame(function () { root.classList.add('show'); });
    return { root: root, panel: panel, body: root.querySelector('.rz-modal__body'),
      foot: root.querySelector('.rz-modal__foot'), close: close };
  }

  function confirmDialog(message, onYes) {
    var m = openModal({ title: '确认操作' });
    m.body.innerHTML = '<p style="margin:0;font-size:.94rem">' + esc(message) + '</p>';
    var cancel = document.createElement('button'); cancel.className = 'rz-btn'; cancel.textContent = '取消';
    var ok = document.createElement('button'); ok.className = 'rz-btn rz-btn--danger'; ok.textContent = '确定删除';
    cancel.onclick = m.close;
    ok.onclick = function () { m.close(); onYes(); };
    m.foot.appendChild(cancel); m.foot.appendChild(ok);
  }

  function fmtRange(a, b) {
    a = (a || '').trim(); b = (b || '').trim();
    if (a && b) return formatMonthLabel(a) + ' ~ ' + formatMonthLabel(b);
    return formatMonthLabel(a || b || '');
  }

  var MONTH_NAMES = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月'];
  var MONTH_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"></rect><path d="M16 2v4M8 2v4M3 10h18"></path></svg>';

  function normalizeMonth(value) {
    var match = String(value || '').trim().match(/^(\d{4})-(\d{1,2})/);
    if (!match) return '';
    var month = Math.max(1, Math.min(12, parseInt(match[2], 10) || 1));
    return match[1] + '-' + String(month).padStart(2, '0');
  }

  function currentMonthValue() {
    var now = new Date();
    return now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');
  }

  function monthYear(value) {
    var normalized = normalizeMonth(value);
    if (normalized) return parseInt(normalized.slice(0, 4), 10);
    return new Date().getFullYear();
  }

  function monthValue(year, monthIndex) {
    return String(year) + '-' + String(monthIndex + 1).padStart(2, '0');
  }

  function compareMonth(a, b) {
    a = normalizeMonth(a); b = normalizeMonth(b);
    if (!a && !b) return 0;
    if (!a) return -1;
    if (!b) return 1;
    return a === b ? 0 : (a > b ? 1 : -1);
  }

  function formatMonthLabel(value) {
    value = normalizeMonth(value);
    if (!value) return '';
    return value.slice(0, 4) + '年' + value.slice(5, 7) + '月';
  }

  function monthPickerHtml(name, value, opts) {
    opts = opts || {};
    value = normalizeMonth(value);
    var placeholder = opts.placeholder || '请选择年月';
    var label = formatMonthLabel(value) || placeholder;
    return '<div class="rz-month-field" data-rz-month-picker data-year="' + monthYear(value) + '">' +
      '<input type="hidden" name="' + esc(name) + '" value="' + esc(value) + '">' +
      '<button type="button" class="rz-month-trigger" data-rz-month-open aria-expanded="false">' +
      '<span class="rz-month-trigger__label' + (value ? '' : ' is-placeholder') + '">' + esc(label) + '</span>' +
      '<span class="rz-month-trigger__icon">' + MONTH_ICON + '</span></button>' +
      '<div class="rz-month-panel" data-rz-month-panel hidden></div></div>';
  }

  function monthRangePickerHtml(startName, endName, values, opts) {
    values = values || {}; opts = opts || {};
    var start = normalizeMonth(values.start);
    var end = normalizeMonth(values.end);
    var anchor = start || end || currentMonthValue();
    return '<div class="rz-month-range" data-rz-month-range data-role="start" data-year="' + monthYear(anchor) + '">' +
      '<input type="hidden" name="' + esc(startName) + '" value="' + esc(start) + '" data-rz-range-start>' +
      '<input type="hidden" name="' + esc(endName) + '" value="' + esc(end) + '" data-rz-range-end>' +
      '<button type="button" class="rz-month-trigger" data-rz-month-open aria-expanded="false">' +
      '<span class="rz-month-trigger__label' + (start || end ? '' : ' is-placeholder') + '">' +
      esc(rangeLabel(start, end, opts.placeholder || '请选择起止年月')) + '</span>' +
      '<span class="rz-month-trigger__icon">' + MONTH_ICON + '</span></button>' +
      '<div class="rz-month-panel rz-month-panel--range" data-rz-month-panel hidden></div></div>';
  }

  function rangeLabel(start, end, placeholder) {
    if (start && end) return formatMonthLabel(start) + ' 至 ' + formatMonthLabel(end);
    if (start) return '开始 ' + formatMonthLabel(start) + '，请选择结束';
    if (end) return '结束 ' + formatMonthLabel(end);
    return placeholder;
  }

  function closeMonthPickers(except) {
    document.querySelectorAll('[data-rz-month-picker], [data-rz-month-range]').forEach(function (root) {
      if (except && root === except) return;
      root.classList.remove('is-open');
      var panel = root.querySelector('[data-rz-month-panel]');
      var trigger = root.querySelector('[data-rz-month-open]');
      if (panel) panel.hidden = true;
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
    });
  }

  function toggleMonthPanel(root, open) {
    var panel = root.querySelector('[data-rz-month-panel]');
    var trigger = root.querySelector('[data-rz-month-open]');
    if (!panel) return;
    if (open) {
      if (root.matches('[data-rz-month-picker]')) {
        var value = root.querySelector('input[type="hidden"]')?.value || '';
        root.dataset.year = String(monthYear(value || currentMonthValue()));
        renderSingleMonthPicker(root);
      } else if (root.matches('[data-rz-month-range]')) {
        var start = root.querySelector('[data-rz-range-start]')?.value || '';
        var end = root.querySelector('[data-rz-range-end]')?.value || '';
        root.dataset.year = String(monthYear(start || end || currentMonthValue()));
        renderRangeMonthPicker(root);
      }
      closeMonthPickers(root);
      panel.hidden = false;
      root.classList.add('is-open');
      if (trigger) trigger.setAttribute('aria-expanded', 'true');
    } else {
      panel.hidden = true;
      root.classList.remove('is-open');
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
    }
  }

  function setTriggerLabel(root, text, isPlaceholder) {
    var label = root.querySelector('.rz-month-trigger__label');
    if (!label) return;
    label.textContent = text;
    label.classList.toggle('is-placeholder', !!isPlaceholder);
  }

  function renderSingleMonthPicker(root) {
    var input = root.querySelector('input[type="hidden"]');
    var panel = root.querySelector('[data-rz-month-panel]');
    if (!input || !panel) return;
    var selected = normalizeMonth(input.value);
    input.value = selected;
    var year = parseInt(root.dataset.year || monthYear(selected), 10) || new Date().getFullYear();
    root.dataset.year = String(year);
    setTriggerLabel(root, formatMonthLabel(selected) || '请选择年月', !selected);
    var now = currentMonthValue();
    panel.innerHTML = monthPanelHead(year) + '<div class="rz-month-grid">' +
      MONTH_NAMES.map(function (label, index) {
        var value = monthValue(year, index);
        var cls = 'rz-month-option' + (value === selected ? ' is-selected' : '') + (value === now ? ' is-current' : '');
        return '<button type="button" class="' + cls + '" data-rz-month-value="' + value + '">' + label + '</button>';
      }).join('') + '</div><div class="rz-month-panel__actions">' +
      '<button type="button" data-rz-month-clear>清空</button>' +
      '<button type="button" data-rz-month-today>本月</button></div>';
  }

  function renderRangeMonthPicker(root) {
    var startInput = root.querySelector('[data-rz-range-start]');
    var endInput = root.querySelector('[data-rz-range-end]');
    var panel = root.querySelector('[data-rz-month-panel]');
    if (!startInput || !endInput || !panel) return;
    var start = normalizeMonth(startInput.value);
    var end = normalizeMonth(endInput.value);
    startInput.value = start; endInput.value = end;
    var year = parseInt(root.dataset.year || monthYear(start || end), 10) || new Date().getFullYear();
    root.dataset.year = String(year);
    var role = root.dataset.role === 'end' ? 'end' : 'start';
    setTriggerLabel(root, rangeLabel(start, end, '请选择起止年月'), !(start || end));
    var now = currentMonthValue();
    panel.innerHTML = '<div class="rz-month-range__roles" role="tablist" aria-label="选择时间类型">' +
      '<button type="button" data-rz-range-role="start" class="' + (role === 'start' ? 'is-active' : '') + '">开始</button>' +
      '<button type="button" data-rz-range-role="end" class="' + (role === 'end' ? 'is-active' : '') + '">结束</button></div>' +
      monthPanelHead(year) + '<div class="rz-month-grid">' +
      MONTH_NAMES.map(function (label, index) {
        var value = monthValue(year, index);
        var inRange = start && end && compareMonth(value, start) >= 0 && compareMonth(value, end) <= 0;
        var cls = 'rz-month-option' + (value === now ? ' is-current' : '') +
          (inRange ? ' is-range' : '') + (value === start ? ' is-start' : '') + (value === end ? ' is-end' : '');
        return '<button type="button" class="' + cls + '" data-rz-month-value="' + value + '">' + label + '</button>';
      }).join('') + '</div><div class="rz-month-panel__result">' +
      esc(rangeLabel(start, end, '先选开始，再选结束')) + '</div><div class="rz-month-panel__actions">' +
      '<button type="button" data-rz-month-clear>清空</button>' +
      '<button type="button" data-rz-month-today>本月</button></div>';
  }

  function monthPanelHead(year) {
    return '<div class="rz-month-panel__head">' +
      '<button type="button" data-rz-month-nav="-1" aria-label="上一年">‹</button>' +
      '<strong>' + year + '年</strong>' +
      '<button type="button" data-rz-month-nav="1" aria-label="下一年">›</button></div>';
  }

  function bindMonthGlobals() {
    if (document.__rzMonthPickerBound) return;
    document.__rzMonthPickerBound = true;
    document.addEventListener('click', function (event) {
      if (!event.target.closest('[data-rz-month-picker], [data-rz-month-range]')) closeMonthPickers();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeMonthPickers();
    });
  }

  function initMonthPickers(scope) {
    scope = scope || document;
    bindMonthGlobals();
    scope.querySelectorAll('[data-rz-month-picker]').forEach(function (root) {
      if (root.dataset.rzMonthReady) { renderSingleMonthPicker(root); return; }
      root.dataset.rzMonthReady = '1';
      renderSingleMonthPicker(root);
      root.addEventListener('click', function (event) {
        event.stopPropagation();
        var trigger = event.target.closest('[data-rz-month-open]');
        if (trigger) { toggleMonthPanel(root, !root.classList.contains('is-open')); return; }
        var nav = event.target.closest('[data-rz-month-nav]');
        if (nav) { root.dataset.year = String((parseInt(root.dataset.year, 10) || new Date().getFullYear()) + parseInt(nav.dataset.rzMonthNav, 10)); renderSingleMonthPicker(root); return; }
        var month = event.target.closest('[data-rz-month-value]');
        if (month) {
          root.querySelector('input[type="hidden"]').value = month.dataset.rzMonthValue;
          renderSingleMonthPicker(root); toggleMonthPanel(root, false); return;
        }
        if (event.target.closest('[data-rz-month-clear]')) {
          root.querySelector('input[type="hidden"]').value = '';
          renderSingleMonthPicker(root); toggleMonthPanel(root, false); return;
        }
        if (event.target.closest('[data-rz-month-today]')) {
          var today = currentMonthValue();
          root.dataset.year = today.slice(0, 4);
          root.querySelector('input[type="hidden"]').value = today;
          renderSingleMonthPicker(root); toggleMonthPanel(root, false);
        }
      });
    });
    scope.querySelectorAll('[data-rz-month-range]').forEach(function (root) {
      if (root.dataset.rzMonthReady) { renderRangeMonthPicker(root); return; }
      root.dataset.rzMonthReady = '1';
      renderRangeMonthPicker(root);
      root.addEventListener('click', function (event) {
        event.stopPropagation();
        var trigger = event.target.closest('[data-rz-month-open]');
        if (trigger) { toggleMonthPanel(root, !root.classList.contains('is-open')); return; }
        var role = event.target.closest('[data-rz-range-role]');
        if (role) { root.dataset.role = role.dataset.rzRangeRole || 'start'; renderRangeMonthPicker(root); return; }
        var nav = event.target.closest('[data-rz-month-nav]');
        if (nav) { root.dataset.year = String((parseInt(root.dataset.year, 10) || new Date().getFullYear()) + parseInt(nav.dataset.rzMonthNav, 10)); renderRangeMonthPicker(root); return; }
        var startInput = root.querySelector('[data-rz-range-start]');
        var endInput = root.querySelector('[data-rz-range-end]');
        var month = event.target.closest('[data-rz-month-value]');
        if (month) {
          var selected = month.dataset.rzMonthValue;
          if (root.dataset.role === 'end') {
            endInput.value = selected;
            if (!startInput.value || compareMonth(selected, startInput.value) < 0) startInput.value = selected;
            renderRangeMonthPicker(root);
            if (startInput.value && endInput.value) toggleMonthPanel(root, false);
            return;
          } else {
            startInput.value = selected;
            if (endInput.value && compareMonth(endInput.value, selected) < 0) endInput.value = selected;
            root.dataset.role = 'end';
          }
          renderRangeMonthPicker(root); return;
        }
        if (event.target.closest('[data-rz-month-clear]')) {
          startInput.value = ''; endInput.value = ''; root.dataset.role = 'start';
          renderRangeMonthPicker(root); return;
        }
        if (event.target.closest('[data-rz-month-today]')) {
          var today = currentMonthValue();
          root.dataset.year = today.slice(0, 4);
          if (root.dataset.role === 'end') endInput.value = today;
          else { startInput.value = today; if (endInput.value && compareMonth(endInput.value, today) < 0) endInput.value = today; root.dataset.role = 'end'; }
          renderRangeMonthPicker(root);
        }
      });
    });
  }

  function syncMonthPickers(scope) {
    scope = scope || document;
    scope.querySelectorAll('[data-rz-month-picker]').forEach(renderSingleMonthPicker);
    scope.querySelectorAll('[data-rz-month-range]').forEach(renderRangeMonthPicker);
  }

  window.RZ = { esc: esc, md: md, toast: toast, api: api, track: track, openModal: openModal,
    confirmDialog: confirmDialog, fmtRange: fmtRange, monthPickerHtml: monthPickerHtml,
    monthRangePickerHtml: monthRangePickerHtml, initMonthPickers: initMonthPickers,
    syncMonthPickers: syncMonthPickers, formatMonthLabel: formatMonthLabel,
    compareMonth: compareMonth };
})();
