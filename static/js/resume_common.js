/* Resume console shared helpers — window.RZ.
   Plain JS (no module), loaded before each page script. Provides fetch/toast/
   modal/markdown utilities so the per-page scripts stay focused. */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
    });
  }

  // Minimal, safe markdown → HTML (paragraphs, bullets, **bold**). Matches the
  // server-side render so the preview reads the same.
  function md(text) {
    var raw = String(text == null ? '' : text).trim();
    if (!raw) return '<p style="color:#9aa">（空）</p>';
    var blocks = raw.replace(/\r\n/g, '\n').split(/\n\n+/);
    return blocks.map(function (block) {
      block = block.trim();
      if (/^[-*]\s/.test(block)) {
        var items = block.split('\n').filter(Boolean).map(function (line) {
          return '<li>' + bold(esc(line.replace(/^[-*]\s+/, ''))) + '</li>';
        }).join('');
        return '<ul>' + items + '</ul>';
      }
      return '<p>' + bold(esc(block)).replace(/\n/g, '<br>') + '</p>';
    }).join('');
  }
  function bold(s) { return s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'); }

  function toast(message, type) {
    var box = document.getElementById('toast-container');
    if (!box) { if (type === 'error') alert(message); return; }
    var el = document.createElement('div');
    el.className = 'toast toast-' + (type || 'info');
    el.textContent = message;
    el.style.cssText = 'background:' + (type === 'error' ? '#dc2626' : type === 'success' ? '#16a34a' : '#334155') +
      ';color:#fff;padding:10px 16px;border-radius:10px;margin-top:8px;box-shadow:0 10px 30px -10px rgba(0,0,0,.4);font-weight:600;font-size:.86rem;max-width:340px';
    box.appendChild(el);
    setTimeout(function () { el.style.transition = 'opacity .3s'; el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 300); }, 2600);
  }

  async function api(url, opts) {
    if (window.CareerTools) return window.CareerTools.request(url, opts);
    opts = opts || {};
    var init = { credentials: 'same-origin', headers: {} };
    if (opts.method) init.method = opts.method;
    if (opts.body !== undefined && !(opts.body instanceof FormData)) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    } else if (opts.body instanceof FormData) {
      init.body = opts.body;
    }
    var resp = await fetch(url, init);
    var data = null;
    try { data = await resp.json(); } catch (e) { data = null; }
    if (!resp.ok) {
      var msg = (data && (data.detail || data.error || data.message)) || ('请求失败 (' + resp.status + ')');
      throw new Error(typeof msg === 'string' ? msg : '请求失败');
    }
    return data;
  }

  function track(eventName, context, surface) {
    var eventId = 'evt-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
    fetch('/api/career-tools/events', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        surface: surface || 'resume', event_name: eventName,
        context: context || {}, client_event_id: eventId
      })
    }).catch(function () {});
  }

  // Modal: builds an overlay, returns { root, body, foot, close }. The caller fills body.
  function openModal(opts) {
    opts = opts || {};
    var root = document.createElement('div');
    root.className = 'rz-modal';
    root.innerHTML =
      '<div class="rz-modal__panel ' + (opts.wide ? 'rz-modal__panel--wide' : '') + '">' +
      '<div class="rz-modal__head"><h3>' + esc(opts.title || '') + '</h3>' +
      '<button type="button" class="rz-modal__close" aria-label="关闭">&times;</button></div>' +
      '<div class="rz-modal__body"></div>' +
      '<div class="rz-modal__foot"></div></div>';
    document.body.appendChild(root);
    var panel = root.querySelector('.rz-modal__panel');
    var previousFocus = document.activeElement, closed = false;
    panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true'); panel.setAttribute('aria-label', opts.title || '对话框');
    function close() {
      if (closed) return; closed = true;
      if (opts.onClose) opts.onClose();
      document.removeEventListener('keydown', onKey);
      root.classList.remove('show');
      setTimeout(function () { root.remove(); if (previousFocus && previousFocus.isConnected && !document.querySelector('.rz-modal.show')) previousFocus.focus(); }, 200);
    }
    root.addEventListener('click', function (e) { if (e.target === root) close(); });
    root.querySelector('.rz-modal__close').addEventListener('click', close);
    function onKey(e) {
      if (Array.from(document.querySelectorAll('.rz-modal')).pop() !== root) return;
      if (e.key === 'Escape') { close(); return; }
      if (e.key !== 'Tab') return;
      var nodes = Array.from(root.querySelectorAll('button, input, select, textarea, a[href], [tabindex="0"]')).filter(function (node) { return !node.disabled && node.offsetParent !== null; });
      if (!nodes.length) return;
      if (e.shiftKey && document.activeElement === nodes[0]) { e.preventDefault(); nodes[nodes.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === nodes[nodes.length - 1]) { e.preventDefault(); nodes[0].focus(); }
    }
    document.addEventListener('keydown', onKey);
    requestAnimationFrame(function () { root.classList.add('show'); root.querySelector('.rz-modal__close').focus(); });
    return { root: root, panel: panel, body: root.querySelector('.rz-modal__body'),
      foot: root.querySelector('.rz-modal__foot'), close: close };
  }

  function openJob(options) {
    var taskPoll;
    var modal = openModal({ title: options.title + ' · 处理状态', onClose: function () { if (taskPoll) taskPoll.stop(); } });
    modal.body.textContent = '正在读取处理状态…';
    taskPoll = window.CareerTools.poll({ interval: 8000,
      delay: function (result) { return Math.max(8000, Number((result.job || {}).retry_after || 0) * 1000); },
      load: async function () { return modal.root.isConnected ? api(options.base + '/job') : { closed: true }; },
      done: function (result) { return result.closed || !window.CareerTools.pending(result.job); },
      onData: function (result) {
        if (result.closed) return;
        var job = result.job;
        modal.body.innerHTML = job ? '<h4>' + esc(window.CareerTools.taskLabel(job)) + '</h4><p>' +
          esc(job.message || job.error_message || job.error_text || '已保存的资料不会因处理失败而丢失。') + '</p>' +
          (job.updated_at ? '<small>最近更新：' + esc(String(job.updated_at).replace('T', ' ').slice(0, 16)) + '</small>' : '') : '当前没有处理任务，你可以继续编辑。';
        modal.foot.innerHTML = '';
        if (!job) return;
        ['retry', 'cancel'].forEach(function (action) {
          if (!(action === 'retry' ? job.can_retry || job.retryable : job.can_cancel || job.cancellable)) return;
          var button = document.createElement('button'); button.className = 'rz-btn'; button.textContent = action === 'retry' ? '重试' : '取消任务';
          button.onclick = async function () {
            button.disabled = true;
            try { await api(options.base + '/job/' + action, { method: 'POST', body: { revision: options.revision } });
              modal.close(); if (options.onChange) options.onChange();
            } catch (error) { toast(error.message, 'error'); button.disabled = false; }
          }; modal.foot.appendChild(button);
        });
      }, onError: function (error) { modal.body.textContent = error.message; }
    });
    return modal;
  }

  var downloads = new Set();
  async function downloadFile(url, trigger) {
    if (downloads.has(url)) return;
    downloads.add(url);
    var original = trigger ? trigger.textContent : '', controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 120000);
    if (trigger) { trigger.textContent = '正在准备文件…'; trigger.setAttribute('aria-disabled', 'true'); }
    try {
      var response = await fetch(url, { credentials: 'same-origin', signal: controller.signal });
      var type = response.headers.get('Content-Type') || '';
      if (!response.ok || !/application\/(pdf|vnd\.openxmlformats-officedocument\.wordprocessingml\.document|octet-stream)/i.test(type)) {
        var body = await response.json().catch(function () { return {}; });
        var error = new Error(typeof body.detail === 'string' ? body.detail : (body.detail || {}).message || '文件暂未生成，请稍后重试。');
        error.status = response.status; error.retryAfter = response.headers.get('Retry-After'); throw error;
      }
      var blob = await response.blob(), objectUrl = URL.createObjectURL(blob);
      var disposition = response.headers.get('Content-Disposition') || '', match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      var filename = /pdf/i.test(type) ? '我的简历.pdf' : '我的简历.docx';
      if (match) { try { filename = decodeURIComponent(match[1]); } catch (_) {} }
      else { match = disposition.match(/filename="?([^";]+)"?/i); if (match) filename = match[1]; }
      var link = document.createElement('a'); link.href = objectUrl; link.download = filename.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_'); link.hidden = true;
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(function () { URL.revokeObjectURL(objectUrl); }, 60000);
      toast('文件已准备，正在下载所选版本', 'success');
    } catch (error) {
      var modal = openModal({ title: error.status === 429 ? '转换处理中，稍后重试' : '暂未完成下载' });
      var message = error.status === 429 ? '当前文件转换较多，请稍后重试。当前页面和所选版本仍保留。' :
        error.status === 401 ? '登录已失效。请重新登录后回到此页重试，当前页面和草稿仍保留。' :
        error.name === 'AbortError' ? '文件准备超时，可以稍后重试。当前页面和所选版本仍保留。' : error.message;
      modal.body.textContent = message + (error.retryAfter ? ' 建议等待 ' + error.retryAfter + ' 秒。' : '');
      if (error.status === 401) { var login = document.createElement('a'); login.className = 'rz-btn'; login.href = '/student/login'; login.target = '_blank'; login.rel = 'noopener'; login.textContent = '在新页登录'; modal.foot.appendChild(login); }
      var retry = document.createElement('button'); retry.className = 'rz-btn rz-btn--primary'; retry.textContent = '重试下载';
      var waitSeconds = error.status === 429 ? Math.max(1, Math.min(300, Number(error.retryAfter || 10))) : 0;
      if (waitSeconds) { retry.disabled = true; retry.textContent = waitSeconds + ' 秒后可重试'; setTimeout(function () { if (retry.isConnected) { retry.disabled = false; retry.textContent = '重试下载'; } }, waitSeconds * 1000); }
      retry.onclick = function () { modal.close(); downloadFile(url, trigger); }; modal.foot.appendChild(retry);
    } finally { clearTimeout(timeout); downloads.delete(url); if (trigger && trigger.isConnected) { trigger.textContent = original; trigger.removeAttribute('aria-disabled'); } }
  }
  document.addEventListener('click', function (event) {
    var link = event.target.closest('a[href]');
    if (!link) return;
    var url; try { url = new URL(link.href); } catch (_) { return; }
    if (url.origin === location.origin && /^\/api\/resume\/resumes\/\d+\/export$/.test(url.pathname)) {
      event.preventDefault(); downloadFile(url.pathname + url.search, link);
    }
  });

  function suggestionStorageKey(kind) { return 'resume-suggestion:' + (document.body.dataset.studentId || 'local') + ':' + kind; }
  function pendingSuggestion(kind) { try { return JSON.parse(sessionStorage.getItem(suggestionStorageKey(kind)) || 'null'); } catch (_) { return null; } }
  async function requestSuggestion(options) {
    var saved = options.resume ? pendingSuggestion(options.kind) : null;
    var response = saved ? { job: { id: saved.id } } : await api(options.url, { method: 'POST', body: options.body });
    if (!response.job) { if (options.onResult) options.onResult(response, function () {}, response); return; }
    var id = response.job.id || response.job.job_id;
    function remember() { try { sessionStorage.setItem(suggestionStorageKey(options.kind), JSON.stringify({ id: id })); } catch (_) {} }
    function forget() { try { sessionStorage.removeItem(suggestionStorageKey(options.kind)); } catch (_) {} }
    remember();
    var polling, modal = openModal({ title: 'AI 建议处理状态', onClose: function () { if (polling) polling.stop(); } });
    modal.body.innerHTML = '<p>建议在后台处理，你可以关闭此窗口继续编辑。返回后可查看上次建议，结果需要核对采用。</p>';
    polling = window.CareerTools.poll({ interval: 8000,
      load: function () { return api('/api/resume/suggestions/jobs/' + id); },
      delay: function (result) { return Math.max(8000, Number((result.job || {}).retry_after || 0) * 1000); },
      done: function (result) { return !!result.result || !window.CareerTools.pending(result.job); },
      onData: function (result) {
        modal.body.innerHTML = '<h4>' + esc(window.CareerTools.taskLabel(result.job || {})) + '</h4><p>' + esc((result.job || {}).error_message || '原始资料保持不变，核对建议后再采用。') + '</p>';
        modal.foot.innerHTML = '';
        if (result.result) {
          var review = document.createElement('button'); review.className = 'rz-btn rz-btn--primary'; review.textContent = '查看并核对建议';
          review.onclick = function () { modal.close(); if (options.onResult) options.onResult(result.result, forget, result); };
          modal.foot.appendChild(review);
        }
        ['retry', 'cancel'].forEach(function (action) {
          var job = result.job || {}; if (!(action === 'retry' ? job.retryable || job.can_retry : job.cancellable || job.can_cancel)) return;
          var button = document.createElement('button'); button.className = 'rz-btn'; button.textContent = action === 'retry' ? '重试建议' : '取消建议任务';
          button.onclick = async function () {
            button.disabled = true;
            try { var updated = await api('/api/resume/suggestions/jobs/' + id + '/' + action, { method: 'POST' });
              id = (updated.job || {}).id || (updated.job || {}).job_id || id; remember(); modal.close();
              if (action === 'retry') requestSuggestion(Object.assign({}, options, { resume: true })); else forget();
            } catch (error) { toast(error.message, 'error'); button.disabled = false; }
          }; modal.foot.appendChild(button);
        });
      }, onError: function (error) { modal.body.textContent = error.message; if ([401, 403, 404].indexOf(error.status) >= 0) forget(); }
    });
  }

  function confirmDialog(message, onYes) {
    var m = openModal({ title: '确认操作' });
    m.body.innerHTML = '<p style="margin:0;font-size:.94rem">' + esc(message) + '</p>';
    var cancel = document.createElement('button'); cancel.className = 'rz-btn'; cancel.textContent = '取消';
    var ok = document.createElement('button'); ok.className = 'rz-btn rz-btn--danger'; ok.textContent = '确定删除';
    cancel.onclick = m.close;
    ok.onclick = function () { m.close(); onYes(); };
    m.foot.appendChild(cancel); m.foot.appendChild(ok);
  }

  function fmtRange(a, b) {
    a = (a || '').trim(); b = (b || '').trim();
    if (a && b) return formatMonthLabel(a) + ' ~ ' + formatMonthLabel(b);
    return formatMonthLabel(a || b || '');
  }

  var DRAFT_LABELS = {
    title: '标题', target_position: '目标岗位', name: '姓名', gender: '性别', birthday: '出生日期',
    phone: '电话', email: '邮箱', address: '地址', expected_position: '期望岗位', expected_industry: '期望行业',
    expected_city: '期望城市', expected_salary: '期望薪资', political_status: '政治面貌', ethnicity: '民族',
    native_place: '籍贯', hometown: '籍贯', qq: 'QQ', wechat: '微信', id_card: '证件号码', height: '身高', weight: '体重', website: '个人网站', portfolio_url: '作品链接',
    school: '学校', college: '院系', major: '专业', degree: '学历层次', gpa: '成绩',
    start_date: '开始时间', end_date: '结束时间', role: '担任角色', organization: '单位 / 组织',
    content: '内容', content_md: '内容', description: '说明', contribution: '本人贡献', achievement: '成果',
    level: '掌握程度', acquired_date: '取得时间', expiry_date: '有效期', issuing_authority: '颁发机构',
    company_name: '单位名称', position_name: '岗位名称', job_description: '岗位要求', notes: '备注',
    applied_at: '投递时间', applied_on: '投递日期', next_followup_at: '下次跟进', next_action_at: '下一步时间', next_action: '下一步行动', note: '备注', channel: '投递渠道', contact_name: '联系人',
    contact_info: '联系方式', optimized_summary_md: '本份职业摘要', summary_md: '职业摘要'
  };
  var DRAFT_SECTIONS = { personal: '个人信息', self_intro: '自我介绍', education: '学习经历',
    experience: '实践经历', skill: '技能', certificate: '证书', skill_cert: '技能与证书', tech_stack: '岗位能力清单' };

  function draftSummary(content) {
    if (typeof content === 'string') return content;
    var lines = [];
    function add(label, value) {
      if (value != null && String(value).trim()) lines.push(label + '：' + String(value));
    }
    function fields(value, prefix) {
      if (!value || typeof value !== 'object') return;
      Object.keys(value).forEach(function (key) {
        if (DRAFT_LABELS[key]) add((prefix || '') + DRAFT_LABELS[key], value[key]);
      });
    }
    content = content || {};
    fields(content);
    if (content.template_key) add('简历模板', { classic: '经典单栏', sidebar: '双栏侧边', modern: '现代强调' }[content.template_key] || '已选择');
    if (content.status) add('当前进展', { draft: '草稿', wishlist: '想投', preparing: '准备中', applied: '已投递', written_test: '笔试', screening: '筛选中', interview: '面试中', offer: '已获录用', rejected: '未通过', withdrawn: '已撤回', closed: '已结束' }[content.status] || '已选择');
    var layout = content.layout || {};
    if (Array.isArray(layout.personal_fields)) add('已选个人信息', layout.personal_fields.map(function (key) { return DRAFT_LABELS[key] || '个人资料'; }).join('、'));
    (layout.blocks || []).forEach(function (block) {
      var count = (block.ids || []).length + (block.skill_ids || []).length + (block.cert_ids || []).length;
      add(DRAFT_SECTIONS[block.type] || '已选内容', count ? count + ' 项' : '已包含');
    });
    (content.content_overrides || []).forEach(function (item, index) {
      fields(item.fields, (DRAFT_SECTIONS[item.section] || '本份内容') + '（' + (index + 1) + '） · ');
    });
    (content.tech_stack || []).forEach(function (item) {
      if (typeof item === 'string') add('岗位能力', item);
      else if (item) add(item.group || '岗位能力', (item.items || []).join('、'));
    });
    if (Array.isArray(content.selected_sections)) add('选中导入的内容', content.selected_sections.map(function (key) { return DRAFT_SECTIONS[key] || '其他内容'; }).join('、'));
    return lines.join('\n\n') || '当前选择和填写内容仍在原页面中。可关闭本窗口继续核对，或下载完整草稿备份。';
  }

  function conflictContent(modal, localContent) {
    // Freeze a complete backup independently of subsequent form edits. No raw
    // payload is added to the DOM, and no download starts without a click.
    var backup = JSON.stringify(typeof localContent === 'string' ? { content: localContent } : (localContent || {}), null, 2);
    modal.body.innerHTML += '<label class="rz-field">当前草稿摘要<textarea class="rz-textarea" readonly rows="10"></textarea></label>';
    modal.body.querySelector('textarea').value = draftSummary(localContent);
    var download = document.createElement('button'); download.type = 'button'; download.className = 'rz-btn';
    download.textContent = '下载草稿备份';
    download.onclick = function () {
      var url = URL.createObjectURL(new Blob([backup], { type: 'application/json;charset=utf-8' }));
      var link = document.createElement('a'); link.href = url;
      link.download = '简历草稿备份-' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      toast('已生成草稿备份，请确认文件已下载后再载入最新版本。', 'success');
    };
    modal.foot.appendChild(download);
  }

  function conflict(error, localContent, reload) {
    if (error.detail && error.detail.code === 'rollout_limited') { toast(error.message, 'info'); return; }
    if ([401, 403, 404].indexOf(error.status) >= 0) {
      var unavailable = openModal({ title: '资料暂不可用', wide: true });
      unavailable.body.innerHTML = '<p>' + esc(error.message) + '。当前草稿仍保留在这个页面中。你可以复制摘要或下载完整备份后，返回自己的资料页面。</p>';
      conflictContent(unavailable, localContent);
      var returnLink = document.createElement('a'); returnLink.className = 'rz-btn'; returnLink.href = '/resume'; returnLink.textContent = '返回我的工作台'; unavailable.foot.appendChild(returnLink);
      return;
    }
    if (error.status !== 409 && error.status !== 428) { toast(error.message, 'error'); return; }
    var m = openModal({ title: '这份资料有了新版本', wide: true });
    m.body.innerHTML = '<p>另一页面或任务已更新这份资料。当前草稿仍保留在这个页面中。你可以继续编辑，或先复制摘要、下载完整备份，再载入最新版本核对。</p>';
    conflictContent(m, localContent);
    var stay = document.createElement('button'); stay.className = 'rz-btn'; stay.textContent = '继续查看当前输入'; stay.onclick = m.close;
    m.foot.appendChild(stay);
    if (reload) { var latest = document.createElement('button'); latest.className = 'rz-btn rz-btn--primary'; latest.textContent = '我已保留输入，载入最新版本';
      latest.onclick = function () { m.close(); reload(); }; m.foot.appendChild(latest); }
  }
  function compareSuggestion(original, proposed, accept) {
    var m = openModal({ title: '核对 AI 建议', wide: true });
    m.body.innerHTML = '<p>请核对姓名、经历、数字和技能是否真实，再决定是否采用。</p><div class="rz-candidate-compare"><section><h4>当前内容</h4><div class="rz-md">' + md(original) +
      '</div></section><section><h4>建议内容</h4><div class="rz-md">' + md(proposed) + '</div></section></div>';
    var keep = document.createElement('button'); keep.className = 'rz-btn'; keep.textContent = '保留当前内容'; keep.onclick = m.close;
    var use = document.createElement('button'); use.className = 'rz-btn rz-btn--primary'; use.textContent = '采用建议';
    use.onclick = function () { accept(); m.close(); }; m.foot.appendChild(keep); m.foot.appendChild(use);
  }

  var MONTH_NAMES = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月'];
  var MONTH_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"></rect><path d="M16 2v4M8 2v4M3 10h18"></path></svg>';

  function normalizeMonth(value) {
    var match = String(value || '').trim().match(/^(\d{4})-(\d{1,2})/);
    if (!match) return '';
    var month = Math.max(1, Math.min(12, parseInt(match[2], 10) || 1));
    return match[1] + '-' + String(month).padStart(2, '0');
  }

  function currentMonthValue() {
    var now = new Date();
    return now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');
  }

  function monthYear(value) {
    var normalized = normalizeMonth(value);
    if (normalized) return parseInt(normalized.slice(0, 4), 10);
    return new Date().getFullYear();
  }

  function monthValue(year, monthIndex) {
    return String(year) + '-' + String(monthIndex + 1).padStart(2, '0');
  }

  function compareMonth(a, b) {
    a = normalizeMonth(a); b = normalizeMonth(b);
    if (!a && !b) return 0;
    if (!a) return -1;
    if (!b) return 1;
    return a === b ? 0 : (a > b ? 1 : -1);
  }

  function formatMonthLabel(value) {
    value = normalizeMonth(value);
    if (!value) return '';
    return value.slice(0, 4) + '年' + value.slice(5, 7) + '月';
  }

  function monthPickerHtml(name, value, opts) {
    opts = opts || {};
    value = normalizeMonth(value);
    var placeholder = opts.placeholder || '请选择年月';
    var label = formatMonthLabel(value) || placeholder;
    return '<div class="rz-month-field" data-rz-month-picker data-year="' + monthYear(value) + '">' +
      '<input type="hidden" name="' + esc(name) + '" value="' + esc(value) + '">' +
      '<button type="button" class="rz-month-trigger" data-rz-month-open aria-expanded="false">' +
      '<span class="rz-month-trigger__label' + (value ? '' : ' is-placeholder') + '">' + esc(label) + '</span>' +
      '<span class="rz-month-trigger__icon">' + MONTH_ICON + '</span></button>' +
      '<div class="rz-month-panel" data-rz-month-panel hidden></div></div>';
  }

  function monthRangePickerHtml(startName, endName, values, opts) {
    values = values || {}; opts = opts || {};
    var start = normalizeMonth(values.start);
    var end = normalizeMonth(values.end);
    var anchor = start || end || currentMonthValue();
    return '<div class="rz-month-range" data-rz-month-range data-role="start" data-year="' + monthYear(anchor) + '">' +
      '<input type="hidden" name="' + esc(startName) + '" value="' + esc(start) + '" data-rz-range-start>' +
      '<input type="hidden" name="' + esc(endName) + '" value="' + esc(end) + '" data-rz-range-end>' +
      '<button type="button" class="rz-month-trigger" data-rz-month-open aria-expanded="false">' +
      '<span class="rz-month-trigger__label' + (start || end ? '' : ' is-placeholder') + '">' +
      esc(rangeLabel(start, end, opts.placeholder || '请选择起止年月')) + '</span>' +
      '<span class="rz-month-trigger__icon">' + MONTH_ICON + '</span></button>' +
      '<div class="rz-month-panel rz-month-panel--range" data-rz-month-panel hidden></div></div>';
  }

  function rangeLabel(start, end, placeholder) {
    if (start && end) return formatMonthLabel(start) + ' 至 ' + formatMonthLabel(end);
    if (start) return '开始 ' + formatMonthLabel(start) + '，请选择结束';
    if (end) return '结束 ' + formatMonthLabel(end);
    return placeholder;
  }

  function closeMonthPickers(except) {
    document.querySelectorAll('[data-rz-month-picker], [data-rz-month-range]').forEach(function (root) {
      if (except && root === except) return;
      root.classList.remove('is-open');
      var panel = root.querySelector('[data-rz-month-panel]');
      var trigger = root.querySelector('[data-rz-month-open]');
      if (panel) panel.hidden = true;
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
    });
  }

  function toggleMonthPanel(root, open) {
    var panel = root.querySelector('[data-rz-month-panel]');
    var trigger = root.querySelector('[data-rz-month-open]');
    if (!panel) return;
    if (open) {
      if (root.matches('[data-rz-month-picker]')) {
        var value = root.querySelector('input[type="hidden"]')?.value || '';
        root.dataset.year = String(monthYear(value || currentMonthValue()));
        renderSingleMonthPicker(root);
      } else if (root.matches('[data-rz-month-range]')) {
        var start = root.querySelector('[data-rz-range-start]')?.value || '';
        var end = root.querySelector('[data-rz-range-end]')?.value || '';
        root.dataset.year = String(monthYear(start || end || currentMonthValue()));
        renderRangeMonthPicker(root);
      }
      closeMonthPickers(root);
      panel.hidden = false;
      root.classList.add('is-open');
      if (trigger) trigger.setAttribute('aria-expanded', 'true');
    } else {
      panel.hidden = true;
      root.classList.remove('is-open');
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
    }
  }

  function setTriggerLabel(root, text, isPlaceholder) {
    var label = root.querySelector('.rz-month-trigger__label');
    if (!label) return;
    label.textContent = text;
    label.classList.toggle('is-placeholder', !!isPlaceholder);
  }

  function renderSingleMonthPicker(root) {
    var input = root.querySelector('input[type="hidden"]');
    var panel = root.querySelector('[data-rz-month-panel]');
    if (!input || !panel) return;
    var selected = normalizeMonth(input.value);
    input.value = selected;
    var year = parseInt(root.dataset.year || monthYear(selected), 10) || new Date().getFullYear();
    root.dataset.year = String(year);
    setTriggerLabel(root, formatMonthLabel(selected) || '请选择年月', !selected);
    var now = currentMonthValue();
    panel.innerHTML = monthPanelHead(year) + '<div class="rz-month-grid">' +
      MONTH_NAMES.map(function (label, index) {
        var value = monthValue(year, index);
        var cls = 'rz-month-option' + (value === selected ? ' is-selected' : '') + (value === now ? ' is-current' : '');
        return '<button type="button" class="' + cls + '" data-rz-month-value="' + value + '">' + label + '</button>';
      }).join('') + '</div><div class="rz-month-panel__actions">' +
      '<button type="button" data-rz-month-clear>清空</button>' +
      '<button type="button" data-rz-month-today>本月</button></div>';
  }

  function renderRangeMonthPicker(root) {
    var startInput = root.querySelector('[data-rz-range-start]');
    var endInput = root.querySelector('[data-rz-range-end]');
    var panel = root.querySelector('[data-rz-month-panel]');
    if (!startInput || !endInput || !panel) return;
    var start = normalizeMonth(startInput.value);
    var end = normalizeMonth(endInput.value);
    startInput.value = start; endInput.value = end;
    var year = parseInt(root.dataset.year || monthYear(start || end), 10) || new Date().getFullYear();
    root.dataset.year = String(year);
    var role = root.dataset.role === 'end' ? 'end' : 'start';
    setTriggerLabel(root, rangeLabel(start, end, '请选择起止年月'), !(start || end));
    var now = currentMonthValue();
    panel.innerHTML = '<div class="rz-month-range__roles" role="tablist" aria-label="选择时间类型">' +
      '<button type="button" data-rz-range-role="start" class="' + (role === 'start' ? 'is-active' : '') + '">开始</button>' +
      '<button type="button" data-rz-range-role="end" class="' + (role === 'end' ? 'is-active' : '') + '">结束</button></div>' +
      monthPanelHead(year) + '<div class="rz-month-grid">' +
      MONTH_NAMES.map(function (label, index) {
        var value = monthValue(year, index);
        var inRange = start && end && compareMonth(value, start) >= 0 && compareMonth(value, end) <= 0;
        var cls = 'rz-month-option' + (value === now ? ' is-current' : '') +
          (inRange ? ' is-range' : '') + (value === start ? ' is-start' : '') + (value === end ? ' is-end' : '');
        return '<button type="button" class="' + cls + '" data-rz-month-value="' + value + '">' + label + '</button>';
      }).join('') + '</div><div class="rz-month-panel__result">' +
      esc(rangeLabel(start, end, '先选开始，再选结束')) + '</div><div class="rz-month-panel__actions">' +
      '<button type="button" data-rz-month-clear>清空</button>' +
      '<button type="button" data-rz-month-today>本月</button></div>';
  }

  function monthPanelHead(year) {
    return '<div class="rz-month-panel__head">' +
      '<button type="button" data-rz-month-nav="-1" aria-label="上一年">‹</button>' +
      '<strong>' + year + '年</strong>' +
      '<button type="button" data-rz-month-nav="1" aria-label="下一年">›</button></div>';
  }

  function bindMonthGlobals() {
    if (document.__rzMonthPickerBound) return;
    document.__rzMonthPickerBound = true;
    document.addEventListener('click', function (event) {
      if (!event.target.closest('[data-rz-month-picker], [data-rz-month-range]')) closeMonthPickers();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeMonthPickers();
    });
  }

  function initMonthPickers(scope) {
    scope = scope || document;
    bindMonthGlobals();
    scope.querySelectorAll('[data-rz-month-picker]').forEach(function (root) {
      if (root.dataset.rzMonthReady) { renderSingleMonthPicker(root); return; }
      root.dataset.rzMonthReady = '1';
      renderSingleMonthPicker(root);
      root.addEventListener('click', function (event) {
        event.stopPropagation();
        var trigger = event.target.closest('[data-rz-month-open]');
        if (trigger) { toggleMonthPanel(root, !root.classList.contains('is-open')); return; }
        var nav = event.target.closest('[data-rz-month-nav]');
        if (nav) { root.dataset.year = String((parseInt(root.dataset.year, 10) || new Date().getFullYear()) + parseInt(nav.dataset.rzMonthNav, 10)); renderSingleMonthPicker(root); return; }
        var month = event.target.closest('[data-rz-month-value]');
        if (month) {
          root.querySelector('input[type="hidden"]').value = month.dataset.rzMonthValue;
          renderSingleMonthPicker(root); toggleMonthPanel(root, false); return;
        }
        if (event.target.closest('[data-rz-month-clear]')) {
          root.querySelector('input[type="hidden"]').value = '';
          renderSingleMonthPicker(root); toggleMonthPanel(root, false); return;
        }
        if (event.target.closest('[data-rz-month-today]')) {
          var today = currentMonthValue();
          root.dataset.year = today.slice(0, 4);
          root.querySelector('input[type="hidden"]').value = today;
          renderSingleMonthPicker(root); toggleMonthPanel(root, false);
        }
      });
    });
    scope.querySelectorAll('[data-rz-month-range]').forEach(function (root) {
      if (root.dataset.rzMonthReady) { renderRangeMonthPicker(root); return; }
      root.dataset.rzMonthReady = '1';
      renderRangeMonthPicker(root);
      root.addEventListener('click', function (event) {
        event.stopPropagation();
        var trigger = event.target.closest('[data-rz-month-open]');
        if (trigger) { toggleMonthPanel(root, !root.classList.contains('is-open')); return; }
        var role = event.target.closest('[data-rz-range-role]');
        if (role) { root.dataset.role = role.dataset.rzRangeRole || 'start'; renderRangeMonthPicker(root); return; }
        var nav = event.target.closest('[data-rz-month-nav]');
        if (nav) { root.dataset.year = String((parseInt(root.dataset.year, 10) || new Date().getFullYear()) + parseInt(nav.dataset.rzMonthNav, 10)); renderRangeMonthPicker(root); return; }
        var startInput = root.querySelector('[data-rz-range-start]');
        var endInput = root.querySelector('[data-rz-range-end]');
        var month = event.target.closest('[data-rz-month-value]');
        if (month) {
          var selected = month.dataset.rzMonthValue;
          if (root.dataset.role === 'end') {
            endInput.value = selected;
            if (!startInput.value || compareMonth(selected, startInput.value) < 0) startInput.value = selected;
            renderRangeMonthPicker(root);
            if (startInput.value && endInput.value) toggleMonthPanel(root, false);
            return;
          } else {
            startInput.value = selected;
            if (endInput.value && compareMonth(endInput.value, selected) < 0) endInput.value = selected;
            root.dataset.role = 'end';
          }
          renderRangeMonthPicker(root); return;
        }
        if (event.target.closest('[data-rz-month-clear]')) {
          startInput.value = ''; endInput.value = ''; root.dataset.role = 'start';
          renderRangeMonthPicker(root); return;
        }
        if (event.target.closest('[data-rz-month-today]')) {
          var today = currentMonthValue();
          root.dataset.year = today.slice(0, 4);
          if (root.dataset.role === 'end') endInput.value = today;
          else { startInput.value = today; if (endInput.value && compareMonth(endInput.value, today) < 0) endInput.value = today; root.dataset.role = 'end'; }
          renderRangeMonthPicker(root);
        }
      });
    });
  }

  function syncMonthPickers(scope) {
    scope = scope || document;
    scope.querySelectorAll('[data-rz-month-picker]').forEach(renderSingleMonthPicker);
    scope.querySelectorAll('[data-rz-month-range]').forEach(renderRangeMonthPicker);
  }

  window.RZ = { esc: esc, md: md, toast: toast, api: api, track: track, openModal: openModal,
    conflict: conflict, compareSuggestion: compareSuggestion, openJob: openJob, downloadFile: downloadFile, requestSuggestion: requestSuggestion, pendingSuggestion: pendingSuggestion,
    confirmDialog: confirmDialog, fmtRange: fmtRange, monthPickerHtml: monthPickerHtml,
    monthRangePickerHtml: monthRangePickerHtml, initMonthPickers: initMonthPickers,
    syncMonthPickers: syncMonthPickers, formatMonthLabel: formatMonthLabel,
    compareMonth: compareMonth };
})();
