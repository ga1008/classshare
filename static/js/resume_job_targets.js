/* Explainable job-description analysis for the student resume workbench. */
(function () {
  'use strict';
  var RZ = window.RZ;
  var form = document.getElementById('rzJobForm');
  var position = document.getElementById('rzJobPosition');
  var company = document.getElementById('rzJobCompany');
  var description = document.getElementById('rzJobDescription');
  var count = document.getElementById('rzJobCount');
  var submit = document.getElementById('rzJobAnalyze');
  var result = document.getElementById('rzJobResult');
  var history = document.getElementById('rzJobHistory');

  function builderUrl(item) {
    var params = new URLSearchParams({
      auto: '1', source: 'job_analysis', target: item.target_position || '', job_id: String(item.id || '')
    });
    return '/resume/builder?' + params.toString();
  }

  function requirementList(items, emptyText) {
    if (!items || !items.length) return '<p class="rz-job-muted">' + RZ.esc(emptyText) + '</p>';
    return '<ul class="rz-job-requirements">' + items.map(function (item) {
      return '<li>' + RZ.esc(item) + '</li>';
    }).join('') + '</ul>';
  }

  function renderCapabilities(items) {
    if (!items || !items.length) return '<p class="rz-job-muted">岗位描述中没有识别到明确能力词，请结合原文人工确认。</p>';
    return '<div class="rz-job-capabilities">' + items.map(function (item) {
      var evidence = (item.evidence || []).map(function (entry) {
        return RZ.esc(entry.source + '：' + entry.label);
      }).join('、');
      return '<article class="rz-job-capability ' + (item.matched ? 'is-matched' : 'is-gap') + '">'
        + '<div><strong>' + RZ.esc(item.name) + '</strong><span>'
        + (item.matched ? '已有资料证据' : item.importance === 'preferred' ? '加分项待补充' : '核心要求待补充')
        + '</span></div><p>' + (item.matched ? evidence : '当前资料中没有找到可验证证据') + '</p></article>';
    }).join('') + '</div>';
  }

  function renderExperienceFeedback(items) {
    if (!items || !items.length) {
      return '<div class="rz-job-callout">还没有经历资料。先添加课程项目、比赛、社团、实习或志愿服务中的一段真实经历。</div>';
    }
    return '<div class="rz-job-feedback">' + items.map(function (item) {
      var supported = (item.supported_capabilities || []).length
        ? '<div class="rz-job-feedback__tags">可支撑：' + item.supported_capabilities.map(RZ.esc).join('、') + '</div>' : '';
      var suggestions = item.suggestions || [];
      return '<article><strong>' + RZ.esc(item.title) + '</strong>' + supported
        + (suggestions.length ? '<ul>' + suggestions.map(function (suggestion) {
          return '<li>' + RZ.esc(suggestion) + '</li>';
        }).join('') + '</ul>' : '<p>这段经历的角色、行动和结果已经较完整，下一步只需按岗位要求取舍。</p>')
        + '</article>';
    }).join('') + '</div>';
  }

  function renderResult(item) {
    var analysis = item.analysis || {};
    var score = Math.max(0, Math.min(100, Number(analysis.coverage_score || item.coverage_score || 0)));
    result.innerHTML = '<div class="rz-job-result__top">'
      + '<div class="rz-job-score" style="--rz-job-score:' + score + '%"><strong>' + score + '</strong><span>资料覆盖度</span></div>'
      + '<div><span class="rz-home-eyebrow">' + RZ.esc(item.company_name || '目标岗位') + '</span><h3>'
      + RZ.esc(item.target_position || '') + '</h3><p>' + RZ.esc(analysis.summary || '') + '</p></div></div>'
      + '<div class="rz-job-result__actions"><a class="rz-btn rz-btn--primary" href="' + RZ.esc(builderUrl(item)) + '">生成定向简历</a>'
      + '<a class="rz-btn" href="/resume/applications?job_id=' + Number(item.id || 0) + '">加入投递跟踪</a>'
      + '<a class="rz-btn" href="/resume/profile/experience">补充经历证据</a></div>'
      + '<section class="rz-job-result__section"><h4>能力证据与缺口</h4>' + renderCapabilities(analysis.capabilities) + '</section>'
      + '<div class="rz-job-result__cols"><section><h4>核心要求</h4>'
      + requirementList(analysis.must_have, '没有识别到明确的硬性要求') + '</section><section><h4>加分要求</h4>'
      + requirementList(analysis.nice_to_have, '没有单独列出加分项') + '</section></div>'
      + '<section class="rz-job-result__section"><h4>逐段经历改进</h4>'
      + renderExperienceFeedback(analysis.experience_feedback) + '</section>'
      + '<p class="rz-job-disclaimer">' + RZ.esc(analysis.disclaimer || '') + '</p>';
  }

  function renderHistory(items) {
    if (!items || !items.length) {
      history.innerHTML = '<div class="rz-home-empty">还没有岗位分析。第一次建议从你最近真正想投的岗位开始。</div>';
      return;
    }
    history.innerHTML = items.map(function (item) {
      return '<article class="rz-job-history__item" data-job-id="' + Number(item.id) + '">'
        + '<div><span>' + RZ.esc(item.company_name || '未填写公司') + '</span><strong>' + RZ.esc(item.target_position) + '</strong>'
        + '<small>当前资料覆盖度 ' + Number(item.coverage_score || 0) + '%</small></div>'
        + '<div><button type="button" data-job-open="' + Number(item.id) + '">查看</button>'
        + '<a href="' + RZ.esc(builderUrl(item)) + '">生成简历</a>'
        + '<a href="/resume/applications?job_id=' + Number(item.id) + '">跟踪</a>'
        + '<button type="button" class="is-danger" data-job-delete="' + Number(item.id) + '">删除</button></div></article>';
    }).join('');
    history.querySelectorAll('[data-job-open]').forEach(function (button) {
      button.addEventListener('click', function () { openTarget(button.dataset.jobOpen); });
    });
    history.querySelectorAll('[data-job-delete]').forEach(function (button) {
      button.addEventListener('click', function () { removeTarget(button.dataset.jobDelete); });
    });
  }

  async function loadHistory() {
    var data = await RZ.api('/api/resume/job-targets');
    renderHistory(data.items || []);
  }

  async function openTarget(id) {
    try {
      var data = await RZ.api('/api/resume/job-targets/' + encodeURIComponent(id));
      renderResult(data.item || {});
      result.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) { RZ.toast(error.message, 'error'); }
  }

  async function removeTarget(id) {
    if (!window.confirm('删除这条岗位分析？已生成的简历不会受影响。')) return;
    try {
      await RZ.api('/api/resume/job-targets/' + encodeURIComponent(id), { method: 'DELETE' });
      RZ.toast('已删除岗位分析', 'success');
      await loadHistory();
    } catch (error) { RZ.toast(error.message, 'error'); }
  }

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    submit.disabled = true; submit.textContent = '正在比对资料…';
    try {
      var data = await RZ.api('/api/resume/job-targets/analyze', {
        method: 'POST', body: {
          target_position: position.value.trim(), company_name: company.value.trim(), job_description: description.value.trim()
        }
      });
      renderResult(data.item || {});
      await loadHistory();
      RZ.toast('岗位分析已完成', 'success');
      result.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) { RZ.toast(error.message, 'error'); }
    finally { submit.disabled = false; submit.textContent = '分析岗位要求'; }
  });
  description.addEventListener('input', function () { count.textContent = String(description.value.length); });
  loadHistory().catch(function (error) { RZ.toast(error.message, 'error'); });
})();
