/* 职业发展网络 · 校园星空扇区（Canvas2D，无依赖）
 *
 * 视觉模型：
 *   - 地面/学校剪影 = 现在；越往天空越代表更远的未来。
 *   - 每次只展示一个就业分类扇区，所有该分类下的就业方向从地面向天空扇形散发。
 *   - 星光亮度 = 推荐度/契合度；点击就业星光后，当前方向的成长路径被点亮并弹出信息卡。
 *   - 左右切换分类时，当前扇区绕地面旋转下去，下一个扇区从另一侧旋转上来，星空背景同步旋转并带动态模糊。
 *
 * 暴露 window.CareerNetwork。回调：onHighlight(cards)、onBackground()、onHover(info|null)、onSectorChange(info)。
 */
(function () {
  'use strict';

  var TAU = Math.PI * 2;
  var ORIGIN_ID = '__ground_now__';
  var SWITCH_MS = 900;
  var FAN_ENTER_ANGLE = 1.08;
  var MAX_STAGE_COUNT = 5;
  var TIME_LABELS = ['现在', '0-1 年', '1-3 年', '3-5 年', '5-10 年', '10 年+'];

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function ease(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function hexToRgb(hex) {
    var h = String(hex || '#6ee7ff').replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }
  function rgba(c, a) { return 'rgba(' + c.r + ',' + c.g + ',' + c.b + ',' + clamp(a, 0, 1) + ')'; }
  function colorHex(c) {
    return '#' + ((1 << 24) + (c.r << 16) + (c.g << 8) + c.b).toString(16).slice(1);
  }
  function rotatePoint(x, y, px, py, angle) {
    var dx = x - px, dy = y - py;
    var cs = Math.cos(angle), sn = Math.sin(angle);
    return { x: px + dx * cs - dy * sn, y: py + dx * sn + dy * cs };
  }
  function ellipsis(ctx, text, maxWidth) {
    text = String(text || '');
    if (ctx.measureText(text).width <= maxWidth) return text;
    var out = text;
    while (out.length > 1 && ctx.measureText(out + '…').width > maxWidth) out = out.slice(0, -1);
    return out + '…';
  }

  function CareerNetwork(canvas, options) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.opts = options || {};
    this.onHighlight = this.opts.onHighlight || function () {};
    this.onBackground = this.opts.onBackground || function () {};
    this.onHover = this.opts.onHover || function () {};
    this.onSectorChange = this.opts.onSectorChange || function () {};

    this.dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
    this.W = 0;
    this.H = 0;
    this.spacing = 1;
    this.targetSpacing = 1;
    this.timeSec = 0;
    this.skyBaseAngle = 0;
    this._raf = null;

    this.network = { cats: [], nodes: [], links: [] };
    this.sectors = [];
    this.sectorIndex = 0;
    this.transition = null;
    this.byId = {};
    this.adjF = {};
    this.adjB = {};
    this.selectedId = null;
    this.related = null;
    this.hoverId = null;
    this.clickables = [];
    this.pointer = { x: 0, y: 0, downX: 0, downY: 0, moved: false };

    this.skyStars = [];
    this.milkyStars = [];
    this.nebula = [];
    this.grass = [];
    this._seedScene();
    this._bind();
    this.resize();
  }

  CareerNetwork.prototype._hash = function (str) {
    var h = 2166136261;
    str = String(str || '');
    for (var i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = (h * 16777619) >>> 0;
    }
    return h >>> 0;
  };

  CareerNetwork.prototype._rnd = function (key, shift) {
    var h = typeof key === 'number' ? key >>> 0 : this._hash(key);
    return ((h >>> (shift || 0)) % 10000) / 10000;
  };

  CareerNetwork.prototype._seedScene = function () {
    var i, h, t, off;
    this.skyStars = [];
    for (i = 0; i < 1100; i++) {
      h = this._hash('sky-star-' + i);
      this.skyStars.push({
        wx: (this._rnd(h, 0) - 0.5) * 2.55,
        wy: -0.08 - Math.pow(this._rnd(h, 8), 0.82) * 1.08,
        core: 0.35 + this._rnd(h, 18) * 1.45,
        alpha: 0.08 + this._rnd(h, 3) * 0.62,
        phase: this._rnd(h, 6) * TAU,
        speed: 0.38 + this._rnd(h, 11) * 0.95,
        warm: h % 9 === 0,
        spike: h % 41 === 0
      });
    }

    this.milkyStars = [];
    for (i = 0; i < 2600; i++) {
      h = this._hash('milky-star-' + i);
      t = this._rnd(h, 2);
      off = (this._rnd(h, 12) - 0.5) * (0.22 + 0.18 * Math.sin(t * Math.PI));
      this.milkyStars.push({
        t: t,
        off: off,
        core: 0.32 + this._rnd(h, 18) * 1.15,
        alpha: 0.05 + this._rnd(h, 5) * 0.28,
        phase: this._rnd(h, 7) * TAU,
        speed: 0.22 + this._rnd(h, 14) * 0.72,
        warm: h % 8 === 0
      });
    }

    this.nebula = [
      { x: 0.22, y: 0.26, r: 0.35, color: hexToRgb('#3268d8'), alpha: 0.08 },
      { x: 0.72, y: 0.20, r: 0.32, color: hexToRgb('#9b5de5'), alpha: 0.07 },
      { x: 0.64, y: 0.55, r: 0.42, color: hexToRgb('#0f9f9a'), alpha: 0.055 },
      { x: 0.36, y: 0.64, r: 0.30, color: hexToRgb('#f5b14c'), alpha: 0.04 }
    ];

    this.grass = [];
    for (i = 0; i < 260; i++) {
      h = this._hash('grass-' + i);
      this.grass.push({
        x: this._rnd(h, 0),
        h: 8 + this._rnd(h, 9) * 28,
        bend: (this._rnd(h, 18) - 0.5) * 10,
        a: 0.12 + this._rnd(h, 4) * 0.32
      });
    }
  };

  CareerNetwork.prototype._layout = function () {
    var ground = this.H * (this.W < 760 ? 0.84 : 0.82);
    var top = Math.max(118, this.H * 0.14);
    return {
      groundY: ground,
      horizonY: ground - Math.min(82, this.H * 0.105),
      topY: top,
      pivotX: this.W / 2,
      pivotY: ground + Math.min(38, this.H * 0.045),
      skyHeight: Math.max(180, ground - top)
    };
  };

  CareerNetwork.prototype.setData = function (network, personalized) {
    this.network = network || { cats: [], nodes: [], links: [] };
    this.personalized = personalized || {};
    this._buildSectors();
    this.select(null);
    this.sectorIndex = clamp(this.sectorIndex, 0, Math.max(0, this.sectors.length - 1));
    this.transition = null;
    this._emitSector();
    this.start();
  };

  CareerNetwork.prototype._buildSectors = function () {
    var self = this;
    var cats = this.network.cats || [];
    var nodes = this.network.nodes || [];
    var catMeta = {};
    cats.forEach(function (c) {
      catMeta[c.id] = {
        id: c.id,
        name: c.name || c.id,
        icon: c.icon || '',
        color: hexToRgb(c.c1 || '#6ee7ff'),
        c1: c.c1 || '#6ee7ff'
      };
    });

    var catOrder = cats.map(function (c) { return c.id; });
    nodes.forEach(function (n) {
      if (catOrder.indexOf(n.cat) < 0) catOrder.push(n.cat);
      if (!catMeta[n.cat]) catMeta[n.cat] = { id: n.cat, name: n.cat || '就业方向', icon: '', color: hexToRgb('#6ee7ff'), c1: '#6ee7ff' };
    });

    this.sectors = [];
    this.byId = {};
    this.adjF = {};
    this.adjB = {};

    catOrder.forEach(function (cid) {
      var dirs = nodes.filter(function (n) { return n.cat === cid; });
      if (!dirs.length) return;
      dirs = dirs.slice().sort(function (a, b) {
        var ar = Number(a.rec || 0), br = Number(b.rec || 0);
        if (br !== ar) return br - ar;
        return String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN');
      });
      var sec = {
        id: cid,
        meta: catMeta[cid],
        directions: [],
        stars: []
      };
      dirs.forEach(function (d, di) {
        var rec = clamp(Number(d.rec || 3), 1, 5);
        var glow = typeof d.glow === 'number'
          ? clamp(d.glow, 0.16, 1)
          : clamp(0.14 + rec * 0.16 + (d.highlighted ? 0.16 : 0), 0.2, 1);
        var tl = (d.tl && d.tl.length ? d.tl : [['0-1 年', d.name || '入门方向', d.desc || '']]).slice(0, MAX_STAGE_COUNT);
        var slot = dirs.length === 1 ? 0 : (di / (dirs.length - 1) - 0.5) * 2;
        var seed = self._hash((d.tag || d.name || 'dir') + '-layout');
        var path = {
          tag: d.tag,
          dir: d,
          rec: rec,
          glow: glow,
          slot: slot,
          curve: (self._rnd(seed, 5) - 0.5) * 0.26,
          labelSide: slot < -0.18 ? -1 : (slot > 0.18 ? 1 : (di % 2 ? 1 : -1)),
          labelStage: dirs.length > 7 ? (di % Math.min(3, tl.length)) : 0,
          stars: []
        };
        tl.forEach(function (stage, si) {
          var sid = (d.tag || ('dir' + di)) + '-' + si;
          var sh = self._hash(sid);
          var star = {
            id: sid,
            tag: d.tag,
            cat: d.cat,
            stage: si,
            stageCount: tl.length,
            dir: d,
            direction: path,
            name: d.name || '就业方向',
            phase: stage[0] || TIME_LABELS[Math.min(si + 1, TIME_LABELS.length - 1)] || '',
            role: stage[1] || d.name || '',
            sdesc: stage[2] || '',
            rec: rec,
            glow: clamp(glow * (1 - si * 0.04), 0.12, 1),
            color: catMeta[d.cat] ? catMeta[d.cat].color : hexToRgb('#6ee7ff'),
            core: 1.8 + rec * 0.58,
            twPhase: self._rnd(sh, 2) * TAU,
            twSpeed: 0.55 + self._rnd(sh, 14) * 0.85,
            jitterX: (self._rnd(sh, 5) - 0.5) * 28,
            jitterY: (self._rnd(sh, 11) - 0.5) * 16,
            x: 0,
            y: 0,
            sx: 0,
            sy: 0
          };
          path.stars.push(star);
          sec.stars.push(star);
          self.byId[sid] = star;
        });
        sec.directions.push(path);
      });
      self.sectors.push(sec);
    });

    function link(from, to, kind) {
      self.adjF[from] = self.adjF[from] || [];
      self.adjB[to] = self.adjB[to] || [];
      self.adjF[from].push(to);
      self.adjB[to].push(from);
    }
    this.sectors.forEach(function (sec) {
      sec.directions.forEach(function (path) {
        if (!path.stars.length) return;
        link(ORIGIN_ID, path.stars[0].id, 'fan');
        for (var i = 0; i < path.stars.length - 1; i++) link(path.stars[i].id, path.stars[i + 1].id, 'main');
      });
    });
    (this.network.links || []).forEach(function (l) {
      var from = l[0] + '-' + l[1];
      var to = l[2] + '-' + l[3];
      if (self.byId[from] && self.byId[to]) link(from, to, 'cross');
    });
  };

  CareerNetwork.prototype._bind = function () {
    var self = this;
    this._onResize = function () { self.resize(); };
    window.addEventListener('resize', this._onResize);
    this.canvas.addEventListener('pointerdown', function (e) { self._pointerDown(e); });
    this.canvas.addEventListener('pointermove', function (e) { self._pointerMove(e); });
    window.addEventListener('pointerup', function (e) { self._pointerUp(e); });
    this.canvas.addEventListener('pointerleave', function () {
      self.hoverId = null;
      self.onHover(null);
      self.canvas.style.cursor = 'default';
    });
    this.canvas.addEventListener('wheel', function (e) { self._wheel(e); }, { passive: false });
  };

  CareerNetwork.prototype.resize = function () {
    var rect = this.canvas.getBoundingClientRect();
    this.W = rect.width || window.innerWidth;
    this.H = rect.height || window.innerHeight;
    this.canvas.width = Math.round(this.W * this.dpr);
    this.canvas.height = Math.round(this.H * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    if (this.selectedId) this._emitHighlight();
  };

  CareerNetwork.prototype._localPos = function (e) {
    var r = this.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  CareerNetwork.prototype._pointerDown = function (e) {
    var p = this._localPos(e);
    this.pointer.x = p.x;
    this.pointer.y = p.y;
    this.pointer.downX = p.x;
    this.pointer.downY = p.y;
    this.pointer.moved = false;
    if (this.canvas.setPointerCapture) this.canvas.setPointerCapture(e.pointerId);
  };

  CareerNetwork.prototype._pointerMove = function (e) {
    var p = this._localPos(e);
    this.pointer.x = p.x;
    this.pointer.y = p.y;
    var dx = p.x - this.pointer.downX;
    var dy = p.y - this.pointer.downY;
    if (dx * dx + dy * dy > 36) this.pointer.moved = true;
    var hit = this._pick(p.x, p.y);
    if ((hit && hit.id) !== this.hoverId) {
      this.hoverId = hit ? hit.id : null;
      this.canvas.style.cursor = hit ? 'pointer' : 'default';
    }
    this.onHover(hit ? { name: hit.name, phase: hit.phase, role: hit.role, rec: hit.rec, x: hit.sx, y: hit.sy } : null);
  };

  CareerNetwork.prototype._pointerUp = function () {
    if (this.pointer.moved) return;
    var hit = this._pick(this.pointer.x, this.pointer.y);
    if (hit) {
      this.select(hit.id);
    } else {
      this.select(null);
      this.onBackground();
    }
  };

  CareerNetwork.prototype._wheel = function (e) {
    e.preventDefault();
    var factor = Math.pow(1.0013, -e.deltaY);
    this.targetSpacing = clamp(this.targetSpacing * factor, 0.72, 1.48);
    if (this.selectedId) this._emitHighlight();
  };

  CareerNetwork.prototype._pick = function (x, y) {
    if (this.transition) return null;
    var best = null;
    var bestD = Infinity;
    for (var i = 0; i < this.clickables.length; i++) {
      var s = this.clickables[i];
      var r = s.hitR || 13;
      var dx = x - s.sx;
      var dy = y - s.sy;
      var d = dx * dx + dy * dy;
      if (d <= r * r && d < bestD) {
        best = s;
        bestD = d;
      }
    }
    return best;
  };

  CareerNetwork.prototype.sectorInfo = function () {
    var sec = this.sectors[this.sectorIndex] || null;
    return {
      index: this.sectorIndex,
      total: this.sectors.length,
      id: sec ? sec.id : '',
      name: sec ? sec.meta.name : '',
      count: sec ? sec.directions.length : 0,
      transitioning: !!this.transition
    };
  };

  CareerNetwork.prototype._emitSector = function () {
    this.onSectorChange(this.sectorInfo());
  };

  CareerNetwork.prototype.nextSector = function () { this._switchSector(1); };
  CareerNetwork.prototype.prevSector = function () { this._switchSector(-1); };

  CareerNetwork.prototype._switchSector = function (dir) {
    if (this.transition || this.sectors.length < 2) return;
    var from = this.sectorIndex;
    var total = this.sectors.length;
    var to = (from + dir + total) % total;
    this.select(null);
    this.transition = {
      from: from,
      to: to,
      dir: dir,
      started: performance.now(),
      t: 0,
      skyFrom: this.skyBaseAngle,
      skyTo: this.skyBaseAngle + dir * 0.36
    };
    this.onHighlight([]);
    this._emitSector();
  };

  CareerNetwork.prototype.select = function (id) {
    this.selectedId = id || null;
    if (!id) {
      this.related = null;
      this.onHighlight([]);
      return;
    }
    var selected = this.byId[id];
    if (!selected) return;
    var active = this.sectors[this.sectorIndex];
    if (!active || selected.cat !== active.id) return;

    var self = this;
    var set = {};
    set[ORIGIN_ID] = true;
    function include(sid) {
      if (sid === ORIGIN_ID) return true;
      var s = self.byId[sid];
      return !!(s && s.cat === active.id);
    }
    function add(sid, stack) {
      if (!include(sid) || set[sid]) return;
      set[sid] = true;
      stack.push(sid);
    }

    var st = [id], cur;
    set[id] = true;
    while (st.length) {
      cur = st.pop();
      (this.adjB[cur] || []).forEach(function (sid) { add(sid, st); });
    }
    st = [id];
    while (st.length) {
      cur = st.pop();
      (this.adjF[cur] || []).forEach(function (sid) { add(sid, st); });
    }

    this.related = set;
    this._emitHighlight();
  };

  CareerNetwork.prototype._emitHighlight = function () {
    if (!this.selectedId || !this.related) {
      this.onHighlight([]);
      return;
    }
    var cards = [];
    var self = this;
    var layout = this._layout();
    var sec = this.sectors[this.sectorIndex];
    if (!sec) return;
    this._projectSector(sec, layout, 0);
    Object.keys(this.related).forEach(function (id) {
      if (id === ORIGIN_ID) return;
      var s = self.byId[id];
      if (!s || s.cat !== sec.id) return;
      var d = s.dir || {};
      var skills = ((d.pre || []).concat(d.know || [])).slice(0, 3);
      cards.push({
        id: id,
        tag: s.tag,
        x: s.sx,
        y: s.sy,
        name: s.name,
        phase: s.phase,
        role: s.role,
        rec: s.rec,
        stage: s.stage,
        sdesc: s.sdesc,
        desc: d.desc || '',
        tip: d.tip || '',
        baseRec: d.base_rec,
        lang: !!d.lang,
        cat: s.cat,
        skills: skills,
        isClicked: id === self.selectedId,
        colorHex: colorHex(s.color)
      });
    });
    this.onHighlight(cards);
  };

  CareerNetwork.prototype._projectSector = function (sec, layout, angle) {
    var self = this;
    var available = layout.skyHeight;
    sec.directions.forEach(function (path) {
      path.stars.forEach(function (s) {
        var stageCount = Math.max(1, s.stageCount || path.stars.length);
        var future = (s.stage + 1) / (stageCount + 1);
        var dist = available * (0.13 + future * 0.84) * self.spacing;
        var y = layout.groundY - dist + s.jitterY;
        var fanFactor = self.W < 640 ? 0.56 : 0.92;
        var fanWidth = Math.max(42, dist * fanFactor);
        var stageCurve = Math.sin(future * Math.PI) * path.curve * fanWidth;
        var x = layout.pivotX + path.slot * fanWidth + stageCurve + s.jitterX;
        var p = rotatePoint(x, y, layout.pivotX, layout.pivotY, angle);
        s.x = x;
        s.y = y;
        s.sx = p.x;
        s.sy = p.y;
        s.hitR = Math.max(11, s.core + 8);
      });
    });
  };

  CareerNetwork.prototype._drawBackground = function (ctx, layout, angle, blur) {
    var W = this.W, H = this.H;
    var sky = ctx.createLinearGradient(0, 0, 0, H);
    sky.addColorStop(0, '#02040d');
    sky.addColorStop(0.45, '#09162c');
    sky.addColorStop(0.78, '#10223b');
    sky.addColorStop(1, '#07100d');
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, W, H);

    ctx.save();
    ctx.translate(layout.pivotX, layout.pivotY);
    ctx.rotate(angle);
    ctx.translate(-layout.pivotX, -layout.pivotY);
    if (blur > 0.1) ctx.filter = 'blur(' + blur.toFixed(1) + 'px)';

    this._drawNebula(ctx);
    this._drawMilkyWay(ctx, layout);
    this._drawSkyStars(ctx, layout);

    ctx.restore();
    ctx.filter = 'none';
  };

  CareerNetwork.prototype._drawNebula = function (ctx) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (var i = 0; i < this.nebula.length; i++) {
      var n = this.nebula[i];
      var x = n.x * this.W;
      var y = n.y * this.H;
      var r = n.r * Math.max(this.W, this.H);
      var g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, rgba(n.color, n.alpha));
      g.addColorStop(0.46, rgba(n.color, n.alpha * 0.32));
      g.addColorStop(1, rgba(n.color, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  };

  CareerNetwork.prototype._milkyPoint = function (t, off, layout) {
    var W = this.W, H = this.H;
    var x = lerp(-W * 0.18, W * 1.18, t);
    var curve = Math.sin((t - 0.08) * Math.PI * 1.22);
    var y = lerp(layout.groundY - layout.skyHeight * 0.18, layout.topY + layout.skyHeight * 0.06, t) - curve * H * 0.16;
    var nx = -0.56 + t * 1.18;
    var ny = -0.82 - Math.cos(t * Math.PI) * 0.16;
    return {
      x: x + off * W * 0.28,
      y: y + off * H * 0.18,
      nx: nx,
      ny: ny
    };
  };

  CareerNetwork.prototype._drawMilkyWay = function (ctx, layout) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    function band(stroke, width, alpha, shadow) {
      ctx.save();
      ctx.shadowColor = stroke;
      ctx.shadowBlur = shadow;
      ctx.strokeStyle = stroke.replace('ALPHA', alpha);
      ctx.lineWidth = width;
      ctx.beginPath();
      ctx.moveTo(-this.W * 0.18, layout.groundY - layout.skyHeight * 0.18);
      ctx.bezierCurveTo(this.W * 0.23, layout.topY - this.H * 0.04, this.W * 0.62, layout.topY + this.H * 0.04, this.W * 1.18, layout.topY + layout.skyHeight * 0.22);
      ctx.stroke();
      ctx.restore();
    }
    band.call(this, 'rgba(92,166,255,ALPHA)', 126, 0.055, 34);
    band.call(this, 'rgba(235,246,255,ALPHA)', 62, 0.045, 24);
    band.call(this, 'rgba(255,226,172,ALPHA)', 22, 0.034, 16);

    ctx.globalCompositeOperation = 'source-over';
    ctx.strokeStyle = 'rgba(1,7,18,0.18)';
    ctx.lineWidth = 28;
    ctx.beginPath();
    ctx.moveTo(-this.W * 0.12, layout.groundY - layout.skyHeight * 0.1);
    ctx.bezierCurveTo(this.W * 0.22, layout.topY + this.H * 0.04, this.W * 0.6, layout.topY + this.H * 0.12, this.W * 1.12, layout.topY + layout.skyHeight * 0.3);
    ctx.stroke();

    ctx.globalCompositeOperation = 'lighter';
    for (var i = 0; i < this.milkyStars.length; i++) {
      var s = this.milkyStars[i];
      var p = this._milkyPoint(s.t, s.off, layout);
      var tw = 0.72 + 0.28 * Math.sin(this.timeSec * s.speed + s.phase);
      var a = s.alpha * tw;
      var col = s.warm ? '255,231,183' : '206,231,255';
      ctx.fillStyle = 'rgba(' + col + ',' + a + ')';
      ctx.beginPath();
      ctx.arc(p.x, p.y, s.core, 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  };

  CareerNetwork.prototype._drawSkyStars = function (ctx, layout) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (var i = 0; i < this.skyStars.length; i++) {
      var s = this.skyStars[i];
      var x = layout.pivotX + s.wx * this.W * this.spacing;
      var y = layout.pivotY + s.wy * this.H * this.spacing;
      if (x < -30 || x > this.W + 30 || y < -40 || y > layout.groundY + 20) continue;
      var tw = 0.68 + 0.32 * Math.sin(this.timeSec * s.speed + s.phase);
      var a = s.alpha * tw;
      var col = s.warm ? '255,227,177' : '210,230,255';
      ctx.fillStyle = 'rgba(' + col + ',' + a + ')';
      ctx.beginPath();
      ctx.arc(x, y, s.core, 0, TAU);
      ctx.fill();
      if (s.spike && a > 0.18) {
        ctx.strokeStyle = 'rgba(' + col + ',' + Math.min(0.28, a * 0.75) + ')';
        ctx.lineWidth = 0.7;
        var L = 6 + s.core * 6;
        ctx.beginPath();
        ctx.moveTo(x - L, y);
        ctx.lineTo(x + L, y);
        ctx.moveTo(x, y - L);
        ctx.lineTo(x, y + L);
        ctx.stroke();
      }
    }
    ctx.restore();
  };

  CareerNetwork.prototype._drawTimeBands = function (ctx, layout, sec, opacity) {
    ctx.save();
    ctx.strokeStyle = 'rgba(190,216,255,' + (0.08 * opacity) + ')';
    ctx.fillStyle = 'rgba(210,230,255,' + (0.34 * opacity) + ')';
    ctx.font = '700 11px "PingFang SC","Microsoft YaHei",sans-serif';
    ctx.textAlign = 'center';
    ctx.setLineDash([2, 9]);
    var color = sec && sec.meta ? sec.meta.color : hexToRgb('#6ee7ff');
    for (var i = 1; i <= 4; i++) {
      var f = i / 5;
      var d = layout.skyHeight * (0.13 + f * 0.84) * this.spacing;
      var y = layout.groundY - d;
      var spread = Math.max(64, d * 0.98);
      ctx.strokeStyle = rgba(color, 0.08 * opacity);
      ctx.beginPath();
      ctx.moveTo(layout.pivotX - spread, y);
      ctx.quadraticCurveTo(layout.pivotX, y - 22, layout.pivotX + spread, y);
      ctx.stroke();
      ctx.fillStyle = 'rgba(215,232,255,' + (0.24 * opacity) + ')';
      ctx.fillText(TIME_LABELS[i] || '', layout.pivotX - spread - 34, y + 4);
    }
    ctx.setLineDash([]);
    ctx.restore();
  };

  CareerNetwork.prototype._star = function (ctx, x, y, core, col, bright, spike) {
    var halo = core * (4.2 + bright * 5.8);
    var g = ctx.createRadialGradient(x, y, 0, x, y, halo);
    g.addColorStop(0, rgba(col, 0.62 * bright));
    g.addColorStop(0.42, rgba(col, 0.16 * bright));
    g.addColorStop(1, rgba(col, 0));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(x, y, halo, 0, TAU);
    ctx.fill();
    if (spike && bright > 0.55) {
      var L = core * (5 + bright * 5);
      ctx.strokeStyle = rgba(col, 0.46 * bright);
      ctx.lineWidth = 0.85;
      ctx.beginPath();
      ctx.moveTo(x - L, y);
      ctx.lineTo(x + L, y);
      ctx.moveTo(x, y - L);
      ctx.lineTo(x, y + L);
      ctx.stroke();
    }
  };

  CareerNetwork.prototype._drawSector = function (ctx, sec, layout, angle, opacity, blur, activeForHit) {
    if (!sec) return;
    this._projectSector(sec, layout, angle);
    if (activeForHit) this.clickables = [];
    ctx.save();
    if (blur > 0.1) ctx.filter = 'blur(' + blur.toFixed(1) + 'px)';

    this._drawTimeBands(ctx, layout, sec, opacity);

    var hasSel = !!(this.selectedId && this.related);
    var color = sec.meta.color || hexToRgb('#6ee7ff');
    if (this.W >= 700) {
      var titleY = Math.max(104, layout.topY - 26);
      ctx.save();
      ctx.textAlign = 'center';
      ctx.font = '800 13px "PingFang SC","Microsoft YaHei",sans-serif';
      ctx.fillStyle = rgba(color, 0.75 * opacity);
      var title = (sec.meta.icon ? sec.meta.icon + ' ' : '') + sec.meta.name + ' · ' + sec.directions.length + ' 个方向';
      ctx.fillText(title, layout.pivotX, titleY);
      ctx.restore();
    }

    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    for (var p = 0; p < sec.directions.length; p++) {
      var path = sec.directions[p];
      if (!path.stars.length) continue;
      var hotPath = hasSel && path.stars.some(function (s) { return !!this.related[s.id]; }, this);
      var dimPath = hasSel && !hotPath;
      var lineAlpha = (dimPath ? 0.05 : 0.13 + path.glow * 0.18) * opacity;
      var lineWidth = hotPath ? 2.2 : 0.9;
      ctx.strokeStyle = hotPath ? rgba(color, 0.86 * opacity) : rgba(color, lineAlpha);
      ctx.lineWidth = lineWidth;
      ctx.beginPath();
      var first = path.stars[0];
      ctx.moveTo(layout.pivotX, layout.groundY - 10);
      ctx.quadraticCurveTo((layout.pivotX + first.sx) / 2, layout.groundY - 44, first.sx, first.sy);
      for (var si = 1; si < path.stars.length; si++) {
        var prev = path.stars[si - 1], cur = path.stars[si];
        ctx.quadraticCurveTo((prev.sx + cur.sx) / 2, Math.min(prev.sy, cur.sy) - 18, cur.sx, cur.sy);
      }
      ctx.stroke();
    }
    ctx.restore();

    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (var i = 0; i < sec.stars.length; i++) {
      var s = sec.stars[i];
      var inPath = !hasSel || (this.related && this.related[s.id]);
      var tw = 0.7 + 0.3 * Math.sin(this.timeSec * s.twSpeed + s.twPhase);
      var bright = s.glow * tw * (inPath ? 1 : 0.17) * opacity;
      this._star(ctx, s.sx, s.sy, s.core, s.color, bright, s.glow > 0.56 && inPath);
    }
    ctx.restore();

    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    for (var j = 0; j < sec.stars.length; j++) {
      var n = sec.stars[j];
      var related = !hasSel || (this.related && this.related[n.id]);
      var selected = n.id === this.selectedId;
      var hovered = n.id === this.hoverId;
      ctx.fillStyle = 'rgba(255,255,255,' + ((related ? 0.96 : 0.28) * opacity) + ')';
      ctx.beginPath();
      ctx.arc(n.sx, n.sy, n.core * (selected ? 1.45 : 1), 0, TAU);
      ctx.fill();
      if ((selected || hovered) && related) {
        ctx.strokeStyle = selected ? 'rgba(255,255,255,' + opacity + ')' : rgba(n.color, 0.9 * opacity);
        ctx.lineWidth = selected ? 2 : 1.2;
        ctx.beginPath();
        ctx.arc(n.sx, n.sy, n.core + (selected ? 6 : 4), 0, TAU);
        ctx.stroke();
      }
      if (activeForHit) this.clickables.push(n);
    }
    ctx.restore();

    this._drawDirectionLabels(ctx, sec, opacity, hasSel);
    if (hasSel) this._drawSelectedBeam(ctx, sec, layout, opacity);

    ctx.restore();
    ctx.filter = 'none';
  };

  CareerNetwork.prototype._drawDirectionLabels = function (ctx, sec, opacity, hasSel) {
    ctx.save();
    ctx.font = '800 11px "PingFang SC","Microsoft YaHei",sans-serif';
    ctx.textBaseline = 'middle';
    sec.directions.forEach(function (path) {
      if (!path.stars.length) return;
      var labelStage = Math.min(path.stars.length - 1, Math.max(0, path.labelStage || 0));
      var s = path.stars[labelStage];
      var hot = !hasSel || path.stars.some(function (st) { return this.related && this.related[st.id]; }, this);
      var a = (hot ? 0.78 : 0.22) * opacity;
      var text = ellipsis(ctx, path.dir.name || '', this.W < 640 ? 92 : 104);
      var side = path.labelSide;
      if (this.W < 640) {
        if (s.sx < 118) side = 1;
        else if (s.sx > this.W - 118) side = -1;
      }
      var x = s.sx + side * 14;
      var y = s.sy + (labelStage === 0 ? 18 : (labelStage === 1 ? 6 : -10));
      ctx.textAlign = side > 0 ? 'left' : 'right';
      var w = ctx.measureText(text).width + 12;
      var bx = side > 0 ? x - 6 : x - w + 6;
      ctx.fillStyle = 'rgba(3,7,14,' + (0.5 * a) + ')';
      ctx.fillRect(bx, y - 10, w, 20);
      ctx.fillStyle = 'rgba(232,242,255,' + a + ')';
      ctx.fillText(text, x, y);
    }, this);
    ctx.restore();
  };

  CareerNetwork.prototype._drawSelectedBeam = function (ctx, sec, layout, opacity) {
    if (!this.related) return;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.lineCap = 'round';
    var color = sec.meta.color || hexToRgb('#6ee7ff');
    sec.directions.forEach(function (path) {
      var pts = path.stars.filter(function (s) { return this.related[s.id]; }, this);
      if (!pts.length) return;
      ctx.strokeStyle = rgba(color, 0.34 * opacity);
      ctx.lineWidth = 9;
      ctx.beginPath();
      ctx.moveTo(layout.pivotX, layout.groundY - 6);
      pts.forEach(function (p) { ctx.lineTo(p.sx, p.sy); });
      ctx.stroke();
      ctx.strokeStyle = 'rgba(235,250,255,' + (0.82 * opacity) + ')';
      ctx.lineWidth = 1.8;
      ctx.beginPath();
      ctx.moveTo(layout.pivotX, layout.groundY - 6);
      pts.forEach(function (p) { ctx.lineTo(p.sx, p.sy); });
      ctx.stroke();
    }, this);
    ctx.restore();
  };

  CareerNetwork.prototype._drawGround = function (ctx, layout) {
    var W = this.W, H = this.H;
    ctx.save();
    var ground = ctx.createLinearGradient(0, layout.horizonY, 0, H);
    ground.addColorStop(0, 'rgba(9,30,22,0.55)');
    ground.addColorStop(0.34, '#07140f');
    ground.addColorStop(1, '#020604');
    ctx.fillStyle = ground;
    ctx.beginPath();
    ctx.moveTo(0, layout.horizonY + 18);
    ctx.quadraticCurveTo(W * 0.28, layout.horizonY - 12, W * 0.55, layout.horizonY + 7);
    ctx.quadraticCurveTo(W * 0.78, layout.horizonY + 25, W, layout.horizonY - 3);
    ctx.lineTo(W, H);
    ctx.lineTo(0, H);
    ctx.closePath();
    ctx.fill();

    this._drawSchool(ctx, layout);

    ctx.save();
    ctx.strokeStyle = 'rgba(122,210,151,0.16)';
    ctx.lineWidth = 1;
    for (var i = 0; i < this.grass.length; i++) {
      var g = this.grass[i];
      var x = g.x * W;
      var y = layout.horizonY + 20 + (i % 13);
      ctx.globalAlpha = g.a;
      ctx.beginPath();
      ctx.moveTo(x, y + g.h);
      ctx.quadraticCurveTo(x + g.bend, y + g.h * 0.45, x + g.bend * 0.6, y);
      ctx.stroke();
    }
    ctx.restore();

    var glow = ctx.createRadialGradient(layout.pivotX, layout.groundY - 8, 0, layout.pivotX, layout.groundY - 8, 150);
    glow.addColorStop(0, 'rgba(255,235,170,0.32)');
    glow.addColorStop(0.28, 'rgba(110,231,255,0.14)');
    glow.addColorStop(1, 'rgba(110,231,255,0)');
    ctx.globalCompositeOperation = 'lighter';
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(layout.pivotX, layout.groundY - 8, 150, 0, TAU);
    ctx.fill();
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = 'rgba(255,245,205,0.96)';
    ctx.beginPath();
    ctx.arc(layout.pivotX, layout.groundY - 8, 4.8, 0, TAU);
    ctx.fill();
    ctx.font = '800 12px "PingFang SC","Microsoft YaHei",sans-serif';
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(255,240,180,0.92)';
    ctx.fillText('现在', layout.pivotX, layout.groundY + 18);
    ctx.restore();
  };

  CareerNetwork.prototype._drawSchool = function (ctx, layout) {
    var W = this.W;
    var y = layout.horizonY + 34;
    var cx = layout.pivotX;
    var scale = clamp(W / 1440, 0.62, 1.15);
    ctx.save();
    ctx.fillStyle = 'rgba(2,8,15,0.96)';
    ctx.strokeStyle = 'rgba(150,195,230,0.16)';
    ctx.lineWidth = 1;

    function rect(x, top, w, h) {
      ctx.beginPath();
      ctx.rect(x, top, w, h);
      ctx.fill();
      ctx.stroke();
    }
    rect(cx - 250 * scale, y - 44 * scale, 180 * scale, 44 * scale);
    rect(cx + 70 * scale, y - 44 * scale, 180 * scale, 44 * scale);
    rect(cx - 92 * scale, y - 82 * scale, 184 * scale, 82 * scale);
    rect(cx - 34 * scale, y - 128 * scale, 68 * scale, 46 * scale);

    ctx.beginPath();
    ctx.moveTo(cx - 112 * scale, y - 82 * scale);
    ctx.lineTo(cx, y - 118 * scale);
    ctx.lineTo(cx + 112 * scale, y - 82 * scale);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(cx - 270 * scale, y - 44 * scale);
    ctx.lineTo(cx - 160 * scale, y - 76 * scale);
    ctx.lineTo(cx - 50 * scale, y - 44 * scale);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx + 50 * scale, y - 44 * scale);
    ctx.lineTo(cx + 160 * scale, y - 76 * scale);
    ctx.lineTo(cx + 270 * scale, y - 44 * scale);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = 'rgba(255,215,135,0.33)';
    for (var row = 0; row < 3; row++) {
      for (var col = 0; col < 6; col++) {
        if ((row + col) % 4 === 0) continue;
        ctx.fillRect(cx - 72 * scale + col * 26 * scale, y - 66 * scale + row * 19 * scale, 9 * scale, 8 * scale);
      }
    }
    for (col = 0; col < 6; col++) {
      if (col % 3 !== 1) ctx.fillRect(cx - 228 * scale + col * 24 * scale, y - 28 * scale, 8 * scale, 7 * scale);
      if (col % 3 !== 0) ctx.fillRect(cx + 92 * scale + col * 24 * scale, y - 28 * scale, 8 * scale, 7 * scale);
    }

    ctx.fillStyle = 'rgba(1,5,10,0.98)';
    ctx.beginPath();
    ctx.rect(cx - 14 * scale, y - 30 * scale, 28 * scale, 30 * scale);
    ctx.fill();
    ctx.restore();
  };

  CareerNetwork.prototype._draw = function () {
    var ctx = this.ctx;
    var layout = this._layout();
    var blur = 0;
    var skyAngle = this.skyBaseAngle + Math.sin(this.timeSec * 0.018) * 0.018;

    if (this.transition) {
      var k = ease(this.transition.t);
      skyAngle = lerp(this.transition.skyFrom, this.transition.skyTo, k) + Math.sin(this.timeSec * 0.02) * 0.018;
      blur = Math.sin(k * Math.PI) * 3.4;
    }

    this._drawBackground(ctx, layout, skyAngle, blur);
    this.clickables = [];

    if (this.transition) {
      var tr = this.transition;
      var t = ease(tr.t);
      var fromSec = this.sectors[tr.from];
      var toSec = this.sectors[tr.to];
      this._drawSector(ctx, fromSec, layout, -tr.dir * FAN_ENTER_ANGLE * t, 1 - t * 0.28, Math.sin(t * Math.PI) * 2.1, false);
      this._drawSector(ctx, toSec, layout, tr.dir * FAN_ENTER_ANGLE * (1 - t), 0.42 + t * 0.58, Math.sin(t * Math.PI) * 2.4, false);
    } else {
      this._drawSector(ctx, this.sectors[this.sectorIndex], layout, 0, 1, 0, true);
    }

    this._drawGround(ctx, layout);
  };

  CareerNetwork.prototype.start = function () {
    if (this._raf) return;
    var self = this;
    var last = performance.now();
    function loop(now) {
      var dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      self.timeSec += dt;
      self.spacing = lerp(self.spacing, self.targetSpacing, 0.16);
      if (self.transition) {
        self.transition.t = Math.min(1, (now - self.transition.started) / SWITCH_MS);
        if (self.transition.t >= 1) {
          self.skyBaseAngle = self.transition.skyTo;
          self.sectorIndex = self.transition.to;
          self.transition = null;
          self._emitSector();
        }
      }
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
