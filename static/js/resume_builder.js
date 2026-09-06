/* Résumé builder — drag (and click) palette items into ordered zones, pick a
   template + personal fields, then create the résumé (background render). */
(function () {
  'use strict';
  var RZ = window.RZ;

  var DATA = null;
  var TEMPLATES = [];
  var POSITION_OPTIONS = [];
  var EDIT_ID = null;
  var REVISION = 0, contentOverrides = [], acceptedSummary = '', acceptedTech = [], saving = false, savedFingerprint = '', readyToEdit = false;
  var draftClientId = window.crypto && crypto.randomUUID ? crypto.randomUUID() : 'draft-' + Date.now() + '-' + Math.random().toString(36).slice(2);
  var lastAutoTitle = '';
  var sourceContext = {};
  var activeDrag = null;
  var lastAdded = null;
  var PERSONAL_REQUIRED = ['name', 'expected_position'];
  var PERSONAL_LABELS = { name: '姓名', gender: '性别', birthday: '出生日期', email: '邮箱', phone: '手机', qq: 'QQ', wechat: '微信',
    expected_position: '目标岗位', expected_industry: '意向行业', expected_salary: '期望薪资', hometown: '籍贯', address: '现居地址' };
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
    { key: 'tech_stack', label: '岗位能力清单' }
  ];

  function labelOf(kind, item) {
    if (kind === 'personal_field') return item.label + (item.value ? '：' + item.value : '（待完善）');
    if (kind === 'self_intro') return (item.title || '自我介绍') + '：' + (item.content_md || '').slice(0, 16);
    if (kind === 'education') return item.school || '学习经历';
    if (kind === 'experience') return item.title || '经历';
    return item.name || '项';
  }
  function itemById(kind, id) {
    if (kind === 'personal') return DATA.personal || {};
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
      return '<button type="button" class="rz-tpl' + (t.key === state.template ? ' active' : '') + '" data-tpl="' + RZ.esc(t.key) + '" aria-pressed="' + (t.key === state.template) + '">' +
        '<strong>' + RZ.esc(t.label) + '</strong><small>' + RZ.esc(t.description) + '</small></button>';
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
      return { id: key, key: key, label: labels[key] || PERSONAL_LABELS[key] || '个人资料', value: String(personal[key] || '').trim() };
    }).filter(function (item) { return includeBlank || item.value; });
  }

  function chip(kind, item, inZone) {
    var locked = kind === 'personal_field' && PERSONAL_REQUIRED.indexOf(item.id) >= 0;
    var x = inZone && !locked ? '<button type="button" class="rz-chip__x" title="移除">✕</button>' : '';
    var lock = locked && inZone ? '<span class="rz-chip__lock">必填</span>' : '';
    return '<div class="rz-chip' + (inZone ? ' in-zone' : '') + '"' + (inZone ? '' : ' role="button" tabindex="0" aria-label="添加 ' + RZ.esc(labelOf(kind, item)) + '"') + ' draggable="true" data-kind="' + kind + '" data-id="' + RZ.esc(item.id) + '">' +
      '<span class="rz-chip__label">' + RZ.esc(labelOf(kind, item)) + '</span>' + lock + x + '</div>';
  }

  function renderZones() {
    var html = ZONES.map(function (z) {
      if (z.key === 'tech_stack') {
        var targetHint = state.target_position
          ? '当前目标岗位：' + RZ.esc(state.target_position) + '。文件使用当前已确认的能力清单；新的 AI 建议需核对后采用。'
          : '填写目标岗位后，可在“我的简历”按需生成建议并核对采用。';
        return '<div class="rz-zone" data-zone="tech_stack"><div class="rz-zone__head"><strong>岗位能力清单</strong>' +
          '<label style="font-size:.8rem;font-weight:600"><input type="checkbox" id="rzTechToggle" ' +
          (state.tech_stack ? 'checked' : '') + '> 包含已确认的能力清单</label></div>' +
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
      el.addEventListener('keydown', function (event) { if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); var kind = el.dataset.kind, id = el.dataset.id; addToState(kind, id);
        var added = Array.from(document.querySelectorAll('.rz-zone .rz-chip')).find(function (item) { return item.dataset.kind === kind && item.dataset.id === id; });
        if (added) { added.tabIndex = -1; added.focus(); }
      } });
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
    var wasOpen = mobilePalette.aside.classList.contains('open');
    mobilePalette.aside.classList.remove('open');
    if (mobilePalette.scrim) mobilePalette.scrim.classList.remove('show');
    if (mobilePalette.button) mobilePalette.button.setAttribute('aria-expanded', 'false');
    document.body.style.overflow = mobilePalette.bodyOverflow || '';
    document.documentElement.style.overflow = mobilePalette.htmlOverflow || '';
    if (wasOpen && mobilePalette.button) mobilePalette.button.focus();
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
    if (mobilePalette.close) mobilePalette.close.focus();
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

  function draftBody() {
    return { title: document.getElementById('rzResumeTitle').value.trim() || '我的简历',
      target_position: state.target_position.trim(), template_key: state.template, layout: buildLayout(),
      source_context: sourceContext, revision: REVISION, content_overrides: contentOverrides, optimized_summary_md: acceptedSummary, tech_stack: acceptedTech, draft: true, client_id: draftClientId };
  }
  function fingerprint() { var body = draftBody(); delete body.revision; return JSON.stringify(body); }
  function draftStatus(message) { document.getElementById('rzDraftStatus').textContent = message; }
  async function submit(event, draftOnly) {
    if (saving || !readyToEdit) return;
    var btn = document.getElementById('rzBuildSubmit');
    var layout = buildLayout();
    if (!draftOnly && !state.target_position.trim()) { RZ.toast('请先填写这份简历的目标岗位，或先保存草稿', 'error'); return; }
    if (!draftOnly && !layout.blocks.length) { RZ.toast('请至少加入一项内容，或先保存草稿', 'error'); return; }
    saving = true; btn.disabled = true; document.getElementById('rzSaveDraft').disabled = true; btn.textContent = '正在保存…';
    var body = draftBody(), sentFingerprint = fingerprint(), draftSaved = false;
    try {
      var saved = EDIT_ID ? await RZ.api('/api/resume/resumes/' + EDIT_ID, { method: 'PUT', body: body }) :
        await RZ.api('/api/resume/resumes', { method: 'POST', body: body });
      draftSaved = true;
      EDIT_ID = saved.id || EDIT_ID; REVISION = Number(saved.revision || (saved.resume || {}).revision || REVISION + 1);
      savedFingerprint = sentFingerprint;
      history.replaceState(null, '', '/resume/builder?edit=' + EDIT_ID);
      draftStatus('草稿已保存 · 版本 ' + REVISION + (fingerprint() !== savedFingerprint ? ' · 还有新修改未保存' : ''));
      if (!draftOnly) {
        await RZ.api('/api/resume/resumes/' + EDIT_ID + '/publish', { method: 'POST', body: { revision: REVISION } });
        RZ.toast('已开始生成文件，可在“我的简历”查看进度', 'success');
        if (fingerprint() === savedFingerprint) window.location.href = '/resume/list';
      } else RZ.toast('草稿已保存', 'success');
    } catch (error) {
      draftStatus(error.status === 409 ? '版本冲突 · 当前编辑仍保留，尚未覆盖服务器' : draftSaved ? '草稿已保存 · 文件暂未生成，请查看提示后继续编辑' : '暂未完成保存 · 当前编辑仍保留');
      if (draftSaved && (error.status === 400 || error.status === 422)) openBuildIssues({ missing: [{ label: error.message }] });
      else RZ.conflict(error, body, async function () { await prefillFromResume(EDIT_ID); renderTemplates(); renderZones(); renderPalette(); savedFingerprint = fingerprint(); });
    } finally { saving = false; btn.disabled = false; btn.textContent = '生成文件'; document.getElementById('rzSaveDraft').disabled = false; }
  }

  async function prefillFromResume(id) {
    var d = await RZ.api('/api/resume/resumes/' + id);
    var r = d.resume || {};
    EDIT_ID = id;
    REVISION = Number(r.revision || 0);
    acceptedSummary = r.optimized_summary_md || '';
    acceptedTech = Array.isArray(r.tech_stack) ? r.tech_stack : [];
    contentOverrides = Array.isArray(r.content_overrides) ? r.content_overrides : [];
    var bundle = r.content_snapshot || (r.snapshot || {}).bundle;
    if (bundle) Object.keys(bundle).forEach(function (key) {
      if (key === 'personal') DATA[key] = bundle[key];
      else if (Array.isArray(bundle[key])) {
        var existing = DATA[key] || [], snapshotIds = new Set(bundle[key].map(function (item) { return String(item.id); }));
        DATA[key] = bundle[key].concat(existing.filter(function (item) { return !snapshotIds.has(String(item.id)); }));
      }
    });
    contentOverrides.forEach(function (entry) { var item = itemById(entry.section, entry.id); if (item) Object.assign(item, entry.fields || {}); });
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
    document.getElementById('rzBuildSubmit').textContent = '生成文件';
    draftStatus('已载入版本 ' + REVISION + (r.render_revision ? ' · 已生成文件版本 ' + r.render_revision : ' · 尚未生成文件'));
    if ((r.snapshot || {}).legacy_materials_reconstructed) draftStatus('已载入版本 ' + REVISION + ' · 这份历史简历的编辑材料由现有资料重建，请逐项核对；原版本文件仍可在历史中查看。');
  }

  function editContent() {
    if (!DATA) return;
    var fields = {
      personal: PERSONAL_LABELS,
      self_intro: { title: '标题', content_md: '自我介绍' }, education: { school: '学校', degree: '学历/学位层次（不确定可留空）', major: '专业', college: '学院', start_date: '开始年月', end_date: '结束年月', content: '学习内容' },
      experience: { title: '名称', role: '角色', start_date: '开始年月', end_date: '结束年月', content: '背景与任务', contribution: '我的行动', achievement: '真实结果' },
      skill: { name: '技能', level: '熟练程度', acquired_date: '获得年月', expiry_date: '有效期', description: '说明' }, certificate: { name: '证书', acquired_date: '获得年月', expiry_date: '有效期', description: '说明' }
    };
    var entries = [];
    Object.keys(fields).forEach(function (section) {
      var ids = section === 'personal' ? [0] : state[section === 'certificate' ? 'cert' : section];
      ids.forEach(function (id) { var item = itemById(section, id); if (item) entries.push({ section: section, id: id, item: item }); });
    });
    var modal = RZ.openModal({ title: '编辑本份简历文字', wide: true });
    modal.body.innerHTML = '<p>这里的修改只用于这份简历。请保留真实经历、技能和数字。</p>' +
      '<fieldset class="rz-snapshot-fields"><legend>本份职业摘要与能力清单</legend><label class="rz-field">职业摘要<textarea class="rz-textarea" id="rzSnapshotSummary" maxlength="2000" rows="4">' + RZ.esc(acceptedSummary) + '</textarea></label><p>生成文件优先使用此摘要；留空时使用所选素材中的自我介绍。</p>' +
      '<label class="rz-field">岗位能力清单<textarea class="rz-textarea" id="rzSnapshotCapabilities" maxlength="6000" rows="4" placeholder="例如：语言沟通：英语写作、课堂沟通">' + RZ.esc(acceptedTech.map(function (group) { return typeof group === 'string' ? group : (group.group || '相关技能') + '：' + (group.items || []).join('、'); }).join('\n')) + '</textarea></label><p>每行一个分组，写成“分组名称：技能一、技能二”。只填写真实掌握且有资料支持的能力。</p></fieldset>' +
      (entries.length ? entries.map(function (entry, index) {
        return '<fieldset class="rz-snapshot-fields"><legend>' + (entry.section === 'personal' ? '本份个人信息' : RZ.esc(labelOf(entry.section, entry.item))) + '</legend>' +
          Object.keys(fields[entry.section]).map(function (key) {
            var value = RZ.esc(entry.item[key] || ''), attrs = ' data-entry="' + index + '" data-field="' + key + '"';
            var input = key === 'degree' ? '<select class="rz-select"' + attrs + '>' + ['', '高中', '中专', '大专', '本科', '硕士', '博士', '其他'].map(function (degree) { return '<option value="' + degree + '"' + (degree === entry.item[key] ? ' selected' : '') + '>' + (degree || '待确认') + '</option>'; }).join('') + '</select>' :
              entry.section === 'personal' || /_date$/.test(key) ? '<input class="rz-input" maxlength="200" value="' + value + '"' + attrs + '>' :
              '<textarea class="rz-textarea" rows="3" maxlength="6000"' + attrs + '>' + value + '</textarea>';
            return '<label class="rz-field">' + RZ.esc(fields[entry.section][key]) + input + '</label>'; }).join('') + '</fieldset>';
      }).join('') : '<p>先从素材区加入一段经历或介绍，再编辑这份简历的文字。</p>');
    var save = document.createElement('button'); save.className = 'rz-btn rz-btn--primary'; save.textContent = '应用到当前草稿';
    save.onclick = function () {
      acceptedSummary = modal.body.querySelector('#rzSnapshotSummary').value;
      acceptedTech = modal.body.querySelector('#rzSnapshotCapabilities').value.split('\n').map(function (line) {
        var parts = line.trim().split(/[:：]/), group = parts.length > 1 ? parts.shift().trim() : '相关技能';
        var items = parts.join('：').split(/[、,，;]/).map(function (item) { return item.trim(); }).filter(Boolean);
        return { group: group || '相关技能', items: items };
      }).filter(function (group) { return group.items.length; });
      modal.body.querySelectorAll('[data-entry]').forEach(function (input) {
        var entry = entries[Number(input.dataset.entry)], key = input.dataset.field;
        if (String(entry.item[key] || '') === input.value) return;
        var override = contentOverrides.find(function (item) { return item.section === entry.section && String(item.id) === String(entry.id); });
        if (!override) { override = { section: entry.section, id: entry.id, fields: {} }; contentOverrides.push(override); }
        override.fields[key] = input.value; entry.item[key] = input.value;
      });
      renderZones(); renderPalette(); draftStatus('文字已应用到当前草稿 · 记得保存'); modal.close();
    };
    modal.foot.appendChild(save);
  }

  async function init() {
    var initialControls = ['rzResumeTitle', 'rzTargetPosition', 'rzBuildSubmit', 'rzSaveDraft', 'rzEditContent'].map(function (id) { return document.getElementById(id); }).filter(Boolean);
    initialControls.forEach(function (control) { control.disabled = true; });
    draftStatus('正在读取这份简历与素材…');
    document.getElementById('rzBuildSubmit').addEventListener('click', submit);
    document.getElementById('rzSaveDraft').addEventListener('click', function () { submit(null, true); });
    document.getElementById('rzEditContent').addEventListener('click', editContent);
    window.addEventListener('beforeunload', function (event) {
      if (readyToEdit && fingerprint() !== savedFingerprint) { event.preventDefault(); event.returnValue = ''; }
    });
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
        direction_id: params.get('direction_id') || '',
        recommendation_revision: params.get('recommendation_revision') || '',
        target_position: queryTarget,
        job_id: queryJobId
      };
      state.target_position = queryTarget || t.expected_position || '';
      if (targetInput) targetInput.value = state.target_position;
      var editId = params.get('edit');
      if (editId) {
        await prefillFromResume(editId);
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
      readyToEdit = true; savedFingerprint = fingerprint();
      initialControls.forEach(function (control) { control.disabled = false; });
      if (!editId) draftStatus('当前为未保存草稿');
    } catch (e) {
      var status = document.getElementById('rzDraftStatus'); status.textContent = '无法载入：' + e.message + '。';
      var back = document.createElement('a'); back.href = '/resume/list'; back.textContent = ' 返回我的简历'; status.appendChild(back);
      var retry = document.createElement('button'); retry.type = 'button'; retry.className = 'rz-btn rz-btn--sm'; retry.textContent = '重新加载'; retry.onclick = function () { window.location.reload(); }; status.appendChild(retry);
      RZ.toast(e.message, 'error');
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
