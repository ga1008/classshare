/* Private job-application progress board. */
(function () {
  'use strict';
  var RZ = window.RZ;
  var ITEMS = [];
  var STATUSES = [];
  var RESUMES = [];
  var TARGETS = [];
  var board = document.getElementById('rzAppBoard');
  var stats = document.getElementById('rzAppStats');
  var newButton = document.getElementById('rzAppNew');
  var GROUPS = [
    { key: 'discover', label: '想投与准备', statuses: ['wishlist', 'preparing'] },
    { key: 'applied', label: '已投递', statuses: ['applied'] },
    { key: 'assessment', label: '笔试与面试', statuses: ['written_test', 'interview'] },
    { key: 'outcome', label: '结果归档', statuses: ['offer', 'rejected', 'closed'] }
  ];

  function escAttr(value) { return RZ.esc(String(value == null ? '' : value)); }
  function statusLabel(value) {
    var match = STATUSES.filter(function (item) { return item.value === value; })[0];
    return match ? match.label : value;
  }
  function dateLabel(value) {
    if (!value) return '';
    return String(value).replace('T', ' ');
  }
  function renderStats() {
    var active = ITEMS.filter(function (item) { return ['applied', 'written_test', 'interview'].indexOf(item.status) >= 0; }).length;
    var offers = ITEMS.filter(function (item) { return item.status === 'offer'; }).length;
    var next = ITEMS.filter(function (item) { return item.next_action && item.next_action_at; }).length;
    stats.innerHTML = [
      ['全部记录', ITEMS.length], ['进行中', active], ['待办提醒', next], ['Offer', offers]
    ].map(function (entry) {
      return '<article><span>' + RZ.esc(entry[0]) + '</span><strong>' + Number(entry[1]) + '</strong></article>';
    }).join('');
  }
  function card(item) {
    var next = item.next_action
      ? '<div class="rz-app-card__next"><span>下一步</span><strong>' + RZ.esc(item.next_action) + '</strong>'
        + (item.next_action_at ? '<small>' + RZ.esc(dateLabel(item.next_action_at)) + '</small>' : '') + '</div>' : '';
    return '<article class="rz-app-card" data-application-id="' + Number(item.id) + '">'
      + '<div class="rz-app-card__status">' + RZ.esc(statusLabel(item.status)) + '</div>'
      + '<strong>' + RZ.esc(item.target_position) + '</strong><p>' + RZ.esc(item.company_name) + '</p>'
      + '<div class="rz-app-card__meta">' + (item.channel ? '<span>' + RZ.esc(item.channel) + '</span>' : '')
      + (item.applied_on ? '<span>投递 ' + RZ.esc(item.applied_on) + '</span>' : '')
      + (item.resume_title ? '<span>简历：' + RZ.esc(item.resume_title) + (item.resume_revision ? ' · 版本 ' + Number(item.resume_revision) : '') + '</span>' : '') + '</div>' + next
      + '<button type="button" data-application-edit="' + Number(item.id) + '">查看与更新</button></article>';
  }
  function renderBoard() {
    renderStats();
    board.innerHTML = GROUPS.map(function (group) {
      var items = ITEMS.filter(function (item) { return group.statuses.indexOf(item.status) >= 0; });
      return '<section class="rz-app-column"><header><div><span>' + RZ.esc(group.label) + '</span><strong>' + items.length + '</strong></div></header>'
        + '<div class="rz-app-column__body">' + (items.length ? items.map(card).join('')
          : '<div class="rz-app-column__empty">暂无记录</div>') + '</div></section>';
    }).join('');
    board.querySelectorAll('[data-application-edit]').forEach(function (button) {
      button.addEventListener('click', function () {
        var item = ITEMS.filter(function (row) { return String(row.id) === button.dataset.applicationEdit; })[0];
        if (item) openForm(item);
      });
    });
  }
  function optionHtml(value, label, selected) {
    return '<option value="' + escAttr(value) + '"' + (String(value) === String(selected || '') ? ' selected' : '') + '>' + RZ.esc(label) + '</option>';
  }
  function formHtml(item) {
    var statusOptions = STATUSES.map(function (status) { return optionHtml(status.value, status.label, item.status || 'wishlist'); }).join('');
    var targetOptions = '<option value="">不关联岗位分析</option>' + TARGETS.map(function (target) {
      return optionHtml(target.id, (target.company_name ? target.company_name + ' · ' : '') + target.target_position, item.job_target_id);
    }).join('');
    if (item.job_target_id && !TARGETS.some(function (target) { return String(target.id) === String(item.job_target_id); })) {
      targetOptions += optionHtml(item.job_target_id, (item.target_position || '原岗位分析') + '（已归档，保留记录）', item.job_target_id);
    }
    var resumeOptions = '<option value="">暂不关联简历</option>' + RESUMES.map(function (resume) {
      return optionHtml(resume.id, resume.title + (resume.target_position ? ' · ' + resume.target_position : ''), item.resume_id);
    }).join('');
    if (item.resume_id && !RESUMES.some(function (resume) { return String(resume.id) === String(item.resume_id); })) {
      resumeOptions += optionHtml(item.resume_id, (item.resume_title || '原简历') + '（保留历史记录）', item.resume_id);
    }
    return '<div class="rz-form-grid rz-app-form">'
      + '<div class="rz-field"><label>公司 / 组织<span class="req">*</span></label><input class="rz-input" name="company_name" aria-label="公司或组织" value="' + escAttr(item.company_name) + '"></div>'
      + '<div class="rz-field"><label>目标岗位<span class="req">*</span></label><input class="rz-input" name="target_position" aria-label="目标岗位" value="' + escAttr(item.target_position) + '"></div>'
      + '<div class="rz-field"><label>当前状态</label><select class="rz-select" name="status" aria-label="当前状态">' + statusOptions + '</select></div>'
      + '<div class="rz-field"><label>投递渠道</label><input class="rz-input" name="channel" aria-label="投递渠道" value="' + escAttr(item.channel) + '" placeholder="官网 / 招聘平台 / 内推"></div>'
      + '<div class="rz-field"><label>投递日期</label><input class="rz-input" type="date" name="applied_on" aria-label="投递日期" value="' + escAttr(item.applied_on) + '"></div>'
      + '<div class="rz-field"><label>下一步时间</label><input class="rz-input" type="datetime-local" name="next_action_at" aria-label="下一步时间" value="' + escAttr(item.next_action_at) + '"></div>'
      + '<div class="rz-field rz-field--full"><label>下一步行动</label><input class="rz-input" name="next_action" aria-label="下一步行动" value="' + escAttr(item.next_action) + '" placeholder="例如：周五前准备英文自我介绍"></div>'
      + '<div class="rz-field"><label>关联岗位分析</label><select class="rz-select" name="job_target_id" aria-label="关联岗位分析">' + targetOptions + '</select></div>'
      + '<div class="rz-field"><label>本次使用简历</label><select class="rz-select" name="resume_id" aria-label="本次使用简历">' + resumeOptions + '</select></div>'
      + '<div class="rz-field rz-field--full"><label>备注</label><textarea class="rz-textarea" name="note" aria-label="备注" placeholder="面试反馈、联系人、要补充的材料等">' + RZ.esc(item.note || '') + '</textarea></div></div>';
  }
  function collect(scope) {
    var data = {};
    ['company_name', 'target_position', 'status', 'channel', 'applied_on', 'next_action_at', 'next_action', 'job_target_id', 'resume_id', 'note'].forEach(function (key) {
      var input = scope.querySelector('[name="' + key + '"]'); data[key] = input ? input.value.trim() : '';
    });
    return data;
  }
  function openForm(seed) {
    var item = Object.assign({ status: 'wishlist' }, seed || {});
    var isEdit = !!item.id;
    var modal = RZ.openModal({ title: isEdit ? '更新投递进展' : '添加投递记录', wide: true });
    modal.body.innerHTML = formHtml(item);
    var remove = document.createElement('button'); remove.type = 'button'; remove.className = 'rz-btn rz-btn--danger'; remove.textContent = '删除';
    remove.onclick = function () {
      RZ.confirmDialog('删除这条投递记录吗？', async function () {
        try { await RZ.api('/api/resume/applications/' + item.id, { method: 'DELETE' }); modal.close(); await load(); RZ.toast('已删除', 'success'); }
        catch (error) { RZ.toast(error.message, 'error'); }
      });
    };
    var cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'rz-btn'; cancel.textContent = '取消'; cancel.onclick = modal.close;
    var save = document.createElement('button'); save.type = 'button'; save.className = 'rz-btn rz-btn--primary'; save.textContent = '保存';
    save.onclick = async function () {
      var payload = collect(modal.body);
      if (isEdit && item.revision != null) payload.revision = item.revision;
      if (!payload.company_name || !payload.target_position) { RZ.toast('请填写公司和目标岗位', 'error'); return; }
      save.disabled = true;
      try {
        await RZ.api(isEdit ? '/api/resume/applications/' + item.id : '/api/resume/applications', {
          method: isEdit ? 'PUT' : 'POST', body: payload
        });
        modal.close(); await load(); RZ.toast('投递进展已保存', 'success');
      } catch (error) { RZ.conflict(error, payload, function () { modal.close(); load(); }); save.disabled = false; }
    };
    if (isEdit) modal.foot.appendChild(remove);
    modal.foot.appendChild(cancel); modal.foot.appendChild(save);
  }
  async function load() {
    var data = await RZ.api('/api/resume/applications'); ITEMS = data.items || []; STATUSES = data.statuses || []; renderBoard();
  }
  async function init() {
    try {
      var data = await Promise.all([RZ.api('/api/resume/applications'), RZ.api('/api/resume/resumes'), RZ.api('/api/resume/job-targets')]);
      ITEMS = data[0].items || []; STATUSES = data[0].statuses || []; RESUMES = data[1].items || []; TARGETS = data[2].items || []; renderBoard();
      var requestedJobId = new URLSearchParams(window.location.search).get('job_id');
      if (requestedJobId) {
        var target = TARGETS.filter(function (item) { return String(item.id) === String(requestedJobId); })[0];
        if (target) openForm({ job_target_id: target.id, company_name: target.company_name, target_position: target.target_position });
      }
    } catch (error) { RZ.toast(error.message, 'error'); }
  }
  newButton.addEventListener('click', function () { openForm({}); });
  init();
})();
