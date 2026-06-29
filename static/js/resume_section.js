/* Generic list-section controller for: education / experience / skill /
   certificate / self_intro. Handles card list, create/edit modal, image
   attachments (cert/skill/experience), the self-intro AI optimize/generate
   flow, and polling while AI placeholders are generating. */
(function () {
  'use strict';
  var RZ = window.RZ;
  var box = document.getElementById('rzCards');
  var SECTION = box.dataset.section;

  var SCHEMAS = {
    education: {
      label: '学历 / 学习经历',
      sub: '从高中开始的学习经历。首次访问会由 AI 自动整理一条大学经历，可继续编辑。',
      attach: false,
      fields: [
        { key: 'kind', label: '类型', type: 'select', required: true,
          options: [['high_school', '高中'], ['university', '大学'], ['training', '培训']] },
        { key: 'school', label: '学校 / 机构名称', type: 'text', required: true, full: true },
        { key: 'college', label: '学院（大学填写）', type: 'text' },
        { key: 'major', label: '专业（大学填写）', type: 'text' },
        { key: 'start_date', label: '开始时间', type: 'month', required: true },
        { key: 'end_date', label: '结束时间', type: 'month', required: true },
        { key: 'content', label: '学习内容', type: 'textarea', full: true }
      ]
    },
    experience: {
      label: '经验 · 项目 / 比赛',
      sub: '记录项目、比赛经历，突出你的角色、贡献与成果。',
      attach: true,
      fields: [
        { key: 'kind', label: '类型', type: 'select', required: true,
          options: [['project', '项目'], ['competition', '比赛']] },
        { key: 'title', label: '名称', type: 'text', required: true, full: true },
        { key: 'start_date', label: '开始时间', type: 'month', required: true },
        { key: 'end_date', label: '结束时间', type: 'month', required: true },
        { key: 'role', label: '个人角色', type: 'text' },
        { key: 'content', label: '内容描述', type: 'textarea', full: true },
        { key: 'contribution', label: '我的贡献', type: 'textarea', full: true },
        { key: 'achievement', label: '获得成果', type: 'textarea', full: true }
      ]
    },
    skill: {
      label: '技能',
      sub: '专业技能、工具、语言能力等。',
      attach: true,
      fields: [
        { key: 'name', label: '技能名称', type: 'text', required: true, full: true },
        { key: 'level', label: '熟练程度', type: 'text' },
        { key: 'acquired_date', label: '获得时间', type: 'month', required: true },
        { key: 'expiry_date', label: '有效期（选填）', type: 'month' },
        { key: 'description', label: '说明', type: 'textarea', full: true }
      ]
    },
    certificate: {
      label: '证书',
      sub: '各类资格证书、获奖证书，可上传证书图片。',
      attach: true,
      fields: [
        { key: 'name', label: '证书名称', type: 'text', required: true, full: true },
        { key: 'acquired_date', label: '获得时间', type: 'month', required: true },
        { key: 'expiry_date', label: '有效期（选填）', type: 'month' },
        { key: 'description', label: '说明', type: 'textarea', full: true }
      ]
    },
    self_intro: {
      label: '自我介绍',
      sub: '可填写多份。AI 优化会润色你的文字；AI 生成会综合你的全部资料深度撰写。',
      attach: false,
      selfIntro: true
    }
  };

  var CFG = SCHEMAS[SECTION] || SCHEMAS.skill;
  var SECTION_API = SECTION; // server normalizes - to _
  var pollTimer = null;

  // ----- card rendering -----
  function cardMeta(item) {
    if (SECTION === 'education') {
      var kind = { high_school: '高中', university: '大学', training: '培训' }[item.kind] || '';
      return [kind, item.major, RZ.fmtRange(item.start_date, item.end_date)].filter(Boolean).join(' · ');
    }
    if (SECTION === 'experience') {
      var k = item.kind === 'competition' ? '比赛' : '项目';
      return [k, RZ.fmtRange(item.start_date, item.end_date)].filter(Boolean).join(' · ');
    }
    if (SECTION === 'skill') return [item.level, item.acquired_date, item.expiry_date ? '有效期 ' + item.expiry_date : ''].filter(Boolean).join(' · ');
    if (SECTION === 'certificate') return [item.acquired_date, item.expiry_date ? '有效期 ' + item.expiry_date : ''].filter(Boolean).join(' · ');
    if (SECTION === 'self_intro') return (item.content_md || '').slice(0, 60);
    return '';
  }
  function cardTitle(item) {
    if (SECTION === 'education') return item.school || '学习经历';
    if (SECTION === 'experience') return item.title || '经历';
    if (SECTION === 'self_intro') return item.title || '自我介绍';
    return item.name || '记录';
  }

  function renderCard(item) {
    if (SECTION === 'self_intro' && item.status === 'generating') {
      return '<div class="rz-card rz-card--placeholder" data-generating="1">' +
        '<div class="rz-card__title"><span class="rz-spin"></span> AI 正在整理…</div>' +
        '<div class="rz-card__meta">综合你的资料深度撰写中，请稍候</div></div>';
    }
    var failed = item.status === 'failed';
    var thumbs = '';
    if (CFG.attach && item.attachments && item.attachments.length) {
      thumbs = '<div class="rz-card__thumbs">' + item.attachments.slice(0, 5).map(function (a) {
        return '<img src="' + a.url + '" alt="">';
      }).join('') + '</div>';
    }
    return '<div class="rz-card' + (failed ? ' rz-card--failed' : '') + '" data-id="' + item.id + '">' +
      '<div class="rz-card__title">' + RZ.esc(cardTitle(item)) + '</div>' +
      '<div class="rz-card__meta">' + RZ.esc(cardMeta(item)) +
      (failed ? '<br><span style="color:#dc2626">生成失败，点击查看</span>' : '') + '</div>' +
      thumbs + '</div>';
  }

  async function load() {
    try {
      var data = await RZ.api('/api/resume/sections/' + SECTION_API);
      var items = data.items || [];
      if (!items.length) {
        box.innerHTML = '<div class="rz-empty" style="grid-column:1/-1">' +
          '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M8 10h8M8 14h5"></path></svg>' +
          '<div>还没有' + RZ.esc(CFG.label) + '，点右上角「新建」开始添加</div></div>';
      } else {
        box.innerHTML = items.map(renderCard).join('');
        box.querySelectorAll('.rz-card[data-id]').forEach(function (card) {
          card.addEventListener('click', function () {
            var item = items.filter(function (i) { return String(i.id) === card.dataset.id; })[0];
            if (item) openDetail(item);
          });
        });
      }
      managePolling(items);
    } catch (e) { RZ.toast(e.message, 'error'); }
  }

  function managePolling(items) {
    var anyGen = items.some(function (i) { return i.status === 'generating'; });
    if (anyGen && !pollTimer) pollTimer = setInterval(load, 3000);
    else if (!anyGen && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  // ----- detail view -----
  function openDetail(item) {
    var m = RZ.openModal({ title: cardTitle(item), wide: SECTION === 'self_intro' });
    if (SECTION === 'self_intro') {
      m.body.innerHTML = '<div class="rz-md">' + RZ.md(item.content_md) + '</div>' +
        (item.error_text ? '<div style="color:#b45309;font-size:.8rem;margin-top:8px">' + RZ.esc(item.error_text) + '</div>' : '');
    } else {
      m.body.innerHTML = detailHtml(item);
    }
    var edit = btn('编辑', 'rz-btn');
    edit.onclick = function () { m.close(); openForm(item); };
    var del = btn('删除', 'rz-btn rz-btn--danger');
    del.onclick = function () {
      m.close();
      RZ.confirmDialog('确定删除「' + cardTitle(item) + '」吗？', async function () {
        try { await RZ.api('/api/resume/sections/' + SECTION_API + '/' + item.id, { method: 'DELETE' });
          RZ.toast('已删除', 'success'); load(); } catch (e) { RZ.toast(e.message, 'error'); }
      });
    };
    m.foot.appendChild(del); m.foot.appendChild(edit);
  }

  function detailHtml(item) {
    var rows = CFG.fields.map(function (f) {
      var v = item[f.key];
      if (f.type === 'select') {
        var opt = (f.options || []).filter(function (o) { return o[0] === v; })[0];
        v = opt ? opt[1] : v;
      }
      if (!v) return '';
      return '<div style="margin-bottom:8px"><div style="color:#999;font-size:.78rem">' + RZ.esc(f.label) +
        '</div><div style="white-space:pre-wrap">' + RZ.esc(v) + '</div></div>';
    }).join('');
    var imgs = '';
    if (CFG.attach && item.attachments && item.attachments.length) {
      imgs = '<div class="rz-attach">' + item.attachments.map(function (a) {
        return '<a href="' + a.url + '" target="_blank" class="rz-attach__item"><img src="' + a.url + '"></a>';
      }).join('') + '</div>';
    }
    return rows + imgs;
  }

  // ----- create / edit form -----
  function fieldHtml(f, val) {
    val = val || '';
    var req = f.required ? '<span class="req">*</span>' : '';
    var input;
    if (f.type === 'select') {
      input = '<select class="rz-select" name="' + f.key + '">' +
        f.options.map(function (o) { return '<option value="' + o[0] + '"' + (o[0] === val ? ' selected' : '') + '>' + o[1] + '</option>'; }).join('') + '</select>';
    } else if (f.type === 'textarea') {
      input = '<textarea class="rz-textarea" name="' + f.key + '">' + RZ.esc(val) + '</textarea>';
    } else if (f.type === 'month') {
      input = '<input class="rz-input" type="month" name="' + f.key + '" value="' + RZ.esc(val) + '">';
    } else {
      input = '<input class="rz-input" type="text" name="' + f.key + '" value="' + RZ.esc(val) + '">';
    }
    return '<div class="rz-field' + (f.full ? ' rz-field--full' : '') + '"><label>' + RZ.esc(f.label) + req + '</label>' + input + '</div>';
  }

  function openStandardForm(item) {
    item = item || {};
    var isEdit = !!item.id;
    var m = RZ.openModal({ title: (isEdit ? '编辑' : '新建') + CFG.label, wide: true });
    m.body.innerHTML = '<div class="rz-form-grid">' +
      CFG.fields.map(function (f) { return fieldHtml(f, item[f.key]); }).join('') + '</div>' +
      (CFG.attach ? '<div class="rz-field rz-field--full"><label>图片附件（≤5 张，每张 ≤5MB）</label>' +
        '<div class="rz-attach" id="rzAttachBox"></div></div>' : '');
    if (CFG.attach) buildAttachUI(m.body.querySelector('#rzAttachBox'), item);

    var cancel = btn('取消', 'rz-btn'); cancel.onclick = m.close;
    var save = btn('保存', 'rz-btn rz-btn--primary');
    save.onclick = async function () {
      var payload = collectForm(m.body);
      var miss = CFG.fields.filter(function (f) { return f.required && !payload[f.key]; });
      if (miss.length) { RZ.toast('请填写：' + miss.map(function (f) { return f.label; }).join('、'), 'error'); return; }
      save.disabled = true;
      try {
        var id = item.id;
        if (isEdit) {
          await RZ.api('/api/resume/sections/' + SECTION_API + '/' + id, { method: 'PUT', body: payload });
        } else {
          var res = await RZ.api('/api/resume/sections/' + SECTION_API, { method: 'POST', body: payload });
          id = res.id;
        }
        if (CFG.attach) await uploadStaged(id);
        RZ.toast('已保存', 'success'); m.close(); load();
      } catch (e) { RZ.toast(e.message, 'error'); save.disabled = false; }
    };
    m.foot.appendChild(cancel); m.foot.appendChild(save);
  }

  function collectForm(scope) {
    var out = {};
    CFG.fields.forEach(function (f) {
      var el = scope.querySelector('[name="' + f.key + '"]');
      out[f.key] = el ? el.value.trim() : '';
    });
    return out;
  }

  // ----- attachments (staged for new items, immediate for existing) -----
  var staged = [];
  function buildAttachUI(container, item) {
    staged = [];
    function refresh() {
      var existing = (item.attachments || []).map(function (a) {
        return '<div class="rz-attach__item"><img src="' + a.url + '"><button type="button" class="rz-attach__del" data-del="' + a.id + '">&times;</button></div>';
      }).join('');
      var stagedHtml = staged.map(function (s, i) {
        return '<div class="rz-attach__item"><img src="' + s.url + '"><button type="button" class="rz-attach__del" data-stage="' + i + '">&times;</button></div>';
      }).join('');
      var total = (item.attachments || []).length + staged.length;
      var add = total < 5 ? '<label class="rz-attach__add">+<input type="file" accept="image/*" hidden id="rzAddImg"></label>' : '';
      container.innerHTML = existing + stagedHtml + add +
        '<div class="rz-attach__hint">已用 ' + total + '/5 张；支持 PNG/JPG/GIF/WebP，单张 ≤5MB</div>';
      var input = container.querySelector('#rzAddImg');
      if (input) input.addEventListener('change', function () {
        if (!this.files || !this.files[0]) return;
        var f = this.files[0];
        if (f.size > 5 * 1024 * 1024) { RZ.toast('单张图片不能超过 5MB', 'error'); return; }
        if (item.id) { uploadOne(item, f, refresh); }
        else { staged.push({ file: f, url: URL.createObjectURL(f) }); refresh(); }
      });
      container.querySelectorAll('[data-del]').forEach(function (b) {
        b.addEventListener('click', async function () {
          try { await RZ.api('/api/resume/attachments/' + b.dataset.del, { method: 'DELETE' });
            item.attachments = (item.attachments || []).filter(function (a) { return String(a.id) !== b.dataset.del; });
            refresh(); } catch (e) { RZ.toast(e.message, 'error'); }
        });
      });
      container.querySelectorAll('[data-stage]').forEach(function (b) {
        b.addEventListener('click', function () { staged.splice(parseInt(b.dataset.stage, 10), 1); refresh(); });
      });
    }
    refresh();
  }

  async function uploadOne(item, file, done) {
    var fd = new FormData(); fd.append('file', file);
    try {
      var res = await RZ.api('/api/resume/attachments?owner_kind=' + SECTION + '&owner_id=' + item.id, { method: 'POST', body: fd });
      item.attachments = (item.attachments || []).concat([res.attachment]);
      if (done) done();
    } catch (e) { RZ.toast(e.message, 'error'); }
  }

  async function uploadStaged(ownerId) {
    for (var i = 0; i < staged.length; i++) {
      var fd = new FormData(); fd.append('file', staged[i].file);
      try { await RZ.api('/api/resume/attachments?owner_kind=' + SECTION + '&owner_id=' + ownerId, { method: 'POST', body: fd }); }
      catch (e) { RZ.toast('部分图片上传失败：' + e.message, 'error'); }
    }
    staged = [];
  }

  // ----- self-intro special create flow -----
  function openSelfIntroForm() {
    var m = RZ.openModal({ title: '新建自我介绍', wide: true });
    m.body.innerHTML =
      '<textarea class="rz-textarea" id="rzIntroText" style="min-height:220px" placeholder="输入你的自我介绍，可使用空行分段、- 列表、**加粗**…"></textarea>' +
      '<div style="color:var(--rz-muted);font-size:.78rem;margin-top:6px">「AI 优化」润色当前内容；「AI 生成」会综合你填写的全部资料深度撰写（在列表中显示生成进度）。</div>';
    var ta = m.body.querySelector('#rzIntroText');

    var optimize = btn('✨ AI 优化', 'rz-btn');
    optimize.onclick = async function () {
      if (!ta.value.trim()) { RZ.toast('请先输入内容', 'error'); return; }
      optimize.disabled = true; optimize.textContent = '优化中…';
      try { var d = await RZ.api('/api/resume/self-intro/optimize', { method: 'POST', body: { text: ta.value } });
        if (d.ok) { ta.value = d.content; RZ.toast('已优化', 'success'); } else RZ.toast(d.error || '优化失败', 'error');
      } catch (e) { RZ.toast(e.message, 'error'); }
      finally { optimize.disabled = false; optimize.textContent = '✨ AI 优化'; }
    };
    var generate = btn('🤖 AI 生成', 'rz-btn');
    generate.onclick = async function () {
      generate.disabled = true;
      try { await RZ.api('/api/resume/self-intro/generate', { method: 'POST' });
        RZ.toast('AI 正在生成，请在列表中查看进度', 'success'); m.close(); load();
      } catch (e) { RZ.toast(e.message, 'error'); generate.disabled = false; }
    };
    var cancel = btn('取消', 'rz-btn'); cancel.onclick = m.close;
    var save = btn('保存', 'rz-btn rz-btn--primary');
    save.onclick = async function () {
      if (!ta.value.trim()) { RZ.toast('请输入内容', 'error'); return; }
      save.disabled = true;
      try { await RZ.api('/api/resume/sections/self_intro', { method: 'POST', body: { content_md: ta.value, title: '自我介绍', source: 'manual' } });
        RZ.toast('已保存', 'success'); m.close(); load();
      } catch (e) { RZ.toast(e.message, 'error'); save.disabled = false; }
    };
    m.foot.appendChild(optimize); m.foot.appendChild(generate);
    m.foot.appendChild(cancel); m.foot.appendChild(save);
  }

  function openSelfIntroEdit(item) {
    var m = RZ.openModal({ title: '编辑自我介绍', wide: true });
    m.body.innerHTML = '<textarea class="rz-textarea" id="rzIntroText" style="min-height:220px">' + RZ.esc(item.content_md) + '</textarea>';
    var ta = m.body.querySelector('#rzIntroText');
    var cancel = btn('取消', 'rz-btn'); cancel.onclick = m.close;
    var save = btn('保存', 'rz-btn rz-btn--primary');
    save.onclick = async function () {
      if (!ta.value.trim()) { RZ.toast('请输入内容', 'error'); return; }
      save.disabled = true;
      try { await RZ.api('/api/resume/sections/self_intro/' + item.id, { method: 'PUT', body: { content_md: ta.value, title: item.title || '自我介绍', source: item.source || 'manual' } });
        RZ.toast('已保存', 'success'); m.close(); load();
      } catch (e) { RZ.toast(e.message, 'error'); save.disabled = false; }
    };
    m.foot.appendChild(cancel); m.foot.appendChild(save);
  }

  function btn(text, cls) { var b = document.createElement('button'); b.className = cls; b.textContent = text; return b; }

  function openForm(item) {
    if (CFG.selfIntro) { if (item && item.id) openSelfIntroEdit(item); else openSelfIntroForm(); }
    else openStandardForm(item);
  }

  function init() {
    document.getElementById('rzSecTitle').textContent = CFG.label;
    document.getElementById('rzSecSub').textContent = CFG.sub || '';
    document.getElementById('rzNewBtn').addEventListener('click', function () { openForm(null); });
    load();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
