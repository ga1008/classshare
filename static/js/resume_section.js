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
        { key: 'degree', label: '学历/学位层次（不确定可留空）', type: 'select', full: true,
          options: [['', '待确认'], ['高中', '高中'], ['中专', '中专'], ['大专', '大专'], ['本科', '本科'], ['硕士', '硕士'], ['博士', '博士'], ['其他', '其他']] },
        { key: 'college', label: '学院（大学填写）', type: 'text' },
        { key: 'major', label: '专业（大学填写）', type: 'text' },
        { key: 'start_date', label: '开始时间', type: 'month', required: true },
        { key: 'end_date', label: '结束时间', type: 'month', required: true },
        { key: 'content', label: '学习内容', type: 'textarea', full: true }
      ]
    },
    experience: {
      label: '经历 · 不只项目和比赛',
      sub: '实习、课程作业、社团、志愿服务、兼职和调研都可以成为证据。重点写清你的角色、行动与真实结果。',
      attach: true,
      fields: [
        { key: 'kind', label: '类型', type: 'select', required: true,
          options: [['internship', '实习'], ['project', '项目'], ['course', '课程成果'], ['competition', '比赛'],
            ['campus', '社团 / 学生工作'], ['volunteer', '志愿服务'], ['part_time', '兼职'], ['research', '调研 / 科研']] },
        { key: 'title', label: '名称', type: 'text', required: true, full: true, placeholder: '例如：校园消费调研项目' },
        { key: 'start_date', label: '开始时间', type: 'month', required: true },
        { key: 'end_date', label: '结束时间', type: 'month', required: true },
        { key: 'role', label: '个人角色', type: 'text', placeholder: '例如：组长 / 数据整理 / 活动策划' },
        { key: 'content', label: '背景与任务', type: 'textarea', full: true, placeholder: '当时要解决什么问题？你的任务是什么？' },
        { key: 'contribution', label: '我的行动', type: 'textarea', full: true, placeholder: '你亲自做了哪些动作？用了什么方法或工具？' },
        { key: 'achievement', label: '真实结果', type: 'textarea', full: true, placeholder: '交付了什么？有多少人使用、效率怎样、获得什么反馈？没有数字就写真实结果。' }
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
      return [item.degree || kind, item.major, RZ.fmtRange(item.start_date, item.end_date)].filter(Boolean).join(' · ');
    }
    if (SECTION === 'experience') {
      var option = SCHEMAS.experience.fields[0].options.find(function (entry) { return entry[0] === item.kind; });
      var k = option ? option[1] : '经历';
      return [k, RZ.fmtRange(item.start_date, item.end_date)].filter(Boolean).join(' · ');
    }
    if (SECTION === 'skill') return [item.level, RZ.formatMonthLabel(item.acquired_date), item.expiry_date ? '有效期 ' + RZ.formatMonthLabel(item.expiry_date) : ''].filter(Boolean).join(' · ');
    if (SECTION === 'certificate') return [RZ.formatMonthLabel(item.acquired_date), item.expiry_date ? '有效期 ' + RZ.formatMonthLabel(item.expiry_date) : ''].filter(Boolean).join(' · ');
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
      return '<div class="rz-card rz-card--placeholder" role="button" tabindex="0" data-id="' + Number(item.id) + '" data-generating="1">' +
        '<div class="rz-card__title"><span class="rz-spin"></span> AI 正在整理…</div>' +
        '<div class="rz-card__meta">可离开此页，点击查看进度或取消任务</div></div>';
    }
    var failed = item.status === 'failed';
    var thumbs = '';
    if (CFG.attach && item.attachments && item.attachments.length) {
      thumbs = '<div class="rz-card__thumbs">' + item.attachments.slice(0, 5).map(function (a) {
        return '<img src="' + a.url + '" alt="">';
      }).join('') + '</div>';
    }
    return '<div class="rz-card' + (failed ? ' rz-card--failed' : '') + '" role="button" tabindex="0" data-id="' + Number(item.id) + '">' +
      '<div class="rz-card__title">' + RZ.esc(cardTitle(item)) + '</div>' +
      '<div class="rz-card__meta">' + RZ.esc(cardMeta(item)) +
      (failed ? '<br><span style="color:#dc2626">生成失败，点击查看</span>' : '') + '</div>' +
      thumbs + '</div>';
  }

  async function load(quiet) {
    try {
      var data = await RZ.api('/api/resume/sections/' + SECTION_API);
      var meta = data.meta || {};
      if (Array.isArray(meta.experience_kinds) && meta.experience_kinds.length) SCHEMAS.experience.fields[0].options =
        meta.experience_kinds.map(function (item) { return [item.value, item.label]; });
      if (Array.isArray(meta.education_degrees) && meta.education_degrees.length) SCHEMAS.education.fields.find(function (field) { return field.key === 'degree'; }).options =
        [['', '待确认']].concat(meta.education_degrees.filter(Boolean).map(function (degree) { return [degree, degree]; }));
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
          card.addEventListener('keydown', function (event) { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); card.click(); } });
        });
      }
      managePolling(items);
      return items;
    } catch (e) { if (quiet === true) throw e; RZ.toast(e.message, 'error'); }
  }

  function managePolling(items) {
    var anyGen = items.some(function (i) { return i.status === 'generating'; });
    if (anyGen && (!pollTimer || !pollTimer.active())) pollTimer = window.CareerTools.poll({
      load: function () { return load(true); }, interval: 8000, immediate: false,
      done: function (items) { return !items.some(function (item) { return item.status === 'generating'; }); },
      onError: function (error, count) { if (count === 1) RZ.toast(error.message, 'error'); }
    });
    else if (!anyGen && pollTimer) { pollTimer.stop(); pollTimer = null; }
  }

  // ----- detail view -----
  function openDetail(item) {
    if (SECTION === 'self_intro' && item.status === 'generating') { RZ.openJob({ title: cardTitle(item), base: '/api/resume/self-intro/' + item.id, revision: item.revision, onChange: load }); return; }
    var m = RZ.openModal({ title: cardTitle(item), wide: SECTION === 'self_intro' });
    if (SECTION === 'self_intro') {
      m.body.innerHTML = '<div class="rz-md">' + RZ.md(item.content_md) + '</div>' +
        (item.error_text ? '<div style="color:#b45309;font-size:.8rem;margin-top:8px">' + RZ.esc(item.error_text) + '</div>' : '');
    } else {
      m.body.innerHTML = detailHtml(item);
    }
    var edit = btn('编辑', 'rz-btn');
    edit.onclick = function () { m.close(); openForm(item); };
    if (SECTION === 'self_intro' && item.active_job_id) {
      var task = btn('查看处理状态', 'rz-btn');
      task.onclick = function () { m.close(); RZ.openJob({ title: cardTitle(item), base: '/api/resume/self-intro/' + item.id, revision: item.revision, onChange: load }); };
      m.foot.appendChild(task);
    }
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
      if (f.type === 'month') v = RZ.formatMonthLabel(v) || v;
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
      input = '<textarea class="rz-textarea" name="' + f.key + '" placeholder="' + RZ.esc(f.placeholder || '') + '">' + RZ.esc(val) + '</textarea>';
    } else if (f.type === 'month') {
      input = RZ.monthPickerHtml(f.key, val, { placeholder: f.required ? '请选择年月' : '可选年月' });
    } else {
      input = '<input class="rz-input" type="text" name="' + f.key + '" value="' + RZ.esc(val) + '" placeholder="' + RZ.esc(f.placeholder || '') + '">';
    }
    return '<div class="rz-field' + (f.full ? ' rz-field--full' : '') + '"><label>' + RZ.esc(f.label) + req + '</label>' + input + '</div>';
  }

  function formFieldsHtml(item) {
    var hasRange = CFG.fields.some(function (f) { return f.key === 'start_date'; }) &&
      CFG.fields.some(function (f) { return f.key === 'end_date'; });
    var html = [];
    CFG.fields.forEach(function (f) {
      if (hasRange && f.key === 'end_date') return;
      if (hasRange && f.key === 'start_date') {
        html.push('<div class="rz-field rz-field--full"><label>起止时间<span class="req">*</span></label>' +
          RZ.monthRangePickerHtml('start_date', 'end_date', {
            start: item.start_date,
            end: item.end_date
          }, { placeholder: '请选择开始和结束年月' }) + '</div>');
        return;
      }
      html.push(fieldHtml(f, item[f.key]));
    });
    return html.join('');
  }

  function openStandardForm(item) {
    item = item || {};
    var isEdit = !!item.id;
    var m = RZ.openModal({ title: (isEdit ? '编辑' : '新建') + CFG.label, wide: true });
    m.body.innerHTML = '<div class="rz-form-grid">' +
      formFieldsHtml(item) + '</div>' +
      (CFG.attach ? '<div class="rz-field rz-field--full"><label>图片附件（≤5 张，每张 ≤5MB）</label>' +
        '<div class="rz-attach" id="rzAttachBox"></div></div>' : '');
    RZ.initMonthPickers(m.body);
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
          payload.revision = item.revision;
          await RZ.api('/api/resume/sections/' + SECTION_API + '/' + id, { method: 'PUT', body: payload });
        } else {
          var res = await RZ.api('/api/resume/sections/' + SECTION_API, { method: 'POST', body: payload });
          id = res.id;
        }
        if (CFG.attach) await uploadStaged(id);
        RZ.toast('已保存', 'success'); m.close(); load();
      } catch (e) { RZ.conflict(e, payload, function () { m.close(); load(); }); save.disabled = false; }
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
    var recoverSuggestion = btn('查看上次建议', 'rz-btn'); recoverSuggestion.hidden = !RZ.pendingSuggestion('intro');
    async function optimizeIntro(resume) {
      if (!resume && !ta.value.trim()) { RZ.toast('请先输入内容', 'error'); return; }
      optimize.disabled = true;
      try { await RZ.requestSuggestion({ kind: 'intro', url: '/api/resume/self-intro/optimize', body: { text: ta.value }, resume: resume,
        onResult: function (result, forget, meta) {
          if (!result.content && !result.content_md) { RZ.toast(result.error || '暂无可用建议', 'info'); forget(); return; }
          var current = ta.value;
          RZ.compareSuggestion(current || meta.input_text || '', result.content || result.content_md || '', function () {
            if (ta.value !== current) { RZ.toast('对比期间原文已有修改，请重新核对建议', 'info'); return; }
            ta.value = result.content || result.content_md || ''; forget();
          });
        } }); recoverSuggestion.hidden = false;
      } catch (error) { RZ.toast(error.message, 'error'); }
      finally { optimize.disabled = false; }
    }
    optimize.onclick = function () { optimizeIntro(false); }; recoverSuggestion.onclick = function () { optimizeIntro(true); };
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
    m.foot.appendChild(optimize); m.foot.appendChild(recoverSuggestion); m.foot.appendChild(generate);
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
      try { await RZ.api('/api/resume/sections/self_intro/' + item.id, { method: 'PUT', body: { revision: item.revision, content_md: ta.value, title: item.title || '自我介绍', source: item.source || 'manual' } });
        RZ.toast('已保存', 'success'); m.close(); load();
      } catch (e) { RZ.conflict(e, ta.value, function () { m.close(); load(); }); save.disabled = false; }
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
