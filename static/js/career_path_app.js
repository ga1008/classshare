/* 职业发展网络 · 页面编排：欢迎打字机 → 性格测试 → 等待 AI → 渲染时间轴网络 + 详情/必备知识 */
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
    quiz: document.getElementById('career-quiz'),
    waiting: document.getElementById('career-waiting'),
    waitTitle: document.getElementById('career-waiting-title'),
    waitDesc: document.getElementById('career-waiting-desc'),
    rain: document.getElementById('career-rain')
  };

  var net = null;
  var pollTimer = null;
  var rain = null;
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
    stopRain();
    show(el.boot, false); show(el.stage, false); show(el.topbar, false);
    show(el.waiting, false); show(el.intro, true); show(el.quiz, false);
    el.typewriter.style.display = '';

    var draft = (s && s.draft) || [];
    if (draft.length) { resumeQuiz(draft); return; }

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
        setTimeout(next, 2400);
      });
    }
    next();
  }

  // ---------- 性格测试 ----------
  var QUESTIONS = [];
  var ANSWERS = [];
  var qIndex = 0;

  function loadQuestions() {
    return fetchJSON(QUESTIONS_URL).then(function (r) { QUESTIONS = r.questions || []; });
  }

  function beginQuiz() {
    loadQuestions().then(function () {
      ANSWERS = [];
      qIndex = 0;
      el.typewriter.style.display = 'none';
      show(el.quiz, true);
      renderQuestion();
    }).catch(function () {
      el.typewriter.textContent = '题目加载失败，请刷新重试。';
    });
  }

  function resumeQuiz(draft) {
    loadQuestions().then(function () {
      ANSWERS = (draft || []).slice();
      var answered = {};
      ANSWERS.forEach(function (a) { answered[a.question_id] = true; });
      var firstUnanswered = -1;
      for (var i = 0; i < QUESTIONS.length; i++) { if (!answered[QUESTIONS[i].id]) { firstUnanswered = i; break; } }
      qIndex = firstUnanswered < 0 ? QUESTIONS.length : firstUnanswered;
      el.typewriter.style.display = '';
      typeLine('欢迎回来，我们接着上次继续 ✦', function () {
        setTimeout(function () {
          el.typewriter.style.display = 'none';
          show(el.quiz, true);
          if (qIndex >= QUESTIONS.length) { submitAnswers(); } else { renderQuestion(); }
        }, 1100);
      });
    }).catch(function () {
      el.typewriter.textContent = '题目加载失败，请刷新重试。';
    });
  }

  function setAnswer(qid, value) {
    var found = ANSWERS.find(function (a) { return a.question_id === qid; });
    if (found) found.value = value; else ANSWERS.push({ question_id: qid, value: value });
  }

  function saveProgress() {
    fetch(PROGRESS_URL, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers: ANSWERS })
    }).catch(function () {});
  }

  function advance() {
    saveProgress();
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

  // ---------- 背景数据瀑布（黑客帝国式：密集、快速、字形突变） ----------
  var RAIN_GLYPHS = 'ﾊﾐﾋｰｳｼﾅﾓﾆｻﾜﾂｵﾘｱﾎﾃﾏｹﾒｴｶｷﾑﾕﾗｾﾈ0123456789ABCDEFGHXYZ:.=*+<>|/\\';
  function rainGlyph() { return RAIN_GLYPHS.charAt((Math.random() * RAIN_GLYPHS.length) | 0); }

  function buildRainPhrases(s) {
    var ph = [];
    if (s.major && s.major.name) { ph.push(s.major.name); ph.push(s.major.name + '·职业网络'); }
    var nodes = (s.network && s.network.nodes) || [];
    nodes.forEach(function (n) {
      ph.push(n.name);
      var r = Math.max(1, Math.min(5, n.rec || 3));
      ph.push(n.name + ' ' + '★★★★★'.slice(0, r));
      if (n.lang) ph.push(n.name + ' ⭐外语');
    });
    var dims = (s.test_result && s.test_result.top_dims) || [];
    dims.forEach(function (d) { ph.push((d.label || d.dim) + (d.score != null ? ' ' + d.score : '')); });
    if (s.test_result && s.test_result.holland_code) ph.push('HOLLAND·' + s.test_result.holland_code);
    var tl = s.timeline || {};
    if (tl.graduation_year) ph.push(tl.graduation_year + '届毕业');
    if (tl.years_to_graduation != null) ph.push('T-' + tl.years_to_graduation + 'Y');
    if (s.student && s.student.name) ph.push(s.student.name);
    if (s.student && s.student.class_name) ph.push(s.student.class_name);
    ph.push('MATCHING', 'ANALYZING', '计算推荐值', '匹配知识栈', '0110', 'WEIGHTING', '推演路径', 'RECOMMEND');
    return ph.filter(Boolean);
  }

  function CareerRain(canvas, phrases) {
    var self = this;
    this.canvas = canvas; this.ctx = canvas.getContext('2d');
    this.phrases = (phrases && phrases.length) ? phrases : ['ANALYZING', '匹配中'];
    this.dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
    this.font = 15; this.rowH = this.font * 1.1; this.colW = this.font * 1.34;
    this._raf = null;
    this._onResize = function () { self.resize(); };
    window.addEventListener('resize', this._onResize);
    this.resize();
    this.start();
  }
  CareerRain.prototype._pick = function () { return this.phrases[(Math.random() * this.phrases.length) | 0]; };
  CareerRain.prototype.resize = function () {
    var r = this.canvas.getBoundingClientRect();
    this.W = r.width || window.innerWidth; this.H = r.height || window.innerHeight;
    this.canvas.width = Math.round(this.W * this.dpr);
    this.canvas.height = Math.round(this.H * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    var n = Math.max(1, Math.floor(this.W / this.colW));
    this.cols = [];
    for (var i = 0; i < n; i++) this.cols.push(this._spawn(i, true));
  };
  CareerRain.prototype._spawn = function (i, initial) {
    var content = Math.random() < 0.42;
    return {
      x: i * this.colW + this.colW * 0.5,
      headY: initial ? (Math.random() * this.H) : (-(0.05 + Math.random() * 0.5) * this.H),
      speed: this.rowH * (17 + Math.random() * 23),
      trail: 13 + (Math.random() * 22 | 0),
      alpha: 0.5 + Math.random() * 0.5,
      content: content,
      phrase: content ? this._pick() : '',
      pi: 0, sep: 0,
      buf: [], acc: 0
    };
  };
  CareerRain.prototype._emit = function (c) {
    if (c.content && c.phrase) {
      if (c.pi >= c.phrase.length) {
        if (c.sep <= 0) c.sep = 1 + (Math.random() * 2 | 0);
        c.sep--;
        if (c.sep <= 0) { c.phrase = this._pick(); c.pi = 0; }
        return rainGlyph();
      }
      return c.phrase.charAt(c.pi++) || rainGlyph();
    }
    return rainGlyph();
  };
  CareerRain.prototype.start = function () {
    if (this._raf) return;
    var self = this, last = performance.now();
    function loop(now) {
      var dt = Math.min((now - last) / 1000, 0.05); last = now;
      self._step(dt); self._draw(); self._raf = requestAnimationFrame(loop);
    }
    this._raf = requestAnimationFrame(loop);
  };
  CareerRain.prototype._step = function (dt) {
    for (var i = 0; i < this.cols.length; i++) {
      var c = this.cols[i];
      var adv = c.speed * dt;
      c.headY += adv; c.acc += adv;
      while (c.acc >= this.rowH) {
        c.acc -= this.rowH;
        c.buf.unshift(this._emit(c));
        if (c.buf.length > c.trail) c.buf.pop();
      }
      if (!c.content && c.buf.length > 2 && Math.random() < 0.5) {
        c.buf[1 + (Math.random() * (c.buf.length - 1) | 0)] = rainGlyph();
      }
      if (c.headY - c.trail * this.rowH > this.H) this.cols[i] = this._spawn(i, false);
    }
  };
  CareerRain.prototype._draw = function () {
    var ctx = this.ctx; ctx.clearRect(0, 0, this.W, this.H);
    ctx.font = '700 ' + this.font + 'px "Consolas","PingFang SC","Microsoft YaHei",monospace';
    ctx.textAlign = 'center';
    for (var i = 0; i < this.cols.length; i++) {
      var c = this.cols[i], buf = c.buf, n = buf.length;
      for (var k = 0; k < n; k++) {
        var ch = buf[k]; if (ch === ' ') continue;
        var y = c.headY - k * this.rowH;
        if (y < -this.rowH || y > this.H + this.rowH) continue;
        if (k === 0) {
          ctx.fillStyle = 'rgba(228,255,240,' + Math.min(0.97, c.alpha + 0.34) + ')';
        } else if (k === 1) {
          ctx.fillStyle = 'rgba(168,250,206,' + (c.alpha * 0.92) + ')';
        } else {
          var f = 1 - k / n;
          var a = c.alpha * f * f;
          if (a < 0.014) continue;
          ctx.fillStyle = 'rgba(70,221,158,' + a + ')';
        }
        ctx.fillText(ch, c.x, y);
      }
    }
  };
  CareerRain.prototype.destroy = function () {
    if (this._raf) cancelAnimationFrame(this._raf); this._raf = null;
    window.removeEventListener('resize', this._onResize);
    if (this.ctx) this.ctx.clearRect(0, 0, this.W, this.H);
  };

  function startRain(s) {
    if (!el.rain) return;
    stopRain();
    try { rain = new CareerRain(el.rain, buildRainPhrases(s)); } catch (e) { rain = null; }
  }
  function stopRain() { if (rain) { rain.destroy(); rain = null; } }

  // ---------- 等待 AI ----------
  function startWaiting(s) {
    show(el.boot, false); show(el.intro, false); show(el.stage, false);
    show(el.topbar, false); show(el.waiting, true);
    if (s.phase === 'network_generating') {
      el.waitTitle.textContent = '正在为「' + esc(s.major && s.major.name || '你的专业') + '」绘制职业网络…';
      el.waitDesc.textContent = '这是你所在专业的第一位探索者，AI 正在搜集该专业的就业方向与推荐指数。稍后会自动呈现。';
    } else {
      el.waitTitle.textContent = '正在为你设计专属职业网络…';
      el.waitDesc.textContent = '深度思考型 AI 正在结合你的性格、专业与毕业时间，重新设计推荐与必备知识。这通常需要 1–3 分钟。';
    }
    requestAnimationFrame(function () { startRain(s); });
    startPolling();
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(function () {
      loadState().then(function (s) {
        if (s.phase === 'ready') { stopPolling(); startNetwork(s); }
        else if (s.phase === 'intro') { stopPolling(); startIntro(s); }
      }).catch(function () {});
    }, 6000);
  }
  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

  // ---------- 渲染时间轴网络 ----------
  function startNetwork(s) {
    stopPolling(); stopRain();
    show(el.boot, false); show(el.intro, false); show(el.waiting, false);
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
    net.setData(network, s.personalized || {});
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
    var cat = catOf(s, data.cat);
    var c1 = cat.c1 || '#6ee7ff';
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
      h += '<p class="career-prep__empty">该方向暂无细分知识栈，可参考右侧节奏建议先打好通用基础。</p>';
    }
    h += '</div>';
    h += '<aside class="career-prep__aside">';
    if (deadline) h += '<div class="career-prep__deadline"><span class="career-prep__aside-t">⏳ 时间与节奏</span>' + esc(deadline) + '</div>';
    h += '</aside>';
    h += '</div>';
    el.prep.innerHTML = h;
    el.prep.querySelector('.career-prep__close').addEventListener('click', closePanels);
    el.prep.classList.add('show'); show(el.prep, true);
  }

  function hideInfoPanels() {
    el.detail.classList.remove('show');
    el.prep.classList.remove('show');
    setTimeout(function () { show(el.detail, false); show(el.prep, false); }, 420);
  }

  function closePanels() {
    hideInfoPanels();
    root.classList.remove('is-detail');
    if (net) net.clear();
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
