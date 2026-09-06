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
      var supported = item.state === 'evidence_present' || (!item.state && item.matched);
      var evidence = (item.evidence || []).map(function (entry) {
        return RZ.esc([entry.source, entry.label].filter(Boolean).join('：'));
      }).join('、');
      return '<article class="rz-job-capability ' + (supported ? 'is-matched' : 'is-gap') + '">'
        + '<div><strong>' + RZ.esc(item.name) + '</strong><span>'
        + (supported ? '材料中出现相关证据' : '待补充资料核对')
        + '</span></div><p>' + (supported ? evidence + '（自述待核验）' : '当前资料未找到相关证据，不能据此判断你不具备这项能力。') + '</p></article>';
    }).join('') + '</div>';
  }

  function renderHardRequirements(items) {
    if (!items || !items.length) return '<p class="rz-job-muted">尚未识别到明确岗位条件，请核对职位原文。</p>';
    var labels = { met: '材料有支持（自述待核验）', failed: '当前冲突', unknown: '待确认' };
    return '<div class="rz-job-capabilities">' + items.map(function (item) {
      return '<article class="rz-job-capability ' + (item.state === 'met' ? 'is-matched' : 'is-gap') + '"><div><strong>' + RZ.esc(item.text) +
        '</strong><span>' + RZ.esc(labels[item.state] || labels.unknown) + '</span></div><p>' +
        RZ.esc((item.importance === 'preferred' ? '优先条件 · ' : '必要条件 · ') + (item.reason || '请核对原文与真实经历')) + '</p></article>';
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
    var insufficient = analysis.coverage_status === 'insufficient_extraction';
    result.innerHTML = '<div class="rz-job-result__top">'
      + (insufficient ? '<div class="rz-job-callout">识别不足，待核对</div>' : '<div class="rz-job-score" style="--rz-job-score:' + score + '%"><strong>' + score + '</strong><span>能力词资料覆盖度</span></div>')
      + '<div><span class="rz-home-eyebrow">' + RZ.esc(item.company_name || '目标岗位') + '</span><h3>'
      + RZ.esc(item.target_position || '') + '</h3><p>' + RZ.esc(analysis.summary || '') + '</p></div></div>'
      + '<div class="rz-job-result__actions"><a class="rz-btn rz-btn--primary" href="' + RZ.esc(builderUrl(item)) + '">生成定向简历</a>'
      + '<a class="rz-btn" href="/resume/applications?job_id=' + Number(item.id || 0) + '">加入投递跟踪</a>'
      + '<a class="rz-btn" href="/resume/profile/experience">补充经历证据</a></div>'
      + '<section class="rz-job-result__section"><h4>岗位条件核对</h4>' + renderHardRequirements(analysis.hard_requirements) + '</section>'
      + '<section class="rz-job-result__section"><h4>能力证据与缺口</h4><p class="rz-job-muted">能力词覆盖度仅反映现有资料中的关键词证据，不代表整个岗位的匹配度或录用概率。</p>' + renderCapabilities(analysis.capabilities) + '</section>'
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
        + '<small>' + ((item.analysis || {}).coverage_status === 'insufficient_extraction' || item.coverage_status === 'insufficient_extraction' ? '识别不足，待核对' : '能力词资料覆盖度 ' + Number(item.coverage_score || 0) + '%') + '</small></div>'
        + '<div><button type="button" data-job-open="' + Number(item.id) + '">查看</button>'
        + '<a href="' + RZ.esc(builderUrl(item)) + '">生成简历</a>'
        + '<a href="/resume/applications?job_id=' + Number(item.id) + '">跟踪</a>'
        + '<button type="button" class="is-danger" data-job-delete="' + Number(item.id) + '">归档</button></div></article>';
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
      result.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    } catch (error) {
      result.hidden = false;
      result.innerHTML = '<div class="rz-empty" role="status"><h3>这条岗位分析暂不可用</h3><p>' + RZ.esc(error.message) +
        '</p><p>可以从自己的分析记录重新选择，或继续填写新的岗位要求。当前表单内容仍保留。</p></div>';
      result.tabIndex = -1; result.focus();
    }
  }

  async function removeTarget(id) {
    if (!window.confirm('归档这条岗位分析？投递记录和已有简历仍会保留。')) return;
    try {
      await RZ.api('/api/resume/job-targets/' + encodeURIComponent(id), { method: 'DELETE' });
      RZ.toast('已归档岗位分析', 'success');
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
      result.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    } catch (error) { RZ.toast(error.message, 'error'); }
    finally { submit.disabled = false; submit.textContent = '分析岗位要求'; }
  });
  description.addEventListener('input', function () { count.textContent = String(description.value.length); });
  loadHistory().catch(function (error) { RZ.toast(error.message, 'error'); });
  var requestedTarget = new URLSearchParams(window.location.search).get('job_id');
  if (requestedTarget) openTarget(requestedTarget);
})();
