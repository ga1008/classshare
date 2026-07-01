/* 我的简历 — list cards (with rendering placeholder + poll), preview iframe,
   edit (→ builder), delete, and Word/PDF download. */
(function () {
  'use strict';
  var RZ = window.RZ;
  var box = document.getElementById('rzResumes');
  var importBtn = document.getElementById('rzImportResumeBtn');
  var importFile = document.getElementById('rzImportResumeFile');
  var importDrop = document.getElementById('rzImportDrop');
  var readinessBox = document.getElementById('rzReadiness');
  var filterBar = document.getElementById('rzResumeFilters');
  var pollTimer = null;
  var currentFilter = 'all';
  var lastItems = [];
  var TPL_LABEL = { classic: '经典单栏', sidebar: '双栏侧边', modern: '现代强调' };

  function fmtTime(s) { return (s || '').replace('T', ' ').slice(0, 16); }

  function unresolvedConflicts(r) {
    var summary = r.import_summary || {};
    var conflicts = Array.isArray(summary.conflicts) ? summary.conflicts : [];
    return conflicts.filter(function (c) { return c && !c.accepted; });
  }

  function isProcessing(r) {
    return r.status === 'rendering' || r.status === 'optimizing' || r.status === 'parsing';
  }

  function itemMatchesFilter(r) {
    if (currentFilter === 'ready') return r.status === 'ready';
    if (currentFilter === 'processing') return isProcessing(r);
    if (currentFilter === 'review') return r.status === 'failed' || unresolvedConflicts(r).length > 0;
    return true;
  }

  function renderReadiness(readiness) {
    if (!readinessBox || !readiness) return;
    var score = Math.max(0, Math.min(100, Number(readiness.score || 0)));
    var checks = Array.isArray(readiness.checks) ? readiness.checks : [];
    var actions = Array.isArray(readiness.next_actions) ? readiness.next_actions : [];
    readinessBox.innerHTML = '<div class="rz-readiness__score"><strong>' + score + '</strong>' +
      '<span>' + RZ.esc(readiness.message || '简历准备度') + '</span>' +
      '<div class="rz-readiness__bar"><i style="width:' + score + '%"></i></div></div>' +
      '<div class="rz-readiness__main"><div class="rz-readiness__checks">' +
      checks.map(function (item) {
        return '<a class="rz-readiness__check" data-status="' + RZ.esc(item.status || 'todo') + '" href="' + RZ.esc(item.href || '#') + '">' +
          '<span class="rz-readiness__dot"></span><span class="rz-readiness__label">' + RZ.esc(item.label || '') + '</span>' +
          '<span class="rz-readiness__count">' + RZ.esc(item.count || '') + '</span></a>';
      }).join('') + '</div><div class="rz-readiness__actions">' +
      actions.map(function (item) {
        return '<a href="' + RZ.esc(item.href || '#') + '">' + RZ.esc(item.label || '') + '</a>';
      }).join('') + '</div></div>';
  }

  function renderFilters(items) {
    if (!filterBar) return;
    var counts = {
      all: items.length,
      ready: items.filter(function (r) { return r.status === 'ready'; }).length,
      processing: items.filter(isProcessing).length,
      review: items.filter(function (r) { return r.status === 'failed' || unresolvedConflicts(r).length > 0; }).length
    };
    var filters = [
      ['all', '全部', counts.all],
      ['ready', '可投递', counts.ready],
      ['processing', '处理中', counts.processing],
      ['review', '待确认', counts.review]
    ];
    filterBar.innerHTML = filters.map(function (f) {
      return '<button type="button" class="' + (currentFilter === f[0] ? 'active' : '') + '" data-filter="' + f[0] + '">' +
        RZ.esc(f[1]) + ' ' + f[2] + '</button>';
    }).join('');
    filterBar.querySelectorAll('[data-filter]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        currentFilter = btn.dataset.filter || 'all';
        renderResumeGrid(lastItems);
        renderFilters(lastItems);
      });
    });
  }

  function renderResumeGrid(items) {
    var visible = items.filter(itemMatchesFilter);
    if (!visible.length) {
      box.innerHTML = '<div class="rz-empty" style="grid-column:1/-1">' +
        '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"></path><path d="M14 2v5h5"></path></svg>' +
        '<div>' + (items.length ? '当前筛选下没有简历' : '还没有简历，点击“新建简历”或拖入已有简历开始') + '</div></div>';
      return;
    }
    box.innerHTML = visible.map(renderCard).join('');
    bind(visible);
  }

  function renderCard(r) {
    if (r.status === 'rendering' || r.status === 'optimizing' || r.status === 'parsing') {
      var loadingTitle = r.status === 'optimizing'
        ? 'AI 正在按目标岗位优化…'
        : (r.status === 'parsing' ? '正在解析导入简历…' : '正在渲染整合…');
      var loadingMeta = r.status === 'parsing'
        ? (r.source_filename || r.title || '简历文件')
        : (r.target_position ? '目标岗位：' + r.target_position : r.title);
      return '<div class="rz-card rz-card--placeholder" data-rendering="1">' +
        '<div class="rz-card__title"><span class="rz-spin"></span> ' + loadingTitle + '</div>' +
        '<div class="rz-card__meta">' + RZ.esc(loadingMeta) + '</div></div>';
    }
    var failed = r.status === 'failed';
    var tag = '<span class="rz-card__tag">' + RZ.esc(TPL_LABEL[r.template_key] || r.template_key) + '</span>';
    var target = r.target_position ? '<span class="rz-card__target">目标：' + RZ.esc(r.target_position) + '</span>' : '';
    var optimized = r.optimized_summary_md ? '<span class="rz-card__target rz-card__target--ok">AI 已优化</span>' : '';
    var notes = r.optimization_notes && Array.isArray(r.optimization_notes.items) ? r.optimization_notes.items : [];
    var noteHtml = notes.length ? '<div class="rz-card__note">' + RZ.esc(notes[0]) + '</div>' : '';
    var importSummary = r.import_summary || {};
    var importMsg = importSummary.message || importSummary.source_filename || '';
    var conflictCount = unresolvedConflicts(r).length;
    var importHtml = importMsg ? '<div class="rz-card__note rz-card__note--import">' + RZ.esc(importMsg) + '</div>' : '';
    var importBtnHtml = importSummary.source === 'import'
      ? '<button class="rz-btn rz-btn--sm" data-act="import-summary">导入结果</button>'
      : '';
    if (importSummary.source === 'import' && conflictCount) {
      importBtnHtml = '<button class="rz-btn rz-btn--sm" data-act="import-summary">待确认 ' + conflictCount + '</button>';
    }
    var actionList = '<div class="rz-card__actions">' +
      (failed ? '' : '<button class="rz-btn rz-btn--sm" data-act="preview">预览</button>') +
      '<button class="rz-btn rz-btn--sm rz-btn--primary" data-act="optimize">AI 优化</button>' +
      importBtnHtml +
      '<button class="rz-btn rz-btn--sm" data-act="edit">编辑</button>' +
      (failed ? '' : '<a class="rz-btn rz-btn--sm" href="/api/resume/resumes/' + r.id + '/export?fmt=pdf" target="_blank">PDF</a>' +
      '<a class="rz-btn rz-btn--sm" href="/api/resume/resumes/' + r.id + '/export?fmt=docx" target="_blank">Word</a>') +
      '<button class="rz-btn rz-btn--sm rz-btn--danger" data-act="del">删除</button></div>';
    return '<div class="rz-card' + (failed ? ' rz-card--failed' : '') + '" data-id="' + r.id + '" style="cursor:default">' +
      '<div class="rz-card__title">' + RZ.esc(r.title) + '</div>' +
      '<div class="rz-card__meta">' + tag + target + optimized + fmtTime(r.updated_at) +
      (failed && r.error_text ? '<br><span style="color:#dc2626">' + RZ.esc(r.error_text) + '</span>' : '') + '</div>' +
      importHtml + noteHtml + actionList + '</div>';
  }

  function bind(items) {
    box.querySelectorAll('.rz-card[data-id]').forEach(function (card) {
      var id = card.dataset.id;
      var r = items.filter(function (x) { return String(x.id) === id; })[0];
      card.querySelectorAll('[data-act]').forEach(function (b) {
        b.addEventListener('click', function (e) {
          e.stopPropagation();
          var act = b.dataset.act;
          if (act === 'preview') openPreview(r);
          else if (act === 'optimize') optimizeResume(r, b);
          else if (act === 'import-summary') openImportSummary(r);
          else if (act === 'edit') window.location.href = '/resume/builder?edit=' + id;
          else if (act === 'del') del(r);
        });
      });
    });
  }

  function openPreview(r) {
    var m = RZ.openModal({ title: r.title + ' · 预览', wide: true });
    m.body.classList.add('rz-modal__body--preview');
    m.body.innerHTML = '<div class="rz-preview-tools" aria-label="预览尺寸">' +
      '<button type="button" class="rz-btn rz-btn--sm" data-preview-mode="fit">适应宽度</button>' +
      '<button type="button" class="rz-btn rz-btn--sm" data-preview-mode="original">原始尺寸</button></div>' +
      '<div class="rz-preview-shell"><iframe class="rz-preview-frame" src="/api/resume/resumes/' + r.id + '/preview"></iframe></div>';
    var shell = m.body.querySelector('.rz-preview-shell');
    var frame = m.body.querySelector('.rz-preview-frame');
    var modeBtns = m.body.querySelectorAll('[data-preview-mode]');
    var mode = window.matchMedia && window.matchMedia('(max-width: 768px)').matches ? 'fit' : 'original';
    function applyPreviewMode(nextMode) {
      mode = nextMode || mode;
      modeBtns.forEach(function (btn) {
        btn.classList.toggle('active', btn.dataset.previewMode === mode);
      });
      if (mode === 'fit') {
        var scale = Math.min(1, Math.max(0.32, (shell.clientWidth || 820) / 820));
        shell.style.setProperty('--rz-preview-scale', scale.toFixed(3));
        shell.classList.add('is-fit');
      } else {
        shell.style.setProperty('--rz-preview-scale', '1');
        shell.classList.remove('is-fit');
      }
    }
    modeBtns.forEach(function (btn) {
      btn.addEventListener('click', function () { applyPreviewMode(btn.dataset.previewMode); });
    });
    frame.addEventListener('load', function () { setTimeout(function () { applyPreviewMode(mode); }, 60); });
    setTimeout(function () { applyPreviewMode(mode); }, 60);
    var pdf = document.createElement('a'); pdf.className = 'rz-btn'; pdf.textContent = '下载 PDF';
    pdf.href = '/api/resume/resumes/' + r.id + '/export?fmt=pdf'; pdf.target = '_blank';
    var word = document.createElement('a'); word.className = 'rz-btn'; word.textContent = '下载 Word';
    word.href = '/api/resume/resumes/' + r.id + '/export?fmt=docx'; word.target = '_blank';
    var optimizeBtn = document.createElement('button'); optimizeBtn.className = 'rz-btn rz-btn--primary'; optimizeBtn.textContent = 'AI 优化这份';
    optimizeBtn.onclick = function () { optimizeResume(r, optimizeBtn, m.close); };
    var close = document.createElement('button'); close.className = 'rz-btn rz-btn--primary'; close.textContent = '关闭'; close.onclick = m.close;
    m.foot.appendChild(optimizeBtn); m.foot.appendChild(pdf); m.foot.appendChild(word); m.foot.appendChild(close);
  }

  async function optimizeResume(r, trigger, done) {
    if (!r.target_position) {
      RZ.toast('这份简历还没有目标岗位，请先编辑补充', 'error');
      return;
    }
    var oldText = trigger ? trigger.textContent : '';
    if (trigger) { trigger.disabled = true; trigger.textContent = '优化中…'; }
    try {
      await RZ.api('/api/resume/resumes/' + r.id + '/optimize', { method: 'POST' });
      RZ.toast('AI 正在按目标岗位优化这份简历', 'success');
      if (typeof done === 'function') done();
      load();
    } catch (e) {
      RZ.toast(e.message, 'error');
      if (trigger) { trigger.disabled = false; trigger.textContent = oldText || 'AI 优化'; }
    }
  }

  function del(r) {
    RZ.confirmDialog('确定删除简历「' + r.title + '」吗？', async function () {
      try { await RZ.api('/api/resume/resumes/' + r.id, { method: 'DELETE' }); RZ.toast('已删除', 'success'); load(); }
      catch (e) { RZ.toast(e.message, 'error'); }
    });
  }

  function totalCount(map) {
    if (!map || typeof map !== 'object') return 0;
    return Object.keys(map).reduce(function (sum, key) {
      return sum + (Array.isArray(map[key]) ? map[key].length : 0);
    }, 0);
  }

  function sectionLabel(key) {
    return {
      personal: '个人信息',
      self_intro: '自我介绍',
      education: '学历',
      experience: '经验',
      skill: '技能',
      certificate: '证书'
    }[key] || key;
  }

  function listMap(map, empty) {
    map = map || {};
    var rows = [];
    Object.keys(map).forEach(function (key) {
      var value = map[key];
      if (!Array.isArray(value) || !value.length) return;
      rows.push('<li>' + RZ.esc(sectionLabel(key)) + '：' + value.length + ' 项</li>');
    });
    return rows.length ? '<ul class="rz-import-summary__list">' + rows.join('') + '</ul>' : '<div class="rz-card__meta">' + RZ.esc(empty) + '</div>';
  }

  function openImportSummary(r) {
    var s = r.import_summary || {};
    var conflicts = Array.isArray(s.conflicts) ? s.conflicts : [];
    var warnings = Array.isArray(s.warnings) ? s.warnings : [];
    var pendingCount = conflicts.filter(function (c) { return c && !c.accepted; }).length;
    var m = RZ.openModal({ title: '导入结果' });
    var conflictHtml = conflicts.length ? conflicts.slice(0, 12).map(function (c) {
      var field = c.field ? ' · ' + c.field : '';
      var accepted = !!c.accepted;
      var index = conflicts.indexOf(c);
      return '<div class="rz-import-summary__conflict' + (accepted ? ' is-accepted' : '') + '"><strong>' + RZ.esc(sectionLabel(c.section)) + RZ.esc(field) +
        '</strong><br>已有：' + RZ.esc(c.existing || c.existing_id || '') +
        '<br>导入：' + RZ.esc(c.incoming || '') +
        '<div class="rz-import-summary__conflict-actions">' +
        (accepted
          ? '<span class="rz-card__target rz-card__target--ok">已采用</span>'
          : '<button type="button" class="rz-btn rz-btn--sm rz-btn--primary" data-accept-conflict="' + index + '">采用导入值</button>') +
        '</div></div>';
    }).join('') : '<div class="rz-card__meta">没有发现需要确认的相似冲突。</div>';
    var warningHtml = warnings.length
      ? '<ul class="rz-import-summary__list">' + warnings.slice(0, 12).map(function (w) { return '<li>' + RZ.esc(w) + '</li>'; }).join('') + '</ul>'
      : '<div class="rz-card__meta">无额外提示。</div>';
    m.body.innerHTML = '<div class="rz-import-summary">' +
      '<div class="rz-import-summary__grid">' +
      '<div class="rz-import-summary__stat"><strong>' + totalCount(s.added) + '</strong><span>自动新增</span></div>' +
      '<div class="rz-import-summary__stat"><strong>' + totalCount(s.updated) + '</strong><span>补全资料</span></div>' +
      '<div class="rz-import-summary__stat"><strong>' + pendingCount + '</strong><span>待确认冲突</span></div>' +
      '</div>' +
      '<div class="rz-import-summary__section"><h4>新增内容</h4>' + listMap(s.added, '没有新增资料。') + '</div>' +
      '<div class="rz-import-summary__section"><h4>补全内容</h4>' + listMap(s.updated, '没有补全现有资料。') + '</div>' +
      '<div class="rz-import-summary__section"><h4>相似冲突</h4>' + conflictHtml + '</div>' +
      '<div class="rz-import-summary__section"><h4>系统提示</h4>' + warningHtml + '</div>' +
      '</div>';
    m.body.querySelectorAll('[data-accept-conflict]').forEach(function (btn) {
      btn.addEventListener('click', async function () {
        btn.disabled = true;
        try {
          var result = await RZ.api('/api/resume/resumes/' + r.id + '/import-conflicts/' + btn.dataset.acceptConflict + '/accept', { method: 'POST' });
          r.import_summary = result.summary || r.import_summary;
          RZ.toast('已采用导入值，并同步刷新简历预览', 'success');
          m.close();
          load();
        } catch (e) {
          btn.disabled = false;
          RZ.toast(e.message, 'error');
        }
      });
    });
    var edit = document.createElement('a');
    edit.className = 'rz-btn';
    edit.href = '/resume/builder?edit=' + r.id;
    edit.textContent = '编辑简历';
    var close = document.createElement('button');
    close.className = 'rz-btn rz-btn--primary';
    close.textContent = '知道了';
    close.onclick = m.close;
    m.foot.appendChild(edit);
    m.foot.appendChild(close);
  }

  async function uploadImportFile(file) {
    if (!file) return;
    var max = 20 * 1024 * 1024;
    if (file.size > max) {
      RZ.toast('简历文件不能超过 20MB', 'error');
      return;
    }
    var fd = new FormData();
    fd.append('file', file);
    if (importBtn) {
      importBtn.disabled = true;
      importBtn.textContent = '上传中…';
    }
    try {
      await RZ.api('/api/resume/import', { method: 'POST', body: fd });
      RZ.toast('已开始解析简历，完成后卡片会自动更新', 'success');
      load();
    } catch (e) {
      RZ.toast(e.message, 'error');
    } finally {
      if (importBtn) {
        importBtn.disabled = false;
        importBtn.textContent = '导入简历';
      }
      if (importFile) importFile.value = '';
    }
  }

  async function load() {
    try {
      var results = await Promise.all([
        RZ.api('/api/resume/resumes'),
        RZ.api('/api/resume/readiness')
      ]);
      var items = results[0].items || [];
      lastItems = items;
      renderReadiness(results[1].readiness);
      renderFilters(items);
      renderResumeGrid(items);
      var rendering = items.some(isProcessing);
      if (rendering && !pollTimer) pollTimer = setInterval(load, 2500);
      else if (!rendering && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    } catch (e) { RZ.toast(e.message, 'error'); }
  }

  if (importBtn && importFile) {
    importBtn.addEventListener('click', function () { importFile.click(); });
    importFile.addEventListener('change', function () { uploadImportFile(importFile.files && importFile.files[0]); });
  }
  if (importDrop && importFile) {
    importDrop.addEventListener('click', function () { importFile.click(); });
    ['dragenter', 'dragover'].forEach(function (type) {
      importDrop.addEventListener(type, function (event) {
        event.preventDefault();
        importDrop.classList.add('is-hot');
      });
    });
    ['dragleave', 'drop'].forEach(function (type) {
      importDrop.addEventListener(type, function (event) {
        event.preventDefault();
        if (type === 'dragleave' && event.relatedTarget && importDrop.contains(event.relatedTarget)) return;
        importDrop.classList.remove('is-hot');
      });
    });
    importDrop.addEventListener('drop', function (event) {
      var file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      uploadImportFile(file);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load);
  else load();
})();
