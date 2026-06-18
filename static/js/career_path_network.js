/* 职业发展网络 · 银河系式 3D 动态星系（Canvas2D，无依赖）
 *
 * 模型：把"现在→未来"的时间线卷进极坐标。
 *   - 星系中心 = 现在/起点；半径 = 时间（内圈 0–1 年 → 外圈 10 年+，带浮动）。
 *   - 角度 = 就业方向（按大类分组成扇区，整圈铺满该专业所有方向）。
 *   - 每个方向是一条由内向外的"成长线"：它的 4 个阶段是 4 颗星，连成一条径向旋臂。
 *   - 星越亮 = 推荐度越高；不推荐的也在场、可点。背景密布同款星尘 + 模糊星云，整体缓慢自转。
 *   - 点击一颗星：从中心连出亮线，点亮"能发展到它的所有前序 + 它之后所有可发展方向"，
 *     其余变暗，并在每颗高亮星旁弹出信息卡（去重叠）。点空白复位。
 *   - 滚轮缩放只改变星与星的"间距"（半径），星/卡片/连线粗细都不变大。
 *
 * 暴露 window.CareerNetwork。回调：onHighlight(cards)、onBackground()、onHover(info|null)、onOpenDetail(tag)。
 */
(function () {
  'use strict';

  var TAU = Math.PI * 2;
  var TILT = 0.92;
  var COS_T = Math.cos(TILT), SIN_T = Math.sin(TILT);
  var ROT_SPEED = 0.025;
  var RINGS = [150, 280, 410, 540];      // 时间环基础半径（缩放前）
  var RING_LABELS = ['0–1 年', '3–5 年', '5–10 年', '10 年+'];
  var TWIST = 0.0013;                     // 旋臂扭转
  var ORIGIN_ID = '__origin__';

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function hexToRgb(hex) {
    var h = String(hex || '#6ee7ff').replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }
  function rgba(c, a) { return 'rgba(' + c.r + ',' + c.g + ',' + c.b + ',' + a + ')'; }

  function CareerNetwork(canvas, options) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.opts = options || {};
    this.onHighlight = this.opts.onHighlight || function () {};
    this.onBackground = this.opts.onBackground || function () {};
    this.onHover = this.opts.onHover || function () {};

    this.dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
    this.W = 0; this.H = 0;
    this.spacing = 1; this.targetSpacing = 1;       // 缩放只改这个（星距）
    this.pan = { x: 0, y: 0 };
    this.rotation = 0;
    this.stars = [];          // 阶段星
    this.bg = [];             // 背景星尘
    this.nebula = [];         // 模糊星云
    this.edges = [];
    this.origin = null;
    this.selectedId = null;
    this.related = null;
    this.hoverId = null;
    this.timeSec = 0;
    this._raf = null;

    this.drag = { panning: false, lastX: 0, lastY: 0, moved: false, downX: 0, downY: 0 };
    this.pointer = { x: 0, y: 0, inside: false };

    this._bind();
    this.resize();
  }

  CareerNetwork.prototype._hash = function (str) {
    var h = 2166136261; str = String(str || '');
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = (h * 16777619) >>> 0; }
    return h >>> 0;
  };

  CareerNetwork.prototype.setData = function (network, personalized) {
    var self = this;
    var cats = network.cats || [];
    var dirs = network.nodes || [];
    var links = network.links || [];

    this.catColor = {};
    this.catMeta = {};
    cats.forEach(function (c) {
      self.catColor[c.id] = hexToRgb(c.c1 || '#6ee7ff');
      self.catMeta[c.id] = { id: c.id, name: c.name || c.id, color: hexToRgb(c.c1 || '#6ee7ff') };
    });

    // 方向按大类分组排序，整圈铺开成扇区
    var catOrder = cats.map(function (c) { return c.id; });
    if (!catOrder.length) catOrder = dirs.map(function (d) { return d.cat; }).filter(function (v, i, a) { return a.indexOf(v) === i; });
    var ordered = [];
    catOrder.forEach(function (cid) { dirs.forEach(function (d) { if (d.cat === cid) ordered.push(d); }); });
    dirs.forEach(function (d) { if (ordered.indexOf(d) < 0) ordered.push(d); });

    var nDir = ordered.length || 1;
    this.sectors = [];
    var cursor = 0;
    catOrder.forEach(function (cid) {
      var count = ordered.filter(function (d) { return d.cat === cid; }).length;
      if (!count) return;
      self.sectors.push({
        id: cid,
        start: (cursor / nDir) * TAU,
        end: ((cursor + count) / nDir) * TAU,
        mid: ((cursor + count / 2) / nDir) * TAU,
        meta: self.catMeta[cid] || { id: cid, name: cid, color: hexToRgb('#6ee7ff') }
      });
      cursor += count;
    });

    this.stars = [];
    this.byId = {};
    var dirAngle = {};
    ordered.forEach(function (d, di) {
      var base = (di / nDir) * TAU;
      dirAngle[d.tag] = base;
      var col = self.catColor[d.cat] || hexToRgb('#6ee7ff');
      var rec = d.rec || 3;
      var dirGlow = (typeof d.glow === 'number') ? d.glow
        : (rec >= 5 ? 0.95 : rec >= 4 ? 0.62 : rec >= 3 ? 0.4 : 0.24);
      dirGlow = clamp(dirGlow, 0.14, 1);
      var tl = d.tl || [];
      var stageCount = Math.max(1, Math.min(tl.length, 4));
      for (var s = 0; s < stageCount; s++) {
        var seed = self._hash(d.tag + '-' + s);
        var baseR = RINGS[Math.min(s, RINGS.length - 1)];
        var r = baseR + (((seed % 1000) / 1000 - 0.5) * 56);
        var theta0 = base + r * TWIST + (((seed >>> 7) % 1000) / 1000 - 0.5) * 0.10;
        var hh = (((seed >>> 11) % 1000) / 1000 - 0.5) * 44;
        var st = tl[s] || ['', d.name, ''];
        var star = {
          id: d.tag + '-' + s, tag: d.tag, stage: s, dir: d,
          name: d.name, cat: d.cat, rec: rec,
          phase: st[0] || '', role: st[1] || d.name, sdesc: st[2] || '',
          color: col, lang: !!d.lang, highlighted: !!d.highlighted,
          glow: clamp(dirGlow * (1 - s * 0.05), 0.12, 1),
          r: r, theta0: theta0, h: hh,
          core: 1.3 + rec * 0.5, twPhase: (seed % 628) / 100, twSpeed: 0.5 + ((seed >>> 5) % 100) / 80,
          sx: 0, sy: 0, depth: 0
        };
        self.stars.push(star);
        self.byId[star.id] = star;
      }
    });

    // 起点（现在）
    this.origin = {
      id: ORIGIN_ID, name: this.opts.hubLabel || '起点 · 现在', isOrigin: true,
      r: 0, theta0: 0, h: 0, color: hexToRgb('#fde68a'), glow: 1, core: 7,
      twPhase: 0, twSpeed: 0.7, sx: 0, sy: 0, depth: 0
    };

    // 背景星尘（密集、与方向星同款审美，只是更小更暗、不可交互）
    this.bg = [];
    var BG = 980;
    for (var i = 0; i < BG; i++) {
      var b = self._hash('bg' + i);
      var u = (b % 1000) / 1000;
      var v = ((b >>> 10) % 1000) / 1000;
      var rr = 24 + Math.pow(u, 0.72) * (RINGS[3] + 170);
      var arm = b % 5;
      var spiral = arm * TAU / 5 + rr * TWIST * 1.35 + (v - 0.5) * (0.42 + rr / 950);
      var theta = (b % 4 === 0) ? ((b >>> 6) % 6283) / 1000 : spiral;
      this.bg.push({
        r: rr, theta0: theta, h: (((b >>> 11) % 1000) / 1000 - 0.5) * 90,
        glow: 0.05 + ((b >>> 3) % 100) / 240, core: 0.5 + ((b >>> 5) % 100) / 95,
        twPhase: (b % 628) / 100, twSpeed: 0.4 + ((b >>> 8) % 100) / 70,
        warm: (b % 9 === 0), tint: (b % 4 === 0)
      });
    }

    // 模糊星云
    this.nebula = [];
    var NEB = [['#3b6fd8', 0.10], ['#7c3aed', 0.09], ['#0f766e', 0.07], ['#b45309', 0.06]];
    for (var k = 0; k < NEB.length; k++) {
      var nb = self._hash('neb' + k);
      this.nebula.push({
        r: 120 + (nb % 360), theta0: (nb % 6283) / 1000, h: 0,
        rad: 220 + (nb % 200), color: hexToRgb(NEB[k][0]), alpha: NEB[k][1]
      });
    }

    // 边 + 邻接（阶段级图）
    this.edges = []; this.adjF = {}; this.adjB = {};
    function link(from, to, kind) {
      self.edges.push({ from: from, to: to, kind: kind });
      (self.adjF[from] = self.adjF[from] || []).push(to);
      (self.adjB[to] = self.adjB[to] || []).push(from);
    }
    ordered.forEach(function (d) {
      var tl = d.tl || [];
      var sc = Math.max(1, Math.min(tl.length, 4));
      link(ORIGIN_ID, d.tag + '-0', 'fan');
      for (var s = 0; s < sc - 1; s++) link(d.tag + '-' + s, d.tag + '-' + (s + 1), 'main');
    });
    links.forEach(function (l) {
      var from = l[0] + '-' + l[1], to = l[2] + '-' + l[3];
      if (self.byId[from] && self.byId[to]) link(from, to, 'cross');
    });

    this.fitView();
    this.start();
  };

  CareerNetwork.prototype.fitView = function () {
    var R = RINGS[3] + 70;
    var sx = (this.W * 0.46) / R;
    var sy = (this.H * 0.44) / (R * COS_T);
    this.spacing = this.targetSpacing = clamp(Math.min(sx, sy), 0.3, 2.2);
    this.pan.x = 0; this.pan.y = this.H * 0.04;
  };

  // 极坐标(r,θ,h) → 自转 → 倾斜 → 屏幕。缩放只作用于半径(间距)，不作用于元素尺寸。
  CareerNetwork.prototype._project = function (o) {
    var r = o.r * this.spacing;
    var th = o.theta0 + this.rotation;
    var x = r * Math.cos(th);
    var y0 = r * Math.sin(th);
    var hz = o.h * this.spacing;
    var ty = y0 * COS_T - hz * SIN_T;
    o.depth = y0 * SIN_T + hz * COS_T;
    o.sx = this.W / 2 + this.pan.x + x;
    o.sy = this.H / 2 + this.pan.y + ty;
    return o;
  };

  CareerNetwork.prototype.resize = function () {
    var rect = this.canvas.getBoundingClientRect();
    this.W = rect.width; this.H = rect.height;
    this.canvas.width = Math.round(this.W * this.dpr);
    this.canvas.height = Math.round(this.H * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    if (this.selectedId && this.related) this._emitHighlight();
  };

  CareerNetwork.prototype._bind = function () {
    var self = this, c = this.canvas;
    this._onResize = function () { self.resize(); };
    window.addEventListener('resize', this._onResize);
    c.addEventListener('pointerdown', function (e) { self._down(e); });
    c.addEventListener('pointermove', function (e) { self._move(e); });
    window.addEventListener('pointerup', function (e) { self._up(e); });
    c.addEventListener('pointerleave', function () { self.pointer.inside = false; self.hoverId = null; self.onHover(null); });
    c.addEventListener('wheel', function (e) { self._wheel(e); }, { passive: false });
  };

  CareerNetwork.prototype._localPos = function (e) {
    var rect = this.canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  CareerNetwork.prototype._pick = function (sx, sy) {
    var best = null, bestD = 1e9;
    for (var i = 0; i < this.stars.length; i++) {
      var n = this.stars[i];
      var rr = n.core + 10;
      var dx = sx - n.sx, dy = sy - n.sy, d = dx * dx + dy * dy;
      if (d < rr * rr && d < bestD) { best = n; bestD = d; }
    }
    return best;
  };

  CareerNetwork.prototype._down = function (e) {
    this.canvas.setPointerCapture && this.canvas.setPointerCapture(e.pointerId);
    var p = this._localPos(e);
    this.drag.downX = p.x; this.drag.downY = p.y; this.drag.lastX = p.x; this.drag.lastY = p.y;
    this.drag.moved = false; this.drag.panning = true;
    this.pointer.x = p.x; this.pointer.y = p.y; this.pointer.inside = true;
  };

  CareerNetwork.prototype._move = function (e) {
    var p = this._localPos(e);
    this.pointer.x = p.x; this.pointer.y = p.y; this.pointer.inside = true;
    var dx = p.x - this.drag.downX, dy = p.y - this.drag.downY;
    if (dx * dx + dy * dy > 25) this.drag.moved = true;
    if (this.drag.panning && this.drag.moved) {
      this.pan.x += (p.x - this.drag.lastX);
      this.pan.y += (p.y - this.drag.lastY);
      if (this.selectedId) this._emitHighlight();
    }
    this.drag.lastX = p.x; this.drag.lastY = p.y;
    if (!this.drag.moved) {
      var hit = this._pick(p.x, p.y);
      var id = hit ? hit.id : null;
      if (id !== this.hoverId) {
        this.hoverId = id;
        this.canvas.style.cursor = hit ? 'pointer' : 'grab';
        this.onHover(hit ? { name: hit.name, phase: hit.phase, role: hit.role, rec: hit.rec, x: hit.sx, y: hit.sy } : null);
      } else if (hit) {
        this.onHover({ name: hit.name, phase: hit.phase, role: hit.role, rec: hit.rec, x: hit.sx, y: hit.sy });
      }
    }
  };

  CareerNetwork.prototype._up = function () {
    if (!this.drag.moved) {
      var hit = this._pick(this.pointer.x, this.pointer.y);
      if (hit) { this.select(hit.id); }
      else { this.select(null); this.onBackground(); }
    }
    this.drag.panning = false;
  };

  CareerNetwork.prototype._wheel = function (e) {
    e.preventDefault();
    var p = this._localPos(e);
    // 以光标为锚心缩放"间距"：保持光标下的世界点不动
    var wx = (p.x - this.W / 2 - this.pan.x);
    var wy = (p.y - this.H / 2 - this.pan.y);
    var old = this.spacing;
    var factor = Math.pow(1.0016, -e.deltaY);
    this.spacing = this.targetSpacing = clamp(this.spacing * factor, 0.22, 4);
    var rscale = this.spacing / old;
    this.pan.x = p.x - this.W / 2 - wx * rscale;
    this.pan.y = p.y - this.H / 2 - wy * rscale;
    if (this.selectedId) this._emitHighlight();
  };

  CareerNetwork.prototype.select = function (id) {
    this.selectedId = id;
    if (!id) { this.related = null; this.onHighlight([]); return; }
    var set = {}; set[ORIGIN_ID] = true; set[id] = true;
    var st = [id], x; // 上游
    while (st.length) { x = st.pop(); (this.adjB[x] || []).forEach(function (p) { if (!set[p]) { set[p] = true; st.push(p); } }); }
    st = [id];        // 下游
    while (st.length) { x = st.pop(); (this.adjF[x] || []).forEach(function (n) { if (!set[n]) { set[n] = true; st.push(n); } }); }
    this.related = set;
    this._emitHighlight();
  };

  CareerNetwork.prototype._emitHighlight = function () {
    if (!this.selectedId || !this.related) { this.onHighlight([]); return; }
    var cards = [], self = this;
    Object.keys(this.related).forEach(function (id) {
      if (id === ORIGIN_ID) return;
      var s = self.byId[id]; if (!s) return;
      self._project(s);
      var d = s.dir;
      var skills = ((d.pre || []).concat(d.know || [])).slice(0, 3);
      cards.push({
        id: id, tag: s.tag, x: s.sx, y: s.sy,
        name: s.name, phase: s.phase, role: s.role, rec: s.rec,
        stage: s.stage, sdesc: s.sdesc, desc: d.desc || '', tip: d.tip || '',
        baseRec: d.base_rec, lang: !!d.lang,
        cat: s.cat, skills: skills, isClicked: id === self.selectedId,
        colorHex: '#' + ((1 << 24) + (s.color.r << 16) + (s.color.g << 8) + s.color.b).toString(16).slice(1)
      });
    });
    this.onHighlight(cards);
  };

  CareerNetwork.prototype._star = function (ctx, x, y, core, col, bright, spike) {
    core = Math.max(0.4, Number(core) || 0.4);
    var haloR = core * (2.0 + bright * 3.2);
    var g = ctx.createRadialGradient(x, y, 0, x, y, haloR);
    g.addColorStop(0, rgba(col, 0.6 * bright));
    g.addColorStop(0.4, rgba(col, 0.15 * bright));
    g.addColorStop(1, rgba(col, 0));
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(x, y, haloR, 0, TAU); ctx.fill();
    if (spike && bright > 0.5) {
      var L = core * (3 + bright * 6);
      ctx.strokeStyle = rgba(col, 0.4 * bright); ctx.lineWidth = 0.8;
      ctx.beginPath(); ctx.moveTo(x - L, y); ctx.lineTo(x + L, y); ctx.moveTo(x, y - L); ctx.lineTo(x, y + L); ctx.stroke();
    }
  };

  CareerNetwork.prototype._drawGuides = function (ctx, hasSel) {
    if (!this.origin) return;
    var ox = this.origin.sx, oy = this.origin.sy;

    ctx.save();
    ctx.translate(ox, oy);
    ctx.scale(1, COS_T);
    ctx.setLineDash([4, 12]);
    for (var i = 0; i < RINGS.length; i++) {
      var r = RINGS[i] * this.spacing;
      ctx.strokeStyle = 'rgba(185,215,255,' + (hasSel ? 0.05 : 0.09) + ')';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(0, 0, r, 0, TAU); ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.restore();

    ctx.save();
    ctx.font = '700 11px "PingFang SC","Microsoft YaHei",sans-serif';
    ctx.textAlign = 'left';
    ctx.fillStyle = 'rgba(185,215,255,' + (hasSel ? 0.22 : 0.38) + ')';
    for (var l = 0; l < RINGS.length; l++) {
      var rr = RINGS[l] * this.spacing;
      ctx.fillText(RING_LABELS[l] || '', ox + rr * 0.72 + 8, oy - rr * COS_T * 0.36);
    }
    ctx.restore();

    for (var s = 0; s < (this.sectors || []).length; s++) {
      var sec = this.sectors[s];
      var a = sec.start;
      var edge = { r: RINGS[3] + 82, theta0: a, h: 0 };
      var inner = { r: 78, theta0: a, h: 0 };
      this._project(edge); this._project(inner);
      ctx.strokeStyle = rgba(sec.meta.color, hasSel ? 0.05 : 0.13);
      ctx.lineWidth = 0.8;
      ctx.beginPath(); ctx.moveTo(inner.sx, inner.sy); ctx.lineTo(edge.sx, edge.sy); ctx.stroke();

      var label = { r: RINGS[3] + 110, theta0: sec.mid, h: 0 };
      this._project(label);
      ctx.font = '800 12px "PingFang SC","Microsoft YaHei",sans-serif';
      ctx.textAlign = 'center';
      ctx.fillStyle = rgba(sec.meta.color, hasSel ? 0.22 : 0.58);
      ctx.fillText(sec.meta.name, label.sx, label.sy);
    }
  };

  CareerNetwork.prototype._draw = function () {
    var ctx = this.ctx, self = this;
    ctx.clearRect(0, 0, this.W, this.H);
    var hasSel = !!this.selectedId;

    this._project(this.origin);
    for (var i = 0; i < this.stars.length; i++) this._project(this.stars[i]);

    // 0) 时间环 + 就业类型扇区
    this._drawGuides(ctx, hasSel);

    // 1) 模糊星云
    ctx.globalCompositeOperation = 'lighter';
    for (var m = 0; m < this.nebula.length; m++) {
      var nb = this.nebula[m]; this._project(nb);
      var rad = nb.rad * this.spacing;
      var g = ctx.createRadialGradient(nb.sx, nb.sy, 0, nb.sx, nb.sy, rad);
      g.addColorStop(0, rgba(nb.color, nb.alpha * (hasSel ? 0.4 : 1)));
      g.addColorStop(1, rgba(nb.color, 0));
      ctx.fillStyle = g;
      ctx.save(); ctx.translate(nb.sx, nb.sy); ctx.scale(1, COS_T); ctx.translate(-nb.sx, -nb.sy);
      ctx.beginPath(); ctx.arc(nb.sx, nb.sy, rad, 0, TAU); ctx.fill(); ctx.restore();
    }

    // 2) 背景星尘（同款审美）
    for (var b = 0; b < this.bg.length; b++) {
      var s = this.bg[b]; this._project(s);
      var tw = 0.6 + 0.4 * Math.sin(this.timeSec * s.twSpeed + s.twPhase);
      var a = s.glow * tw * (hasSel ? 0.45 : 1);
      if (a < 0.02) continue;
      var col = s.warm ? '255,228,190' : s.tint ? '180,200,255' : '205,220,255';
      ctx.fillStyle = 'rgba(' + col + ',' + a + ')';
      ctx.beginPath(); ctx.arc(s.sx, s.sy, Math.max(0.35, s.core || 0.35), 0, TAU); ctx.fill();
    }

    // 3) 边（连线）：线宽固定，不随缩放变化
    ctx.globalCompositeOperation = 'source-over';
    for (var e = 0; e < this.edges.length; e++) {
      var L = this.edges[e];
      var A = L.from === ORIGIN_ID ? this.origin : this.byId[L.from];
      var B = L.to === ORIGIN_ID ? this.origin : this.byId[L.to];
      if (!A || !B) continue;
      var hot = hasSel && this.related && this.related[L.from] && this.related[L.to];
      var a2, w, stroke;
      if (L.kind === 'cross') {
        a2 = hot ? 0.9 : (hasSel ? 0.04 : 0.16); w = hot ? 2 : 1; stroke = hot ? '244,221,255' : '167,139,250';
      } else {
        a2 = hot ? 0.85 : (hasSel ? 0.03 : 0.10); w = hot ? 1.8 : 0.9; stroke = hot ? '180,235,255' : '130,170,225';
      }
      if (a2 < 0.02) continue;
      ctx.strokeStyle = 'rgba(' + stroke + ',' + a2 + ')'; ctx.lineWidth = w;
      ctx.beginPath(); ctx.moveTo(A.sx, A.sy); ctx.lineTo(B.sx, B.sy); ctx.stroke();
    }

    // 4) 阶段星（按深度排序）
    var order = this.stars.slice().sort(function (p, q) { return p.depth - q.depth; });
    ctx.globalCompositeOperation = 'lighter';
    for (var k = 0; k < order.length; k++) {
      var n = order[k];
      var dim = hasSel && this.related && !this.related[n.id];
      var tw2 = 0.72 + 0.28 * Math.sin(this.timeSec * n.twSpeed + n.twPhase);
      var bright = n.glow * tw2 * (dim ? 0.18 : 1);
      this._star(ctx, n.sx, n.sy, n.core, n.color, bright, n.glow > 0.55 && !dim);
    }
    // 实心核
    ctx.globalCompositeOperation = 'source-over';
    for (var d2 = 0; d2 < order.length; d2++) {
      var nn = order[d2];
      var dim2 = hasSel && this.related && !this.related[nn.id];
      var isSel = nn.id === this.selectedId, isHover = nn.id === this.hoverId;
      ctx.fillStyle = 'rgba(255,255,255,' + (dim2 ? 0.25 : 0.95) + ')';
      ctx.beginPath(); ctx.arc(nn.sx, nn.sy, nn.core * (isSel ? 1.5 : 1), 0, TAU); ctx.fill();
      if ((isSel || isHover) && !dim2) {
        ctx.strokeStyle = isSel ? '#fff' : rgba(nn.color, 0.9);
        ctx.lineWidth = isSel ? 2 : 1.2;
        ctx.beginPath(); ctx.arc(nn.sx, nn.sy, nn.core + (isSel ? 6 : 4), 0, TAU); ctx.stroke();
      }
    }

    // 5) 起点
    var hb = 1 + 0.1 * Math.sin(this.timeSec * 1.1);
    ctx.globalCompositeOperation = 'lighter';
    this._star(ctx, this.origin.sx, this.origin.sy, this.origin.core * hb, this.origin.color, 1, true);
    ctx.globalCompositeOperation = 'source-over';
    var og = ctx.createRadialGradient(this.origin.sx, this.origin.sy, 0, this.origin.sx, this.origin.sy, this.origin.core);
    og.addColorStop(0, '#fffbe8'); og.addColorStop(1, 'rgba(251,191,36,0.85)');
    ctx.fillStyle = og;
    ctx.beginPath(); ctx.arc(this.origin.sx, this.origin.sy, this.origin.core * 0.7, 0, TAU); ctx.fill();
    ctx.font = '800 12px "PingFang SC","Microsoft YaHei",sans-serif'; ctx.textAlign = 'center';
    var hl = this.origin.name, hw = ctx.measureText(hl).width;
    ctx.fillStyle = 'rgba(6,9,18,0.5)';
    ctx.fillRect(this.origin.sx - hw / 2 - 6, this.origin.sy + 14, hw + 12, 18);
    ctx.fillStyle = '#fde68a';
    ctx.fillText(hl, this.origin.sx, this.origin.sy + 27);
  };

  CareerNetwork.prototype.start = function () {
    if (this._raf) return;
    var self = this, last = performance.now();
    function loop(now) {
      var dt = Math.min((now - last) / 1000, 0.05); last = now;
      self.timeSec += dt;
      self.rotation += ROT_SPEED * dt * (self.selectedId ? 0 : 1);  // 选中时冻结自转
      self.spacing = lerp(self.spacing, self.targetSpacing, 0.2);
      self._draw();
      self._raf = requestAnimationFrame(loop);
    }
    this._raf = requestAnimationFrame(loop);
  };

  CareerNetwork.prototype.destroy = function () {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    window.removeEventListener('resize', this._onResize);
  };

  window.CareerNetwork = CareerNetwork;
})();
