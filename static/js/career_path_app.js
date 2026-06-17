/* 职业发展网络 · 页面编排：欢迎打字机 → 性格测试 → 等待 AI → 渲染星图 + 详情/必备知识 */
(function () {
  'use strict';

  var root = document.getElementById('career-root');
  if (!root) return;
  var STATE_URL = root.dataset.stateUrl;
  var QUESTIONS_URL = root.dataset.questionsUrl;
  var ANSWERS_URL = root.dataset.answersUrl;

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
    quiz: document.getElementById('career-quiz'),
    waiting: document.getElementById('career-waiting'),
    waitTitle: document.getElementById('career-waiting-title'),
    waitDesc: document.getElementById('career-waiting-desc')
  };

  var net = null;
  var pollTimer = null;
  var STATE = null;

  // ---------- 工具 ----------
  function show(node, on) { if (node) node.hidden = !on; }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m];
    });
  }
  function stars(n) { n = Math.max(0, Math.min(5, n | 0)); return '★★★★★'.slice(0, n) + '☆☆☆☆☆'.slice(0, 5 - n); }

  function fetchJSON(url, opts) {
    return fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin' }, opts || {}))
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  }

  // ---------- 引导：拉取状态 ----------
  function loadState() {
    return fetchJSON(STATE_URL).then(function (s) { STATE = s; return s; });
  }

  function route(s) {
    if (s.phase === 'intro') { startIntro(s); }
    else if (s.phase === 'personalizing' || s.phase === 'network_generating') { startWaiting(s); }
    else { startNetwork(s); }
  }

  // ---------- 打字机欢迎 ----------
  function typeLine(text, done) {
    var i = 0;
    el.typewriter.innerHTML = '';
    var span = document.createElement('span');
    var caret = document.createElement('span');
    caret.className = 'caret'; caret.textContent = '▌';
    el.typewriter.appendChild(span); el.typewriter.appendChild(caret);
    var timer = setInterval(function () {
      span.textContent = text.slice(0, ++i);
      if (i >= text.length) { clearInterval(timer); if (done) done(); }
    }, 55);
  }

  function startIntro(s) {
    show(el.boot, false); show(el.stage, false); show(el.topbar, false);
    show(el.waiting, false); show(el.intro, true); show(el.quiz, false);
    var addr = (s.student && s.student.address) || '同学';
    var lines = [
      '欢迎 ' + addr + '，让我们来看看专属于你的职业生涯网络',
      '在真正进入之前，我们先来看几个简单的问题'
    ];
    var idx = 0;
    function next() {
      if (idx >= lines.length) { beginQuiz(); return; }
      typeLine(lines[idx], function () {
        idx++;
        setTimeout(next, 2400); // 打完字停留约 3 秒（含打字时间）
      });
    }
    next();
  }

  // ---------- 性格测试 ----------
  var QUESTIONS = [];
  var ANSWERS = [];
  var qIndex = 0;

  function beginQuiz() {
    fetchJSON(QUESTIONS_URL).then(function (r) {
      QUESTIONS = r.questions || [];
      ANSWERS = [];
      qIndex = 0;
      el.typewriter.style.display = 'none';
      show(el.quiz, true);
      renderQuestion();
    }).catch(function () {
      el.typewriter.textContent = '题目加载失败，请刷新重试。';
    });
  }

  function setAnswer(qid, value) {
    var found = ANSWERS.find(function (a) { return a.question_id === qid; });
    if (found) found.value = value; else ANSWERS.push({ question_id: qid, value: value });
  }

  function advance() {
    qIndex++;
    if (qIndex >= QUESTIONS.length) { submitAnswers(); return; }
    renderQuestion();
  }

  function renderQuestion() {
    var q = QUESTIONS[qIndex];
    if (!q) { submitAnswers(); return; }
    var pct = Math.round((qIndex / QUESTIONS.length) * 100);
    var h = '';
    h += '<div class="career-quiz__progress"><div class="career-quiz__bar"><i style="width:' + pct + '%"></i></div>'
      + '<span class="career-quiz__count">' + (qIndex + 1) + ' / ' + QUESTIONS.length + '</span></div>';
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
    el.quiz.innerHTML = h;
    wireQuestion(q);
  }

  function wireQuestion(q) {
    var opts = el.quiz.querySelectorAll('.career-opt, .career-scale button');
    if (q.kind === 'single') {
      opts.forEach(function (b) {
        b.addEventListener('click', function () { setAnswer(q.id, b.dataset.value); setTimeout(advance, 180); });
      });
    } else if (q.kind === 'scale') {
      opts.forEach(function (b) {
        b.addEventListener('click', function () { setAnswer(q.id, parseInt(b.dataset.value, 10)); setTimeout(advance, 180); });
      });
    } else if (q.kind === 'multi') {
      var selected = [];
      var confirm = document.getElementById('career-confirm');
      var max = q.max_select || 99;
      opts.forEach(function (b) {
        b.addEventListener('click', function () {
          var v = b.dataset.value, i = selected.indexOf(v);
          if (i >= 0) { selected.splice(i, 1); b.classList.remove('selected'); }
          else { if (selected.length >= max) return; selected.push(v); b.classList.add('selected'); }
          confirm.disabled = selected.length === 0;
        });
      });
      confirm.addEventListener('click', function () { if (!selected.length) return; setAnswer(q.id, selected.slice()); advance(); });
    } else if (q.kind === 'text') {
      var ta = document.getElementById('career-text');
      var confirm2 = document.getElementById('career-confirm');
      var skip = document.getElementById('career-skip');
      confirm2.addEventListener('click', function () { setAnswer(q.id, (ta.value || '').trim()); advance(); });
      if (skip) skip.addEventListener('click', function () { setAnswer(q.id, ''); advance(); });
    }
  }

  function submitAnswers() {
    el.quiz.innerHTML = '<div class="career-quiz__q" style="text-align:center">正在提交你的作答…</div>';
    fetchJSON(ANSWERS_URL, { method: 'POST', body: JSON.stringify({ answers: ANSWERS }) })
      .then(function () { return loadState(); })
      .then(function (s) { route(s); })
      .catch(function () { el.quiz.innerHTML = '<div class="career-quiz__q" style="text-align:center">提交失败，请刷新重试。</div>'; });
  }

  // ---------- 等待 AI ----------
  function startWaiting(s) {
    show(el.boot, false); show(el.intro, false); show(el.stage, false);
    show(el.topbar, false); show(el.waiting, true);
    if (s.phase === 'network_generating') {
      el.waitTitle.textContent = '正在为「' + esc(s.major && s.major.name || '你的专业') + '」绘制职业星图…';
      el.waitDesc.textContent = '这是你所在专业的第一位探索者，AI 正在搜集该专业的就业方向与推荐指数。稍后会自动呈现。';
    } else {
      el.waitTitle.textContent = '正在为你设计专属职业网络…';
      el.waitDesc.textContent = '深度思考型 AI 正在结合你的性格、专业与毕业时间，重新设计推荐与必备知识。这通常需要 1–3 分钟。';
    }
    startPolling();
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(function () {
      loadState().then(function (s) {
        if (s.phase === 'ready') { stopPolling(); startNetwork(s); }
        else if (s.phase === 'intro') { stopPolling(); startIntro(s); }
        else { if (s.phase === 'network_generating') { /* keep waiting copy */ } }
      }).catch(function () {});
    }, 6000);
  }
  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

  // ---------- 渲染星图 ----------
  function startNetwork(s) {
    stopPolling();
    show(el.boot, false); show(el.intro, false); show(el.waiting, false);
    show(el.topbar, true); show(el.stage, true);
    renderTopbar(s);
    renderBanner(s);
    renderLegend(s);
    var network = s.network || { cats: [], nodes: [], links: [] };
    if (!net) {
      net = new window.CareerNetwork(el.canvas, {
        hubLabel: (s.timeline && s.timeline.graduation_year ? ('🎓 ' + s.timeline.graduation_year + ' 毕业') : '起点 · 现在'),
        onSelect: function (data, node) { openDetail(data, node, s); },
        onBackground: function () { closePanels(); }
      });
    }
    // 等 canvas 尺寸稳定再 setData
    requestAnimationFrame(function () { net.resize(); net.setData(network, s.personalized); });
    el.redo.hidden = false;
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
    if (!p.greeting && !p.summary) { show(el.banner, false); return; }
    var h = '';
    if (p.greeting) h += '<h2>' + esc(p.greeting) + '</h2>';
    if (p.summary) h += '<p>' + esc(p.summary) + '</p>';
    el.banner.innerHTML = h; show(el.banner, true);
  }

  function renderLegend(s) {
    var items = [
      '<span class="it"><span class="dot" style="background:#6ee7ff;box-shadow:0 0 10px #6ee7ff"></span>越亮＝越契合你</span>',
      '<span class="it"><span class="dot" style="background:#a78bfa"></span>紫线＝可转向的分叉</span>',
      '<span class="it hint">点击节点看详情与必备知识 · 滚轮缩放 · 拖拽平移 · 鼠标可拨弄节点</span>'
    ];
    el.legend.innerHTML = items.join('');
  }

  // ---------- 详情卡片 + 必备知识 ----------
  function catOf(s, catId) {
    return ((s.network && s.network.cats) || []).find(function (c) { return c.id === catId; }) || {};
  }

  function openDetail(data, node, s) {
    if (!data) return;
    var cat = catOf(s, data.cat);
    var c1 = cat.c1 || '#6ee7ff';
    var h = '';
    h += '<div class="career-detail__head">';
    h += '<div class="career-detail__glow" style="background:radial-gradient(600px circle at 80% -20%,' + c1 + ',transparent 60%)"></div>';
    h += '<button class="career-detail__close" type="button" aria-label="关闭">✕</button>';
    h += '<div class="career-detail__cat">' + esc((cat.icon || '') + ' ' + (cat.name || '') + ' · ' + (data.tag || '')) + '</div>';
    h += '<div class="career-detail__title">' + esc(data.name) + '</div>';
    h += '<div class="career-detail__stars">' + stars(data.rec) + '<small>推荐度 ' + (data.rec || 0) + '/5'
      + (data.base_rec && data.base_rec !== data.rec ? '（已按你的特质调整）' : '') + '</small></div>';
    h += '</div><div class="career-detail__body">';

    if (data.tip) h += sec('为你定制的建议', '<div class="career-tip">' + esc(data.tip) + '</div>');
    if (data.reason) h += sec('为什么推荐 / 适合谁', '<p>' + esc(data.reason) + '</p>');
    if (data.pre && data.pre.length) h += sec('必备前提条件', pills(data.pre));
    if (data.know && data.know.length) h += sec('知识 / 经验储备', pills(data.know));
    if (data.tl && data.tl.length) h += sec('成长阶段线　3-5 年 · 5-10 年 · 10 年+', timeline(data.tl));
    if (data.branch) h += sec('发展选项 / 可转向', '<div class="career-branch"><b>分叉路径</b>　' + esc(data.branch) + '</div>');
    if (data.trend) h += sec('未来趋势 · 将来会怎样', '<p>' + esc(data.trend) + '</p>');
    h += '</div>';
    el.detail.innerHTML = h;
    el.detail.querySelector('.career-detail__close').addEventListener('click', closePanels);
    el.detail.classList.add('show'); show(el.detail, true);
    el.detail.querySelector('.career-detail__body').scrollTop = 0;

    openPrep(data, s);
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
      + '<h3>选择「' + esc(data.name) + '」从现在到毕业要补的知识栈</h3>'
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
    if (deadline) h += '<div class="career-prep__deadline">⏳ ' + esc(deadline) + '</div>';

    var stacks = (card && card.stacks) || [];
    if (stacks.length) {
      h += '<div class="career-prep__grid">';
      stacks.forEach(function (st, i) {
        var lvl = i === 0 ? 'l0' : i === 1 ? 'l1' : 'l2';
        h += '<div class="career-stack career-stack--' + lvl + '">'
          + '<span class="career-stack__level">' + esc(st.level || '') + '</span><ul>'
          + (st.items || []).map(function (it) { return '<li>' + esc(it) + '</li>'; }).join('')
          + '</ul></div>';
      });
      h += '</div>';
    }
    el.prep.innerHTML = h;
    el.prep.querySelector('.career-prep__close').addEventListener('click', closePanels);
    el.prep.classList.add('show'); show(el.prep, true);
  }

  function closePanels() {
    el.detail.classList.remove('show');
    el.prep.classList.remove('show');
    if (net) net.select(null);
    setTimeout(function () { show(el.detail, false); show(el.prep, false); }, 420);
  }

  // ---------- 重新测试 ----------
  el.redo.addEventListener('click', function () {
    if (!confirm('确定要重新做一次职业性格测试吗？将重新生成你的专属网络。')) return;
    fetchJSON('/api/career-path/reset', { method: 'POST' }).then(function () { return loadState(); }).then(route);
  });

  // ---------- 启动 ----------
  loadState().then(route).catch(function () {
    el.boot.innerHTML = '<p style="color:#fb7185">加载失败，请刷新重试或返回 <a href="/dashboard" style="color:#6ee7ff">首页</a>。</p>';
  });
})();
