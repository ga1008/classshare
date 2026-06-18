/* 职业发展网络 · 时间轴 SVG 网络图（无依赖，模板化 + 可平移缩放）
 *
 * 还原参考版「职业发展网络图」的横向时间轴样式，并以专业数据为模板套用到任意专业：
 *   - 左侧 = 现在（毕业起点），向右沿时间轴展开到 10 年以后。
 *   - 每个就业大类(cat)一组，纵向堆叠；该类下所有方向(node)各占一行，4 个成长阶段(tl)沿时间轴排开。
 *   - 节点越亮＝越推荐 / 越契合（rec 与个性化 dim_glow 决定亮度与闪烁）。
 *   - 点击节点：高亮其来路 + 全部下游分支 + 可转向(links)，并自动把这条路线缩放进「安全可视区」
 *     （详情面板左侧 / 必备知识面板上方），避免被弹层遮挡；点击空白复位为全景。
 *   - 交互：滚轮（或 Ctrl+滚轮）以光标为中心缩放、按住拖动平移、右下角缩放按钮、双击复位全景。
 *   - 缩放分级：放大显示各阶段职位简称，缩小只保留方向名，减少拥挤遮挡。
 *
 * 暴露 window.CareerNetwork。
 *   new CareerNetwork(container, { onSelect(node,stage), onClear(), tipEl, originLabel })
 *   .setData(network, personalized) / .select(id) / .clear() / .fitAll() / .destroy()
 */
(function () {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var MAX_STAGES = 4;
  var DEFAULT_TIME_LABELS = ['0–1 年 · 现在', '3–5 年', '5–10 年', '10 年以后 · 未来'];
  var AXIS_LABELS = ['0–1 年', '3–5 年', '5–10 年', '10 年 +'];
  var CONTENT_W = 1240;
  var MIN_SCALE = 0.18, MAX_SCALE = 2.8, ZOOM_NEAR = 1.12;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m];
    });
  }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function stars(r) { r = clamp(r | 0, 0, 5); return '★★★★★'.slice(0, r) + '☆☆☆☆☆'.slice(0, 5 - r); }
  function shortRole(s) {
    s = String(s || '').trim();
    // 取第一个分隔符前的主体，再限长，作为"职位简称"
    s = s.split(/[\/·(（]/)[0].trim() || s;
    return s.length > 7 ? s.slice(0, 7) + '…' : s;
  }

  function CareerNetwork(container, opts) {
    this.container = typeof container === 'string' ? document.getElementById(container) : container;
    this.opts = opts || {};
    this.onSelect = this.opts.onSelect || function () {};
    this.onClear = this.opts.onClear || function () {};
    this.tip = this.opts.tipEl || document.getElementById('career-tip');

    this.network = { cats: [], nodes: [], links: [] };
    this.personalized = {};
    this.byTag = {};
    this.nodeStages = {};
    this.adjF = {};
    this.adjB = {};
    this.selectedId = null;

    this.coord = {};
    this.contentH = 800;
    this.cw = 0; this.ch = 0;
    this.tx = 0; this.ty = 0; this.scale = 1;
    this._anim = null;
    this._drag = null;
    this.svg = null; this.vp = null; this.controls = null;
    this._bound = false; this._onResize = null; this._resizeRAF = null;
  }

  CareerNetwork.prototype.setData = function (network, personalized) {
    this.network = network || { cats: [], nodes: [], links: [] };
    this.personalized = personalized || {};
    this._index();
    this._render();
    this._bind();
  };

  CareerNetwork.prototype._index = function () {
    var self = this;
    this.byTag = {}; this.nodeStages = {}; this.adjF = {}; this.adjB = {};
    (this.network.nodes || []).forEach(function (n) { if (n.tag) self.byTag[n.tag] = n; });
    function link(from, to) { (self.adjF[from] = self.adjF[from] || []).push(to); (self.adjB[to] = self.adjB[to] || []).push(from); }
    (this.network.nodes || []).forEach(function (n) {
      var stages = self._stagesOf(n);
      link('origin', n.tag + '-0');
      for (var i = 0; i < stages.length - 1; i++) link(n.tag + '-' + i, n.tag + '-' + (i + 1));
      stages.forEach(function (st, i) { self.nodeStages[n.tag + '-' + i] = { node: n, stage: i }; });
    });
    (this.network.links || []).forEach(function (l) {
      if (!Array.isArray(l) || l.length < 4) return;
      var from = l[0] + '-' + l[1], to = l[2] + '-' + l[3];
      if (self.nodeStages[from] && self.nodeStages[to]) link(from, to);
    });
  };

  CareerNetwork.prototype._stagesOf = function (node) {
    var tl = (node.tl && node.tl.length) ? node.tl : [['0–1 年', node.name || '入门', node.desc || '']];
    return tl.slice(0, MAX_STAGES);
  };

  CareerNetwork.prototype._catMeta = function () {
    var cats = (this.network.cats || []).slice();
    var seen = {};
    cats.forEach(function (c) { seen[c.id] = true; });
    (this.network.nodes || []).forEach(function (n) {
      if (n.cat && !seen[n.cat]) { seen[n.cat] = true; cats.push({ id: n.cat, name: n.cat, c1: '#6ee7ff', icon: '✨' }); }
    });
    return cats;
  };

  CareerNetwork.prototype._columnLabels = function () {
    var counts = [{}, {}, {}, {}];
    var self = this;
    (this.network.nodes || []).forEach(function (n) {
      self._stagesOf(n).forEach(function (st, i) {
        var lab = String((st && st[0]) || '').trim();
        if (lab) counts[i][lab] = (counts[i][lab] || 0) + 1;
      });
    });
    return counts.map(function (c, i) {
      var best = '', bestN = 0;
      Object.keys(c).forEach(function (k) { if (c[k] > bestN) { bestN = c[k]; best = k; } });
      return best || DEFAULT_TIME_LABELS[i];
    });
  };

  CareerNetwork.prototype._brightness = function (node) {
    if (typeof node.glow === 'number' && isFinite(node.glow)) return clamp(node.glow, 0.12, 1);
    var rec = clamp(Number(node.rec || 3), 1, 5);
    return clamp(0.18 + rec * 0.16, 0.2, 1);
  };

  CareerNetwork.prototype._render = function () {
    var self = this;
    var rect = this.container.getBoundingClientRect();
    this.cw = rect.width || window.innerWidth;
    this.ch = rect.height || window.innerHeight;

    var W = CONTENT_W;
    var originX = 96;
    var firstCol = Math.round(W * 0.235);
    var lastCol = W - Math.round(W * 0.10);
    var stageX = [0, 1, 2, 3].map(function (i) { return Math.round(firstCol + (lastCol - firstCol) * (i / 3)); });
    var rowH = 46, catGap = 18, catHeadH = 36, topPad = 68;

    var cats = this._catMeta();
    var colorOf = {};
    cats.forEach(function (c) { colorOf[c.id] = c.c1 || '#6ee7ff'; });

    var y = topPad;
    var heads = [], rows = [];
    cats.forEach(function (c) {
      var list = (self.network.nodes || []).filter(function (n) { return n.cat === c.id; });
      if (!list.length) return;
      list = list.slice().sort(function (a, b) { return (b.rec || 0) - (a.rec || 0); });
      heads.push({ cat: c, y: y + 22 });
      y += catHeadH;
      list.forEach(function (n) { n._y = y; rows.push(n); y += rowH; });
      y += catGap;
    });
    var bodyBottom = y;
    var H = bodyBottom + 80;
    this.contentH = H;
    var originY = (topPad + catHeadH + (bodyBottom - catGap)) / 2;
    var axisY = H - 52;

    var coord = { origin: { x: originX, y: originY } };
    rows.forEach(function (n) {
      self._stagesOf(n).forEach(function (st, i) { coord[n.tag + '-' + i] = { x: stageX[i], y: n._y }; });
    });
    this.coord = coord;

    var timeLab = this._columnLabels();
    var g = '';

    // 时间竖向网格 + 顶部时间标签
    stageX.forEach(function (x, i) {
      g += '<line x1="' + x + '" y1="' + (topPad - 32) + '" x2="' + x + '" y2="' + axisY + '" stroke="rgba(255,255,255,.05)" stroke-width="1"/>';
      g += '<text x="' + x + '" y="' + (topPad - 38) + '" text-anchor="middle" class="cn-axislab">' + esc(timeLab[i]) + '</text>';
    });

    // 分类标题
    heads.forEach(function (h) {
      var label = (h.cat.icon ? h.cat.icon + ' ' : '') + (h.cat.name || h.cat.id);
      g += '<text x="22" y="' + h.y + '" class="cn-catlabel" fill="' + esc(h.cat.c1 || '#6ee7ff') + '">' + esc(label) + '</text>';
      g += '<line x1="22" y1="' + (h.y + 9) + '" x2="' + (W - 18) + '" y2="' + (h.y + 9) + '" stroke="' + esc(h.cat.c1 || '#6ee7ff') + '" stroke-opacity=".12" stroke-width="1"/>';
    });

    // 边：扇出
    rows.forEach(function (n) {
      var a = coord.origin, b = coord[n.tag + '-0'];
      if (!b) return;
      g += '<g class="cn-edge fan" data-from="origin" data-to="' + n.tag + '-0">'
        + '<path d="M' + a.x + ' ' + a.y + ' C ' + (a.x + 90) + ' ' + a.y + ', ' + (b.x - 90) + ' ' + b.y + ', ' + b.x + ' ' + b.y + '" '
        + 'fill="none" stroke="rgba(110,231,255,.12)" stroke-width="1.2"/></g>';
    });

    // 边：主成长线
    rows.forEach(function (n) {
      var col = colorOf[n.cat] || '#6ee7ff';
      var rec = clamp(Number(n.rec || 3), 1, 5);
      var op = rec >= 5 ? 0.9 : rec >= 4 ? 0.62 : 0.36;
      var stages = self._stagesOf(n);
      for (var i = 0; i < stages.length - 1; i++) {
        var a = coord[n.tag + '-' + i], b = coord[n.tag + '-' + (i + 1)];
        g += '<g class="cn-edge main" data-from="' + n.tag + '-' + i + '" data-to="' + n.tag + '-' + (i + 1) + '">'
          + '<line x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '" stroke="' + col + '" stroke-opacity="' + (op * 0.38) + '" stroke-width="2"/>'
          + '<line class="cn-flow" x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '" stroke="' + col + '" stroke-opacity="' + (op * 0.8) + '" stroke-width="2"/></g>';
      }
    });

    // 边：跨方向分叉
    (this.network.links || []).forEach(function (l) {
      if (!Array.isArray(l) || l.length < 4) return;
      var a = coord[l[0] + '-' + l[1]], b = coord[l[2] + '-' + l[3]];
      if (!a || !b) return;
      var mx = (a.x + b.x) / 2;
      var d = 'M' + a.x + ' ' + a.y + ' C ' + mx + ' ' + a.y + ', ' + mx + ' ' + b.y + ', ' + b.x + ' ' + b.y;
      g += '<g class="cn-edge cross" data-from="' + l[0] + '-' + l[1] + '" data-to="' + l[2] + '-' + l[3] + '">'
        + '<path d="' + d + '" fill="none" stroke="#0a0f1d" stroke-opacity=".85" stroke-width="4.5"/>'
        + '<path class="cn-cu" d="' + d + '" fill="none" stroke="#d8b4fe" stroke-opacity=".7" stroke-width="1.8" marker-end="url(#cnArrC)"/></g>';
    });

    // 起点
    var gradLabel = this.opts.originLabel || '🎓 毕业';
    g += '<g class="cn-node" data-id="origin">'
      + '<circle class="cn-origin-h" cx="' + originX + '" cy="' + originY + '" r="34" fill="#fbbf24" opacity=".30" filter="url(#cnBlurO)"/>'
      + '<circle class="cn-core" data-origin="1" cx="' + originX + '" cy="' + originY + '" r="13" fill="#fde68a" stroke="#fff" stroke-width="1.5"/></g>';
    g += '<text x="' + originX + '" y="' + (originY + 31) + '" text-anchor="middle" class="cn-origin-t">' + esc(gradLabel) + '</text>';
    g += '<text x="' + originX + '" y="' + (originY + 46) + '" text-anchor="middle" class="cn-origin-s">起点 · 现在</text>';

    // 节点
    rows.forEach(function (n) {
      var col = colorOf[n.cat] || '#6ee7ff';
      var rec = clamp(Number(n.rec || 3), 1, 5);
      var bright = self._brightness(n);
      var hot = !!n.highlighted;
      g += '<text class="cn-nlabel" data-tag="' + n.tag + '" x="' + (stageX[0] - 16) + '" y="' + (n._y - 14) + '" fill="' + col + '">'
        + (n.lang ? '⭐ ' : '') + esc(n.name) + (hot ? ' ✦' : '') + '</text>';
      self._stagesOf(n).forEach(function (st, i) {
        var x = stageX[i], yy = n._y;
        var haloR = 9 + rec * 2.4 + (hot ? 3 : 0);
        var haloOp = clamp(bright * 0.85, 0.12, 0.95);
        var coreR = 4.2 + rec * 0.9;
        var flick = bright >= 0.8 ? 'cn-flick5' : bright >= 0.55 ? 'cn-flick4' : '';
        g += '<g class="cn-node" data-id="' + n.tag + '-' + i + '" data-tag="' + n.tag + '">'
          + '<circle class="cn-halo ' + flick + '" cx="' + x + '" cy="' + yy + '" r="' + haloR + '" fill="' + col + '" opacity="' + haloOp + '" filter="url(#cnBlur)"/>'
          + (hot ? '<circle class="cn-ring" cx="' + x + '" cy="' + yy + '" r="' + (coreR + 5) + '" fill="none" stroke="#fff" stroke-opacity=".7" stroke-width="1.1"/>' : '')
          + '<circle class="cn-core" cx="' + x + '" cy="' + yy + '" r="' + coreR + '" fill="' + col + '" fill-opacity="' + (rec >= 3 ? 1 : 0.7) + '" '
          + 'stroke="#fff" stroke-opacity="' + (bright >= 0.8 ? 0.9 : 0.5) + '" stroke-width="1.3" data-tag="' + n.tag + '" data-i="' + i + '"/>'
          + '<text class="cn-rolelab" x="' + x + '" y="' + (yy + coreR + 12) + '" text-anchor="middle">' + esc(shortRole(st[1])) + '</text>'
          + '</g>';
      });
    });

    // 时间轴
    g += '<line x1="' + originX + '" y1="' + axisY + '" x2="' + (W - 26) + '" y2="' + axisY + '" stroke="#6ee7ff" stroke-opacity=".5" stroke-width="2" marker-end="url(#cnArr)"/>';
    g += '<text x="' + originX + '" y="' + (axisY + 22) + '" text-anchor="middle" class="cn-axisnow">现在</text>';
    g += '<text x="' + (W - 30) + '" y="' + (axisY + 22) + '" text-anchor="end" class="cn-axislab">未来 →</text>';
    stageX.forEach(function (x, i) {
      g += '<circle cx="' + x + '" cy="' + axisY + '" r="4" fill="#6ee7ff"/>';
      g += '<text x="' + x + '" y="' + (axisY + 22) + '" text-anchor="middle" class="cn-axissub">' + esc(AXIS_LABELS[i]) + '</text>';
    });

    var s = '<svg class="career-svg" width="' + this.cw + '" height="' + this.ch + '" xmlns="' + NS + '">'
      + '<defs>'
      + '<filter id="cnBlur" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="5.5"/></filter>'
      + '<filter id="cnBlurO" x="-120%" y="-120%" width="340%" height="340%"><feGaussianBlur stdDeviation="9"/></filter>'
      + '<marker id="cnArr" markerWidth="11" markerHeight="11" refX="8" refY="4" orient="auto"><path d="M0,0 L9,4 L0,8" fill="#6ee7ff"/></marker>'
      + '<marker id="cnArrC" markerWidth="10" markerHeight="10" refX="7" refY="3.5" orient="auto"><path d="M0,0 L8,3.5 L0,7" fill="#d8b4fe"/></marker>'
      + '</defs>'
      + '<g class="cn-vp">' + g + '</g></svg>';

    this.container.innerHTML = s;
    this.svg = this.container.querySelector('svg');
    this.vp = this.svg.querySelector('.cn-vp');
    this._buildControls();
    this._wire();

    if (this.selectedId && this.nodeStages[this.selectedId]) {
      this._applySelection(this.selectedId);
      this._fitSelection(this.selectedId, false);
    } else {
      this.fitAll(false);
    }
  };

  // ---- 视图变换：平移 / 缩放 ----
  CareerNetwork.prototype._apply = function () {
    if (!this.vp) return;
    this.vp.setAttribute('transform', 'translate(' + this.tx.toFixed(2) + ',' + this.ty.toFixed(2) + ') scale(' + this.scale.toFixed(4) + ')');
    this.vp.classList.toggle('zoom-near', this.scale >= ZOOM_NEAR);
    this.vp.classList.toggle('zoom-far', this.scale < 0.5);
  };

  CareerNetwork.prototype._animateTo = function (tx, ty, s, animate) {
    var self = this;
    if (this._anim) { cancelAnimationFrame(this._anim); this._anim = null; }
    if (animate === false) { this.tx = tx; this.ty = ty; this.scale = s; this._apply(); return; }
    var t0 = performance.now(), d = 460;
    var fx = this.tx, fy = this.ty, fs = this.scale;
    function step(now) {
      var k = Math.min(1, (now - t0) / d), e = 1 - Math.pow(1 - k, 3);
      self.tx = fx + (tx - fx) * e; self.ty = fy + (ty - fy) * e; self.scale = fs + (s - fs) * e;
      self._apply();
      if (k < 1) self._anim = requestAnimationFrame(step); else self._anim = null;
    }
    self._anim = requestAnimationFrame(step);
  };

  CareerNetwork.prototype._fitBox = function (box, area, animate) {
    var s = clamp(Math.min(area.w / box.w, area.h / box.h), MIN_SCALE, MAX_SCALE);
    var tx = area.x + area.w / 2 - (box.x + box.w / 2) * s;
    var ty = area.y + area.h / 2 - (box.y + box.h / 2) * s;
    this._animateTo(tx, ty, s, animate);
  };

  CareerNetwork.prototype.fitAll = function (animate) {
    var box = { x: -140, y: 0, w: CONTENT_W + 170, h: this.contentH };
    var m = 18;
    this._fitBox(box, { x: m, y: m + 44, w: this.cw - m * 2, h: this.ch - m * 2 - 44 }, animate);
  };

  CareerNetwork.prototype._safeArea = function () {
    var cw = this.cw, ch = this.ch;
    if (cw <= 720) return { x: 14, y: 60, w: cw - 28, h: ch - 120 }; // 移动端弹层为全屏遮罩，直接居中
    var panelW = Math.min(460, cw * 0.92);
    var prepH = Math.min(ch * 0.46, 320);
    return { x: 24, y: 70, w: Math.max(220, cw - panelW - 48), h: Math.max(200, ch - prepH - 88) };
  };

  CareerNetwork.prototype._fitSelection = function (id, animate) {
    // 只取「该方向自身的时间轴行 + 起点」作为取景框（不含跨方向行），
    // 这样整条成长路线（4 个阶段）恰好落进安全区、缩放可读，且不被弹层遮挡。
    var ns = this.nodeStages[id];
    if (!ns) return;
    var self = this;
    var node = ns.node;
    var xs = [], ys = [];
    if (this.coord.origin) { xs.push(this.coord.origin.x); ys.push(this.coord.origin.y); }
    this._stagesOf(node).forEach(function (st, i) {
      var c = self.coord[node.tag + '-' + i];
      if (c) { xs.push(c.x); ys.push(c.y); }
    });
    if (!xs.length) return;
    var padX = 70, padY = 120;
    var box = {
      x: Math.min.apply(null, xs) - padX,
      y: Math.min.apply(null, ys) - padY,
      w: (Math.max.apply(null, xs) - Math.min.apply(null, xs)) + padX + 70,
      h: (Math.max.apply(null, ys) - Math.min.apply(null, ys)) + padY * 2
    };
    this._fitBox(box, this._safeArea(), animate);
  };

  CareerNetwork.prototype._zoomAt = function (cx, cy, factor) {
    var ns = clamp(this.scale * factor, MIN_SCALE, MAX_SCALE);
    var k = ns / this.scale;
    this.tx = cx - (cx - this.tx) * k;
    this.ty = cy - (cy - this.ty) * k;
    this.scale = ns;
    if (this._anim) { cancelAnimationFrame(this._anim); this._anim = null; }
    this._apply();
  };

  CareerNetwork.prototype.zoomBy = function (factor) {
    this._zoomAt(this.cw / 2, this.ch / 2, factor);
  };

  // ---- 选择高亮 ----
  CareerNetwork.prototype._related = function (id) {
    var out = {}; out[id] = true;
    var st = [id], x;
    while (st.length) { x = st.pop(); (this.adjB[x] || []).forEach(function (p) { if (!out[p]) { out[p] = true; st.push(p); } }); }
    st = [id];
    while (st.length) { x = st.pop(); (this.adjF[x] || []).forEach(function (p) { if (!out[p]) { out[p] = true; st.push(p); } }); }
    return out;
  };

  CareerNetwork.prototype._applySelection = function (id) {
    if (!this.svg) return;
    var rel = this._related(id);
    var tags = {};
    Object.keys(rel).forEach(function (k) { tags[k.split('-')[0]] = true; });
    this.svg.classList.add('sel');
    this.svg.querySelectorAll('.cn-node').forEach(function (e) { e.classList.toggle('hot', !!rel[e.dataset.id]); });
    this.svg.querySelectorAll('.cn-edge').forEach(function (e) { e.classList.toggle('hot', !!(rel[e.dataset.from] && rel[e.dataset.to])); });
    this.svg.querySelectorAll('.cn-nlabel').forEach(function (e) { e.classList.toggle('hot', !!tags[e.dataset.tag]); });
  };

  CareerNetwork.prototype.select = function (id) {
    this.selectedId = id || null;
    if (!this.svg) return;
    if (!id) { this.clear(); return; }
    this._applySelection(id);
    this._fitSelection(id, true);
  };

  CareerNetwork.prototype.clear = function () {
    var had = this.selectedId;
    this.selectedId = null;
    if (!this.svg) return;
    this.svg.classList.remove('sel');
    this.svg.querySelectorAll('.hot').forEach(function (e) { e.classList.remove('hot'); });
    this._hideTip();
    if (had) this.fitAll(true);
  };

  // ---- 缩放控件 ----
  CareerNetwork.prototype._buildControls = function () {
    var self = this;
    var host = this.container.parentElement || this.container;
    var old = host.querySelector('.career-zoom');
    if (old) old.remove();
    var bar = document.createElement('div');
    bar.className = 'career-zoom';
    bar.innerHTML = '<button type="button" data-act="in" aria-label="放大">＋</button>'
      + '<button type="button" data-act="out" aria-label="缩小">－</button>'
      + '<button type="button" data-act="fit" aria-label="全景复位">⤢</button>';
    bar.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
    bar.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      if (b.dataset.act === 'in') self.zoomBy(1.25);
      else if (b.dataset.act === 'out') self.zoomBy(0.8);
      else { self.selectedId = null; self.svg.classList.remove('sel'); self.svg.querySelectorAll('.hot').forEach(function (x) { x.classList.remove('hot'); }); self.fitAll(true); self.onClear(); }
    });
    host.appendChild(bar);
    this.controls = bar;
  };

  // ---- 指针 / 滚轮 ----
  CareerNetwork.prototype._wire = function () {
    var self = this;
    var sv = this.svg;
    sv.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      var r = sv.getBoundingClientRect();
      self._drag = { x: e.clientX - r.left, y: e.clientY - r.top, tx: self.tx, ty: self.ty, moved: false, id: e.pointerId };
      self._hideTip();
      if (sv.setPointerCapture) { try { sv.setPointerCapture(e.pointerId); } catch (_) {} }
    });
    sv.addEventListener('pointermove', function (e) {
      if (!self._drag) return;
      var r = sv.getBoundingClientRect();
      var dx = (e.clientX - r.left) - self._drag.x;
      var dy = (e.clientY - r.top) - self._drag.y;
      if (!self._drag.moved && dx * dx + dy * dy > 25) { self._drag.moved = true; sv.classList.add('grabbing'); }
      if (self._drag.moved) {
        if (self._anim) { cancelAnimationFrame(self._anim); self._anim = null; }
        self.tx = self._drag.tx + dx; self.ty = self._drag.ty + dy; self._apply();
      }
    });
    sv.addEventListener('pointerup', function (e) {
      var d = self._drag; self._drag = null; sv.classList.remove('grabbing');
      if (sv.releasePointerCapture) { try { sv.releasePointerCapture(e.pointerId); } catch (_) {} }
      if (d && d.moved) return; // 拖动后不触发点选
      var g = e.target.closest('.cn-node');
      if (g && g.dataset.id !== 'origin') {
        self.selectedId = g.dataset.id;
        self._applySelection(g.dataset.id);
        self._fitSelection(g.dataset.id, true);
        var ns = self.nodeStages[g.dataset.id];
        if (ns) {
          var st = self._stagesOf(ns.node)[ns.stage] || [];
          self.onSelect(ns.node, { phase: st[0] || '', role: st[1] || ns.node.name, sdesc: st[2] || '', stage: ns.stage });
        }
      } else if (!g) {
        self.clear();
        self.onClear();
      }
    });
    sv.addEventListener('pointercancel', function () { self._drag = null; sv.classList.remove('grabbing'); });
    sv.addEventListener('wheel', function (e) {
      e.preventDefault();
      var r = sv.getBoundingClientRect();
      var factor = Math.pow(1.0015, -e.deltaY);
      self._zoomAt(e.clientX - r.left, e.clientY - r.top, factor);
    }, { passive: false });
    sv.addEventListener('dblclick', function (e) {
      e.preventDefault();
      self.selectedId = null; sv.classList.remove('sel');
      sv.querySelectorAll('.hot').forEach(function (x) { x.classList.remove('hot'); });
      self.fitAll(true); self.onClear();
    });
    sv.addEventListener('mouseover', function (e) { if (!self._drag) self._hoverTip(e); });
    sv.addEventListener('mousemove', function (e) { if (!self._drag && self.tip && self.tip.classList.contains('show')) self._posTip(e); });
    sv.addEventListener('mouseout', function (e) { if (e.target.classList && e.target.classList.contains('cn-core')) self._hideTip(); });
  };

  // ---- 浮窗 ----
  CareerNetwork.prototype._hoverTip = function (e) {
    if (!this.tip) return;
    var t = e.target;
    if (!t.classList || !t.classList.contains('cn-core')) return;
    if (t.dataset.origin) {
      this.tip.innerHTML = '<div class="cn-tcat">起点 · 现在</div><div class="cn-tname">' + esc(this.opts.originLabel || '🎓 毕业') + '</div>'
        + '<div class="cn-tdesc">向右就是你的未来。点击任一节点可看它的来路与全部下游分支，并展开为你定制的建议与必备知识。滚轮缩放 · 拖动平移 · 双击复位。</div>';
      this.tip.classList.add('show'); this._posTip(e); return;
    }
    var n = this.byTag[t.dataset.tag];
    if (!n) return;
    var i = +t.dataset.i;
    var st = this._stagesOf(n)[i] || [];
    var catName = '';
    (this.network.cats || []).forEach(function (c) { if (c.id === n.cat) catName = (c.icon ? c.icon + ' ' : '') + c.name; });
    var tipExtra = n.tip ? '<div class="cn-ttip">💡 ' + esc(n.tip) + '</div>' : '';
    this.tip.innerHTML = '<div class="cn-tcat">' + esc(catName || n.cat) + ' · ' + esc(n.tag) + '</div>'
      + '<div class="cn-tname">' + (n.lang ? '⭐ ' : '') + esc(n.name) + '</div>'
      + '<div class="cn-tstars">' + stars(n.rec) + '　推荐度 ' + (n.rec || 0) + '/5'
      + (n.base_rec && n.base_rec !== n.rec ? '（已按你的特质调整）' : '') + '</div>'
      + '<div class="cn-trow"><span class="cn-tphase">' + esc(st[0] || '成长阶段') + '</span> <b>' + esc(st[1] || n.name) + '</b>'
      + (st[2] && st[2] !== '—' ? '<br>' + esc(st[2]) : '') + '</div>'
      + (n.desc ? '<div class="cn-tdesc">' + esc(n.desc) + '</div>' : '')
      + tipExtra;
    this.tip.classList.add('show');
    this._posTip(e);
  };

  CareerNetwork.prototype._posTip = function (e) {
    if (!this.tip) return;
    var pad = 16, w = this.tip.offsetWidth || 276, h = this.tip.offsetHeight || 150;
    var x = e.clientX + 18, y = e.clientY + 14;
    if (x + w + pad > window.innerWidth) x = e.clientX - w - 18;
    if (y + h + pad > window.innerHeight) y = window.innerHeight - h - pad;
    if (x < pad) x = pad;
    if (y < pad) y = pad;
    this.tip.style.left = x + 'px';
    this.tip.style.top = y + 'px';
  };

  CareerNetwork.prototype._hideTip = function () { if (this.tip) this.tip.classList.remove('show'); };

  CareerNetwork.prototype._bind = function () {
    if (this._bound) return;
    this._bound = true;
    var self = this;
    this._onResize = function () {
      if (self._resizeRAF) cancelAnimationFrame(self._resizeRAF);
      self._resizeRAF = requestAnimationFrame(function () {
        var rect = self.container.getBoundingClientRect();
        self.cw = rect.width || self.cw; self.ch = rect.height || self.ch;
        if (self.svg) { self.svg.setAttribute('width', self.cw); self.svg.setAttribute('height', self.ch); }
        if (self.selectedId && self.nodeStages[self.selectedId]) self._fitSelection(self.selectedId, false);
        else self.fitAll(false);
      });
    };
    window.addEventListener('resize', this._onResize);
  };

  CareerNetwork.prototype.destroy = function () {
    if (this._onResize) window.removeEventListener('resize', this._onResize);
    if (this._anim) cancelAnimationFrame(this._anim);
    this._bound = false;
    if (this.controls && this.controls.parentNode) this.controls.parentNode.removeChild(this.controls);
    if (this.container) this.container.innerHTML = '';
    this._hideTip();
  };

  window.CareerNetwork = CareerNetwork;
})();
