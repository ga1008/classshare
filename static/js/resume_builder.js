/* Résumé builder — drag (and click) palette items into ordered zones, pick a
   template + personal fields, then create the résumé (background render). */
(function () {
  'use strict';
  var RZ = window.RZ;

  var DATA = null;
  var TEMPLATES = [];
  var EDIT_ID = null;
  var PERSONAL_REQUIRED = ['name', 'gender', 'birthday', 'email', 'expected_position'];
  var PERSONAL_FIELD_ORDER = [
    'name', 'gender', 'birthday', 'email', 'phone', 'qq', 'wechat',
    'expected_position', 'expected_industry', 'expected_salary', 'hometown', 'address'
  ];
  var state = {
    template: 'classic',
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
    { key: 'experience', label: '项目 / 比赛经验' },
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
        return '<div class="rz-zone" data-zone="tech_stack"><div class="rz-zone__head"><strong>技术栈</strong>' +
          '<label style="font-size:.8rem;font-weight:600"><input type="checkbox" id="rzTechToggle" ' +
          (state.tech_stack ? 'checked' : '') + '> 由 AI 自动生成</label></div>' +
          '<div class="rz-zone__hint">勾选后，根据你的经历与技能自动生成技术栈分组。</div></div>';
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
    var toggle = document.getElementById('rzTechToggle');
    if (toggle) toggle.addEventListener('change', function () { state.tech_stack = toggle.checked; });
  }

  function renderPalette() {
    var groups = [
      { kind: 'personal_field', label: '个人信息' },
      { kind: 'self_intro', label: '自我介绍' },
      { kind: 'education', label: '学习经历' },
      { kind: 'experience', label: '项目 / 比赛' },
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

  function addToState(kind, id) {
    if (kind === 'personal_field') {
      id = String(id);
      if (state.fields.indexOf(id) < 0) state.fields.push(id);
      renderZones(); renderPalette();
      return;
    }
    var key = selKey(kind); id = Number(id);
    if (state[key].indexOf(id) < 0) state[key].push(id);
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
      return;
    }
    var key = selKey(kind); id = Number(id);
    state[key] = state[key].filter(function (x) { return x !== id; });
    renderZones(); renderPalette();
  }

  function zoneAccepts(zoneKey, kind) {
    if (zoneKey === 'personal') return kind === 'personal_field';
    if (zoneKey === 'skill_cert') return kind === 'skill' || kind === 'certificate';
    return zoneKey === kind;
  }

  function bindPalette() {
    document.querySelectorAll('#rzPalette .rz-chip').forEach(function (el) {
      el.addEventListener('dragstart', function (e) {
        e.dataTransfer.setData('text/plain', el.dataset.kind + ':' + el.dataset.id);
      });
      el.addEventListener('click', function () { addToState(el.dataset.kind, el.dataset.id); });
    });
  }

  function bindZones() {
    document.querySelectorAll('.rz-zone').forEach(function (zone) {
      var zk = zone.dataset.zone;
      zone.addEventListener('dragover', function (e) { e.preventDefault(); zone.classList.add('drag-over'); });
      zone.addEventListener('dragleave', function () { zone.classList.remove('drag-over'); });
      zone.addEventListener('drop', function (e) {
        e.preventDefault(); zone.classList.remove('drag-over');
        var raw = e.dataTransfer.getData('text/plain') || '';
        var parts = raw.split(':'); var kind = parts[0]; var id = parts[1];
        if (!kind || !id) return;
        if (zoneAccepts(zk, kind)) addToState(kind, id);
        else RZ.toast('该内容不能放入此区域', 'error');
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

  async function submit() {
    var btn = document.getElementById('rzBuildSubmit');
    var layout = buildLayout();
    if (!layout.blocks.length) { RZ.toast('请至少拖入一项内容', 'error'); return; }
    btn.disabled = true; btn.textContent = '生成中…';
    try {
      var body = {
        title: document.getElementById('rzResumeTitle').value.trim() || '我的简历',
        template_key: state.template, layout: layout
      };
      if (EDIT_ID) await RZ.api('/api/resume/resumes/' + EDIT_ID, { method: 'PUT', body: body });
      else await RZ.api('/api/resume/resumes', { method: 'POST', body: body });
      RZ.toast('已提交，正在渲染整合…', 'success');
      setTimeout(function () { window.location.href = '/resume/list'; }, 700);
    } catch (e) { RZ.toast(e.message, 'error'); btn.disabled = false; btn.textContent = '确定生成'; }
  }

  async function prefillFromResume(id) {
    var d = await RZ.api('/api/resume/resumes/' + id);
    var r = d.resume || {};
    EDIT_ID = id;
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
    document.getElementById('rzBuildSubmit').textContent = '保存修改';
  }

  async function init() {
    document.getElementById('rzBuildSubmit').addEventListener('click', submit);
    initMobilePaletteSheet();
    try {
      DATA = await RZ.api('/api/resume/builder/palette');
      TEMPLATES = DATA.templates || [];
      var t = (DATA.personal || {});
      var editId = new URLSearchParams(window.location.search).get('edit');
      if (editId) {
        try { await prefillFromResume(editId); } catch (e) { RZ.toast('载入简历失败：' + e.message, 'error'); }
      }
      if (!document.getElementById('rzResumeTitle').value && t.expected_position) {
        document.getElementById('rzResumeTitle').value = t.expected_position + (t.name ? ' - ' + t.name : '');
      }
      renderTemplates(); renderZones(); renderPalette();
    } catch (e) { RZ.toast(e.message, 'error'); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
