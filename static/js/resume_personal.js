/* Personal info page — load form, save with validation, AI suggestions, avatar. */
(function () {
  'use strict';
  var RZ = window.RZ;
  var formRevision = 0;

  var FIELD_DEFS = [
    { key: 'name', label: '姓名', type: 'text' },
    { key: 'gender', label: '性别', type: 'select', options: ['男', '女', '其他'] },
    { key: 'birthday', label: '生日', type: 'month' },
    { key: 'phone', label: '手机号（与邮箱至少填一项）', type: 'text' },
    { key: 'email', label: '邮箱（与手机号至少填一项）', type: 'email' },
    { key: 'qq', label: 'QQ', type: 'text' },
    { key: 'wechat', label: '微信', type: 'text' },
    { key: 'expected_position', label: '期望岗位', type: 'position' },
    { key: 'expected_industry', label: '期望行业', type: 'text' },
    { key: 'expected_salary', label: '期望薪资', type: 'text' },
    { key: 'hometown', label: '籍贯', type: 'text' },
    { key: 'address', label: '现居地址', type: 'text', full: true }
  ];
  var REQUIRED = ['name', 'expected_position'];
  var POSITION_OPTIONS = [];

  function fieldHtml(def) {
    var req = REQUIRED.indexOf(def.key) >= 0 ? '<span class="req">*</span>' : '';
    var input;
    if (def.type === 'select') {
      input = '<select class="rz-select" name="' + def.key + '"><option value="">请选择</option>' +
        def.options.map(function (o) { return '<option value="' + o + '">' + o + '</option>'; }).join('') + '</select>';
    } else if (def.type === 'month') {
      input = RZ.monthPickerHtml(def.key, '', { placeholder: '请选择生日年月' });
    } else if (def.type === 'position') {
      input = '<div class="rz-combo" data-rz-position-combo>' +
        '<input class="rz-input rz-combo__input" type="text" name="' + def.key + '" autocomplete="off" placeholder="选择推荐岗位或直接输入">' +
        '<button type="button" class="rz-combo__toggle" data-rz-combo-toggle aria-label="展开推荐岗位" aria-expanded="false">' +
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"></path></svg>' +
        '</button><div class="rz-combo__menu" data-rz-combo-menu hidden></div></div>';
    } else {
      input = '<input class="rz-input" type="' + (def.type === 'email' ? 'email' : 'text') + '" name="' + def.key + '">';
    }
    return '<div class="rz-field' + (def.full ? ' rz-field--full' : '') + '">' +
      '<label>' + RZ.esc(def.label) + req + '</label>' + input + '</div>';
  }

  function fill(info) {
    FIELD_DEFS.forEach(function (def) {
      var el = document.querySelector('[name="' + def.key + '"]');
      if (el) el.value = info[def.key] || '';
    });
    RZ.syncMonthPickers(document.getElementById('rzFields'));
    renderPositionCombo();
  }

  function collect() {
    var out = {};
    FIELD_DEFS.forEach(function (def) {
      var el = document.querySelector('[name="' + def.key + '"]');
      out[def.key] = el ? el.value.trim() : '';
    });
    return out;
  }

  async function load() {
    var fields = document.getElementById('rzFields');
    fields.innerHTML = FIELD_DEFS.map(fieldHtml).join('');
    RZ.initMonthPickers(fields);
    bindPositionCombo(fields);
    try {
      var data = await RZ.api('/api/resume/personal');
      formRevision = Number((data.info || {}).revision || data.revision || 0);
      POSITION_OPTIONS = Array.isArray(data.position_options) ? data.position_options : [];
      fill(data.info || {});
    } catch (e) { RZ.toast(e.message, 'error'); }
  }

  function normalize(value) {
    return String(value || '').trim().toLowerCase();
  }

  function positionMatches(option, query) {
    if (!query) return true;
    var text = [option.label, option.value, option.tag, option.meta].join(' ').toLowerCase();
    return text.indexOf(query.toLowerCase()) >= 0;
  }

  function filteredPositionOptions(query) {
    return POSITION_OPTIONS.filter(function (option) { return option && positionMatches(option, query); }).slice(0, 8);
  }

  function positionOptionHtml(option, active) {
    var label = option.label || option.value || '';
    var meta = option.meta || option.tag || '职业推荐';
    return '<button type="button" class="rz-combo__option' + (active ? ' is-active' : '') + '" data-rz-position-value="' + RZ.esc(label) + '">' +
      '<span class="rz-combo__name">' + RZ.esc(label) + '</span>' +
      '<span class="rz-combo__meta">' + RZ.esc(meta) + '</span>' +
      '</button>';
  }

  function renderPositionCombo() {
    var root = document.querySelector('[data-rz-position-combo]');
    if (!root) return;
    var input = root.querySelector('[name="expected_position"]');
    var menu = root.querySelector('[data-rz-combo-menu]');
    if (!input || !menu) return;
    var query = input.value.trim();
    var options = filteredPositionOptions(query);
    var exists = POSITION_OPTIONS.some(function (option) {
      return normalize(option.value || option.label) === normalize(query);
    });
    var html = '';
    if (query && !exists) {
      html += '<button type="button" class="rz-combo__option rz-combo__option--custom is-active" data-rz-position-value="' + RZ.esc(query) + '">' +
        '<span class="rz-combo__name">' + RZ.esc(query) + '</span>' +
        '<span class="rz-combo__meta">自定义</span></button>';
    }
    html += options.map(function (option, index) {
      return positionOptionHtml(option, !query && index === 0);
    }).join('');
    if (!html) {
      html = '<div class="rz-combo__empty">暂无职业推荐岗位</div>';
    }
    menu.innerHTML = html;
    root.dataset.activeIndex = String(Math.max(0, Array.prototype.findIndex.call(menu.querySelectorAll('.rz-combo__option'), function (btn) {
      return btn.classList.contains('is-active');
    })));
  }

  function setComboOpen(root, open) {
    var menu = root.querySelector('[data-rz-combo-menu]');
    var toggle = root.querySelector('[data-rz-combo-toggle]');
    if (!menu) return;
    if (open) renderPositionCombo();
    root.classList.toggle('is-open', !!open);
    menu.hidden = !open;
    if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function closePositionCombos(except) {
    document.querySelectorAll('[data-rz-position-combo]').forEach(function (root) {
      if (except && root === except) return;
      setComboOpen(root, false);
    });
  }

  function highlightPosition(root, nextIndex) {
    var buttons = root.querySelectorAll('.rz-combo__option');
    if (!buttons.length) return;
    var index = Math.max(0, Math.min(buttons.length - 1, nextIndex));
    buttons.forEach(function (button, i) { button.classList.toggle('is-active', i === index); });
    root.dataset.activeIndex = String(index);
    buttons[index].scrollIntoView({ block: 'nearest' });
  }

  function bindPositionCombo(scope) {
    var root = scope.querySelector('[data-rz-position-combo]');
    if (!root || root.dataset.rzComboReady) return;
    root.dataset.rzComboReady = '1';
    var input = root.querySelector('[name="expected_position"]');
    var menu = root.querySelector('[data-rz-combo-menu]');
    var toggle = root.querySelector('[data-rz-combo-toggle]');
    input.addEventListener('focus', function () { setComboOpen(root, true); });
    input.addEventListener('input', function () { setComboOpen(root, true); });
    input.addEventListener('keydown', function (event) {
      var isOpen = root.classList.contains('is-open');
      var buttons = menu.querySelectorAll('.rz-combo__option');
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        if (!isOpen) setComboOpen(root, true);
        highlightPosition(root, (parseInt(root.dataset.activeIndex || '-1', 10) || -1) + 1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        highlightPosition(root, (parseInt(root.dataset.activeIndex || '0', 10) || 0) - 1);
      } else if (event.key === 'Enter' && isOpen && buttons.length) {
        event.preventDefault();
        buttons[Math.max(0, parseInt(root.dataset.activeIndex || '0', 10) || 0)].click();
      } else if (event.key === 'Escape') {
        setComboOpen(root, false);
      }
    });
    if (toggle) {
      toggle.addEventListener('click', function () {
        var shouldOpen = !root.classList.contains('is-open');
        input.focus();
        setComboOpen(root, shouldOpen);
      });
    }
    if (menu) {
      menu.addEventListener('click', function (event) {
        var option = event.target.closest('[data-rz-position-value]');
        if (!option) return;
        input.value = option.dataset.rzPositionValue || '';
        setComboOpen(root, false);
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      menu.addEventListener('mousemove', function (event) {
        var option = event.target.closest('.rz-combo__option');
        if (!option) return;
        var buttons = Array.prototype.slice.call(menu.querySelectorAll('.rz-combo__option'));
        highlightPosition(root, buttons.indexOf(option));
      });
    }
    if (!document.__rzPositionComboBound) {
      document.__rzPositionComboBound = true;
      document.addEventListener('click', function (event) {
        if (!event.target.closest('[data-rz-position-combo]')) closePositionCombos();
      });
    }
  }

  function init() {
    var form = document.getElementById('rzPersonalForm');
    var personalWriting = false, avatarInput = document.getElementById('rzAvatarInput');
    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      if (personalWriting) return;
      var payload = collect();
      payload.revision = formRevision;
      var missing = REQUIRED.filter(function (k) { return !payload[k]; });
      if (missing.length) { RZ.toast('请填写必填项', 'error'); return; }
      if (!payload.email && !payload.phone) { RZ.toast('请至少填写邮箱或手机号', 'error'); return; }
      var btn = form.querySelector('button[type="submit"]');
      personalWriting = true; btn.disabled = true; avatarInput.disabled = true;
      try {
        var saved = await RZ.api('/api/resume/personal', { method: 'POST', body: payload });
        formRevision = Number(saved.revision || (saved.info || {}).revision || formRevision + 1);
        RZ.toast('已保存', 'success');
      } catch (err) { RZ.conflict(err, payload, load); }
      finally { personalWriting = false; btn.disabled = false; avatarInput.disabled = false; }
    });

    document.getElementById('rzSeedBtn').addEventListener('click', async function () {
      try {
        var d = await RZ.api('/api/resume/personal');
        formRevision = Number((d.info || {}).revision || d.revision || 0);
        POSITION_OPTIONS = Array.isArray(d.position_options) ? d.position_options : POSITION_OPTIONS;
        fill(d.info || {});
        RZ.toast('已带入平台资料', 'success');
      }
      catch (e) { RZ.toast(e.message, 'error'); }
    });

    function reviewPersonalSuggestion(data, forget, meta) {
      var suggestions = data.suggestions || {}, baseline = collect();
      var available = FIELD_DEFS.filter(function (field) { return suggestions[field.key] != null && String(suggestions[field.key]).trim(); });
      if (!available.length) { RZ.toast(data.error || '暂无可用建议', 'info'); forget(); return; }
      var review = RZ.openModal({ title: '核对个人资料建议', wide: true });
      review.body.innerHTML = '<p>勾选需要采用的字段，确认后填入当前表单；保存后才会更新资料。</p>' +
        (meta.profile_revision != null && Number(meta.profile_revision) !== formRevision ? '<p>生成期间资料版本已变化，请逐项重新核对。</p>' : '') +
        available.map(function (field) { return '<section class="rz-snapshot-fields"><label><input type="checkbox" data-suggest-field="' + field.key + '"' + (!baseline[field.key] ? ' checked' : '') + '> ' + RZ.esc(field.label) + '</label><div class="rz-candidate-compare"><p>当前：' + RZ.esc(baseline[field.key] || '未填写') + '</p><p>建议：' + RZ.esc(suggestions[field.key]) + '</p></div></section>'; }).join('');
      var apply = document.createElement('button'); apply.className = 'rz-btn rz-btn--primary'; apply.textContent = '采用选中的建议';
      apply.onclick = function () {
        review.body.querySelectorAll('[data-suggest-field]:checked').forEach(function (check) {
          var input = document.querySelector('[name="' + check.dataset.suggestField + '"]');
          if (input && input.value === (baseline[check.dataset.suggestField] || '')) input.value = String(suggestions[check.dataset.suggestField]);
        });
        renderPositionCombo(); forget(); review.close(); RZ.toast('已填入所选建议，请核对后保存', 'success');
      };
      var keep = document.createElement('button'); keep.className = 'rz-btn'; keep.textContent = '保留现有信息'; keep.onclick = function () { forget(); review.close(); };
      review.foot.appendChild(keep); review.foot.appendChild(apply);
    }
    var suggestButton = document.getElementById('rzSuggestBtn');
    var recoverSuggestion = document.createElement('button'); recoverSuggestion.type = 'button'; recoverSuggestion.className = 'rz-btn'; recoverSuggestion.textContent = '查看上次建议'; recoverSuggestion.hidden = !RZ.pendingSuggestion('personal'); suggestButton.after(recoverSuggestion);
    async function suggest(resume) {
      suggestButton.disabled = true;
      try { await RZ.requestSuggestion({ kind: 'personal', url: '/api/resume/personal/suggest', resume: resume, onResult: reviewPersonalSuggestion }); recoverSuggestion.hidden = false; }
      catch (error) { RZ.toast(error.message, 'error'); }
      finally { suggestButton.disabled = false; }
    }
    suggestButton.addEventListener('click', function () { suggest(false); });
    recoverSuggestion.onclick = function () { suggest(true); };

    avatarInput.addEventListener('change', async function () {
      if (personalWriting || !this.files || !this.files[0]) return;
      var fd = new FormData(); fd.append('file', this.files[0]);
      fd.append('revision', String(formRevision));
      var saveButton = form.querySelector('button[type="submit"]');
      personalWriting = true; this.disabled = true; saveButton.disabled = true;
      try {
        var d = await RZ.api('/api/resume/personal/avatar', { method: 'POST', body: fd });
        formRevision = Number(d.revision);
        var img = document.getElementById('rzAvatarImg');
        img.style.display = ''; document.getElementById('rzAvatarPh').style.display = 'none';
        img.src = d.avatar_url + (d.avatar_url.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now();
        RZ.toast('头像已更新', 'success');
      } catch (e) { RZ.conflict(e, collect(), load); }
      finally { personalWriting = false; this.disabled = false; saveButton.disabled = false; this.value = ''; }
    });

    load();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
