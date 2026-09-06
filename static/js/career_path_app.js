/* Career workspace: quiz, explainable directions, optional graph and background task status. */
(function () {
  'use strict';

  var root = document.getElementById('career-root');
  if (!root) return;
  var STATE_URL = root.dataset.stateUrl;
  var QUESTIONS_URL = root.dataset.questionsUrl;
  var ANSWERS_URL = root.dataset.answersUrl;
  var PROGRESS_URL = '/api/career-path/progress';

  var el = {
    boot: document.getElementById('career-boot'),
    topbar: document.getElementById('career-topbar'),
    topbarMeta: document.getElementById('career-topbar-meta'),
    redo: document.getElementById('career-redo'),
    stage: document.getElementById('career-stage'),
    canvas: document.getElementById('career-canvas'),
    banner: document.getElementById('career-banner'),
    legend: document.getElementById('career-legend'),
    detail: document.getElementById('career-detail'),
    prep: document.getElementById('career-prep'),
    intro: document.getElementById('career-intro'),
    typewriter: document.getElementById('career-typewriter'),
    quiz: document.getElementById('career-quiz')
  };

  var net = null;
  var pollTimer = null;
  var STATE = null;
  var Client = window.CareerTools;
  var activePhase = '', quizActive = false, quizVersion = '', draftRevision = 0;
  var saveQueue = Promise.resolve(), saveError = null, submitting = false, questionLocked = false;
  var panelTimer = null, modalTimer = null, modalReturnFocus = null, detailReturnFocus = null;
  var detailStageScrollTop = null;
  var viewMode = 'list', renderedNetwork = '', lastTaskMarkup = '', browseBase = false, visibleNodes = [];

  // ---------- 工具 ----------
  function show(node, on) { if (node) node.hidden = !on; }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m];
    });
  }
  function stars(n) { n = Math.max(0, Math.min(5, n | 0)); return '★★★★★'.slice(0, n) + '☆☆☆☆☆'.slice(0, 5 - n); }

  function fetchJSON(url, opts) {
    return Client.request(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
  }
  function enc(s) { return encodeURIComponent(String(s == null ? '' : s)); }
  function resumeLink(direction, state) {
    return '/resume/builder?' + new URLSearchParams({ auto: '1', source: 'career', target: direction.name || '',
      career_tag: direction.tag || '', direction_id: direction.direction_id || '', recommendation_revision: (state || {}).result_version || (state || {}).revision || '' }).toString();
  }

  var trackedPage = false;
  var trackedResult = false;
  function track(eventName, context) {
    var eventId = 'evt-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
    fetch('/api/career-tools/events', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ surface: 'career', event_name: eventName,
        context: context || {}, client_event_id: eventId })
    }).catch(function () {});
  }

  // ---------- 招聘平台直达：注册表 + 地域排序 ----------
  // 只收录口碑可靠的大平台与本地特色平台；URL 直接落到对应关键字的搜索结果页。
  var PLATFORMS = {
    boss:   { name: 'BOSS直聘', short: 'BOSS', color: '#00b8b9', bg: 'rgba(0,184,185,.16)', url: function (kw) { return 'https://www.zhipin.com/web/geek/job?query=' + enc(kw); } },
    zhaopin:{ name: '智联招聘', short: '智联', color: '#2e7bff', bg: 'rgba(46,123,255,.16)', url: function (kw) { return 'https://sou.zhaopin.com/?kw=' + enc(kw); } },
    job51:  { name: '前程无忧 51job', short: '51', color: '#ff7a18', bg: 'rgba(255,122,24,.16)', url: function (kw) { return 'https://we.51job.com/pc/search?keyword=' + enc(kw); } },
    lagou:  { name: '拉勾招聘', short: '拉勾', color: '#22c98a', bg: 'rgba(34,201,138,.16)', url: function (kw) { return 'https://www.lagou.com/wn/jobs?kd=' + enc(kw); } },
    liepin: { name: '猎聘', short: '猎聘', color: '#ff8a3d', bg: 'rgba(255,138,61,.16)', url: function (kw) { return 'https://www.liepin.com/zhaopin/?key=' + enc(kw); } },
    gxrc:   { name: '广西人才网', short: '桂才', color: '#3ec46d', bg: 'rgba(62,196,109,.18)', local: true, url: function (kw) { return 'https://s.gxrc.com/sJob?keyword=' + enc(kw); } },
    nfrc:   { name: '南方人才网', short: '南方', color: '#3c8df0', bg: 'rgba(60,141,240,.18)', local: true, url: function (kw) { return 'https://www.job168.com/2010/searchjob/searchjob.php?keyword=' + enc(kw); } }
  };
  // 意向地域 → 优先展示的本地特色平台（其余无可靠本地站则只用大平台）。
  function localPlatformsFor(loc) {
    if (loc === 'nanning') return ['gxrc'];
    if (loc === 'coastal') return ['nfrc'];
    return [];
  }
  function platformOrderFor(loc) {
    var local = localPlatformsFor(loc);
    var big = ['boss', 'zhaopin', 'job51', 'lagou', 'liepin'];
    return local.concat(big.filter(function (p) { return local.indexOf(p) < 0; }));
  }

  // ---------- 岗位搜索关键字缓存 ----------
  var KW_CACHE = Object.create(null);
  function fallbackKeywords(data) {
    var name = String(data.name || '').replace(/[（(].*?[）)]/g, '').trim();
    if (!name) return [];
    var core = name.replace(/(开发工程师|研发工程师|工程师|开发|研发|师)$/, '').trim() || name;
    var out = [];
    function add(x) { x = (x || '').trim(); if (x && out.indexOf(x) < 0 && out.length < 6) out.push(x); }
    add(name);
    if (core !== name) { add(core + '工程师'); add(core + '开发'); add(core); }
    return out;
  }

  // 平台弹窗（全屏浮层，懒创建一次）。
  var modal = null;
  function ensureModal() {
    if (modal) return modal;
    modal = document.createElement('div');
    modal.className = 'career-modal';
    modal.id = 'career-modal';
    modal.hidden = true;
    modal.addEventListener('click', function (e) { if (e.target === modal) closeModal(); });
    root.appendChild(modal);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && modal && !modal.hidden) closeModal(); });
    return modal;
  }
  function closeModal() {
    if (!modal) return;
    modal.classList.remove('show');
    clearTimeout(modalTimer);
    modalTimer = setTimeout(function () { if (modal) { modal.hidden = true; modal.innerHTML = ''; }
      if (modalReturnFocus && modalReturnFocus.isConnected) modalReturnFocus.focus(); }, 260);
  }

  // ---------- 引导：拉取状态 ----------
  function loadState() {
    var url = STATE_URL;
    if (STATE && STATE.result_version) url += (url.indexOf('?') >= 0 ? '&' : '?') + 'known_result_version=' + enc(STATE.result_version);
    return fetchJSON(url).then(function (s) {
      if (s.network_unchanged && STATE) s = Object.assign({}, STATE, s);
      STATE = s; return s;
    });
  }

  function route(s) {
    STATE = s;
    renderTasks(s);
    if (!trackedPage) {
      trackedPage = true;
      track('career_viewed', { phase: s.phase || '', session_status: s.session_status || '' });
    }
    if (s.phase === 'intro' && !browseBase && (activePhase !== 'intro' || !quizActive)) {
      draftRevision = Number(s.draft_revision == null ? s.revision || 0 : s.draft_revision);
      quizVersion = s.quiz_version || quizVersion;
      startIntro(s);
    } else if (s.phase !== 'intro' || browseBase) {
      var signature = JSON.stringify([s.network, s.personalized, s.network_level, s.stale, s.timeline, s.feedback_by_tag, s.rankings]);
      if (activePhase !== 'ready' || signature !== renderedNetwork) {
        renderedNetwork = signature; startNetwork(s);
      }
    }
    if (hasPendingTasks(s)) startPolling(); else stopPolling();
    measureHeader();
  }

  function hasPendingTasks(s) {
    return Object.keys(s.tasks || {}).some(function (key) { return Client.pending(s.tasks[key]); }) ||
      ['personalizing', 'network_generating'].indexOf(s.phase) >= 0;
  }
  function renderTasks(s, error) {
    var box = document.getElementById('career-task-status');
    if (!box) return;
    var h = error ? '<p role="alert">' + esc(error.message) +
      (error.detail && error.detail.code === 'rollout_limited' ? '' : ' <button type="button" data-state-refresh>重试连接</button>') + '</p>' : '';
    if (s.ai_availability && !s.ai_availability.allowed) h += '<p role="status">' + esc(s.ai_availability.message) + '</p>';
    Object.keys(s.tasks || {}).forEach(function (target) {
      var task = s.tasks[target];
      if (task && task.status === 'rollout_limited') return;
      if (!task || (!Client.pending(task) && !task.can_retry && !task.can_cancel && !task.error_code)) return;
      h += '<div class="career-task-row"><div><b>' + (target === 'network' ? '专业方向' : '个人推荐') + ' · ' + esc(Client.taskLabel(task)) +
        '</b><span>' + esc(task.message || '你可以继续测评、浏览方向和编辑简历，结果完成后会更新。') + '</span></div><div>' +
        (task.can_retry ? '<button type="button" data-task-action="retry" data-target="' + esc(target) + '">' + (task.status === 'not_requested' ? '生成详细建议' : '重试') + '</button>' : '') +
        (task.can_cancel ? '<button type="button" data-task-action="cancel" data-target="' + esc(target) + '">取消生成</button>' : '') + '</div></div>';
    });
    if (s.stale) h += '<p>资料已更新，当前结果供你参考。可重新生成推荐以纳入最新资料。</p>';
    if (s.needs_refresh) h += '<p>你在简历资料中的修改可以纳入推荐。<button type="button" data-state-initialize>刷新资料与推荐</button></p>';
    if (s.session_status === 'failed' && !h) h = '<p role="status">个人推荐暂未完成，当前显示基础方向。你的测评资料已保留。</p>';
    if (h !== lastTaskMarkup) { box.innerHTML = h; lastTaskMarkup = h; }
    show(box, !!h); root.classList.toggle('has-task-status', !!h);
    root.style.setProperty('--career-task-height', (h ? box.offsetHeight : 0) + 'px');
  }
  document.getElementById('career-task-status').addEventListener('click', async function (event) {
    var button = event.target.closest('button'); if (!button) return;
    button.disabled = true;
    try {
      if (button.hasAttribute('data-state-refresh')) route(await loadState());
      else if (button.hasAttribute('data-state-initialize')) route(await fetchJSON('/api/career-path/initialize', { method: 'POST' }));
      else {
        var target = button.dataset.target, task = (STATE.tasks || {})[target] || {};
        route(await fetchJSON('/api/career-path/' + button.dataset.taskAction,
          { method: 'POST', body: { target: target, job_id: task.id, revision: STATE.revision } }));
      }
    } catch (error) { renderTasks(STATE || {}, error); }
    finally { button.disabled = false; }
  });

  if (window.ResizeObserver) new ResizeObserver(function () {
    var box = document.getElementById('career-task-status');
    root.style.setProperty('--career-task-height', (box.hidden ? 0 : box.offsetHeight) + 'px');
  }).observe(document.getElementById('career-task-status'));
  function measureHeader() { root.style.setProperty('--career-header-height', Math.ceil(el.topbar.getBoundingClientRect().bottom - root.getBoundingClientRect().top + 12) + 'px'); }
  if (window.ResizeObserver) new ResizeObserver(measureHeader).observe(el.topbar);
  else window.addEventListener('resize', measureHeader);

  // ---------- 测评入口 ----------
  function startIntro(s) {
    activePhase = 'intro'; quizActive = true;
    show(el.boot, false); show(el.stage, false); show(el.topbar, true);
    renderTopbar(s);
    show(el.intro, true); show(el.quiz, false);
    el.typewriter.style.display = '';

    var draft = (s && s.draft) || [];
    if (draft.length || s.quiz_mode === 'full') { resumeQuiz(draft); return; }

    showQuizModeChoice();
  }

  // ---------- 性格测试 ----------
  var QUESTIONS = [];
  var ANSWERS = [];
  var qIndex = 0;
  var quizMode = 'quick';

  function loadQuestions(mode) {
    quizMode = mode === 'full' ? 'full' : 'quick';
    var joiner = QUESTIONS_URL.indexOf('?') >= 0 ? '&' : '?';
    return fetchJSON(QUESTIONS_URL + joiner + 'mode=' + quizMode).then(function (r) {
      QUESTIONS = r.questions || [];
      quizMode = r.mode === 'full' ? 'full' : 'quick';
      quizVersion = r.quiz_version || r.version || quizVersion;
      return r;
    });
  }

  function showQuizModeChoice() {
    el.typewriter.style.display = 'none';
    show(el.quiz, true);
    el.quiz.innerHTML = '<div class="career-quiz-mode">'
      + '<div class="career-quiz__q">先用多长时间认识你的职业偏好？</div>'
      + '<p>两种方式都会生成职业方向；快速测评更适合第一次使用，之后随时可以深入探索。</p>'
      + '<div class="career-quiz-mode__grid">'
      + '<button type="button" data-quiz-mode="quick"><b>快速测评</b><span>7 题 · 约 1 分钟</span><em>推荐第一次使用</em></button>'
      + '<button type="button" data-quiz-mode="full"><b>深度探索</b><span>11 题 · 约 3 分钟</span><em>包含工作环境与长期规划</em></button>'
      + '</div><div class="career-quiz__actions"><button type="button" class="career-btn career-btn--ghost" id="career-browse-base">先浏览基础方向</button><a class="career-btn career-btn--ghost" href="/resume">先整理简历资料</a></div></div>';
    document.getElementById('career-browse-base').onclick = function () { browseBase = true; route(STATE); };
    var buttons = el.quiz.querySelectorAll('[data-quiz-mode]');
    buttons.forEach(function (button) {
      button.addEventListener('click', function () { beginQuiz(button.dataset.quizMode); });
    });
  }

  function beginQuiz(mode) {
    var selectedMode = mode === 'full' ? 'full' : 'quick';
    track('career_quiz_started', { mode: selectedMode });
    el.quiz.querySelectorAll('button').forEach(function (button) { button.disabled = true; });
    loadQuestions(selectedMode).then(function () {
      ANSWERS = [];
      qIndex = 0;
      saveProgress();
      el.typewriter.style.display = 'none';
      show(el.quiz, true);
      renderQuestion();
    }).catch(function (error) {
      showQuizModeChoice();
      el.typewriter.style.display = ''; el.typewriter.textContent = error.message + ' 请重新选择测评方式。';
    });
  }

  function resumeQuiz(draft) {
    loadQuestions((STATE && STATE.quiz_mode) || 'quick').then(function () {
      ANSWERS = (draft || []).slice();
      var answered = Object.create(null);
      ANSWERS.forEach(function (a) { answered[a.question_id] = true; });
      var firstUnanswered = -1;
      for (var i = 0; i < QUESTIONS.length; i++) { if (!answered[QUESTIONS[i].id]) { firstUnanswered = i; break; } }
      qIndex = firstUnanswered < 0 ? QUESTIONS.length : firstUnanswered;
      el.typewriter.style.display = 'none'; show(el.quiz, true);
      if (qIndex >= QUESTIONS.length) { renderSubmitReview(); } else { renderQuestion(); }
    }).catch(function () {
      el.typewriter.textContent = '题目加载失败，请刷新重试。';
    });
  }

  function setAnswer(qid, value) {
    var found = ANSWERS.find(function (a) { return a.question_id === qid; });
    if (found) found.value = value; else ANSWERS.push({ question_id: qid, value: value });
  }

  function saveProgress() {
    var snapshot = JSON.parse(JSON.stringify(ANSWERS));
    saveQueue = saveQueue.then(async function () {
      if (saveError) throw saveError;
      var result = await fetchJSON(PROGRESS_URL, { method: 'POST', body: {
        answers: snapshot, mode: quizMode, quiz_version: quizVersion, revision: draftRevision } });
      draftRevision = Number(result.draft_revision == null ? result.revision : result.draft_revision);
      saveError = null;
    }).catch(function (error) {
      saveError = error;
      var note = document.getElementById('career-quiz-save');
      if (note) note.textContent = error.status === 409 ? '另一页面更新了测评，请先处理版本冲突。当前作答仍保留。' : '暂未保存到服务器，当前作答仍保留。';
    });
    return saveQueue;
  }

  function advance() {
    saveProgress();
    qIndex++;
    if (qIndex >= QUESTIONS.length) { renderSubmitReview(); return; }
    renderQuestion();
  }

  function renderQuestion() {
    questionLocked = false;
    var q = QUESTIONS[qIndex];
    if (!q) { submitAnswers(); return; }
    var pct = Math.round((qIndex / QUESTIONS.length) * 100);
    var h = '';
    h += '<div class="career-quiz__progress"><div class="career-quiz__bar"><i style="width:' + pct + '%"></i></div>'
      + '<span class="career-quiz__count">' + (qIndex + 1) + ' / ' + QUESTIONS.length
      + ' · ' + (quizMode === 'full' ? '深度探索' : '快速测评') + '</span></div>';
    h += '<div class="career-quiz__q">' + esc(q.title) + '</div>';

    if (q.kind === 'single' || q.kind === 'multi') {
      h += '<div class="career-quiz__opts" id="career-opts">';
      (q.options || []).forEach(function (o, i) {
        h += '<button type="button" class="career-opt" data-value="' + esc(o.value) + '" style="animation-delay:' + (i * 0.07) + 's">'
          + (q.kind === 'multi' ? '<span class="career-opt__check"></span>' : '') + esc(o.label) + '</button>';
      });
      h += '</div>';
      if (q.kind === 'multi') {
        h += '<div class="career-quiz__actions"><button type="button" class="career-btn" id="career-confirm" disabled>确认</button></div>';
      }
    } else if (q.kind === 'scale') {
      var sc = q.scale || { min: 1, max: 5 };
      h += '<div class="career-scale" id="career-opts">';
      for (var v = sc.min; v <= sc.max; v++) {
        h += '<button type="button" data-value="' + v + '" style="animation-delay:' + ((v - sc.min) * 0.07) + 's">' + v + '</button>';
      }
      h += '</div><div class="career-scale-label"><span>' + esc(sc.min_label || '') + '</span><span>' + esc(sc.max_label || '') + '</span></div>';
    } else if (q.kind === 'text') {
      h += '<textarea class="career-quiz__text" id="career-text" maxlength="' + (q.max_length || 200) + '" placeholder="' + esc(q.placeholder || '') + '"></textarea>';
      h += '<div class="career-quiz__actions">'
        + (q.optional ? '<button type="button" class="career-btn career-btn--ghost" id="career-skip">跳过</button>' : '')
        + '<button type="button" class="career-btn" id="career-confirm">完成</button></div>';
    }
    h += '<div class="career-quiz__actions"><button type="button" class="career-btn career-btn--ghost" id="career-previous"' + (!qIndex ? ' disabled' : '') + '>上一题</button><span id="career-quiz-save" role="status">作答会自动保存</span></div>';
    el.quiz.innerHTML = h;
    document.getElementById('career-previous').onclick = function () { if (questionLocked || !qIndex) return; qIndex--; renderQuestion(); };
    wireQuestion(q);
  }

  function wireQuestion(q) {
    function choose(value) {
      if (questionLocked) return; questionLocked = true;
      setAnswer(q.id, value);
      opts.forEach(function (button) { button.disabled = true; });
      setTimeout(advance, 180);
    }
    var opts = el.quiz.querySelectorAll('.career-opt, .career-scale button');
    if (q.kind === 'single') {
      opts.forEach(function (b) {
        b.addEventListener('click', function () { choose(b.dataset.value); });
      });
    } else if (q.kind === 'scale') {
      opts.forEach(function (b) {
        b.addEventListener('click', function () { choose(parseInt(b.dataset.value, 10)); });
      });
    } else if (q.kind === 'multi') {
      var previous = ANSWERS.find(function (answer) { return answer.question_id === q.id; });
      var selected = previous && Array.isArray(previous.value) ? previous.value.slice() : [];
      var confirm = document.getElementById('career-confirm');
      var max = q.max_select || 99;
      opts.forEach(function (b) {
        b.classList.toggle('selected', selected.indexOf(b.dataset.value) >= 0);
        b.addEventListener('click', function () {
          var v = b.dataset.value, i = selected.indexOf(v);
          if (i >= 0) { selected.splice(i, 1); b.classList.remove('selected'); }
          else { if (selected.length >= max) return; selected.push(v); b.classList.add('selected'); }
          confirm.disabled = selected.length === 0;
        });
      });
      confirm.disabled = !selected.length;
      confirm.addEventListener('click', function () { if (!selected.length || questionLocked) return; questionLocked = true; setAnswer(q.id, selected.slice()); advance(); });
    } else if (q.kind === 'text') {
      var ta = document.getElementById('career-text');
      var confirm2 = document.getElementById('career-confirm');
      var skip = document.getElementById('career-skip');
      var old = ANSWERS.find(function (answer) { return answer.question_id === q.id; });
      ta.value = old ? old.value : '';
      confirm2.addEventListener('click', function () { setAnswer(q.id, (ta.value || '').trim()); advance(); });
      if (skip) skip.addEventListener('click', function () { setAnswer(q.id, ''); advance(); });
    }
  }

  function submitAnswers() {
    if (submitting) return; submitting = true;
    el.quiz.innerHTML = '<div class="career-quiz__q" style="text-align:center">正在提交你的作答…</div>';
    saveQueue.then(function () {
      if (saveError) throw saveError;
      return fetchJSON(ANSWERS_URL, { method: 'POST', body: { answers: ANSWERS,
        mode: quizMode, quiz_version: quizVersion, revision: draftRevision } });
    })
      .then(function () { return loadState(); })
      .then(function (s) { quizActive = false; route(s); })
      .catch(function (error) { renderSubmitReview(error); })
      .finally(function () { submitting = false; });
  }
  function renderSubmitReview(error) {
    el.quiz.innerHTML = '<div class="career-quiz__q">' + (error ? esc(error.message) : '测评已完成，准备生成你的推荐') + '</div>' +
      '<p>你的选择可以帮助我们解释推荐方向。你也可以回看修改。</p><div class="career-quiz__actions">' +
      '<button type="button" class="career-btn career-btn--ghost" id="career-review-back">回看作答</button>' +
      '<button type="button" class="career-btn" id="career-submit-final">' + (error && error.status === 409 ? '载入另一页面的版本' : '提交并查看方向') + '</button></div>';
    document.getElementById('career-review-back').onclick = function () { qIndex = Math.max(0, QUESTIONS.length - 1); renderQuestion(); };
    document.getElementById('career-submit-final').onclick = async function () {
      if (error && error.status === 409) {
        var latest = await loadState(); saveError = null; quizActive = false; route(latest); return;
      }
      if (saveError) { saveError = null; await saveProgress(); }
      submitAnswers();
    };
  }

  // ---------- 任务状态：有界轮询，始终保留可操作页面 ----------
  function startPolling() {
    if (pollTimer && pollTimer.active()) return;
    pollTimer = Client.poll({ load: loadState, onData: route, interval: 8000, immediate: false,
      done: function (s) { return !hasPendingTasks(s); },
      onError: function (error) { renderTasks(STATE || {}, error); } });
  }
  function stopPolling() { if (pollTimer) { pollTimer.stop(); pollTimer = null; } }

  // ---------- 渲染时间轴网络 ----------
  function startNetwork(s) {
    activePhase = 'ready'; quizActive = false;
    show(el.boot, false); show(el.intro, false);
    show(el.topbar, true); show(el.stage, true);
    renderTopbar(s);
    renderBanner(s);
    renderLegend(s);
    var network = s.network || { cats: [], nodes: [], links: [] };
    if (net) net.destroy();
    net = new window.CareerNetwork(el.canvas, {
      originLabel: (s.timeline && s.timeline.graduation_year ? ('🎓 ' + s.timeline.graduation_year + ' 毕业') : '🎓 毕业'),
      timeline: s.timeline || {},
      tipEl: document.getElementById('career-tip'),
      onSelect: function (node, stage) { openDetail(node, stage, STATE || s); },
      onClear: function () { closePanels(); }
    });
    renderDirections(s); setView(viewMode);
    wireTopPaths(s);
    if (!trackedResult && s.network_level === 'personalized') {
      trackedResult = true;
      track('career_result_viewed', {
        phase: s.phase || 'ready', session_status: s.session_status || '',
        result_count: (network.nodes || []).length
      });
    }
    el.redo.hidden = false;
    el.redo.textContent = s.phase === 'intro' ? '开始测评' : '重新测试';
  }

  function renderTopbar(s) {
    var tl = s.timeline || {};
    var parts = [];
    parts.push('<b>' + esc((s.student && s.student.name) || '') + '</b>');
    if (s.major && s.major.name) parts.push('<span class="pill">' + esc(s.major.name) + '</span>');
    if (s.student && s.student.class_name) parts.push('<span class="pill">' + esc(s.student.class_name) + '</span>');
    if (tl.graduation_date_label) {
      var left = (tl.years_to_graduation != null && tl.years_to_graduation > 0)
        ? ('还有约 ' + tl.years_to_graduation + ' 年毕业') : '即将毕业';
      parts.push('<span class="pill">预计 ' + esc(tl.graduation_date_label) + ' 毕业 · ' + left + '</span>');
    }
    el.topbarMeta.innerHTML = parts.join('');
  }

  function renderBanner(s) {
    var p = s.personalized || {};
    var paths = topPathsFor(s);
    if (!p.greeting && !p.summary && !p.region_note && !paths.length) { show(el.banner, false); return; }
    var h = '<div class="career-result-label">' + (s.network_level === 'personalized' ? '根据你的资料推荐 · 详细建议' :
      s.recommendation_source === 'baseline' && s.session_status !== 'intro' && s.session_status !== 'testing' ? (s.ai_availability && !s.ai_availability.allowed ? '根据你的测评与资料推荐' : '根据你的测评与资料推荐 · 可继续生成详细建议') : '专业基础方向 · 完成测评后可获得个人推荐') + '</div>';
    if (p.greeting) h += '<h2>' + esc(p.greeting) + '</h2>';
    if (p.summary) h += '<p>' + esc(p.summary) + '</p>';
    if (p.region_note) h += '<p class="career-banner__region">📍 ' + esc(p.region_note) + '</p>';
    if (paths.length) {
      h += '<div class="career-top-paths"><div class="career-top-paths__label">最值得先看的方向</div><div class="career-top-paths__grid">';
      paths.forEach(function (path, index) {
        var resumeUrl = resumeLink(path, s);
        h += '<article class="career-top-path"><span>0' + (index + 1) + '</span><div><b>' + esc(path.name) + '</b>' +
          (path.why ? '<small>' + esc(path.why) + '</small>' : '') + '</div><footer>' +
          '<button type="button" data-career-path-tag="' + esc(path.tag) + '">查看方向</button>' +
          '<a href="' + resumeUrl + '">生成简历 →</a></footer></article>';
      });
      h += '</div></div>';
    }
    el.banner.innerHTML = h; show(el.banner, true);
  }

  function topPathsFor(s) {
    var network = s.network || {};
    var nodes = network.nodes || [];
    var byTag = Object.create(null);
    nodes.forEach(function (node) { if (node && node.tag) byTag[node.tag] = node; });
    var paths = ((s.personalized || {}).top_paths || []).map(function (path) {
      var node = byTag[path.tag] || {};
      return { tag: path.tag || node.tag || '', direction_id: node.direction_id || '', name: path.name || node.name || '', why: path.why || node.tip || node.reason || '' };
    }).filter(function (path) { return path.tag && path.name && byTag[path.tag]; });
    if (!paths.length) {
      paths = nodes.slice().sort(function (a, b) {
        return Number(b.rec || 0) - Number(a.rec || 0);
      }).slice(0, 3).map(function (node) {
        return { tag: node.tag || '', direction_id: node.direction_id || '', name: node.name || '', why: node.tip || node.reason || node.desc || '' };
      });
    }
    return paths.slice(0, 3);
  }

  function wireTopPaths(s) {
    el.banner.querySelectorAll('[data-career-path-tag]').forEach(function (button) {
      button.addEventListener('click', function () {
        var tag = button.dataset.careerPathTag || '';
        var node = ((s.network || {}).nodes || []).find(function (item) { return item.tag === tag; });
        if (viewMode === 'network' && net) { if (!net.selectDirection(tag, true) && node) openDetail(node, null, s); }
        else if (node) openDetail(node, null, s);
      });
    });
  }

  function setView(mode) {
    viewMode = mode === 'network' ? 'network' : 'list';
    root.classList.toggle('is-list-view', viewMode === 'list');
    document.querySelectorAll('[data-career-view]').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.dataset.careerView === viewMode));
    });
    show(document.getElementById('career-directions'), viewMode === 'list');
    show(document.getElementById('career-scroll'), viewMode === 'network');
    show(el.legend, viewMode === 'network');
    if (viewMode === 'network' && net && STATE) {
      net.setData({ cats: (STATE.network || {}).cats || [], nodes: visibleNodes, links: (STATE.network || {}).links || [] }, STATE.personalized || {});
      net.fitAll(false);
    }
  }
  function renderDirections(s) {
    var box = document.getElementById('career-directions');
    var query = (document.getElementById('career-search').value || '').trim().toLowerCase();
    var filter = document.getElementById('career-direction-filter').value, feedback = s.feedback_by_tag || {};
    var ranks = Object.create(null);
    (s.rankings || []).forEach(function (rank) { ranks[rank.tag] = rank; });
    var nodes = ((s.network || {}).nodes || []).slice().sort(function (a, b) { return ((ranks[b.tag] || {}).score || b.rec || 0) - ((ranks[a.tag] || {}).score || a.rec || 0); });
    var oldFeedback = (s.unmapped_feedback || []).filter(function (item) { return filter !== 'all' && item.action === filter; });
    var historyNote = oldFeedback.length ? '<div class="career-history-note"><b>此前记录的方向</b><p>' + oldFeedback.map(function (item) { return esc(item.name); }).join('、') +
      '</p><p>专业模板更新后暂未对应到新方向，这些记录已保留。可以在全部方向中重新选择。</p></div>' : '';
    nodes = nodes.filter(function (node) {
      if (filter === 'all' && feedback[node.tag] === 'dismissed') return false;
      if (filter !== 'all' && feedback[node.tag] !== filter) return false;
      return !query || [node.name, node.desc, node.reason, (node.know || []).join(' ')].join(' ').toLowerCase().indexOf(query) >= 0;
    });
    visibleNodes = nodes;
    if (!nodes.length) {
      box.innerHTML = historyNote + '<div class="career-directions-empty"><h2>' + (query ? '没有匹配的方向' : '从你的目标开始') + '</h2><p>' +
        (query ? '试试其他名称或技能关键词。' : filter !== 'all' ? '这里还没有方向，切换“全部方向”继续探索。' : '专业方向正在准备。你可以先完善个人资料、分析目标岗位或开始编辑简历。') +
        '</p><a href="/resume">打开简历工作台 →</a></div>'; return;
    }
    box.innerHTML = historyNote + nodes.map(function (node) {
      var ranking = ranks[node.tag] || {};
      var skills = (node.know || node.pre || []).slice(0, 4);
      var stage = (node.tl || [])[0] || [];
      return '<article class="career-direction"><div class="career-direction__heading"><span>' + esc(catOf(s, node.cat).name || node.cat) +
        '</span><span>' + esc(ranking.horizon === 'validate_now' ? '可以先验证' : ranking.horizon === 'long_term' ? '长期准备' : ranking.horizon === 'prepare' ? '逐步积累' : stars(node.rec)) + '</span></div><h2>' + esc(node.name) + '</h2><p>' + esc(ranking.why || node.tip || node.reason || node.desc || '') +
        '</p>' + (skills.length ? '<div class="career-direction__skills">' + skills.map(function (skill) { return '<span>' + esc(skill) + '</span>'; }).join('') + '</div>' : '') +
        (ranking.evidence ? '<small>关联到 ' + ranking.evidence.length + ' 项自述资料 · ' + (ranking.gaps || []).length + ' 项准备要求待补充证据</small>' : '') +
        (stage[1] ? '<small>起步岗位：' + esc(stage[1]) + '</small>' : '') +
        '<footer><button type="button" data-direction="' + esc(node.tag) + '">查看路径与准备清单</button>' +
        '<a href="' + esc(resumeLink(node, s)) + '">围绕此方向写简历 →</a>' +
        '<button type="button" data-feedback="' + (feedback[node.tag] === 'saved' ? 'restore' : 'favorite') + '" data-tag="' + esc(node.tag) + '">' + (feedback[node.tag] === 'saved' ? '取消收藏' : '收藏') + '</button>' +
        '<button type="button" data-feedback="' + (feedback[node.tag] === 'dismissed' ? 'restore' : 'hide') + '" data-tag="' + esc(node.tag) + '">' + (feedback[node.tag] === 'dismissed' ? '重新考虑' : '暂不考虑') + '</button></footer></article>';
    }).join('');
    box.querySelectorAll('[data-direction]').forEach(function (button) {
      button.onclick = function () {
        var node = nodes.find(function (item) { return item.tag === button.dataset.direction; });
        if (node) openDetail(node, null, STATE || s);
      };
    });
    box.querySelectorAll('[data-feedback]').forEach(function (button) {
      button.onclick = async function () {
        button.disabled = true;
        try { await fetchJSON('/api/career-path/feedback', { method: 'POST', body: { career_tag: button.dataset.tag,
          action: button.dataset.feedback, revision: STATE.revision } }); route(await loadState()); }
        catch (error) { renderTasks(STATE || {}, error); button.disabled = false; }
      };
    });
  }
  document.querySelectorAll('[data-career-view]').forEach(function (button) {
    button.onclick = function () { closePanels(); setView(button.dataset.careerView); };
  });
  document.getElementById('career-search').addEventListener('input', function () {
    if (STATE) { setView('list'); renderDirections(STATE); }
  });
  document.getElementById('career-direction-filter').addEventListener('change', function () { if (STATE) { setView('list'); renderDirections(STATE); } });
  document.getElementById('career-preferences').addEventListener('click', function () {
    openPreferences();
  });
  document.getElementById('career-job-postings').addEventListener('click', openJobPostings);
  function openJobPostings() {
    ensureModal(); clearTimeout(modalTimer); modalReturnFocus = document.activeElement;
    modal.innerHTML = '<div class="career-modal__panel" role="dialog" aria-modal="true" aria-label="真实在招职位"><header class="career-modal__head"><h3>真实在招职位</h3><button class="career-modal__close" aria-label="关闭">✕</button></header>' +
      '<div class="career-modal__body"><p>仅显示带来源、未过期的职位记录。职业方向建议不代表正在招聘；应聘前请打开来源核对最新状态。</p>' +
      '<form class="career-posting-filters career-preferences-form"><label>岗位关键词<input name="keyword" maxlength="80" placeholder="例如：实习、运营"></label>' +
      '<label>城市<input name="city" maxlength="50" placeholder="默认使用职业偏好中的城市"></label><label>条件核对<select name="qualification"><option value="all">所有有效职位</option><option value="no_known_gaps">没有已知条件冲突</option><option value="confirmed">各项条件有资料支持</option></select></label><button class="career-btn" type="submit">筛选职位</button></form>' +
      '<div id="career-postings-results" aria-live="polite"></div><nav id="career-postings-pages" aria-label="职位分页"></nav>' +
      '<p>有自己找到的岗位描述？<a href="/resume/job-targets">导入个人岗位描述并分析</a></p></div></div>';
    modal.hidden = false; modal.classList.add('show'); modal.querySelector('.career-modal__close').onclick = closeModal;
    modal.querySelector('input').focus();
    var form = modal.querySelector('form'), resultBox = modal.querySelector('#career-postings-results'), pager = modal.querySelector('#career-postings-pages');
    var page = 1, requestId = 0;
    async function refresh() {
      var id = ++requestId; form.querySelector('button').disabled = true; resultBox.textContent = '正在读取职位来源…'; pager.innerHTML = '';
      try {
        var query = new URLSearchParams({ page: page, page_size: 20, keyword: form.elements.keyword.value.trim(), city: form.elements.city.value.trim(), qualification: form.elements.qualification.value });
        var result = await fetchJSON('/api/career-path/job-postings?' + query.toString());
        if (id !== requestId || !resultBox.isConnected) return;
        var items = result.items || [];
        resultBox.innerHTML = items.length ? items.map(function (item) {
          var url = ''; try { var parsed = new URL(item.source_url); if (['http:', 'https:'].indexOf(parsed.protocol) >= 0) url = parsed.href; } catch (_) {}
          var requirements = (item.match || {}).hard_requirements || [];
          return '<article class="career-posting"><h4>' + esc(item.title) + '</h4><p>' + esc([item.company, item.city].filter(Boolean).join(' · ')) + '</p>' +
            '<small>来源：' + esc(item.source || '职位来源') + ' · 核对时间：' + esc(String(item.checked_at || '').replace('T', ' ').slice(0, 16) || '待确认') +
            (item.expires_at ? ' · 有效至：' + esc(String(item.expires_at).slice(0, 10)) : '') + '</small>' +
            ((item.match || {}).summary ? '<p>' + esc(item.match.summary) + '</p>' : '') +
            (requirements.length ? '<ul>' + requirements.map(function (condition) { return '<li>' + esc(condition.text) + ' · ' +
              esc(({ met: '材料有支持（自述待核验）', failed: '当前冲突', unknown: '待确认' })[condition.state] || '待确认') + '</li>'; }).join('') + '</ul>' : '<p>条件尚待核对，请阅读职位原文。</p>') +
            '<div class="career-posting-actions">' + (url ? '<a target="_blank" rel="noopener noreferrer" href="' + esc(url) + '">查看来源 ↗</a>' : '') +
            '<button class="career-btn" data-save-posting="' + Number(item.id) + '">保存为我的目标岗位</button></div></article>';
        }).join('') : '<div class="career-posting-empty"><h4>' + (result.empty_reason === 'no_verified_source' ? '暂未接入已核验的职位来源' : '当前筛选下暂无有效职位') +
          '</h4><p>你可以继续探索职业方向，或在简历工作台粘贴自己找到的岗位描述。</p></div>';
        resultBox.querySelectorAll('[data-save-posting]').forEach(function (button) { button.onclick = async function () {
          button.disabled = true;
          try {
            var saved = await fetchJSON('/api/career-path/job-postings/' + button.dataset.savePosting + '/target', { method: 'POST' });
            var targetId = saved.job_target_id || (saved.item || {}).id;
            if (!targetId) throw new Error('岗位已保存，请前往岗位分析页查看。');
            var link = document.createElement('a'); link.href = '/resume/job-targets?job_id=' + enc(targetId); link.textContent = '已保存 · 查看岗位条件与简历建议 →'; button.replaceWith(link);
          } catch (error) { button.textContent = error.message; button.disabled = false; }
        }; });
        var hasNext = typeof result.has_more === 'boolean' ? result.has_more : page * Number(result.page_size || 20) < Number(result.total || 0);
        if (page > 1 || hasNext) {
          pager.innerHTML = '<button class="career-btn" data-page="previous"' + (page === 1 ? ' disabled' : '') + '>上一页</button><span>第 ' + page + ' 页</span><button class="career-btn" data-page="next"' + (hasNext ? '' : ' disabled') + '>下一页</button>';
          pager.querySelectorAll('button').forEach(function (button) { button.onclick = function () { page += button.dataset.page === 'next' ? 1 : -1; refresh(); }; });
        }
      } catch (error) { if (id === requestId) resultBox.textContent = error.message + '，请重新筛选以重试。'; }
      finally { if (id === requestId && form.isConnected) form.querySelector('button').disabled = false; }
    }
    form.onsubmit = function (event) { event.preventDefault(); page = 1; refresh(); }; refresh();
  }
  function openPreferences() {
    ensureModal(); clearTimeout(modalTimer); modalReturnFocus = document.activeElement;
    var preferences = (STATE && STATE.preferences) || {};
    modal.innerHTML = '<div class="career-modal__panel" role="dialog" aria-modal="true" aria-label="职业偏好"><header class="career-modal__head"><h3>职业偏好</h3><button type="button" class="career-modal__close" aria-label="关闭">✕</button></header>' +
      '<form id="career-preferences-form" class="career-modal__body career-preferences-form"><p>填写你当前最在意的方向和约束，推荐会结合这些信息。可以随时调整。</p>' +
      '<label>意向城市<input name="cities" maxlength="120" placeholder="例如：南宁、广州" value="' + esc((preferences.cities || []).join('、')) + '"></label>' +
      '<label>目标方向<input name="target_positions" maxlength="200" placeholder="例如：运营、教师、工程师" value="' + esc((preferences.target_positions || []).join('、')) + '"></label>' +
      '<label>当前目标<select name="goal"><option value="explore">探索方向</option><option value="internship">寻找实习</option><option value="employment">准备就业</option><option value="further_study">继续深造</option></select></label>' +
      '<label>工作方式<select name="work_mode"><option value="flexible">均可考虑</option><option value="onsite">现场办公</option><option value="remote">远程工作</option><option value="hybrid">混合办公</option></select></label>' +
      '<label>其他偏好<textarea name="notes" maxlength="500" placeholder="例如：优先实习、希望跨专业探索">' + esc(preferences.notes || '') + '</textarea></label>' +
      '<p role="status" id="career-preferences-status"></p><button class="career-btn" type="submit">保存偏好</button></form></div>';
    modal.hidden = false; modal.classList.add('show'); modal.querySelector('.career-modal__close').onclick = closeModal;
    modal.querySelector('[name=goal]').value = preferences.goal || 'explore';
    modal.querySelector('[name=work_mode]').value = preferences.work_mode || 'flexible';
    modal.querySelector('input').focus();
    document.getElementById('career-preferences-form').onsubmit = async function (event) {
      event.preventDefault(); var form = event.currentTarget, button = form.querySelector('button[type=submit]'); button.disabled = true;
      function values(name) { return form.elements[name].value.split(/[、,，\n]/).map(function (value) { return value.trim(); }).filter(Boolean); }
      try {
        var result = await fetchJSON('/api/career-path/preferences', { method: 'POST', body: { cities: values('cities'),
          target_positions: values('target_positions'), notes: form.elements.notes.value, goal: form.elements.goal.value,
          work_mode: form.elements.work_mode.value, revision: STATE.revision } });
        route(result.state || await loadState()); closeModal();
      } catch (error) { document.getElementById('career-preferences-status').textContent = error.message; }
      finally { button.disabled = false; }
    };
  }
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') { if (modal && !modal.hidden) closeModal(); else closePanels(); }
    if (event.key !== 'Tab' || !modal || modal.hidden) return;
    var focusable = Array.from(modal.querySelectorAll('button, a[href], input, textarea, select, [tabindex="0"]')).filter(function (node) { return !node.disabled; });
    if (!focusable.length) return;
    if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable[focusable.length - 1].focus(); }
    else if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) { event.preventDefault(); focusable[0].focus(); }
  });

  function renderLegend(s) {
    var items = [
      '<span class="it"><span class="dot d5"></span>★★★★★ 最推荐（最亮·闪烁）</span>',
      '<span class="it"><span class="dot d4"></span>★★★★ 推荐</span>',
      '<span class="it"><span class="dot d3"></span>★★★ 可选（较暗）</span>',
      '<span class="it"><span class="dx"></span>紫色虚线＝可转向的分叉路径</span>',
      '<span class="it hint">💡 点击节点：高亮成长路径并展开定制详情 · 点击空白复位</span>'
    ];
    el.legend.innerHTML = items.join('');
  }

  // ---------- 详情卡片 + 必备知识 ----------
  function catOf(s, catId) {
    return ((s.network && s.network.cats) || []).find(function (c) { return c.id === catId; }) || {};
  }

  function openDetail(data, stageNode, s) {
    if (!data) return;
    clearTimeout(panelTimer);
    if (!root.classList.contains('is-detail')) {
      detailReturnFocus = document.activeElement;
      detailStageScrollTop = el.stage.scrollTop;
    }
    track('career_direction_opened', { career_tag: data.tag || '', target_position: data.name || '', source: 'network' });
    var cat = catOf(s, data.cat);
    var c1 = /^#[a-f0-9]{3,8}$/i.test(cat.c1 || '') ? cat.c1 : '#6ee7ff';
    var h = '';
    h += '<div class="career-detail__head">';
    h += '<div class="career-detail__glow" style="background:radial-gradient(600px circle at 80% -20%,' + c1 + ',transparent 60%)"></div>';
    h += '<button class="career-detail__close" type="button" aria-label="关闭">✕</button>';
    h += '<div class="career-detail__cat">' + esc((cat.icon || '') + ' ' + (cat.name || '') + ' · ' + (data.tag || '')) + '</div>';
    h += '<div class="career-detail__title">' + (data.lang ? '⭐ ' : '') + esc(data.name) + '</div>';
    h += '<div class="career-detail__stars">' + stars(data.rec) + '<small>推荐度 ' + (data.rec || 0) + '/5'
      + (data.base_rec && data.base_rec !== data.rec ? '（已按你的特质调整）' : '') + '</small></div>';
    h += '</div><div class="career-detail__body">';

    if (stageNode && (stageNode.phase || stageNode.role)) {
      h += sec('当前点亮的时间节点', '<div class="career-stage-node"><b>' + esc(stageNode.phase || '成长阶段') + '</b>　'
        + esc(stageNode.role || data.name)
        + (stageNode.sdesc && stageNode.sdesc !== '—' ? '<br>' + esc(stageNode.sdesc) : '') + '</div>');
    }
    if (data.tip) h += sec('为你定制的建议', '<div class="career-tip">' + esc(data.tip) + '</div>');
    var rank = (s.rankings || []).find(function (item) { return item.tag === data.tag; });
    if (rank) {
      h += sec('推荐依据', '<p>' + esc(rank.why || '') + '</p>');
      if (rank.evidence && rank.evidence.length) h += sec('你已提供的资料', '<ul>' + rank.evidence.map(function (item) {
        return '<li>' + esc(item.requirement) + ' <a href="/resume/profile/' + enc(String(item.section || '').replace('_', '-')) + '">核对相关资料</a></li>';
      }).join('') + '</ul><p>资料为你的自述，需要结合实际作品或岗位要求进一步核验。</p>');
      if (rank.gaps && rank.gaps.length) h += sec('待补充的能力证据', pills(rank.gaps) + '<p>缺少资料不代表你不会；可以补充真实经历、作品或证书，再更新推荐。</p>');
    }
    if (data.desc) h += sec('方向简介', '<p>' + esc(data.desc) + '</p>');
    if (data.reason) h += sec('为什么推荐 / 适合谁', '<p>' + esc(data.reason) + '</p>');
    if (data.pre && data.pre.length) h += sec('必备前提条件', pills(data.pre));
    if (data.know && data.know.length) h += sec('知识 / 经验储备', pills(data.know));
    if (data.tl && data.tl.length) h += sec('成长阶段线　现在 → 未来', timeline(data.tl));
    if (data.branch) h += sec('发展选项 / 可转向', '<div class="career-branch"><b>分叉路径</b>　' + esc(data.branch) + '</div>');
    if (data.trend) h += sec('未来趋势 · 将来会怎样', '<p>' + esc(data.trend) + '</p>');
    h += '</div>';
    el.detail.innerHTML = h;
    el.detail.querySelector('.career-detail__close').addEventListener('click', closePanels);
    el.detail.classList.add('show'); show(el.detail, true);
    root.classList.add('is-detail');
    el.detail.querySelector('.career-detail__body').scrollTop = 0;

    openPrep(data, s);
    // On narrow/short screens the stage itself scrolls; its absolute panels
    // must open in view while preserving the student's place in the list.
    el.stage.scrollTop = 0;
    el.detail.querySelector('.career-detail__close').focus({ preventScroll: true });
  }

  function sec(title, body) {
    return '<div class="career-sec"><div class="career-sec__t"><i></i>' + esc(title) + '</div>' + body + '</div>';
  }
  function pills(arr) {
    return '<div class="career-pills">' + arr.map(function (x) { return '<span class="career-pill">' + esc(x) + '</span>'; }).join('') + '</div>';
  }
  function timeline(tl) {
    return '<div class="career-tl">' + tl.map(function (t) {
      var desc = (t[2] && t[2] !== '—') ? '<div class="career-tl__desc">' + esc(t[2]) + '</div>' : '';
      return '<div class="career-tl__item"><div class="career-tl__phase">' + esc(t[0]) + '</div>'
        + '<div class="career-tl__role">' + esc(t[1]) + '</div>' + desc + '</div>';
    }).join('') + '</div>';
  }

  function openPrep(data, s) {
    var card = (s.prep_cards || {})[data.tag];
    var tl = s.timeline || {};
    var advice = (s.personalized && s.personalized.timeline_advice) || '';
    var h = '';
    h += '<div class="career-prep__head"><div>'
      + '<h3>选择「' + esc(data.name) + '」从现在开始的能力准备</h3>'
      + '<p>' + esc(card && card.summary ? card.summary : '按重要程度准备以下能力，越早开始越从容。') + '</p>'
      + '</div><button type="button" class="career-prep__close" aria-label="收起">✕</button></div>';

    var deadline = '';
    if (tl.graduation_date_label) {
      if (tl.years_to_graduation != null && tl.years_to_graduation > 0) {
        var enough = tl.years_to_graduation >= 1.5 ? '时间相对充裕，建议按上面的优先级稳步推进。'
          : (tl.years_to_graduation >= 0.6 ? '时间偏紧，优先死磕「非常重要」项，并尽快找一段实习。'
            : '距离毕业很近，集中火力补「非常重要」项 + 做一个能拿得出手的项目即可。');
        deadline = '距毕业约 ' + tl.years_to_graduation + ' 年（' + (tl.months_to_graduation != null ? tl.months_to_graduation + ' 个月' : '') + '）。' + enough;
      } else {
        deadline = '你已临近或完成毕业，可把这些作为入职前后的进阶清单。';
      }
    }
    if (advice) deadline = deadline ? (deadline + ' ' + advice) : advice;

    // 主体：左侧三档技术栈，右侧时间/节奏建议（⏳）
    var stacks = (card && card.stacks) || [];
    h += '<div class="career-prep__main">';
    h += '<div class="career-prep__stacks">';
    if (stacks.length) {
      stacks.forEach(function (st, i) {
        var lvl = i === 0 ? 'l0' : i === 1 ? 'l1' : 'l2';
        h += '<div class="career-stack career-stack--' + lvl + '">'
          + '<span class="career-stack__level">' + esc(st.level || '') + '</span><ul>'
          + (st.items || []).map(function (it) { return '<li>' + esc(it) + '</li>'; }).join('')
          + '</ul></div>';
      });
    } else {
      h += '<p class="career-prep__empty">该方向暂无细分能力清单，可参考右侧节奏建议先打好通用基础。</p>';
    }
    h += '</div>';
    h += '<aside class="career-prep__aside">';
    if (deadline) h += '<div class="career-prep__deadline"><span class="career-prep__aside-t">⏳ 时间与节奏</span>' + esc(deadline) + '</div>';
    h += '</aside>';
    h += '</div>';

    // 岗位搜索关键字（位于知识栈下方，点击任一关键字 → 各平台一键直达）
    h += '<div class="career-kw" id="career-kw" data-tag="' + esc(data.tag || '') + '"></div>';

    el.prep.innerHTML = h;
    el.prep.querySelector('.career-prep__close').addEventListener('click', closePanels);
    el.prep.classList.add('show'); show(el.prep, true);

    hydrateKeywords(data, s);
  }

  // ---------- 岗位关键字卡片 ----------
  function hydrateKeywords(data, s) {
    var box = document.getElementById('career-kw');
    if (!box || !data.tag) return;
    var cached = KW_CACHE[data.tag] || (s.job_keywords || {})[data.tag];
    if (cached && cached.length) { KW_CACHE[data.tag] = cached; paintKeywords(box, data, cached, s); return; }
    box.innerHTML = '<div class="career-kw__head">🔎 求职搜索关键字</div>'
      + '<div class="career-kw__loading"><span class="career-kw__spin"></span>正在为你生成贴合的求职关键字…</div>';
    fetchJSON('/api/career-path/keywords', { method: 'POST', body: JSON.stringify({ tag: data.tag }) })
      .then(function (r) {
        var kws = (r && r.keywords) || [];
        if (!kws.length) kws = fallbackKeywords(data);
        KW_CACHE[data.tag] = kws;
        repaintIfCurrent(data, kws, s);
      })
      .catch(function () {
        var kws = fallbackKeywords(data);
        KW_CACHE[data.tag] = kws;
        repaintIfCurrent(data, kws, s);
      });
  }
  function repaintIfCurrent(data, kws, s) {
    var box = document.getElementById('career-kw');
    if (box && box.dataset.tag === data.tag) paintKeywords(box, data, kws, s);
  }
  function paintKeywords(box, data, kws, s) {
    if (!kws || !kws.length) { box.innerHTML = ''; return; }
    var chips = kws.map(function (k, i) {
      return '<button type="button" class="career-kw__chip' + (i === 0 ? ' is-top' : '') + '" data-kw="' + esc(k) + '">'
        + '<span class="career-kw__rank">' + (i + 1) + '</span>' + esc(k)
        + '<span class="career-kw__go">直达 ›</span></button>';
    }).join('');
    box.innerHTML = '<div class="career-kw__head">🔎 求职搜索关键字 <small>点击关键字 · 各大平台一键直达搜索结果</small></div>'
      + '<div class="career-kw__chips">' + chips + '</div>';
    box.querySelectorAll('.career-kw__chip').forEach(function (b) {
      b.addEventListener('click', function () { openPlatformModal(data, kws, b.dataset.kw, s); });
    });
  }

  // ---------- 平台一键直达弹窗（按关键字归集） ----------
  function openPlatformModal(data, kws, focusKw, s) {
    ensureModal();
    clearTimeout(modalTimer); modalReturnFocus = document.activeElement;
    var tr = (s && s.test_result) || {};
    var loc = tr.location_pref || '';
    var locLabel = tr.location_label || '';
    var order = platformOrderFor(loc);
    track('career_job_search_opened', {
      career_tag: data.tag || '', target_position: data.name || '', location_pref: loc, source: 'platform_links'
    });

    var groups = kws.map(function (kw, gi) {
      var btns = order.map(function (pid) {
        var p = PLATFORMS[pid];
        if (!p) return '';
        return '<a class="career-pf' + (p.local ? ' is-local' : '') + '" href="' + esc(p.url(kw)) + '"'
          + ' target="_blank" rel="noopener noreferrer">'
          + '<span class="career-pf__logo" style="background:' + p.bg + ';color:' + p.color + '">' + esc(p.short) + '</span>'
          + '<span class="career-pf__meta"><b>' + esc(p.name) + '</b>'
          + '<i>' + (p.local ? '本地特色 · ' : '') + '搜“' + esc(kw) + '”</i></span>'
          + '<span class="career-pf__arrow">↗</span></a>';
      }).join('');
      return '<section class="career-kwgroup' + (kw === focusKw ? ' is-focus' : '') + '" data-kw="' + esc(kw) + '">'
        + '<header class="career-kwgroup__h"><span class="career-kwgroup__rank">' + (gi + 1) + '</span>'
        + '<h4>' + esc(kw) + '</h4>'
        + (gi === 0 ? '<span class="career-kwgroup__tag">最贴合</span>' : '') + '</header>'
        + '<div class="career-kwgroup__pf">' + btns + '</div></section>';
    }).join('');

    var note = locLabel
      ? '已按你的地域意向（' + esc(locLabel) + '）优先推荐本地特色平台，再按口碑大平台排序。'
      : '点击任意平台直接跳到该关键字的搜索结果页（如需登录，注册后即可查看）。';

    var resumeHref = resumeLink(data, STATE);
    var resumeCta = '<a class="career-modal__resume-cta" href="' + resumeHref + '">'
      + '<span class="career-modal__resume-icon">'
      + '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"></path><path d="M14 2v6h6"></path><path d="M8 13h8M8 17h6"></path></svg></span>'
      + '<span class="career-modal__resume-copy"><b>简历管理与优化</b>'
      + '<i>沉淀资料 · AI 撰写 · 拖拽搭建 · 一键导出 Word / PDF</i></span>'
      + '<span class="career-modal__resume-go">打开控制台 →</span></a>';

    modal.innerHTML = '<div class="career-modal__panel" role="dialog" aria-modal="true">'
      + '<header class="career-modal__head">'
      + '<div class="career-modal__titles"><div class="career-modal__cat">' + esc(data.tag || '') + ' · 求职关键字 → 招聘平台</div>'
      + '<h3>按「' + esc(data.name) + '」方向搜索</h3>'
      + '<p>以下为外部招聘平台搜索入口，不代表已核验的在招职位。</p><p>' + note + '</p></div>'
      + '<button type="button" class="career-modal__close" aria-label="关闭">✕</button></header>'
      + '<div class="career-modal__body">' + resumeCta + groups + '</div></div>';

    modal.hidden = false;
    requestAnimationFrame(function () { modal.classList.add('show'); });
    modal.querySelector('.career-modal__close').addEventListener('click', closeModal);
    modal.querySelector('.career-modal__close').focus();
    var focus = modal.querySelector('.career-kwgroup.is-focus');
    if (focus && focus !== modal.querySelector('.career-kwgroup')) {
      setTimeout(function () { focus.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 280);
    }
  }

  function hideInfoPanels() {
    el.detail.classList.remove('show');
    el.prep.classList.remove('show');
    clearTimeout(panelTimer);
    panelTimer = setTimeout(function () { show(el.detail, false); show(el.prep, false); }, 420);
  }

  function closePanels() {
    var wasOpen = root.classList.contains('is-detail');
    hideInfoPanels();
    root.classList.remove('is-detail');
    if (net) net.clear();
    if (wasOpen && detailStageScrollTop !== null) el.stage.scrollTop = detailStageScrollTop;
    if (wasOpen && detailReturnFocus && detailReturnFocus.isConnected) detailReturnFocus.focus({ preventScroll: true });
    detailReturnFocus = null;
    detailStageScrollTop = null;
  }

  // ---------- 重新测试 ----------
  el.redo.addEventListener('click', function () {
    if (STATE.phase === 'intro') { browseBase = false; quizActive = false; route(STATE); return; }
    if (!confirm('确定要重新做一次职业性格测试吗？将重新生成你的专属网络。')) return;
    fetchJSON('/api/career-path/reset', { method: 'POST', body: { revision: STATE.revision } }).then(function () {
      browseBase = false; quizActive = false; saveError = null; return loadState();
    }).then(route).catch(function (error) { renderTasks(STATE || {}, error); });
  });

  // ---------- 启动 ----------
  fetchJSON('/api/career-path/initialize', { method: 'POST' }).then(route).catch(function () {
    el.boot.innerHTML = '<p style="color:#fb7185">加载失败，请刷新重试或返回 <a href="/dashboard" style="color:#6ee7ff">首页</a>。</p>';
  });
})();
