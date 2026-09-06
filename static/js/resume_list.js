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
  var pageOffset = 0, pageSize = 50, listRequest = 0;
  var TPL_LABEL = { classic: '经典单栏', sidebar: '双栏侧边', modern: '现代强调' };

  function fmtTime(s) { return (s || '').replace('T', ' ').slice(0, 16); }

  function unresolvedConflicts(r) {
    var summary = r.import_summary || {};
    var conflicts = Array.isArray(summary.conflicts) ? summary.conflicts : [];
    return conflicts.filter(function (c) { return c && !c.accepted; });
  }

  function isProcessing(r) {
    return r.status === 'rendering' || r.status === 'optimizing' || r.status === 'parsing' || window.CareerTools.pending(r.job);
  }

  function itemMatchesFilter(r) {
    if (currentFilter === 'ready') return r.status === 'ready';
    if (currentFilter === 'processing') return isProcessing(r);
    if (currentFilter === 'review') return r.status === 'failed' || r.status === 'review_ready' || unresolvedConflicts(r).length > 0;
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
      review: items.filter(function (r) { return r.status === 'failed' || r.status === 'review_ready' || unresolvedConflicts(r).length > 0; }).length
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
      return '<div class="rz-card rz-card--placeholder" data-rendering="1" data-id="' + Number(r.id) + '">' +
        '<div class="rz-card__title"><span class="rz-spin"></span> ' + loadingTitle + '</div>' +
        '<div class="rz-card__meta">' + RZ.esc(loadingMeta) + '</div><p>可以离开此页，处理会继续。已保存的内容仍可查看和编辑。</p>' +
        '<div class="rz-card__actions"><button type="button" class="rz-btn rz-btn--sm" data-act="job">查看任务 / 取消</button>' +
        '<button type="button" class="rz-btn rz-btn--sm" data-act="edit">继续编辑</button></div></div>';
    }
    var failed = r.status === 'failed';
    var tag = '<span class="rz-card__tag">' + RZ.esc(TPL_LABEL[r.template_key] || '简历') + '</span>';
    var target = r.target_position ? '<span class="rz-card__target">目标：' + RZ.esc(r.target_position) + '</span>' : '';
    var optimized = r.optimized_summary_md ? '<span class="rz-card__target rz-card__target--ok">已设置职业摘要</span>' : '';
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
      (Number(r.render_revision) > 0 ? '<button class="rz-btn rz-btn--sm" data-act="preview">预览文件</button>' : '') +
      '<button class="rz-btn rz-btn--sm rz-btn--primary" data-act="optimize">AI 优化</button>' +
      (r.status === 'review_ready' ? '<button class="rz-btn rz-btn--sm rz-btn--primary" data-act="candidates">核对待确认内容</button>' : '') +
      '<button class="rz-btn rz-btn--sm" data-act="versions">历史版本</button>' +
      (failed || r.active_job_id ? '<button class="rz-btn rz-btn--sm" data-act="job">处理状态</button>' : '') +
      importBtnHtml +
      '<button class="rz-btn rz-btn--sm" data-act="edit">编辑</button>' +
      (Number(r.render_revision) > 0 ? '<a class="rz-btn rz-btn--sm" href="/api/resume/resumes/' + r.id + '/export?fmt=pdf&revision=' + Number(r.render_revision) + '" target="_blank" rel="noopener">PDF</a>' +
      '<a class="rz-btn rz-btn--sm" href="/api/resume/resumes/' + r.id + '/export?fmt=docx&revision=' + Number(r.render_revision) + '" target="_blank" rel="noopener">Word</a>' : '') +
      '<button class="rz-btn rz-btn--sm rz-btn--danger" data-act="del">删除</button></div>';
    return '<div class="rz-card' + (failed ? ' rz-card--failed' : '') + '" data-id="' + r.id + '" style="cursor:default">' +
      '<div class="rz-card__title">' + RZ.esc(r.title) + '</div>' +
      '<div class="rz-card__meta">' + tag + target + optimized + fmtTime(r.updated_at) +
      '<br>内容版本 ' + Number(r.revision || 1) + (r.render_revision ? ' · 文件版本 ' + Number(r.render_revision) : ' · 草稿尚未生成文件') +
      (r.render_revision && r.render_revision !== r.revision ? '<br><span class="rz-version-note">当前下载为此前版本，请生成文件以应用最新修改。</span>' : '') +
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
          else if (act === 'candidates') openCandidates(r);
          else if (act === 'versions') openVersions(r);
          else if (act === 'job') openJob(r);
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
      '<p class="rz-version-note">正在预览已生成的版本 ' + Number(r.render_revision || r.revision) + '</p><div class="rz-preview-shell"><iframe title="简历预览" class="rz-preview-frame" src="/api/resume/resumes/' + r.id + '/preview?revision=' + Number(r.render_revision || r.revision) + '"></iframe></div>';
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
    pdf.href = '/api/resume/resumes/' + r.id + '/export?fmt=pdf&revision=' + Number(r.render_revision || r.revision); pdf.target = '_blank'; pdf.rel = 'noopener';
    var word = document.createElement('a'); word.className = 'rz-btn'; word.textContent = '下载 Word';
    word.href = '/api/resume/resumes/' + r.id + '/export?fmt=docx&revision=' + Number(r.render_revision || r.revision); word.target = '_blank'; word.rel = 'noopener';
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
      await RZ.api('/api/resume/resumes/' + r.id + '/optimize', { method: 'POST', body: { revision: r.revision } });
      RZ.toast('正在准备优化建议，完成后请核对并决定是否采用', 'success');
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
          var result = await RZ.api('/api/resume/resumes/' + r.id + '/import-conflicts/' + btn.dataset.acceptConflict + '/accept', { method: 'POST', body: { revision: r.revision } });
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

  async function load(quiet) {
    var requestId = ++listRequest;
    try {
      var results = await Promise.all([
        RZ.api('/api/resume/resumes?limit=' + pageSize + '&offset=' + pageOffset + (quiet === true ? '&compact=true' : '')),
        quiet === true ? Promise.resolve({}) : RZ.api('/api/resume/readiness')
      ]);
      if (requestId !== listRequest) return lastItems;
      var items = results[0].items || [];
      if (quiet === true) {
        var fields = ['id', 'status', 'revision', 'render_revision', 'active_job_id', 'error_text'];
        var changed = items.length !== lastItems.length || items.some(function (item, index) {
          var previous = lastItems[index] || {}; return fields.some(function (key) { return String(item[key] || '') !== String(previous[key] || ''); });
        });
        if (changed) return load();
        lastItems = items.map(function (item, index) { return Object.assign({}, lastItems[index], item); });
        return lastItems;
      }
      lastItems = items;
      renderReadiness(results[1].readiness);
      renderFilters(items);
      renderResumeGrid(items);
      var pager = document.getElementById('rzResumePagination');
      if (pager) {
        pager.innerHTML = (pageOffset || results[0].has_more) ? '<button class="rz-btn" data-page="previous"' + (pageOffset ? '' : ' disabled') +
          '>上一页</button><span>第 ' + (Math.floor(pageOffset / pageSize) + 1) + ' 页 · 筛选当前页的 ' + items.length + ' 份简历</span><button class="rz-btn" data-page="next"' +
          (results[0].has_more ? '' : ' disabled') + '>下一页</button>' : '';
        pager.querySelectorAll('[data-page]').forEach(function (button) { button.onclick = function () {
          pageOffset = Math.max(0, pageOffset + (button.dataset.page === 'next' ? pageSize : -pageSize));
          pager.querySelectorAll('button').forEach(function (item) { item.disabled = true; }); load();
        }; });
      }
      var rendering = items.some(isProcessing);
      if (rendering && (!pollTimer || !pollTimer.active())) pollTimer = window.CareerTools.poll({
        load: function () { return load(true); }, interval: 8000, immediate: false,
        done: function (rows) { return !rows.some(isProcessing); },
        onError: function (error, count) { if (count === 1) RZ.toast(error.message, 'error'); }
      });
      else if (!rendering && pollTimer) { pollTimer.stop(); pollTimer = null; }
      return items;
    } catch (e) { if (quiet === true) throw e; RZ.toast(e.message, 'error'); }
  }

  function openJob(r) {
    return RZ.openJob({ title: r.title, base: '/api/resume/resumes/' + r.id, revision: r.revision, onChange: load });
  }
  async function openVersions(r) {
    var modal = RZ.openModal({ title: r.title + ' · 历史版本', wide: true });
    modal.body.textContent = '正在读取历史版本…';
    try {
      var result = await RZ.api('/api/resume/resumes/' + r.id + '/versions');
      var items = result.items || [];
      modal.body.innerHTML = '<p>每个版本保留当时的内容。恢复会创建一个新版本，已有记录仍保留。</p>' +
        (items.length ? items.map(function (version) {
          return '<div class="rz-version-row"><div><b>版本 ' + Number(version.revision) + '</b><small>' + RZ.esc(fmtTime(version.created_at)) +
            ' · ' + RZ.esc(version.status === 'ready' ? '文件已生成' : '内容已保存') + '</small></div><div>' +
            (version.status === 'ready' ? '<a class="rz-btn rz-btn--sm" target="_blank" rel="noopener" href="/api/resume/resumes/' + r.id + '/preview?revision=' + Number(version.revision) + '">查看</a>' : '') +
            (Number(version.revision) !== Number(r.revision) ? '<button class="rz-btn rz-btn--sm" data-restore="' + Number(version.revision) + '">恢复为新版本</button>' : '<span>当前版本</span>') + '</div></div>';
        }).join('') : '<p>尚无历史版本。</p>');
      modal.body.querySelectorAll('[data-restore]').forEach(function (button) {
        button.onclick = async function () {
          button.disabled = true;
          try { await RZ.api('/api/resume/resumes/' + r.id + '/versions/' + button.dataset.restore + '/restore', { method: 'POST', body: { revision: r.revision } });
            modal.close(); RZ.toast('已恢复为新版本', 'success'); load();
          } catch (error) { RZ.conflict(error, r.title, function () { modal.close(); load(); }); button.disabled = false; }
        };
      });
    } catch (error) { modal.body.textContent = error.message; }
  }
  function renderParsed(parsed) {
    parsed = parsed || {};
    var labels = { name: '姓名', phone: '电话', email: '邮箱', title: '名称', school: '学校', degree: '学历/学位层次', major: '专业', role: '角色',
      content: '内容', content_md: '介绍', contribution: '行动', achievement: '结果', description: '说明', level: '水平',
      start_date: '开始时间', end_date: '结束时间', acquired_date: '获得时间', expected_position: '目标岗位' };
    return Object.keys(parsed).filter(function (key) { return ['personal', 'education', 'experience', 'skill', 'certificate', 'self_intro'].indexOf(key) >= 0; }).map(function (section) {
      var rows = Array.isArray(parsed[section]) ? parsed[section] : [parsed[section]];
      return '<section class="rz-import-review-section"><h4><label><input type="checkbox" checked data-import-section="' + section + '"> ' + RZ.esc(sectionLabel(section)) + '</label></h4>' + rows.map(function (item, index) {
        if (!item || typeof item !== 'object') return '<p>' + RZ.esc(item) + '</p>';
        return (section === 'personal' ? '' : '<label><input type="checkbox" checked data-import-item="' + index + '" data-import-owner="' + section + '"> 导入第 ' + (index + 1) + ' 项</label>') +
          '<dl>' + Object.keys(item).filter(function (key) { return labels[key] && item[key]; }).map(function (key) {
          return '<dt>' + (section === 'personal' ? '<label><input type="checkbox" checked data-import-personal="' + key + '"> ' + RZ.esc(labels[key]) + '</label>' : RZ.esc(labels[key])) + '</dt><dd>' + RZ.esc(item[key]) + '</dd>';
        }).join('') + '</dl>';
      }).join('') + '</section>';
    }).join('') || '<p>没有可导入的资料，请核对源文件或重新导入。</p>';
  }
  async function openCandidates(r) {
    var modal = RZ.openModal({ title: r.title + ' · 待确认内容', wide: true });
    modal.body.textContent = '正在读取建议…';
    try {
      var result = await RZ.api('/api/resume/resumes/' + r.id + '/candidates');
      var candidates = (result.items || []).filter(function (item) { return ['pending', 'ready', 'review_ready'].indexOf(item.status) >= 0; });
      if (!candidates.length) { modal.body.textContent = '没有待确认的内容。'; return; }
      var candidate = candidates[0], payload = candidate.payload || {}, importing = candidate.kind === 'import';
      modal.body.innerHTML = (importing ? '' : '<h3>' + (payload.source === 'baseline' ? '基础整理建议' : 'AI 优化建议') + '</h3>' +
        (payload.source === 'baseline' ? '<p>AI 暂不可用，以下是根据已有资料整理的基础建议，请逐项核对。</p>' : '')) +
        '<p>基于内容版本 ' + Number(candidate.base_revision) + '。请核对事实、日期和数字，确认后才会应用。</p>' +
        (importing ? renderParsed(payload.parsed || payload) : '<div class="rz-candidate-compare"><section><h4>当前摘要</h4><div class="rz-md">' + RZ.md(r.optimized_summary_md || '尚无摘要') +
          '</div></section><section><h4>建议摘要</h4><div class="rz-md">' + RZ.md(payload.summary_md || '') + '</div><h4>建议能力清单</h4><div class="rz-md">' +
          RZ.md((payload.tech_stack || []).map(function (item) { return '- ' + (typeof item === 'string' ? item : (item.group || item.name || item.category || '相关技能') + (Array.isArray(item.items) ? '：' + item.items.join('、') : '')); }).join('\n')) + '</div></section></div>' +
          (Array.isArray(payload.notes) ? '<ul>' + payload.notes.map(function (note) { return '<li>' + RZ.esc(note) + '</li>'; }).join('') + '</ul>' : ''));
      var keep = document.createElement('button'); keep.className = 'rz-btn'; keep.textContent = '不采用此建议';
      keep.onclick = async function () {
        keep.disabled = true;
        try { await RZ.api('/api/resume/resumes/' + r.id + '/candidates/' + candidate.id + '/reject', { method: 'POST', body: { revision: r.revision } });
          modal.close(); RZ.toast('已保留原内容', 'success'); load();
        } catch (error) { RZ.conflict(error, payload, function () { modal.close(); load(); }); keep.disabled = false; }
      };
      var apply = document.createElement('button'); apply.className = 'rz-btn rz-btn--primary'; apply.textContent = importing ? '确认导入选中资料' : '核对无误，采用建议';
      apply.onclick = async function () {
        apply.disabled = true;
        var body = { revision: r.revision };
        if (importing) {
          body.selected_sections = Array.from(modal.body.querySelectorAll('[data-import-section]:checked')).map(function (input) { return input.dataset.importSection; });
          if (!body.selected_sections.length) { RZ.toast('请至少选择一类要导入的资料', 'error'); apply.disabled = false; return; }
          body.selected_items = {};
          modal.body.querySelectorAll('[data-import-item]').forEach(function (input) {
            var section = input.dataset.importOwner;
            if (!body.selected_items[section]) body.selected_items[section] = [];
            if (input.checked) body.selected_items[section].push(Number(input.dataset.importItem));
          });
          body.selected_personal_fields = Array.from(modal.body.querySelectorAll('[data-import-personal]:checked')).map(function (input) { return input.dataset.importPersonal; });
        }
        try { var accepted = await RZ.api('/api/resume/resumes/' + r.id + '/candidates/' + candidate.id + '/accept', { method: 'POST', body: body });
          modal.close();
          if (accepted.validation && !accepted.validation.ok) {
            RZ.toast('资料已保存为草稿，请补齐：' + (accepted.validation.missing || []).map(function (item) { return item.label; }).join('、'), 'info');
            window.location.href = '/resume/builder?edit=' + r.id;
          } else { RZ.toast('已应用确认的内容', 'success'); load(); }
        } catch (error) { RZ.conflict(error, payload, function () { modal.close(); load(); }); apply.disabled = false; }
      };
      modal.foot.appendChild(keep); modal.foot.appendChild(apply);
    } catch (error) { modal.body.textContent = error.message; }
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
