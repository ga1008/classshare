/* 职业发展网络 · 时间轴 SVG 网络图（无依赖，模板化）
 *
 * 还原参考版「职业发展网络图」的横向时间轴样式，并以专业数据为模板套用到任意专业：
 *   - 左侧 = 现在（毕业起点），向右沿时间轴展开到 10 年以后。
 *   - 每个就业大类(cat)一组，纵向堆叠；该类下所有方向(node)各占一行，4 个成长阶段(tl)沿时间轴排开。
 *   - 节点越亮＝越推荐 / 越契合（由后端按个人分析给出的 rec 与 dim_glow 决定亮度与闪烁）。
 *   - 点击任意节点：高亮其来路 + 全部下游分支 + 可转向(links)，其余变暗；点击空白复位。
 *   - 悬停显示信息浮窗；点击节点回调 onSelect(node, stage) 让页面弹出定制详情与必备知识。
 *
 * 暴露 window.CareerNetwork。
 *   new CareerNetwork(container, { onSelect(node,stage), onClear(), tipEl })
 *   .setData(network, personalized) / .select(id) / .clear() / .destroy()
 */
(function () {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var MAX_STAGES = 4;
  var DEFAULT_TIME_LABELS = ['0–1 年 · 现在', '3–5 年', '5–10 年', '10 年以后 · 未来'];
  var AXIS_LABELS = ['0–1 年', '3–5 年', '5–10 年', '10 年 +'];

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m];
    });
  }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function stars(r) { r = clamp(r | 0, 0, 5); return '★★★★★'.slice(0, r) + '☆☆☆☆☆'.slice(0, 5 - r); }

  function CareerNetwork(container, opts) {
    this.container = typeof container === 'string' ? document.getElementById(container) : container;
    this.opts = opts || {};
    this.onSelect = this.opts.onSelect || function () {};
    this.onClear = this.opts.onClear || function () {};
    this.tip = this.opts.tipEl || document.getElementById('career-tip');

    this.network = { cats: [], nodes: [], links: [] };
    this.personalized = {};
    this.byTag = {};
    this.nodeStages = {};       // id "tag-i" -> {node, stage}
    this.adjF = {};
    this.adjB = {};
    this.selectedId = null;

    this.svg = null;
    this._bound = false;
    this._onResize = null;
    this._resizeRAF = null;
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
    this.byTag = {};
    this.nodeStages = {};
    this.adjF = {};
    this.adjB = {};
    (this.network.nodes || []).forEach(function (n) { if (n.tag) self.byTag[n.tag] = n; });

    function link(from, to) {
      (self.adjF[from] = self.adjF[from] || []).push(to);
      (self.adjB[to] = self.adjB[to] || []).push(from);
    }
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
    // 容错：节点里出现但 cats 未声明的大类
    (this.network.nodes || []).forEach(function (n) {
      if (n.cat && !seen[n.cat]) { seen[n.cat] = true; cats.push({ id: n.cat, name: n.cat, c1: '#6ee7ff', icon: '✨' }); }
    });
    return cats;
  };

  // 每个时间列的代表标签：取该列最常见的 tl[i][0]，否则用默认。
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
    // dim_glow（个人分析的发光强度）优先；否则用 rec 推导。
    if (typeof node.glow === 'number' && isFinite(node.glow)) return clamp(node.glow, 0.12, 1);
    var rec = clamp(Number(node.rec || 3), 1, 5);
    return clamp(0.18 + rec * 0.16, 0.2, 1);
  };

  CareerNetwork.prototype._render = function () {
    var self = this;
    var cw = (this.container && this.container.clientWidth) || window.innerWidth || 1120;
    var W = Math.max(1060, Math.min(1680, cw - 8));
    var originX = Math.round(W * 0.052) + 18;
    var firstCol = Math.round(W * 0.215);
    var lastCol = W - Math.round(W * 0.115);
    var stageX = [0, 1, 2, 3].map(function (i) { return Math.round(firstCol + (lastCol - firstCol) * (i / 3)); });
    var rowH = 44, catGap = 16, catHeadH = 34, topPad = 66;

    var cats = this._catMeta();
    var colorOf = {};
    cats.forEach(function (c) { colorOf[c.id] = c.c1 || '#6ee7ff'; });

    // 布局：分类标题 + 行
    var y = topPad;
    var heads = [];
    var rows = [];
    cats.forEach(function (c) {
      var list = (self.network.nodes || []).filter(function (n) { return n.cat === c.id; });
      if (!list.length) return;
      list = list.slice().sort(function (a, b) { return (b.rec || 0) - (a.rec || 0); });
      heads.push({ cat: c, y: y + 20 });
      y += catHeadH;
      list.forEach(function (n) { n._y = y; rows.push(n); y += rowH; });
      y += catGap;
    });
    var bodyBottom = y;
    var H = bodyBottom + 78;
    var originY = (topPad + catHeadH + (bodyBottom - catGap)) / 2;
    var axisY = H - 50;

    var coord = { origin: { x: originX, y: originY } };
    rows.forEach(function (n) {
      self._stagesOf(n).forEach(function (st, i) { coord[n.tag + '-' + i] = { x: stageX[i], y: n._y }; });
    });

    var timeLab = this._columnLabels();

    var s = '<svg class="career-svg" viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" xmlns="' + NS + '">';
    s += '<defs>'
      + '<filter id="cnBlur" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="5.5"/></filter>'
      + '<filter id="cnBlurO" x="-120%" y="-120%" width="340%" height="340%"><feGaussianBlur stdDeviation="9"/></filter>'
      + '<marker id="cnArr" markerWidth="11" markerHeight="11" refX="8" refY="4" orient="auto"><path d="M0,0 L9,4 L0,8" fill="#6ee7ff"/></marker>'
      + '<marker id="cnArrC" markerWidth="10" markerHeight="10" refX="7" refY="3.5" orient="auto"><path d="M0,0 L8,3.5 L0,7" fill="#d8b4fe"/></marker>'
      + '</defs>';

    // 时间竖向网格 + 顶部时间标签
    stageX.forEach(function (x, i) {
      s += '<line x1="' + x + '" y1="' + (topPad - 30) + '" x2="' + x + '" y2="' + axisY + '" stroke="rgba(255,255,255,.05)" stroke-width="1"/>';
      s += '<text x="' + x + '" y="' + (topPad - 36) + '" text-anchor="middle" class="cn-axislab">' + esc(timeLab[i]) + '</text>';
    });

    // 分类标题
    heads.forEach(function (h) {
      var label = (h.cat.icon ? h.cat.icon + ' ' : '') + (h.cat.name || h.cat.id);
      s += '<text x="18" y="' + h.y + '" class="cn-catlabel" fill="' + esc(h.cat.c1 || '#6ee7ff') + '">' + esc(label) + '</text>';
      s += '<line x1="18" y1="' + (h.y + 8) + '" x2="' + (W - 20) + '" y2="' + (h.y + 8) + '" stroke="' + esc(h.cat.c1 || '#6ee7ff') + '" stroke-opacity=".12" stroke-width="1"/>';
    });

    // 边：扇出（origin → 每个方向第一阶段）
    rows.forEach(function (n) {
      var a = coord.origin, b = coord[n.tag + '-0'];
      if (!b) return;
      var c1x = a.x + 90, c2x = b.x - 90;
      s += '<g class="cn-edge fan" data-from="origin" data-to="' + n.tag + '-0">'
        + '<path d="M' + a.x + ' ' + a.y + ' C ' + c1x + ' ' + a.y + ', ' + c2x + ' ' + b.y + ', ' + b.x + ' ' + b.y + '" '
        + 'fill="none" stroke="rgba(110,231,255,.12)" stroke-width="1.2"/></g>';
    });

    // 边：主成长线（分段，带流动虚线）
    rows.forEach(function (n) {
      var col = colorOf[n.cat] || '#6ee7ff';
      var rec = clamp(Number(n.rec || 3), 1, 5);
      var op = rec >= 5 ? 0.9 : rec >= 4 ? 0.62 : 0.36;
      var stages = self._stagesOf(n);
      for (var i = 0; i < stages.length - 1; i++) {
        var a = coord[n.tag + '-' + i], b = coord[n.tag + '-' + (i + 1)];
        s += '<g class="cn-edge main" data-from="' + n.tag + '-' + i + '" data-to="' + n.tag + '-' + (i + 1) + '">'
          + '<line x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '" stroke="' + col + '" stroke-opacity="' + (op * 0.38) + '" stroke-width="2"/>'
          + '<line class="cn-flow" x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '" stroke="' + col + '" stroke-opacity="' + (op * 0.8) + '" stroke-width="2"/></g>';
      }
    });

    // 边：跨方向分叉（暗底 + 亮紫虚线 + 箭头）
    (this.network.links || []).forEach(function (l) {
      if (!Array.isArray(l) || l.length < 4) return;
      var a = coord[l[0] + '-' + l[1]], b = coord[l[2] + '-' + l[3]];
      if (!a || !b) return;
      var mx = (a.x + b.x) / 2;
      var d = 'M' + a.x + ' ' + a.y + ' C ' + mx + ' ' + a.y + ', ' + mx + ' ' + b.y + ', ' + b.x + ' ' + b.y;
      s += '<g class="cn-edge cross" data-from="' + l[0] + '-' + l[1] + '" data-to="' + l[2] + '-' + l[3] + '">'
        + '<path d="' + d + '" fill="none" stroke="#0a0f1d" stroke-opacity=".85" stroke-width="4.5"/>'
        + '<path class="cn-cu" d="' + d + '" fill="none" stroke="#d8b4fe" stroke-opacity=".7" stroke-width="1.8" marker-end="url(#cnArrC)"/></g>';
    });

    // 起点
    var gradLabel = this.opts.originLabel || '🎓 毕业';
    s += '<g class="cn-node" data-id="origin">'
      + '<circle class="cn-origin-h" cx="' + originX + '" cy="' + originY + '" r="34" fill="#fbbf24" opacity=".30" filter="url(#cnBlurO)"/>'
      + '<circle class="cn-core" data-origin="1" cx="' + originX + '" cy="' + originY + '" r="13" fill="#fde68a" stroke="#fff" stroke-width="1.5"/></g>';
    s += '<text x="' + originX + '" y="' + (originY + 30) + '" text-anchor="middle" class="cn-origin-t">' + esc(gradLabel) + '</text>';
    s += '<text x="' + originX + '" y="' + (originY + 45) + '" text-anchor="middle" class="cn-origin-s">起点 · 现在</text>';

    // 节点（每个方向的方向名 + 各阶段星）
    rows.forEach(function (n) {
      var col = colorOf[n.cat] || '#6ee7ff';
      var rec = clamp(Number(n.rec || 3), 1, 5);
      var bright = self._brightness(n);
      var hot = !!n.highlighted;
      s += '<text class="cn-nlabel" data-tag="' + n.tag + '" x="' + (stageX[0] - 14) + '" y="' + (n._y - 13) + '" fill="' + col + '">'
        + (n.lang ? '⭐ ' : '') + esc(n.name) + (hot ? ' ✦' : '') + '</text>';
      self._stagesOf(n).forEach(function (st, i) {
        var x = stageX[i], yy = n._y;
        var haloR = 9 + rec * 2.4 + (hot ? 3 : 0);
        var haloOp = clamp(bright * 0.85, 0.12, 0.95);
        var coreR = 4.2 + rec * 0.9;
        var flick = bright >= 0.8 ? 'cn-flick5' : bright >= 0.55 ? 'cn-flick4' : '';
        s += '<g class="cn-node" data-id="' + n.tag + '-' + i + '" data-tag="' + n.tag + '">'
          + '<circle class="cn-halo ' + flick + '" cx="' + x + '" cy="' + yy + '" r="' + haloR + '" fill="' + col + '" opacity="' + haloOp + '" filter="url(#cnBlur)"/>'
          + (hot ? '<circle class="cn-ring" cx="' + x + '" cy="' + yy + '" r="' + (coreR + 5) + '" fill="none" stroke="#fff" stroke-opacity=".7" stroke-width="1.1"/>' : '')
          + '<circle class="cn-core" cx="' + x + '" cy="' + yy + '" r="' + coreR + '" fill="' + col + '" fill-opacity="' + (rec >= 3 ? 1 : 0.7) + '" '
          + 'stroke="#fff" stroke-opacity="' + (bright >= 0.8 ? 0.9 : 0.5) + '" stroke-width="1.3" data-tag="' + n.tag + '" data-i="' + i + '"/></g>';
      });
    });

    // 时间轴
    s += '<line x1="' + originX + '" y1="' + axisY + '" x2="' + (W - 26) + '" y2="' + axisY + '" stroke="#6ee7ff" stroke-opacity=".5" stroke-width="2" marker-end="url(#cnArr)"/>';
    s += '<text x="' + originX + '" y="' + (axisY + 22) + '" text-anchor="middle" class="cn-axisnow">现在</text>';
    s += '<text x="' + (W - 30) + '" y="' + (axisY + 22) + '" text-anchor="end" class="cn-axislab">未来 →</text>';
    stageX.forEach(function (x, i) {
      s += '<circle cx="' + x + '" cy="' + axisY + '" r="4" fill="#6ee7ff"/>';
      s += '<text x="' + x + '" y="' + (axisY + 22) + '" text-anchor="middle" class="cn-axissub">' + esc(AXIS_LABELS[i]) + '</text>';
    });

    s += '</svg>';
    this.container.innerHTML = s;
    this.svg = this.container.querySelector('svg');
    this._wire();
    if (this.selectedId && this.nodeStages[this.selectedId]) this._applySelection(this.selectedId);
  };

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
  };

  CareerNetwork.prototype.clear = function () {
    this.selectedId = null;
    if (!this.svg) return;
    this.svg.classList.remove('sel');
    this.svg.querySelectorAll('.hot').forEach(function (e) { e.classList.remove('hot'); });
    this._hideTip();
  };

  CareerNetwork.prototype._wire = function () {
    var self = this;
    this.svg.addEventListener('click', function (e) {
      var g = e.target.closest('.cn-node');
      if (g && g.dataset.id !== 'origin') {
        self.selectedId = g.dataset.id;
        self._applySelection(g.dataset.id);
        var ns = self.nodeStages[g.dataset.id];
        if (ns) {
          var st = self._stagesOf(ns.node)[ns.stage] || [];
          self.onSelect(ns.node, { phase: st[0] || '', role: st[1] || ns.node.name, sdesc: st[2] || '', stage: ns.stage });
        }
      } else {
        self.clear();
        self.onClear();
      }
    });
    this.svg.addEventListener('mouseover', function (e) { self._hoverTip(e); });
    this.svg.addEventListener('mousemove', function (e) { if (self.tip && self.tip.classList.contains('show')) self._posTip(e); });
    this.svg.addEventListener('mouseout', function (e) {
      if (e.target.classList && e.target.classList.contains('cn-core')) self._hideTip();
    });
  };

  CareerNetwork.prototype._hoverTip = function (e) {
    if (!this.tip) return;
    var t = e.target;
    if (!t.classList || !t.classList.contains('cn-core')) return;
    if (t.dataset.origin) {
      this.tip.innerHTML = '<div class="cn-tcat">起点 · 现在</div><div class="cn-tname">' + esc(this.opts.originLabel || '🎓 毕业') + '</div>'
        + '<div class="cn-tdesc">站在这里，向右就是你的未来。点击任一节点可看它的来路与全部下游分支，并展开为你定制的建议与必备知识。</div>';
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
    var pad = 16, w = this.tip.offsetWidth || 272, h = this.tip.offsetHeight || 150;
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
      self._resizeRAF = requestAnimationFrame(function () { self._render(); });
    };
    window.addEventListener('resize', this._onResize);
  };

  CareerNetwork.prototype.destroy = function () {
    if (this._onResize) window.removeEventListener('resize', this._onResize);
    this._bound = false;
    if (this.container) this.container.innerHTML = '';
    this._hideTip();
  };

  window.CareerNetwork = CareerNetwork;
})();
