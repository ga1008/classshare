/* 职业发展网络 · 3D 动态星系（Canvas2D，无依赖）
 * 一整张倾斜、缓慢自转的螺旋星系：中心是「你 · 起点」的星核，每个就业大类是一条
 * 旋臂向外辐射，每个方向是一颗星——推荐度越高越亮（所有可能路径都在，不推荐的也存在、
 * 可点击，只是暗）。点击某颗星，会点亮它从星核辐射出去的路径与关联分叉，其余隐入背景。
 * 暴露 window.CareerNetwork。
 */
(function () {
  'use strict';

  var TAU = Math.PI * 2;
  var TILT = 0.88;                 // 星盘倾角（弧度）→ 看上去是斜着的圆盘
  var COS_T = Math.cos(TILT), SIN_T = Math.sin(TILT);
  var ROT_SPEED = 0.045;           // 星系自转角速度（弧度/秒）

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
    this.onSelect = this.opts.onSelect || function () {};
    this.onBackground = this.opts.onBackground || function () {};

    this.dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
    this.W = 0; this.H = 0;
    this.scale = 1; this.targetScale = 1;
    this.pan = { x: 0, y: 0 };
    this.rotation = 0;
    this.nodes = [];
    this.bgStars = [];
    this.edges = [];
    this.hub = null;
    this.selectedId = null;
    this.hoverId = null;
    this.related = null;
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
    var nodes = network.nodes || [];
    var links = network.links || [];

    this.catColor = {};
    cats.forEach(function (c) { self.catColor[c.id] = { c1: c.c1 || '#6ee7ff', c2: c.c2 || '#3b82f6', name: c.name, icon: c.icon, id: c.id }; });

    // 旋臂：每个大类一条臂，均匀分布角度
    var catIds = cats.map(function (c) { return c.id; });
    if (!catIds.length) catIds = nodes.map(function (n) { return n.cat; }).filter(function (v, i, a) { return a.indexOf(v) === i; });
    var armBase = {};
    catIds.forEach(function (id, i) { armBase[id] = (i / Math.max(catIds.length, 1)) * TAU; });

    var byCat = {};
    nodes.forEach(function (n) { (byCat[n.cat] = byCat[n.cat] || []).push(n); });

    var INNER = 150, OUTER = 560, TWIST = 0.0045, THICK = 38;
    this.maxR = OUTER + 80;
    this.nodes = [];
    nodes.forEach(function (n) {
      var arm = byCat[n.cat] || [n];
      var idx = arm.indexOf(n);
      var m = arm.length;
      var seed = self._hash((n.tag || n.name) + ':' + n.cat);
      // 每个大类是一个扇形旋臂：节点沿角度铺开 + 半径黄金分割错落，避免堆叠
      var f = m > 1 ? idx / (m - 1) : 0.5;
      var wedge = clamp(0.5 + m * 0.045, 0.5, 1.25);     // 大类越大，扇面越宽
      var rFrac = 0.22 + 0.78 * (((idx * 0.61803) + ((seed % 100) / 700)) % 1);
      var r = INNER + rFrac * (OUTER - INNER);
      var ang = (armBase[n.cat] || 0) + (f - 0.5) * wedge + ((seed % 1000) / 1000 - 0.5) * 0.06;
      var theta0 = ang + r * TWIST;
      var h = (((seed >> 7) % 1000) / 1000 - 0.5) * THICK;
      var col = self.catColor[n.cat] || { c1: '#6ee7ff', c2: '#3b82f6' };
      var rec = n.rec || 3;
      var glow = (typeof n.glow === 'number') ? n.glow
        : (rec >= 5 ? 0.95 : rec >= 4 ? 0.62 : rec >= 3 ? 0.4 : 0.22);
      glow = clamp(glow, 0.12, 1);
      self.nodes.push({
        id: n.tag, tag: n.tag, name: n.name, cat: n.cat, rec: rec, data: n,
        highlighted: !!n.highlighted, lang: !!n.lang,
        glow: glow, color: hexToRgb(col.c1), color2: hexToRgb(col.c2),
        r: r, theta0: theta0, h: h,
        size: 2.6 + rec * 1.1 + (n.highlighted ? 1.4 : 0),
        twPhase: (seed % 628) / 100, twSpeed: 0.5 + ((seed >> 9) % 100) / 80,
        sx: 0, sy: 0, depth: 0, ss: 0
      });
    });

    // 星核（你 · 起点）
    this.hub = {
      id: '__hub__', name: this.opts.hubLabel || '起点 · 现在', isHub: true,
      r: 0, theta0: 0, h: 0, color: hexToRgb('#fde68a'), color2: hexToRgb('#fbbf24'),
      glow: 1, size: 13, twPhase: 0, twSpeed: 0.7, sx: 0, sy: 0, depth: 0, ss: 0
    };

    // 背景星尘（属于星系、一起转、营造“整张星图”的密度与纵深）
    this.bgStars = [];
    var BG = 320;
    for (var i = 0; i < BG; i++) {
      var s2 = self._hash('bg' + i);
      var rr = 50 + ((s2 % 1000) / 1000) * (OUTER + 60);
      var th = ((s2 >> 6) % 6283) / 1000;
      var hh = (((s2 >> 11) % 1000) / 1000 - 0.5) * (THICK + 36);
      self.bgStars.push({
        r: rr, theta0: th, h: hh,
        glow: 0.06 + ((s2 >> 3) % 100) / 220,
        size: 0.5 + ((s2 >> 5) % 100) / 90,
        twPhase: (s2 % 628) / 100, twSpeed: 0.4 + ((s2 >> 8) % 100) / 70,
        warm: (s2 % 7 === 0)
      });
    }

    // 边：星核→各方向（辐射）+ 跨方向分叉
    var byId = {};
    this.nodes.forEach(function (nd) { byId[nd.id] = nd; });
    this.byId = byId;
    this.adj = {};
    this.edges = [];
    this.nodes.forEach(function (nd) {
      self.edges.push({ a: self.hub, b: nd, kind: 'fan' });
      (self.adj[nd.id] = self.adj[nd.id] || []);
    });
    links.forEach(function (l) {
      var a = byId[l[0]], b = byId[l[2]];
      if (a && b) {
        self.edges.push({ a: a, b: b, kind: 'cross' });
        (self.adj[a.id] = self.adj[a.id] || []).push(b.id);
        (self.adj[b.id] = self.adj[b.id] || []).push(a.id);
      }
    });

    this.fitView();
    this.start();
  };

  CareerNetwork.prototype.fitView = function () {
    var spanX = this.maxR * 2;
    var spanY = this.maxR * 2 * COS_T + 140;
    var s = Math.min(this.W / spanX, this.H / spanY) * 0.92;
    this.scale = this.targetScale = clamp(s || 0.6, 0.3, 1.4);
    this.pan.x = 0;
    this.pan.y = this.H * 0.05;     // 略向下，给顶部 banner 留白
  };

  // 3D 投影：极坐标(r,θ,h) → 自转 → 倾斜 → 屏幕
  CareerNetwork.prototype._project = function (obj) {
    var th = obj.theta0 + this.rotation;
    var x = obj.r * Math.cos(th);
    var y0 = obj.r * Math.sin(th);
    var ty = y0 * COS_T - obj.h * SIN_T;
    var tz = y0 * SIN_T + obj.h * COS_T;     // 深度（朝向观者为正）
    obj.sx = this.W / 2 + x * this.scale + this.pan.x;
    obj.sy = this.H / 2 + ty * this.scale + this.pan.y;
    obj.depth = tz;
    return obj;
  };

  CareerNetwork.prototype.resize = function () {
    var rect = this.canvas.getBoundingClientRect();
    this.W = rect.width; this.H = rect.height;
    this.canvas.width = Math.round(this.W * this.dpr);
    this.canvas.height = Math.round(this.H * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  };

  CareerNetwork.prototype._bind = function () {
    var self = this, c = this.canvas;
    this._onResize = function () { self.resize(); };
    window.addEventListener('resize', this._onResize);
    c.addEventListener('pointerdown', function (e) { self._down(e); });
    c.addEventListener('pointermove', function (e) { self._move(e); });
    window.addEventListener('pointerup', function (e) { self._up(e); });
    c.addEventListener('pointerleave', function () { self.pointer.inside = false; });
    c.addEventListener('wheel', function (e) { self._wheel(e); }, { passive: false });
  };

  CareerNetwork.prototype._localPos = function (e) {
    var rect = this.canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  CareerNetwork.prototype._pick = function (sx, sy) {
    var best = null, bestD = 1e9;
    for (var i = 0; i < this.nodes.length; i++) {
      var n = this.nodes[i];
      var rr = Math.max(n.ss + 12, 16);
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
    }
    this.drag.lastX = p.x; this.drag.lastY = p.y;
    if (!this.drag.moved) {
      var hit = this._pick(p.x, p.y);
      this.hoverId = hit ? hit.id : null;
      this.canvas.style.cursor = hit ? 'pointer' : 'grab';
    }
  };

  CareerNetwork.prototype._up = function () {
    if (!this.drag.moved) {
      var hit = this._pick(this.pointer.x, this.pointer.y);
      if (hit) { this.select(hit.id); this.onSelect(hit.data, hit); }
      else { this.select(null); this.onBackground(); }
    }
    this.drag.panning = false;
  };

  CareerNetwork.prototype._wheel = function (e) {
    e.preventDefault();
    var p = this._localPos(e);
    var wx = (p.x - this.W / 2 - this.pan.x) / this.scale;
    var wy = (p.y - this.H / 2 - this.pan.y) / this.scale;
    var factor = Math.pow(1.0015, -e.deltaY);
    this.scale = this.targetScale = clamp(this.scale * factor, 0.22, 3.4);
    this.pan.x = p.x - this.W / 2 - wx * this.scale;
    this.pan.y = p.y - this.H / 2 - wy * this.scale;
  };

  CareerNetwork.prototype.select = function (id) {
    this.selectedId = id;
    if (!id) { this.related = null; return; }
    // 点亮：选中星 + 星核 + 与它相连的分叉端点
    var set = {}; set[id] = true; set['__hub__'] = true;
    (this.adj[id] || []).forEach(function (o) { set[o] = true; });
    this.related = set;
  };

  // --- 渲染 ---
  CareerNetwork.prototype._drawStar = function (ctx, x, y, size, col, bright, spikes) {
    var haloR = size * (2.2 + bright * 4.2);
    var g = ctx.createRadialGradient(x, y, 0, x, y, haloR);
    g.addColorStop(0, rgba(col, 0.55 * bright));
    g.addColorStop(0.35, rgba(col, 0.16 * bright));
    g.addColorStop(1, rgba(col, 0));
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(x, y, haloR, 0, TAU); ctx.fill();
    if (spikes && bright > 0.45) {
      var L = size * (3 + bright * 7);
      var lg = ctx.createLinearGradient(x - L, y, x + L, y);
      lg.addColorStop(0, rgba(col, 0)); lg.addColorStop(0.5, rgba(col, 0.5 * bright)); lg.addColorStop(1, rgba(col, 0));
      ctx.strokeStyle = lg; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x - L, y); ctx.lineTo(x + L, y); ctx.stroke();
      var lg2 = ctx.createLinearGradient(x, y - L, x, y + L);
      lg2.addColorStop(0, rgba(col, 0)); lg2.addColorStop(0.5, rgba(col, 0.5 * bright)); lg2.addColorStop(1, rgba(col, 0));
      ctx.strokeStyle = lg2;
      ctx.beginPath(); ctx.moveTo(x, y - L); ctx.lineTo(x, y + L); ctx.stroke();
    }
  };

  CareerNetwork.prototype._draw = function () {
    var ctx = this.ctx, self = this;
    ctx.clearRect(0, 0, this.W, this.H);
    var hasSel = !!this.selectedId;

    // 项目所有对象
    this._project(this.hub);
    for (var i = 0; i < this.nodes.length; i++) this._project(this.nodes[i]);

    // 1) 星系核球 + 盘面薄雾（倾斜的椭圆辉光）
    ctx.save();
    var hx = this.hub.sx, hy = this.hub.sy;
    var diskR = this.maxR * this.scale;
    var neb = ctx.createRadialGradient(hx, hy, 0, hx, hy, diskR);
    neb.addColorStop(0, 'rgba(150,170,255,0.10)');
    neb.addColorStop(0.18, 'rgba(120,150,230,0.06)');
    neb.addColorStop(0.55, 'rgba(80,110,190,0.03)');
    neb.addColorStop(1, 'rgba(80,110,190,0)');
    ctx.save();
    ctx.translate(hx, hy); ctx.scale(1, COS_T); ctx.translate(-hx, -hy);
    ctx.fillStyle = neb;
    ctx.beginPath(); ctx.arc(hx, hy, diskR, 0, TAU); ctx.fill();
    ctx.restore();

    // 2) 背景星尘
    ctx.globalCompositeOperation = 'lighter';
    for (var b = 0; b < this.bgStars.length; b++) {
      var s = this.bgStars[b]; this._project(s);
      var tw = 0.6 + 0.4 * Math.sin(this.timeSec * s.twSpeed + s.twPhase);
      var a = s.glow * tw * (hasSel ? 0.4 : 1);
      if (a < 0.02) continue;
      ctx.fillStyle = s.warm ? 'rgba(255,225,180,' + a + ')' : 'rgba(180,210,255,' + a + ')';
      var sz = s.size * (0.7 + (s.depth / this.maxR + 0.5) * 0.6) * this.scale;
      ctx.beginPath(); ctx.arc(s.sx, s.sy, Math.max(0.4, sz), 0, TAU); ctx.fill();
    }

    // 3) 边（路径）：默认全部存在但很淡；选中时点亮相关、其余更淡
    ctx.globalCompositeOperation = 'source-over';
    for (var e = 0; e < this.edges.length; e++) {
      var L = this.edges[e], A = L.a, B = L.b;
      var hot = hasSel && this.related && this.related[A.id] && this.related[B.id];
      var baseA, w, stroke;
      if (L.kind === 'fan') {
        baseA = hot ? 0.5 : (hasSel ? 0.02 : 0.07); w = hot ? 1.5 : 0.8;
        stroke = 'rgba(140,180,235,' + baseA + ')';
      } else {
        baseA = hot ? 0.85 : (hasSel ? 0.05 : 0.22); w = hot ? 2.4 : 1.1;
        stroke = 'rgba(190,160,255,' + baseA + ')';
      }
      if (baseA < 0.02) continue;
      ctx.strokeStyle = stroke; ctx.lineWidth = w;
      var mx = (A.sx + B.sx) / 2, my = (A.sy + B.sy) / 2;
      // 让分叉线微微弯，像星座连线
      var bow = (L.kind === 'cross' ? 14 : 6) * (hot ? 1.4 : 1);
      ctx.beginPath(); ctx.moveTo(A.sx, A.sy);
      ctx.quadraticCurveTo(mx, my - bow, B.sx, B.sy); ctx.stroke();
    }

    // 4) 方向星（按深度排序，远的先画）
    var order = this.nodes.slice().sort(function (p, q) { return p.depth - q.depth; });
    ctx.globalCompositeOperation = 'lighter';
    for (var k = 0; k < order.length; k++) {
      var n = order[k];
      var dim = hasSel && this.related && !this.related[n.id];
      var tw2 = 0.7 + 0.3 * Math.sin(this.timeSec * n.twSpeed + n.twPhase);
      var depthF = 0.78 + (n.depth / this.maxR + 0.5) * 0.5;
      var bright = n.glow * tw2 * (dim ? 0.22 : 1);
      var size = n.size * depthF * this.scale;
      n.ss = size;
      this._drawStar(ctx, n.sx, n.sy, size, n.color, bright, n.glow > 0.55 && !dim);
    }

    // 5) 星核
    var hb = 1 + 0.12 * Math.sin(this.timeSec * 1.1);
    this._drawStar(ctx, hx, hy, this.hub.size * this.scale * hb, this.hub.color, 1, true);

    // 6) 星核核心实点
    ctx.globalCompositeOperation = 'source-over';
    var cg = ctx.createRadialGradient(hx, hy, 0, hx, hy, this.hub.size * this.scale * 1.1);
    cg.addColorStop(0, '#fffbe8'); cg.addColorStop(1, 'rgba(251,191,36,0.85)');
    ctx.fillStyle = cg;
    ctx.beginPath(); ctx.arc(hx, hy, this.hub.size * this.scale * 0.62, 0, TAU); ctx.fill();

    // 7) 方向星实心点 + 选中/悬停环
    for (var d = 0; d < order.length; d++) {
      var nn = order[d];
      var dim2 = hasSel && this.related && !this.related[nn.id];
      var isSel = nn.id === this.selectedId, isHover = nn.id === this.hoverId;
      var coreR = nn.ss * (isSel ? 0.62 : 0.42) + 1.2;
      var pg = ctx.createRadialGradient(nn.sx - coreR * 0.3, nn.sy - coreR * 0.3, 0, nn.sx, nn.sy, coreR + 0.5);
      pg.addColorStop(0, rgba(nn.color2 || nn.color, dim2 ? 0.5 : 1));
      pg.addColorStop(1, rgba(nn.color, dim2 ? 0.3 : 0.92));
      ctx.fillStyle = pg;
      ctx.beginPath(); ctx.arc(nn.sx, nn.sy, coreR, 0, TAU); ctx.fill();
      if ((isSel || isHover) && !dim2) {
        ctx.strokeStyle = isSel ? '#fff' : rgba(nn.color, 0.9);
        ctx.lineWidth = isSel ? 2 : 1.3;
        ctx.beginPath(); ctx.arc(nn.sx, nn.sy, coreR + (isSel ? 6 : 4), 0, TAU); ctx.stroke();
      }
    }

    // 8) 标签：星核、选中、悬停、相关、推荐度高/放大时
    ctx.globalCompositeOperation = 'source-over';
    for (var t = 0; t < order.length; t++) {
      var m = order[t];
      var rel = !hasSel || (this.related && this.related[m.id]);
      // 默认只给最亮的（高亮/推荐满级）打标签，避免拥挤；其余悬停或放大后再显示。
      var show = m.id === this.selectedId || m.id === this.hoverId
        || (rel && (m.highlighted || m.rec >= 5)) || (rel && this.scale > 1.12);
      if (!show || !m.name) continue;
      var fontPx = clamp(11 * Math.max(this.scale, 0.72), 10, 15);
      ctx.font = '700 ' + fontPx + 'px "PingFang SC","Microsoft YaHei",sans-serif';
      ctx.textAlign = 'center';
      var label = m.name + (m.lang ? ' ⭐' : '');
      var ly = m.sy + m.ss + fontPx + 2;
      var tw3 = ctx.measureText(label).width;
      ctx.fillStyle = 'rgba(6,9,18,0.5)';
      ctx.fillRect(m.sx - tw3 / 2 - 5, ly - fontPx, tw3 + 10, fontPx + 6);
      ctx.fillStyle = (m.id === this.selectedId) ? '#fff' : 'rgba(232,237,247,0.92)';
      ctx.fillText(label, m.sx, ly);
    }
    // 星核标签
    ctx.font = '800 13px "PingFang SC","Microsoft YaHei",sans-serif';
    ctx.textAlign = 'center';
    var hl = this.hub.name, hw = ctx.measureText(hl).width;
    ctx.fillStyle = 'rgba(6,9,18,0.55)';
    ctx.fillRect(hx - hw / 2 - 6, hy + this.hub.size * this.scale + 4, hw + 12, 19);
    ctx.fillStyle = '#fde68a';
    ctx.fillText(hl, hx, hy + this.hub.size * this.scale + 18);
  };

  CareerNetwork.prototype.start = function () {
    if (this._raf) return;
    var self = this, last = performance.now();
    function loop(now) {
      var dt = Math.min((now - last) / 1000, 0.05); last = now;
      self.timeSec += dt;
      // 选中时几乎停转，便于阅读路径
      self.rotation += ROT_SPEED * dt * (self.selectedId ? 0.08 : 1);
      self.scale = lerp(self.scale, self.targetScale, 0.18);
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
