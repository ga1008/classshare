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
    if (a && b) return a + ' ~ ' + b;
    return a || b || '';
  }

  window.RZ = { esc: esc, md: md, toast: toast, api: api, openModal: openModal,
    confirmDialog: confirmDialog, fmtRange: fmtRange };
})();
