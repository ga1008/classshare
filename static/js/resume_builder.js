/* Résumé builder — drag (and click) palette items into ordered zones, pick a
   template + personal fields, then create the résumé (background render). */
(function () {
  'use strict';
  var RZ = window.RZ;

  var DATA = null;
  var TEMPLATES = [];
  var POSITION_OPTIONS = [];
  var EDIT_ID = null;
  var lastAutoTitle = '';
  var sourceContext = {};
  var activeDrag = null;
  var lastAdded = null;
  var PERSONAL_REQUIRED = ['name', 'expected_position'];
  var PERSONAL_FIELD_ORDER = [
    'name', 'gender', 'birthday', 'email', 'phone', 'qq', 'wechat',
    'expected_position', 'expected_industry', 'expected_salary', 'hometown', 'address'
  ];
  var state = {
    template: 'classic',
    target_position: '',
    fields: PERSONAL_REQUIRED.slice(),
    self_intro: [], education: [], experience: [], skill: [], cert: [],
    tech_stack: true
  };
  var mobilePalette = {
    button: null,
    scrim: null,
    aside: null,
    close: null,
    bodyOverflow: '',
    htmlOverflow: ''
  };

  var ZONES = [
    { key: 'personal', label: '个人信息' },
    { key: 'self_intro', label: '个人介绍' },
    { key: 'education', label: '学习经历' },
    { key: 'experience', label: '实习 / 项目 / 校园经历' },
    { key: 'skill_cert', label: '技能与证书' },
    { key: 'tech_stack', label: '技术栈' }
  ];

  function labelOf(kind, item) {
    if (kind === 'personal_field') return item.label + (item.value ? '：' + item.value : '（待完善）');
    if (kind === 'self_intro') return (item.title || '自我介绍') + '：' + (item.content_md || '').slice(0, 16);
    if (kind === 'education') return item.school || '学习经历';
    if (kind === 'experience') return item.title || '经历';
    return item.name || '项';
  }
  function itemById(kind, id) {
    if (kind === 'personal_field') return personalFieldItems(true).filter(function (i) { return i.id === id; })[0];
    return (DATA[kind] || []).filter(function (i) { return String(i.id) === String(id); })[0];
  }
  function selKey(kind) { return kind === 'certificate' ? 'cert' : kind; }
  function isSelected(kind, id) {
    if (kind === 'personal_field') return state.fields.indexOf(String(id)) >= 0;
    return state[selKey(kind)].indexOf(Number(id)) >= 0;
  }

  function renderTemplates() {
    document.getElementById('rzTplRow').innerHTML = TEMPLATES.map(function (t) {
      return '<div class="rz-tpl' + (t.key === state.template ? ' active' : '') + '" data-tpl="' + t.key + '">' +
        '<strong>' + RZ.esc(t.label) + '</strong><small>' + RZ.esc(t.description) + '</small></div>';
    }).join('');
    document.querySelectorAll('[data-tpl]').forEach(function (el) {
      el.addEventListener('click', function () { state.template = el.dataset.tpl; renderTemplates(); });
    });
  }

  function normalize(value) {
    return String(value || '').trim().toLowerCase();
  }

  function autoTitleFor(target) {
    var name = DATA && DATA.personal ? String(DATA.personal.name || '').trim() : '';
    target = String(target || '').trim();
    return target ? target + (name ? ' - ' + name : '') : (name ? name + '的简历' : '我的简历');
  }

  function syncTitleFromTarget(force) {
    var titleEl = document.getElementById('rzResumeTitle');
    if (!titleEl) return;
    var next = autoTitleFor(state.target_position);
    var current = titleEl.value.trim();
    if (force || !current || current === lastAutoTitle) {
      titleEl.value = next;
      lastAutoTitle = next;
    }
  }

  function sectionCount() {
    var count = 0;
    if (state.self_intro.length) count++;
    if (state.education.length) count++;
    if (state.experience.length) count++;
    if (state.skill.length || state.cert.length) count++;
    if (state.tech_stack) count++;
    return count;
  }

  function requiredPersonalFilled() {
    var personal = DATA && DATA.personal ? DATA.personal : {};
    var core = PERSONAL_REQUIRED.filter(function (key) {
      return String(personal[key] || '').trim();
    }).length;
    var contact = String(personal.email || '').trim() || String(personal.phone || '').trim();
    return core + (contact ? 1 : 0);
  }

  function renderBuildProgress() {
    var box = document.getElementById('rzBuildProgress');
    if (!box || !DATA) return;
    var steps = [
      { key: 'target', label: '目标岗位', done: !!state.target_position.trim() },
      { key: 'personal', label: '基本联系信息', done: requiredPersonalFilled() === PERSONAL_REQUIRED.length + 1 },
      { key: 'intro', label: '自我介绍', done: state.self_intro.length > 0 },
      { key: 'experience', label: '经历证明', done: state.education.length > 0 || state.experience.length > 0 },
      { key: 'skill', label: '技能证书', done: state.skill.length > 0 || state.cert.length > 0 },
      { key: 'layout', label: '简历区块', done: sectionCount() > 1 }
    ];
    var done = steps.filter(function (step) { return step.done; }).length;
    var pct = Math.round(done / steps.length * 100);
    box.innerHTML = '<div class="rz-build-progress__top"><strong>搭建进度</strong><span>' + pct + '%</span></div>' +
      '<div class="rz-build-progress__bar"><i style="width:' + pct + '%"></i></div>' +
      '<div class="rz-build-progress__steps">' + steps.map(function (step) {
        return '<div class="rz-build-progress__step' + (step.done ? ' is-done' : '') + '"><i></i><span>' + RZ.esc(step.label) + '</span></div>';
      }).join('') + '</div>';
  }

  function setTargetPosition(value, forceTitle) {
    state.target_position = String(value || '').trim();
    var input = document.getElementById('rzTargetPosition');
    if (input && input.value !== state.target_position) input.value = state.target_position;
    renderTargetOptions();
    syncTitleFromTarget(!!forceTitle);
    if (DATA) renderZones();
    renderBuildProgress();
  }

  function targetOptionHtml(option, active) {
    var value = option.value || option.label || '';
    var meta = option.meta || option.tag || '职业推荐';
    return '<button type="button" class="rz-target-option' + (active ? ' active' : '') +
      '" data-target-position="' + RZ.esc(value) + '">' +
      '<strong>' + RZ.esc(value) + '</strong><span>' + RZ.esc(meta) + '</span></button>';
  }

  function renderTargetOptions() {
    var box = document.getElementById('rzTargetOptions');
    if (!box) return;
    var current = normalize(state.target_position);
    var options = POSITION_OPTIONS.filter(function (option) {
      return option && String(option.value || option.label || '').trim();
    }).slice(0, 8);
    if (!options.length) {
      box.innerHTML = '<div class="rz-target-options__empty">暂无职业推荐，可直接输入自定义岗位</div>';
      return;
    }
    box.innerHTML = options.map(function (option) {
      return targetOptionHtml(option, normalize(option.value || option.label) === current);
    }).join('');
    box.querySelectorAll('[data-target-position]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        setTargetPosition(btn.dataset.targetPosition || '', true);
      });
    });
  }

  function personalFieldItems(includeBlank) {
    var labels = DATA.personal_labels || {};
    var personal = DATA.personal || {};
    return PERSONAL_FIELD_ORDER.map(function (key) {
      return { id: key, key: key, label: labels[key] || key, value: String(personal[key] || '').trim() };
    }).filter(function (item) { return includeBlank || item.value; });
  }

  function chip(kind, item, inZone) {
    var locked = kind === 'personal_field' && PERSONAL_REQUIRED.indexOf(item.id) >= 0;
    var x = inZone && !locked ? '<button type="button" class="rz-chip__x" title="移除">✕</button>' : '';
    var lock = locked && inZone ? '<span class="rz-chip__lock">必填</span>' : '';
    return '<div class="rz-chip' + (inZone ? ' in-zone' : '') + '" draggable="true" data-kind="' + kind + '" data-id="' + item.id + '">' +
      '<span class="rz-chip__label">' + RZ.esc(labelOf(kind, item)) + '</span>' + lock + x + '</div>';
  }

  function renderZones() {
    var html = ZONES.map(function (z) {
      if (z.key === 'tech_stack') {
        var targetHint = state.target_position
          ? '当前目标岗位：' + RZ.esc(state.target_position) + '。AI 会优先保留与该岗位相关、有材料支撑的技能。'
          : '先填写目标岗位，AI 才能按岗位筛选技术栈。';
        return '<div class="rz-zone" data-zone="tech_stack"><div class="rz-zone__head"><strong>技术栈</strong>' +
          '<label style="font-size:.8rem;font-weight:600"><input type="checkbox" id="rzTechToggle" ' +
          (state.tech_stack ? 'checked' : '') + '> 由 AI 自动生成</label></div>' +
          '<div class="rz-zone__hint">' + targetHint + '</div></div>';
      }
      var inner;
      if (z.key === 'personal') {
        inner = state.fields.map(function (key) {
          var it = itemById('personal_field', key);
          return it ? chip('personal_field', it, true) : '';
        }).join('') || '<div class="rz-zone__hint">拖入个人信息字段</div>';
      } else if (z.key === 'skill_cert') {
        var skills = state.skill.map(function (id) { var it = itemById('skill', id); return it ? chip('skill', it, true) : ''; }).join('');
        var certs = state.cert.map(function (id) { var it = itemById('certificate', id); return it ? chip('certificate', it, true) : ''; }).join('');
        inner = (skills + certs) || '<div class="rz-zone__hint">拖入技能与证书</div>';
      } else {
        var ids = state[z.key];
        inner = ids.map(function (id) { var it = itemById(z.key, id); return it ? chip(z.key, it, true) : ''; }).join('') ||
          '<div class="rz-zone__hint">拖入' + z.label + '</div>';
      }
      return '<div class="rz-zone" data-zone="' + z.key + '"><div class="rz-zone__head"><strong>' + z.label + '</strong></div>' + inner + '</div>';
    }).join('');
    document.getElementById('rzZones').innerHTML = html;
    bindZones();
    markJustAdded();
    renderBuildProgress();
    var toggle = document.getElementById('rzTechToggle');
    if (toggle) toggle.addEventListener('change', function () { state.tech_stack = toggle.checked; renderBuildProgress(); });
  }

  function renderPalette() {
    var groups = [
      { kind: 'personal_field', label: '个人信息' },
      { kind: 'self_intro', label: '自我介绍' },
      { kind: 'education', label: '学习经历' },
      { kind: 'experience', label: '实习 / 项目 / 校园经历' },
      { kind: 'skill', label: '技能' },
      { kind: 'certificate', label: '证书' }
    ];
    var html = groups.map(function (g) {
      var source = g.kind === 'personal_field' ? personalFieldItems(false) : (DATA[g.kind] || []);
      var items = source.filter(function (it) { return !isSelected(g.kind, it.id); });
      var emptyText = g.kind === 'personal_field' ? '（暂无可选字段，去个人信息页完善）' : '（无可用，去左侧菜单添加）';
      var chips = items.length ? items.map(function (it) { return chip(g.kind, it, false); }).join('')
        : '<div class="rz-palette__empty">' + emptyText + '</div>';
      return '<div class="rz-palette__group"><div class="rz-palette__title">' + g.label + '</div>' + chips + '</div>';
    }).join('');
    document.getElementById('rzPalette').innerHTML = html;
    bindPalette();
  }

  function markJustAdded() {
    if (!lastAdded) return;
    var selector = '.rz-zone .rz-chip[data-kind="' + lastAdded.kind + '"][data-id="' + lastAdded.id + '"]';
    var el = document.querySelector(selector);
    if (el) {
      el.classList.add('is-just-added');
      setTimeout(function () { el.classList.remove('is-just-added'); }, 420);
    }
    lastAdded = null;
  }

  function addToState(kind, id) {
    if (kind === 'personal_field') {
      id = String(id);
      if (state.fields.indexOf(id) < 0) state.fields.push(id);
      lastAdded = { kind: kind, id: id };
      renderZones(); renderPalette();
      return;
    }
    var key = selKey(kind); id = Number(id);
    if (state[key].indexOf(id) < 0) state[key].push(id);
    lastAdded = { kind: kind, id: id };
    renderZones(); renderPalette();
  }
  function removeFromState(kind, id) {
    if (kind === 'personal_field') {
      id = String(id);
      if (PERSONAL_REQUIRED.indexOf(id) >= 0) {
        RZ.toast('必填个人信息会默认带入简历', 'info');
        return;
      }
      state.fields = state.fields.filter(function (x) { return x !== id; });
      renderZones(); renderPalette();
      renderBuildProgress();
      return;
    }
    var key = selKey(kind); id = Number(id);
    state[key] = state[key].filter(function (x) { return x !== id; });
    renderZones(); renderPalette();
    renderBuildProgress();
  }

  function zoneAccepts(zoneKey, kind) {
    if (zoneKey === 'personal') return kind === 'personal_field';
    if (zoneKey === 'skill_cert') return kind === 'skill' || kind === 'certificate';
    return zoneKey === kind;
  }

  function dragZones() {
    return Array.prototype.slice.call(document.querySelectorAll('.rz-zone'));
  }

  function setDropGuidance(kind) {
    var builder = document.querySelector('.rz-builder');
    if (builder) builder.classList.toggle('is-drag-guiding', !!kind);
    dragZones().forEach(function (zone) {
      var accepts = !!kind && zoneAccepts(zone.dataset.zone, kind);
      zone.classList.toggle('is-drop-match', accepts);
      zone.classList.toggle('is-drop-mismatch', !!kind && !accepts);
      if (!kind) zone.classList.remove('is-drop-hot');
    });
  }

  function startPaletteDrag(kind, id, source) {
    activeDrag = { kind: kind, id: id };
    if (source) source.classList.add('is-drag-source');
    setDropGuidance(kind);
  }

  function finishPaletteDrag() {
    activeDrag = null;
    document.querySelectorAll('.is-drag-source').forEach(function (el) {
      el.classList.remove('is-drag-source');
    });
    setDropGuidance('');
  }

  function bindPalette() {
    document.querySelectorAll('#rzPalette .rz-chip').forEach(function (el) {
      el.addEventListener('dragstart', function (e) {
        e.dataTransfer.setData('text/plain', el.dataset.kind + ':' + el.dataset.id);
        e.dataTransfer.effectAllowed = 'copy';
        startPaletteDrag(el.dataset.kind, el.dataset.id, el);
      });
      el.addEventListener('dragend', finishPaletteDrag);
      el.addEventListener('click', function () { addToState(el.dataset.kind, el.dataset.id); });
    });
  }

  function bindZones() {
    document.querySelectorAll('.rz-zone').forEach(function (zone) {
      var zk = zone.dataset.zone;
      zone.addEventListener('dragenter', function (e) {
        var kind = activeDrag ? activeDrag.kind : ((e.dataTransfer.getData('text/plain') || '').split(':')[0]);
        if (kind && zoneAccepts(zk, kind)) zone.classList.add('is-drop-hot');
      });
      zone.addEventListener('dragover', function (e) {
        var raw = e.dataTransfer.getData('text/plain') || '';
        var kind = activeDrag ? activeDrag.kind : raw.split(':')[0];
        e.preventDefault();
        if (kind && zoneAccepts(zk, kind)) {
          e.dataTransfer.dropEffect = 'copy';
          zone.classList.add('drag-over', 'is-drop-hot');
        } else {
          e.dataTransfer.dropEffect = 'none';
          zone.classList.remove('drag-over', 'is-drop-hot');
        }
      });
      zone.addEventListener('dragleave', function (e) {
        if (e.relatedTarget && zone.contains(e.relatedTarget)) return;
        zone.classList.remove('drag-over', 'is-drop-hot');
      });
      zone.addEventListener('drop', function (e) {
        e.preventDefault(); zone.classList.remove('drag-over', 'is-drop-hot');
        var raw = e.dataTransfer.getData('text/plain') || '';
        var parts = raw.split(':'); var kind = parts[0]; var id = parts[1];
        if (!kind || !id) return;
        if (zoneAccepts(zk, kind)) addToState(kind, id);
        else RZ.toast('该内容不能放入此区域', 'error');
        finishPaletteDrag();
      });
      zone.querySelectorAll('.rz-chip__x').forEach(function (x) {
        x.addEventListener('click', function (ev) {
          ev.stopPropagation();
          var c = x.closest('.rz-chip');
          removeFromState(c.dataset.kind, c.dataset.id);
        });
      });
    });
  }

  function closePaletteSheet() {
    if (!mobilePalette.aside) return;
    mobilePalette.aside.classList.remove('open');
    if (mobilePalette.scrim) mobilePalette.scrim.classList.remove('show');
    if (mobilePalette.button) mobilePalette.button.setAttribute('aria-expanded', 'false');
    document.body.style.overflow = mobilePalette.bodyOverflow || '';
    document.documentElement.style.overflow = mobilePalette.htmlOverflow || '';
  }

  function openPaletteSheet() {
    if (!mobilePalette.aside) return;
    mobilePalette.bodyOverflow = document.body.style.overflow;
    mobilePalette.htmlOverflow = document.documentElement.style.overflow;
    mobilePalette.aside.classList.add('open');
    if (mobilePalette.scrim) mobilePalette.scrim.classList.add('show');
    if (mobilePalette.button) mobilePalette.button.setAttribute('aria-expanded', 'true');
    document.body.style.overflow = 'hidden';
    document.documentElement.style.overflow = 'hidden';
  }

  function initMobilePaletteSheet() {
    var aside = document.querySelector('.rz-builder__aside');
    if (!aside || document.getElementById('rzPaletteOpen')) return;
    var scrim = document.createElement('div');
    scrim.className = 'rz-builder-scrim';
    scrim.addEventListener('click', closePaletteSheet);
    document.body.appendChild(scrim);

    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'rz-btn rz-btn--primary rz-palette-fab';
    button.id = 'rzPaletteOpen';
    button.setAttribute('aria-controls', 'rzPalette');
    button.setAttribute('aria-expanded', 'false');
    button.textContent = '+ 添加内容';
    button.addEventListener('click', function () {
      if (aside.classList.contains('open')) closePaletteSheet();
      else openPaletteSheet();
    });
    document.body.appendChild(button);

    var close = document.getElementById('rzPaletteClose');
    if (close) close.addEventListener('click', closePaletteSheet);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closePaletteSheet();
    });
    if (window.matchMedia) {
      var mobileQuery = window.matchMedia('(max-width: 768px)');
      var handleViewportChange = function (event) {
        if (!event.matches) closePaletteSheet();
      };
      if (mobileQuery.addEventListener) mobileQuery.addEventListener('change', handleViewportChange);
      else if (mobileQuery.addListener) mobileQuery.addListener(handleViewportChange);
    }
    window.addEventListener('resize', function () {
      if (window.innerWidth > 768) closePaletteSheet();
    });
    mobilePalette = { button: button, scrim: scrim, aside: aside, close: close, bodyOverflow: '', htmlOverflow: '' };
  }

  function buildLayout() {
    var blocks = [];
    if (state.self_intro.length) blocks.push({ type: 'self_intro', ids: state.self_intro });
    if (state.education.length) blocks.push({ type: 'education', ids: state.education });
    if (state.experience.length) blocks.push({ type: 'experience', ids: state.experience });
    if (state.skill.length || state.cert.length) blocks.push({ type: 'skill_cert', skill_ids: state.skill, cert_ids: state.cert });
    if (state.tech_stack) blocks.push({ type: 'tech_stack' });
    return { personal_fields: state.fields.filter(function (key) { return key !== 'name'; }), blocks: blocks };
  }

  function openBuildIssues(validation) {
    validation = validation || {};
    var missing = Array.isArray(validation.missing) ? validation.missing : [];
    var warnings = Array.isArray(validation.warnings) ? validation.warnings : [];
    var m = RZ.openModal({ title: '还差一点就能生成' });
    m.body.innerHTML = '<div class="rz-import-summary">' +
      '<div class="rz-import-summary__section"><h4>需要先补齐</h4>' +
      (missing.length ? '<ul class="rz-import-summary__list">' + missing.map(function (item) {
        return '<li>' + RZ.esc(item.label || item.key || '') + '</li>';
      }).join('') + '</ul>' : '<div class="rz-card__meta">没有必填缺口。</div>') + '</div>' +
      (warnings.length ? '<div class="rz-import-summary__section"><h4>建议优化</h4><ul class="rz-import-summary__list">' +
        warnings.map(function (item) { return '<li>' + RZ.esc(item.label || item.key || '') + '</li>'; }).join('') +
        '</ul></div>' : '') +
      '</div>';
    var first = missing[0];
    if (first && first.href && first.href !== '/resume/builder') {
      var go = document.createElement('a');
      go.className = 'rz-btn rz-btn--primary';
      go.href = first.href;
      go.textContent = '去补充';
      m.foot.appendChild(go);
    }
    var close = document.createElement('button');
    close.className = 'rz-btn';
    close.textContent = '继续编辑';
    close.onclick = m.close;
    m.foot.appendChild(close);
  }

  async function submit() {
    var btn = document.getElementById('rzBuildSubmit');
    var layout = buildLayout();
    if (!state.target_position.trim()) { RZ.toast('请先填写这份简历的目标岗位', 'error'); return; }
    if (!layout.blocks.length) { RZ.toast('请至少拖入一项内容', 'error'); return; }
    btn.disabled = true; btn.textContent = '生成中…';
    try {
      var body = {
        title: document.getElementById('rzResumeTitle').value.trim() || '我的简历',
        target_position: state.target_position.trim(),
        template_key: state.template, layout: layout,
        source_context: sourceContext
      };
      var validation = await RZ.api('/api/resume/builder/validate', { method: 'POST', body: body });
      if (!validation.validation || !validation.validation.ok) {
        openBuildIssues(validation.validation || {});
        btn.disabled = false;
        btn.textContent = EDIT_ID ? '保存修改' : '确定生成';
        return;
      }
      if (EDIT_ID) await RZ.api('/api/resume/resumes/' + EDIT_ID, { method: 'PUT', body: body });
      else await RZ.api('/api/resume/resumes', { method: 'POST', body: body });
      RZ.toast('已提交，正在渲染整合…', 'success');
      setTimeout(function () { window.location.href = '/resume/list'; }, 700);
    } catch (e) { RZ.toast(e.message, 'error'); btn.disabled = false; btn.textContent = EDIT_ID ? '保存修改' : '确定生成'; }
  }

  async function prefillFromResume(id) {
    var d = await RZ.api('/api/resume/resumes/' + id);
    var r = d.resume || {};
    EDIT_ID = id;
    state.target_position = r.target_position || state.target_position;
    sourceContext = r.source_context || sourceContext;
    var layout = r.layout || {};
    if (Array.isArray(layout.personal_fields) && layout.personal_fields.length) {
      state.fields = PERSONAL_REQUIRED.concat(layout.personal_fields).filter(function (value, index, arr) {
        return arr.indexOf(value) === index;
      });
    }
    state.template = r.template_key || state.template;
    state.self_intro = []; state.education = []; state.experience = []; state.skill = []; state.cert = [];
    state.tech_stack = false;
    (layout.blocks || []).forEach(function (b) {
      if (b.type === 'self_intro') state.self_intro = (b.ids || []).slice();
      else if (b.type === 'education') state.education = (b.ids || []).slice();
      else if (b.type === 'experience') state.experience = (b.ids || []).slice();
      else if (b.type === 'skill_cert') { state.skill = (b.skill_ids || []).slice(); state.cert = (b.cert_ids || []).slice(); }
      else if (b.type === 'tech_stack') state.tech_stack = true;
    });
    var titleEl = document.getElementById('rzResumeTitle');
    titleEl.value = r.title || '';
    lastAutoTitle = titleEl.value.trim();
    var targetInput = document.getElementById('rzTargetPosition');
    if (targetInput) targetInput.value = state.target_position || '';
    document.getElementById('rzBuildSubmit').textContent = '保存修改';
  }

  async function init() {
    document.getElementById('rzBuildSubmit').addEventListener('click', submit);
    var targetInput = document.getElementById('rzTargetPosition');
    if (targetInput) {
      targetInput.addEventListener('input', function () { setTargetPosition(targetInput.value, false); });
      targetInput.addEventListener('blur', function () { setTargetPosition(targetInput.value, false); });
    }
    initMobilePaletteSheet();
    try {
      DATA = await RZ.api('/api/resume/builder/palette');
      TEMPLATES = DATA.templates || [];
      POSITION_OPTIONS = Array.isArray(DATA.position_options) ? DATA.position_options : [];
      var t = (DATA.personal || {});
      ['email', 'phone'].forEach(function (key) {
        if (String(t[key] || '').trim() && state.fields.indexOf(key) < 0) state.fields.push(key);
      });
      var params = new URLSearchParams(window.location.search);
      var queryTarget = (params.get('target') || '').trim();
      var querySource = (params.get('source') || '').trim();
      var queryCareerTag = (params.get('career_tag') || '').trim();
      var queryJobId = (params.get('job_id') || '').trim();
      sourceContext = {
        source: querySource || 'builder',
        career_tag: queryCareerTag,
        target_position: queryTarget,
        job_id: queryJobId
      };
      state.target_position = queryTarget || t.expected_position || '';
      if (targetInput) targetInput.value = state.target_position;
      var editId = params.get('edit');
      if (editId) {
        try { await prefillFromResume(editId); } catch (e) { RZ.toast('载入简历失败：' + e.message, 'error'); }
      } else if (params.get('auto') === '1') {
        state.self_intro = (DATA.self_intro || []).map(function (item) { return Number(item.id); });
        state.education = (DATA.education || []).map(function (item) { return Number(item.id); });
        state.experience = (DATA.experience || []).map(function (item) { return Number(item.id); });
        state.skill = (DATA.skill || []).map(function (item) { return Number(item.id); });
        state.cert = (DATA.certificate || []).map(function (item) { return Number(item.id); });
        state.tech_stack = true;
      }
      if (querySource === 'career') {
        RZ.track('career_resume_started', {
          career_tag: queryCareerTag, target_position: state.target_position, source: 'career'
        }, 'career');
      } else if (querySource === 'job_analysis') {
        RZ.track('job_target_resume_started', {
          job_id: queryJobId, target_position: state.target_position, source: 'job_analysis'
        }, 'job');
      }
      renderTargetOptions();
      if (!editId) syncTitleFromTarget(true);
      renderTemplates(); renderZones(); renderPalette();
    } catch (e) { RZ.toast(e.message, 'error'); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
