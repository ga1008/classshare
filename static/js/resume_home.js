/* Result-first landing page for the student resume workbench. */
(function () {
  'use strict';
  var RZ = window.RZ;
  var importBtn = document.getElementById('rzHomeImportBtn');
  var importCard = document.getElementById('rzHomeImportCard');
  var importFile = document.getElementById('rzHomeImportFile');
  var buildBtn = document.getElementById('rzHomeBuildBtn');
  var autoCard = document.getElementById('rzHomeAutoCard');

  function buildUrl(target, careerTag) {
    var params = new URLSearchParams({ auto: '1', source: 'resume_home' });
    if (target) params.set('target', target);
    if (careerTag) params.set('career_tag', careerTag);
    return '/resume/builder?' + params.toString();
  }

  function setBuildLinks(target, careerTag) {
    var href = buildUrl(target, careerTag);
    if (buildBtn) buildBtn.href = href;
    if (autoCard) autoCard.href = href;
  }

  function renderStatus(readiness) {
    var box = document.getElementById('rzHomeStatus');
    var score = Math.max(0, Math.min(100, Number(readiness.score || 0)));
    var counts = readiness.counts || {};
    var ready = Number(counts.ready_resumes || 0);
    var next = (readiness.next_actions || [])[0];
    box.innerHTML = '<div class="rz-home-status__score" style="--rz-home-score:' + score + '%"><strong>' + score + '</strong><span>资料准备度</span></div>' +
      '<div class="rz-home-status__copy"><b>' + RZ.esc(ready ? '已有 ' + ready + ' 份可投递简历' : '先完成第一份可预览简历') + '</b>' +
      '<p>' + RZ.esc(readiness.message || '系统会按目标岗位继续给出改进建议。') + '</p>' +
      (next ? '<a href="' + RZ.esc(next.href || '#') + '">下一步：' + RZ.esc(next.label || '') + ' →</a>' : '') + '</div>';
  }

  function renderPositions(options) {
    var box = document.getElementById('rzHomePositions');
    options = (options || []).slice(0, 3);
    if (!options.length) {
      box.innerHTML = '<div class="rz-home-empty">完成职业方向测评后，这里会显示最值得优先准备的岗位。' +
        '<a href="/career-path">去看看职业方向 →</a></div>';
      return;
    }
    box.innerHTML = options.map(function (option, index) {
      var target = option.value || option.label || '';
      var href = buildUrl(target, option.tag || '');
      return '<article class="rz-home-position">' +
        '<span class="rz-home-position__rank">0' + (index + 1) + '</span>' +
        '<div><strong>' + RZ.esc(target) + '</strong><small>' + RZ.esc(option.meta || '职业推荐') + '</small></div>' +
        (option.hint ? '<p>' + RZ.esc(option.hint) + '</p>' : '') +
        '<a href="' + RZ.esc(href) + '">为这个岗位生成简历 →</a></article>';
    }).join('');
  }

  function renderRecent(items) {
    var box = document.getElementById('rzHomeRecent');
    var resume = (items || [])[0];
    if (!resume) {
      box.innerHTML = '<div class="rz-home-empty">还没有简历。导入已有文件通常是最快的开始方式。</div>';
      return;
    }
    var processing = ['rendering', 'optimizing', 'parsing'].indexOf(resume.status) >= 0;
    box.innerHTML = '<div class="rz-home-recent__card"><div><span>' +
      RZ.esc(processing ? '处理中' : resume.status === 'ready' ? '可投递' : '待检查') + '</span>' +
      '<strong>' + RZ.esc(resume.title || '我的简历') + '</strong>' +
      '<small>' + RZ.esc(resume.target_position ? '目标岗位：' + resume.target_position : '尚未设置目标岗位') + '</small></div>' +
      '<a class="rz-btn rz-btn--primary" href="/resume/list">' + (processing ? '查看进度' : '预览与导出') + '</a></div>';
  }

  function hasReusableContent(readiness) {
    var sections = ((readiness.counts || {}).sections) || {};
    return Object.keys(sections).some(function (key) { return Number(sections[key] || 0) > 0; });
  }

  async function upload(file) {
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) { RZ.toast('简历文件不能超过 20MB', 'error'); return; }
    var fd = new FormData(); fd.append('file', file);
    importBtn.disabled = true; importBtn.textContent = '上传中…';
    try {
      await RZ.api('/api/resume/import', { method: 'POST', body: fd });
      RZ.toast('已开始解析，完成后可直接预览', 'success');
      window.location.href = '/resume/list';
    } catch (e) {
      RZ.toast(e.message, 'error');
      importBtn.disabled = false; importBtn.textContent = '导入已有简历';
    }
  }

  async function load() {
    RZ.track('resume_home_viewed', { mode: 'result_first' }, 'resume');
    try {
      var results = await Promise.all([
        RZ.api('/api/resume/readiness'),
        RZ.api('/api/resume/resumes'),
        RZ.api('/api/resume/personal')
      ]);
      var readiness = results[0].readiness || {};
      var resumes = results[1].items || [];
      var personal = results[2].info || {};
      var options = results[2].position_options || [];
      var target = personal.expected_position || (options[0] && (options[0].value || options[0].label)) || '';
      var tag = options[0] && options[0].tag || '';
      setBuildLinks(target, tag);
      renderStatus(readiness);
      renderPositions(options);
      renderRecent(resumes);
      if (!hasReusableContent(readiness)) {
        buildBtn.textContent = '先补充一段经历';
        buildBtn.href = '/resume/profile/experience';
        autoCard.querySelector('small').textContent = '先添加一段项目、比赛或课程成果，再自动组合';
        autoCard.href = '/resume/profile/experience';
      }
    } catch (e) {
      document.getElementById('rzHomeStatus').innerHTML = '<div class="rz-home-empty">资料加载失败，请刷新重试。</div>';
      RZ.toast(e.message, 'error');
    }
  }

  [importBtn, importCard].forEach(function (button) {
    if (button) button.addEventListener('click', function () { importFile.click(); });
  });
  importFile.addEventListener('change', function () { upload(importFile.files && importFile.files[0]); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load);
  else load();
})();
