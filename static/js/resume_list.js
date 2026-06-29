/* 我的简历 — list cards (with rendering placeholder + poll), preview iframe,
   edit (→ builder), delete, and Word/PDF download. */
(function () {
  'use strict';
  var RZ = window.RZ;
  var box = document.getElementById('rzResumes');
  var pollTimer = null;
  var TPL_LABEL = { classic: '经典单栏', sidebar: '双栏侧边', modern: '现代强调' };

  function fmtTime(s) { return (s || '').replace('T', ' ').slice(0, 16); }

  function renderCard(r) {
    if (r.status === 'rendering') {
      return '<div class="rz-card rz-card--placeholder" data-rendering="1">' +
        '<div class="rz-card__title"><span class="rz-spin"></span> 正在渲染整合…</div>' +
        '<div class="rz-card__meta">' + RZ.esc(r.title) + '</div></div>';
    }
    var failed = r.status === 'failed';
    var tag = '<span class="rz-card__tag">' + RZ.esc(TPL_LABEL[r.template_key] || r.template_key) + '</span>';
    var actions = failed ? '<span style="color:#dc2626">渲染失败</span>' :
      '<div style="margin-top:12px;display:flex;flex-wrap:wrap;gap:6px">' +
      '<button class="rz-btn rz-btn--sm" data-act="preview">预览</button>' +
      '<button class="rz-btn rz-btn--sm" data-act="edit">编辑</button>' +
      '<a class="rz-btn rz-btn--sm" href="/api/resume/resumes/' + r.id + '/export?fmt=pdf" target="_blank">PDF</a>' +
      '<a class="rz-btn rz-btn--sm" href="/api/resume/resumes/' + r.id + '/export?fmt=docx" target="_blank">Word</a>' +
      '<button class="rz-btn rz-btn--sm rz-btn--danger" data-act="del">删除</button></div>';
    return '<div class="rz-card' + (failed ? ' rz-card--failed' : '') + '" data-id="' + r.id + '" style="cursor:default">' +
      '<div class="rz-card__title">' + RZ.esc(r.title) + '</div>' +
      '<div class="rz-card__meta">' + tag + fmtTime(r.updated_at) +
      (failed && r.error_text ? '<br><span style="color:#dc2626">' + RZ.esc(r.error_text) + '</span>' : '') + '</div>' +
      actions + '</div>';
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
    var close = document.createElement('button'); close.className = 'rz-btn rz-btn--primary'; close.textContent = '关闭'; close.onclick = m.close;
    m.foot.appendChild(pdf); m.foot.appendChild(word); m.foot.appendChild(close);
  }

  function del(r) {
    RZ.confirmDialog('确定删除简历「' + r.title + '」吗？', async function () {
      try { await RZ.api('/api/resume/resumes/' + r.id, { method: 'DELETE' }); RZ.toast('已删除', 'success'); load(); }
      catch (e) { RZ.toast(e.message, 'error'); }
    });
  }

  async function load() {
    try {
      var data = await RZ.api('/api/resume/resumes');
      var items = data.items || [];
      if (!items.length) {
        box.innerHTML = '<div class="rz-empty" style="grid-column:1/-1">' +
          '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"></path><path d="M14 2v5h5"></path></svg>' +
          '<div>还没有简历，点「新建简历」像搭积木一样开始制作吧</div></div>';
      } else {
        box.innerHTML = items.map(renderCard).join('');
        bind(items);
      }
      var rendering = items.some(function (i) { return i.status === 'rendering'; });
      if (rendering && !pollTimer) pollTimer = setInterval(load, 2500);
      else if (!rendering && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    } catch (e) { RZ.toast(e.message, 'error'); }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load);
  else load();
})();
