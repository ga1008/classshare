/* 职业发展网络 · Canvas2D 自研物理引擎（3D 星图：游动节点 + 弹性连线 + 缩放 + 鼠标吸附/拨弄）
 * 暴露 window.CareerNetwork。无第三方依赖。
 */
(function () {
  'use strict';

  var TAU = Math.PI * 2;

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
    this.camera = { x: 0, y: 0 };
    this.nodes = [];
    this.links = [];
    this.catColor = {};
    this.hub = null;
    this.selectedId = null;
    this.hoverId = null;

    this.pointer = { x: 0, y: 0, active: false, inside: false };
    this.drag = { node: null, panning: false, lastX: 0, lastY: 0, moved: false, downX: 0, downY: 0 };
    this.timeSec = 0;
    this._raf = null;

    this._bind();
    this.resize();
  }

  CareerNetwork.prototype.setData = function (network, personalized) {
    var self = this;
    var cats = network.cats || [];
    var nodes = network.nodes || [];
    var links = network.links || [];
    this.catColor = {};
    cats.forEach(function (c) { self.catColor[c.id] = { c1: c.c1 || '#6ee7ff', c2: c.c2 || '#3b82f6', name: c.name, icon: c.icon, id: c.id }; });
    this.catList = cats;

    // 布局：每个大类是星系一条旋臂，节点在旋臂簇内散开，中心是“起点/现在”枢纽。
    var catIds = cats.map(function (c) { return c.id; });
    if (!catIds.length) {
      catIds = nodes.map(function (n) { return n.cat; }).filter(function (v, i, a) { return a.indexOf(v) === i; });
    }
    var ringR = 320;
    var catCenter = {};
    catIds.forEach(function (id, i) {
      var ang = (i / Math.max(catIds.length, 1)) * TAU - Math.PI / 2;
      catCenter[id] = { x: Math.cos(ang) * ringR, y: Math.sin(ang) * ringR * 0.82, ang: ang };
    });

    var byCat = {};
    nodes.forEach(function (n) { (byCat[n.cat] = byCat[n.cat] || []).push(n); });

    var GOLDEN = Math.PI * (3 - Math.sqrt(5)); // 黄金角，散点更均匀不重叠
    this.nodes = [];
    nodes.forEach(function (n) {
      var center = catCenter[n.cat] || { x: 0, y: 0, ang: 0 };
      var siblings = byCat[n.cat] || [n];
      var idx = siblings.indexOf(n);
      // 在大类簇中心周围做黄金角螺旋散点：簇越大铺得越开，避免连成一条线
      var blob = 46 + siblings.length * 16;
      var t = (idx + 0.5) / Math.max(siblings.length, 1);
      var rad = Math.sqrt(t) * blob;
      var ang2 = idx * GOLDEN + center.ang;
      var hx = center.x + Math.cos(ang2) * rad;
      var hy = center.y + Math.sin(ang2) * rad * 0.92;
      var col = self.catColor[n.cat] || { c1: '#6ee7ff', c2: '#3b82f6' };
      var rec = n.rec || 3;
      // glow 强度：优先用 AI 给的 dim_glow / node.glow，否则用推荐度映射，制造星星明暗差。
      var glow = (typeof n.glow === 'number') ? n.glow : (rec >= 5 ? 0.92 : rec >= 4 ? 0.6 : rec >= 3 ? 0.36 : 0.2);
      var seed = self._hash(n.tag || n.name);
      self.nodes.push({
        id: n.tag, tag: n.tag, name: n.name, cat: n.cat, rec: rec, data: n,
        highlighted: !!n.highlighted, lang: !!n.lang,
        glow: clamp(glow, 0.12, 1),
        color: hexToRgb(col.c1), color2: hexToRgb(col.c2),
        home: { x: hx, y: hy }, x: hx + (seed % 40 - 20), y: hy + ((seed >> 3) % 40 - 20),
        vx: 0, vy: 0,
        r: 5.2 + rec * 1.5 + (n.highlighted ? 2 : 0),
        z: 0.6 + ((seed % 100) / 100) * 0.8,           // 深度（视差/3D 感）
        wanderPhase: (seed % 628) / 100, wanderSpeed: 0.25 + ((seed >> 5) % 50) / 100,
        wanderAmp: 10 + (seed % 16),
        twPhase: (seed % 314) / 100, twSpeed: 0.6 + ((seed >> 7) % 80) / 60,
        pinned: false
      });
    });

    // 枢纽节点（起点·现在）
    this.hub = {
      id: '__hub__', name: this.opts.hubLabel || '起点 · 现在', isHub: true,
      home: { x: 0, y: 0 }, x: 0, y: 0, vx: 0, vy: 0, r: 13,
      color: hexToRgb('#fde68a'), color2: hexToRgb('#fbbf24'), glow: 1, z: 1.2,
      wanderPhase: 0, wanderSpeed: 0.12, wanderAmp: 4, twPhase: 0, twSpeed: 0.8
    };

    // 连线：枢纽→各节点（扇出，弱）+ 跨方向分叉（强、弹性）
    var nodeById = {};
    this.nodes.forEach(function (nd) { nodeById[nd.id] = nd; });
    this.nodeById = nodeById;
    this.links = [];
    this.nodes.forEach(function (nd) { self.links.push({ a: self.hub, b: nd, kind: 'fan' }); });
    links.forEach(function (l) {
      var a = nodeById[l[0]], b = nodeById[l[2]];
      if (a && b) self.links.push({ a: a, b: b, kind: 'cross' });
    });

    this.fitView();
    this.start();
  };

  CareerNetwork.prototype._hash = function (str) {
    var h = 2166136261; str = String(str || '');
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = (h * 16777619) >>> 0; }
    return h;
  };

  CareerNetwork.prototype.fitView = function () {
    var minX = -1, maxX = 1, minY = -1, maxY = 1;
    this.nodes.forEach(function (n) {
      minX = Math.min(minX, n.home.x); maxX = Math.max(maxX, n.home.x);
      minY = Math.min(minY, n.home.y); maxY = Math.max(maxY, n.home.y);
    });
    var pad = 120;
    var w = (maxX - minX) + pad * 2, h = (maxY - minY) + pad * 2;
    var sx = this.W / w, sy = this.H / h;
    this.targetScale = this.scale = clamp(Math.min(sx, sy), 0.4, 1.4);
    this.camera.x = (minX + maxX) / 2;
    this.camera.y = (minY + maxY) / 2;
  };

  // --- 坐标变换 ---
  CareerNetwork.prototype.toScreen = function (wx, wy) {
    return { x: (wx - this.camera.x) * this.scale + this.W / 2, y: (wy - this.camera.y) * this.scale + this.H / 2 };
  };
  CareerNetwork.prototype.toWorld = function (sx, sy) {
    return { x: (sx - this.W / 2) / this.scale + this.camera.x, y: (sy - this.H / 2) / this.scale + this.camera.y };
  };

  CareerNetwork.prototype.resize = function () {
    var rect = this.canvas.getBoundingClientRect();
    this.W = rect.width; this.H = rect.height;
    this.canvas.width = Math.round(this.W * this.dpr);
    this.canvas.height = Math.round(this.H * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  };

  // --- 事件 ---
  CareerNetwork.prototype._bind = function () {
    var self = this, c = this.canvas;
    this._onResize = function () { self.resize(); };
    window.addEventListener('resize', this._onResize);

    c.addEventListener('pointerdown', function (e) { self._down(e); });
    c.addEventListener('pointermove', function (e) { self._move(e); });
    window.addEventListener('pointerup', function (e) { self._up(e); });
    c.addEventListener('pointerleave', function () { self.pointer.inside = false; self.pointer.active = false; });
    c.addEventListener('wheel', function (e) { self._wheel(e); }, { passive: false });
  };

  CareerNetwork.prototype._localPos = function (e) {
    var rect = this.canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  CareerNetwork.prototype._pick = function (sx, sy) {
    var best = null, bestD = 26 * 26;
    for (var i = 0; i < this.nodes.length; i++) {
      var n = this.nodes[i], p = this.toScreen(n.x, n.y);
      var rr = (n.r * this.scale + 12);
      var dx = sx - p.x, dy = sy - p.y, d = dx * dx + dy * dy;
      if (d < Math.max(bestD, rr * rr) && d < (rr + 8) * (rr + 8)) { best = n; bestD = d; }
    }
    return best;
  };

  CareerNetwork.prototype._down = function (e) {
    this.canvas.setPointerCapture && this.canvas.setPointerCapture(e.pointerId);
    var p = this._localPos(e);
    this.pointer.x = p.x; this.pointer.y = p.y; this.pointer.inside = true;
    this.drag.downX = p.x; this.drag.downY = p.y; this.drag.moved = false;
    this.drag.lastX = p.x; this.drag.lastY = p.y;
    var hit = this._pick(p.x, p.y);
    if (hit) { this.drag.node = hit; hit.grabbed = true; }
    else { this.drag.panning = true; }
  };

  CareerNetwork.prototype._move = function (e) {
    var p = this._localPos(e);
    this.pointer.x = p.x; this.pointer.y = p.y; this.pointer.inside = true; this.pointer.active = true;
    var dx = p.x - this.drag.downX, dy = p.y - this.drag.downY;
    if (dx * dx + dy * dy > 25) this.drag.moved = true;

    if (this.drag.panning) {
      this.camera.x -= (p.x - this.drag.lastX) / this.scale;
      this.camera.y -= (p.y - this.drag.lastY) / this.scale;
    }
    this.drag.lastX = p.x; this.drag.lastY = p.y;
    if (!this.drag.node && !this.drag.panning) {
      var hit = this._pick(p.x, p.y);
      this.hoverId = hit ? hit.id : null;
      this.canvas.style.cursor = hit ? 'pointer' : 'grab';
    }
  };

  CareerNetwork.prototype._up = function (e) {
    var wasNode = this.drag.node;
    if (this.drag.node) this.drag.node.grabbed = false;
    if (!this.drag.moved) {
      var hit = this._pick(this.pointer.x, this.pointer.y);
      if (hit) { this.select(hit.id); this.onSelect(hit.data, hit); }
      else { this.select(null); this.onBackground(); }
    }
    this.drag.node = null; this.drag.panning = false; this.pointer.active = false;
  };

  CareerNetwork.prototype._wheel = function (e) {
    e.preventDefault();
    var p = this._localPos(e);
    var before = this.toWorld(p.x, p.y);
    var factor = Math.pow(1.0015, -e.deltaY);
    this.targetScale = clamp(this.scale * factor, 0.28, 3.2);
    this.scale = this.targetScale;
    // 保持光标下的世界点不动
    this.camera.x = before.x - (p.x - this.W / 2) / this.scale;
    this.camera.y = before.y - (p.y - this.H / 2) / this.scale;
  };

  CareerNetwork.prototype.select = function (id) {
    this.selectedId = id;
    var self = this;
    this.nodes.forEach(function (n) { n.pinned = (n.id === id); });
  };

  CareerNetwork.prototype.zoomBy = function (factor) {
    this.targetScale = clamp(this.scale * factor, 0.28, 3.2); this.scale = this.targetScale;
  };

  // --- 物理 ---
  CareerNetwork.prototype._physics = function (dt) {
    var self = this;
    var pw = this.pointer.inside ? this.toWorld(this.pointer.x, this.pointer.y) : null;
    var attractR = 150, attractR2 = attractR * attractR;
    var nodes = this.nodes;

    // 枢纽轻微浮动
    this.hub.x = lerp(this.hub.x, this.hub.home.x + Math.cos(this.timeSec * 0.4) * 4, 0.05);
    this.hub.y = lerp(this.hub.y, this.hub.home.y + Math.sin(this.timeSec * 0.5) * 4, 0.05);

    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.pinned) { continue; }

      // 1) 弹回 home + 随机游动（在原位附近以随机速度/轨迹游动）
      var wx = n.home.x + Math.cos(this.timeSec * n.wanderSpeed + n.wanderPhase) * n.wanderAmp;
      var wy = n.home.y + Math.sin(this.timeSec * n.wanderSpeed * 1.3 + n.wanderPhase) * n.wanderAmp;
      n.vx += (wx - n.x) * 0.012;
      n.vy += (wy - n.y) * 0.012;

      // 2) 鼠标吸附 / 拨弄
      if (n.grabbed && pw) {
        // 拖拽：跟随指针，但靠弹力（避免穿透其他节点，靠下面的碰撞约束）
        n.vx += (pw.x - n.x) * 0.35;
        n.vy += (pw.y - n.y) * 0.35;
      } else if (pw) {
        var ddx = pw.x - n.x, ddy = pw.y - n.y, d2 = ddx * ddx + ddy * ddy;
        if (d2 < attractR2 && d2 > 1) {
          var d = Math.sqrt(d2);
          // 距离越近吸引力越强、速度越快
          var force = (1 - d / attractR);
          var pull = force * force * 0.9;
          n.vx += (ddx / d) * pull;
          n.vy += (ddy / d) * pull;
        }
      }

      // 3) home 越界回弹（被吸引/拖拽的节点不会跑太远，会回到原位附近）
      var hdx = n.x - n.home.x, hdy = n.y - n.home.y;
      var hd2 = hdx * hdx + hdy * hdy, maxR = 95;
      if (hd2 > maxR * maxR) {
        var hd = Math.sqrt(hd2);
        n.vx -= (hdx / hd) * (hd - maxR) * 0.03;
        n.vy -= (hdy / hd) * (hd - maxR) * 0.03;
      }

      n.vx *= 0.86; n.vy *= 0.86;
      n.x += n.vx; n.y += n.vy;
    }

    // 4) 节点-节点软碰撞（不能越过其他节点边界）
    for (var a = 0; a < nodes.length; a++) {
      for (var b = a + 1; b < nodes.length; b++) {
        var na = nodes[a], nb = nodes[b];
        var dx = nb.x - na.x, dy = nb.y - na.y;
        var min = na.r + nb.r + 8;
        var dd = dx * dx + dy * dy;
        if (dd > 0 && dd < min * min) {
          var dist = Math.sqrt(dd);
          var overlap = (min - dist) / 2;
          var ux = dx / dist, uy = dy / dist;
          if (!na.pinned && !na.grabbed) { na.x -= ux * overlap; na.y -= uy * overlap; }
          if (!nb.pinned && !nb.grabbed) { nb.x += ux * overlap; nb.y += uy * overlap; }
        }
      }
    }
  };

  // --- 渲染 ---
  CareerNetwork.prototype._draw = function () {
    var ctx = this.ctx, self = this;
    ctx.clearRect(0, 0, this.W, this.H);
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';

    // 背景星尘（少量，营造空间感）
    // 连线
    ctx.globalCompositeOperation = 'source-over';
    for (var i = 0; i < this.links.length; i++) {
      var L = this.links[i];
      var pa = this.toScreen(L.a.x, L.a.y), pb = this.toScreen(L.b.x, L.b.y);
      var sel = this.selectedId && (L.a.id === this.selectedId || L.b.id === this.selectedId);
      if (L.kind === 'fan') {
        ctx.strokeStyle = sel ? 'rgba(110,231,255,.4)' : 'rgba(120,150,200,.06)';
        ctx.lineWidth = sel ? 1.4 : 0.8;
      } else {
        ctx.strokeStyle = sel ? 'rgba(216,180,254,.85)' : 'rgba(167,139,250,.18)';
        ctx.lineWidth = sel ? 2.2 : 1.2;
      }
      // 弹性曲线：用中点做轻微下垂的二次曲线，随节点移动而形变
      var mx = (pa.x + pb.x) / 2, my = (pa.y + pb.y) / 2 + Math.sin(this.timeSec + i) * 6 * this.scale;
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.quadraticCurveTo(mx, my, pb.x, pb.y);
      ctx.stroke();
    }

    // 节点（先 halo 后 core）
    var all = this.nodes.concat([this.hub]);
    // 按深度排序（远的先画）
    all.sort(function (a, b) { return a.z - b.z; });

    ctx.globalCompositeOperation = 'lighter';
    for (var h = 0; h < all.length; h++) {
      var n = all[h];
      var p = this.toScreen(n.x, n.y);
      var depth = n.z;
      var rr = (n.r * this.scale) * (0.7 + depth * 0.4);
      // 星星明暗：基础 glow + 闪烁
      var tw = 0.72 + 0.28 * Math.sin(this.timeSec * n.twSpeed + n.twPhase);
      var bright = n.glow * tw;
      var haloR = rr * (2.4 + n.glow * 3.6);
      var col = n.color;
      var g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, haloR);
      g.addColorStop(0, rgba(col, 0.55 * bright));
      g.addColorStop(0.4, rgba(col, 0.18 * bright));
      g.addColorStop(1, rgba(col, 0));
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(p.x, p.y, haloR, 0, TAU); ctx.fill();
    }

    ctx.globalCompositeOperation = 'source-over';
    for (var k = 0; k < all.length; k++) {
      var nn = all[k];
      var pp = this.toScreen(nn.x, nn.y);
      var depth2 = nn.z;
      var cr = (nn.r * this.scale) * (0.7 + depth2 * 0.4);
      var isSel = nn.id === this.selectedId;
      var isHover = nn.id === this.hoverId;
      // core
      var cg = ctx.createRadialGradient(pp.x - cr * 0.3, pp.y - cr * 0.3, 0, pp.x, pp.y, cr);
      cg.addColorStop(0, rgba(nn.color2 || nn.color, 1));
      cg.addColorStop(1, rgba(nn.color, 0.92));
      ctx.fillStyle = cg;
      ctx.beginPath(); ctx.arc(pp.x, pp.y, cr * (isSel ? 1.5 : isHover ? 1.22 : 1), 0, TAU); ctx.fill();
      // ring
      if (nn.highlighted || isSel) {
        ctx.strokeStyle = isSel ? '#fff' : rgba(nn.color, 0.9);
        ctx.lineWidth = isSel ? 2 : 1.4;
        ctx.beginPath(); ctx.arc(pp.x, pp.y, cr * (isSel ? 1.7 : 1.5), 0, TAU); ctx.stroke();
      }
      // label：枢纽、选中、悬停、推荐度高 或 缩放较大时显示
      var showLabel = nn.isHub || isSel || isHover || (this.scale > 0.62 && (nn.rec >= 4 || nn.highlighted)) || this.scale > 1.05;
      if (showLabel && nn.name) {
        var fontPx = nn.isHub ? 13 : clamp(11 * Math.max(this.scale, 0.7), 10, 15);
        ctx.font = '700 ' + fontPx + 'px "PingFang SC","Microsoft YaHei",sans-serif';
        ctx.textAlign = 'center';
        var label = nn.name + (nn.lang ? ' ⭐' : '');
        var ly = pp.y + cr + fontPx + 3;
        ctx.fillStyle = 'rgba(5,8,16,.55)';
        var tw2 = ctx.measureText(label).width;
        ctx.fillRect(pp.x - tw2 / 2 - 5, ly - fontPx, tw2 + 10, fontPx + 6);
        ctx.fillStyle = isSel ? '#fff' : (nn.isHub ? '#fde68a' : 'rgba(232,237,247,.92)');
        ctx.fillText(label, pp.x, ly);
      }
    }
    ctx.restore();
  };

  CareerNetwork.prototype.start = function () {
    if (this._raf) return;
    var self = this, last = performance.now();
    function loop(now) {
      var dt = Math.min((now - last) / 1000, 0.05); last = now;
      self.timeSec += dt;
      self.scale = lerp(self.scale, self.targetScale, 0.18);
      self._physics(dt);
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
