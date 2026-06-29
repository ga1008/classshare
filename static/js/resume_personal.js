/* Personal info page — load form, save with validation, AI suggestions, avatar. */
(function () {
  'use strict';
  var RZ = window.RZ;

  var FIELD_DEFS = [
    { key: 'name', label: '姓名', type: 'text' },
    { key: 'gender', label: '性别', type: 'select', options: ['男', '女', '其他'] },
    { key: 'birthday', label: '生日', type: 'month' },
    { key: 'phone', label: '手机号', type: 'text' },
    { key: 'email', label: '邮箱', type: 'email' },
    { key: 'qq', label: 'QQ', type: 'text' },
    { key: 'wechat', label: '微信', type: 'text' },
    { key: 'expected_position', label: '期望岗位', type: 'text' },
    { key: 'expected_industry', label: '期望行业', type: 'text' },
    { key: 'expected_salary', label: '期望薪资', type: 'text' },
    { key: 'hometown', label: '籍贯', type: 'text' },
    { key: 'address', label: '现居地址', type: 'text', full: true },
    { key: 'id_card', label: '身份证号', type: 'text', full: true }
  ];
  var REQUIRED = ['name', 'gender', 'birthday', 'email', 'expected_position'];

  function fieldHtml(def) {
    var req = REQUIRED.indexOf(def.key) >= 0 ? '<span class="req">*</span>' : '';
    var input;
    if (def.type === 'select') {
      input = '<select class="rz-select" name="' + def.key + '"><option value="">请选择</option>' +
        def.options.map(function (o) { return '<option value="' + o + '">' + o + '</option>'; }).join('') + '</select>';
    } else if (def.type === 'month') {
      input = RZ.monthPickerHtml(def.key, '', { placeholder: '请选择生日年月' });
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
    try {
      var data = await RZ.api('/api/resume/personal');
      fill(data.info || {});
    } catch (e) { RZ.toast(e.message, 'error'); }
  }

  function init() {
    var form = document.getElementById('rzPersonalForm');
    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      var payload = collect();
      var missing = REQUIRED.filter(function (k) { return !payload[k]; });
      if (missing.length) { RZ.toast('请填写必填项', 'error'); return; }
      var btn = form.querySelector('button[type="submit"]');
      btn.disabled = true;
      try {
        await RZ.api('/api/resume/personal', { method: 'POST', body: payload });
        RZ.toast('已保存', 'success');
      } catch (err) { RZ.toast(err.message, 'error'); }
      finally { btn.disabled = false; }
    });

    document.getElementById('rzSeedBtn').addEventListener('click', async function () {
      try { var d = await RZ.api('/api/resume/personal'); fill(d.info || {}); RZ.toast('已带入平台资料', 'success'); }
      catch (e) { RZ.toast(e.message, 'error'); }
    });

    document.getElementById('rzSuggestBtn').addEventListener('click', async function () {
      var btn = this; btn.disabled = true; btn.textContent = '思考中…';
      try {
        var d = await RZ.api('/api/resume/personal/suggest', { method: 'POST' });
        if (d.ok && d.suggestions && Object.keys(d.suggestions).length) {
          Object.keys(d.suggestions).forEach(function (k) {
            var el = document.querySelector('[name="' + k + '"]');
            if (el && !el.value) el.value = d.suggestions[k];
          });
          RZ.toast('已填入 AI 建议，请核对后保存', 'success');
        } else { RZ.toast(d.error || '暂无可用建议', 'info'); }
      } catch (e) { RZ.toast(e.message, 'error'); }
      finally { btn.disabled = false; btn.textContent = '✨ AI 优化建议'; }
    });

    document.getElementById('rzAvatarInput').addEventListener('change', async function () {
      if (!this.files || !this.files[0]) return;
      var fd = new FormData(); fd.append('file', this.files[0]);
      try {
        var d = await RZ.api('/api/resume/personal/avatar', { method: 'POST', body: fd });
        var img = document.getElementById('rzAvatarImg');
        img.style.display = ''; document.getElementById('rzAvatarPh').style.display = 'none';
        img.src = d.avatar_url + '&t=' + Date.now();
        RZ.toast('头像已更新', 'success');
      } catch (e) { RZ.toast(e.message, 'error'); }
      this.value = '';
    });

    load();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
