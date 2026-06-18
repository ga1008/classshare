/* 职业发展网络 · 校园星空扇区（Canvas2D，无依赖）
 *
 * 视觉模型（融合真实照片）：
 *   - 背景层 = 真实银河星空照片（sky_background.webp），随扇区切换绕地面旋转 + 动态模糊。
 *   - 前景层 = 校园建筑 / 草坪 / 人物剪影照片（campus_foreground.webp），固定贴底，星图从屋顶后方升起。
 *   - 就业星图夹在前景与背景之间：靠近地面＝现在，越往天空＝越远的未来；扇形向上散发。
 *   - 每次只展示一个就业分类扇区，所有该分类下的就业方向从地面向天空扇形散发，星光亮度＝推荐度/契合度。
 *   - 左右切换分类：当前扇区绕地面旋转下去，下一个扇区从另一侧旋转上来，星空背景同步旋转并带动态模糊。
 *   - 星光持续呼吸闪烁；前景 / 背景与页面四边平滑渐隐融合。
 *
 * 暴露 window.CareerNetwork。回调：onHighlight(cards)、onBackground()、onHover(info|null)、onSectorChange(info)。
 */
(function () {
  'use strict';

  var TAU = Math.PI * 2;
  var ORIGIN_ID = '__ground_now__';
  var SWITCH_MS = 980;
  var FAN_ENTER_ANGLE = 1.12;     // 扇区进出旋转角（绕地面）
  var SKY_SWING = 0.30;           // 背景星空每次切换跟随旋转角
  var MAX_STAGE_COUNT = 5;
  var TIME_LABELS = ['现在', '0-1 年', '1-3 年', '3-5 年', '5-10 年', '10 年+'];

  // 照片合成参数（按 1600×2142 的素材标定）
  var ASSET_DEFAULTS = {
    background: '/static/img/career/sky_background.webp',
    foreground: '/static/img/career/campus_foreground.webp'
  };
  var BG_OVERSCALE = 1.18;        // 背景放大留旋转余量
  var SKY_BOTTOM_FRAC = 0.84;     // 背景照片此处对齐到画布底（裁掉照片里的楼，露上方银河）
  var FG_BUILDING_LINE = 0.85;    // 前景照片中"屋顶主体"所在的高度比例
  var FG_BUILDING_SCREEN = 0.80;  // 屋顶落在画布高度的此比例处
  var GROUND_FRAC = 0.80;         // 扇形起点（地面）所在画布高度比例

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
    this.assets = Object.assign({}, ASSET_DEFAULTS, this.opts.assets || {});
    this.bgImg = null;
    this.fgImg = null;
    this._seedScene();
    this._loadImages();
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

  CareerNetwork.prototype._loadImages = function () {
    var self = this;
    function load(src, set) {
      if (!src) return;
      var im = new Image();
      im.decoding = 'async';
      im.onload = function () { set(im); };
      im.onerror = function () { set(null); };
      im.src = src;
    }
    load(this.assets.background, function (im) { self.bgImg = im; });
    load(this.assets.foreground, function (im) { self.fgImg = im; });
  };

  // 呼吸闪烁的星尘叠加层（盖在真实星空照片上，给静态照片注入"活"的星光）。
  CareerNetwork.prototype._seedScene = function () {
    var i, h;
    this.skyStars = [];
    for (i = 0; i < 620; i++) {
      h = this._hash('sky-star-' + i);
      this.skyStars.push({
        wx: (this._rnd(h, 0) - 0.5) * 2.7,
        wy: -0.04 - Math.pow(this._rnd(h, 8), 0.78) * 1.18,
        core: 0.35 + this._rnd(h, 18) * 1.7,
        alpha: 0.06 + this._rnd(h, 3) * 0.7,
        phase: this._rnd(h, 6) * TAU,
        speed: 0.45 + this._rnd(h, 11) * 1.25,
        warm: h % 9 === 0,
        spike: h % 23 === 0
      });
    }
  };

  CareerNetwork.prototype._layout = function () {
    var groundY = this.H * GROUND_FRAC;
    var top = Math.max(110, this.H * 0.13);
    return {
      groundY: groundY,
      topY: top,
      pivotX: this.W / 2,
      pivotY: this.H * 0.99,
      skyHeight: Math.max(180, groundY - top)
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

    function link(from, to) {
      self.adjF[from] = self.adjF[from] || [];
      self.adjB[to] = self.adjB[to] || [];
      self.adjF[from].push(to);
      self.adjB[to].push(from);
    }
    this.sectors.forEach(function (sec) {
      sec.directions.forEach(function (path) {
        if (!path.stars.length) return;
        link(ORIGIN_ID, path.stars[0].id);
        for (var i = 0; i < path.stars.length - 1; i++) link(path.stars[i].id, path.stars[i + 1].id);
      });
    });
    (this.network.links || []).forEach(function (l) {
      var from = l[0] + '-' + l[1];
      var to = l[2] + '-' + l[3];
      if (self.byId[from] && self.byId[to]) link(from, to);
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
      skyTo: this.skyBaseAngle + dir * SKY_SWING
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

  // ===== 背景：真实星空照片（旋转 + 模糊）+ 呼吸星尘 =====
  CareerNetwork.prototype._drawBackground = function (ctx, layout, angle, blur) {
    var W = this.W, H = this.H;
    var base = ctx.createLinearGradient(0, 0, 0, H);
    base.addColorStop(0, '#02040c');
    base.addColorStop(0.5, '#05091a');
    base.addColorStop(1, '#02040d');
    ctx.fillStyle = base;
    ctx.fillRect(0, 0, W, H);

    ctx.save();
    ctx.translate(layout.pivotX, layout.pivotY);
    ctx.rotate(angle);
    ctx.translate(-layout.pivotX, -layout.pivotY);
    if (blur > 0.1) ctx.filter = 'blur(' + blur.toFixed(1) + 'px)';

    if (this.bgImg) {
      this._drawSkyPhoto(ctx);
      ctx.filter = 'none';
    } else {
      ctx.filter = 'none';
    }
    this._drawSkyStars(ctx, layout);
    ctx.restore();
    ctx.filter = 'none';
  };

  CareerNetwork.prototype._drawSkyPhoto = function (ctx) {
    var img = this.bgImg;
    var iw = img.naturalWidth || img.width;
    var ih = img.naturalHeight || img.height;
    var s = Math.max(this.W / iw, this.H / ih) * BG_OVERSCALE;
    var dw = iw * s, dh = ih * s;
    var dx = (this.W - dw) / 2;
    var dy = this.H - SKY_BOTTOM_FRAC * ih * s;
    if (dy > 0) dy = 0;
    ctx.drawImage(img, dx, dy, dw, dh);
    // 轻压暗，让职业星光更突出；顶部更暗、银河带保留亮度
    var grad = ctx.createLinearGradient(0, 0, 0, this.H);
    grad.addColorStop(0, 'rgba(3,6,16,0.42)');
    grad.addColorStop(0.4, 'rgba(3,6,16,0.12)');
    grad.addColorStop(1, 'rgba(3,6,16,0.30)');
    ctx.fillStyle = grad;
    ctx.fillRect(dx, dy, dw, dh);
  };

  CareerNetwork.prototype._drawSkyStars = function (ctx, layout) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (var i = 0; i < this.skyStars.length; i++) {
      var s = this.skyStars[i];
      var x = layout.pivotX + s.wx * this.W * this.spacing;
      var y = layout.pivotY + s.wy * this.H * this.spacing;
      if (x < -40 || x > this.W + 40 || y < -50 || y > layout.groundY + 30) continue;
      var tw = 0.6 + 0.4 * Math.sin(this.timeSec * s.speed + s.phase);
      var a = s.alpha * tw;
      if (a < 0.02) continue;
      var col = s.warm ? '255,227,177' : '214,233,255';
      ctx.fillStyle = 'rgba(' + col + ',' + a + ')';
      ctx.beginPath();
      ctx.arc(x, y, s.core, 0, TAU);
      ctx.fill();
      if (s.spike && a > 0.2) {
        ctx.strokeStyle = 'rgba(' + col + ',' + Math.min(0.3, a * 0.7) + ')';
        ctx.lineWidth = 0.7;
        var L = 5 + s.core * 6;
        ctx.beginPath();
        ctx.moveTo(x - L, y); ctx.lineTo(x + L, y);
        ctx.moveTo(x, y - L); ctx.lineTo(x, y + L);
        ctx.stroke();
      }
    }
    ctx.restore();
  };

  CareerNetwork.prototype._drawTimeBands = function (ctx, layout, sec, opacity) {
    ctx.save();
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
      ctx.fillStyle = 'rgba(215,232,255,' + (0.26 * opacity) + ')';
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
      ctx.moveTo(x - L, y); ctx.lineTo(x + L, y);
      ctx.moveTo(x, y - L); ctx.lineTo(x, y + L);
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
      var titleY = Math.max(96, layout.topY - 24);
      ctx.save();
      ctx.textAlign = 'center';
      ctx.font = '800 13px "PingFang SC","Microsoft YaHei",sans-serif';
      ctx.fillStyle = rgba(color, 0.78 * opacity);
      ctx.shadowColor = 'rgba(2,5,12,0.9)';
      ctx.shadowBlur = 10;
      var title = (sec.meta.icon ? sec.meta.icon + ' ' : '') + sec.meta.name + ' · ' + sec.directions.length + ' 个方向';
      ctx.fillText(title, layout.pivotX, titleY);
      ctx.restore();
    }

    // 成长曲线
    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    for (var p = 0; p < sec.directions.length; p++) {
      var path = sec.directions[p];
      if (!path.stars.length) continue;
      var hotPath = hasSel && path.stars.some(function (s) { return !!this.related[s.id]; }, this);
      var dimPath = hasSel && !hotPath;
      var lineAlpha = (dimPath ? 0.05 : 0.14 + path.glow * 0.2) * opacity;
      ctx.strokeStyle = hotPath ? rgba(color, 0.88 * opacity) : rgba(color, lineAlpha);
      ctx.lineWidth = hotPath ? 2.3 : 1;
      ctx.beginPath();
      var first = path.stars[0];
      ctx.moveTo(layout.pivotX, layout.groundY - 8);
      ctx.quadraticCurveTo((layout.pivotX + first.sx) / 2, layout.groundY - 44, first.sx, first.sy);
      for (var si = 1; si < path.stars.length; si++) {
        var prev = path.stars[si - 1], cur = path.stars[si];
        ctx.quadraticCurveTo((prev.sx + cur.sx) / 2, Math.min(prev.sy, cur.sy) - 18, cur.sx, cur.sy);
      }
      ctx.stroke();
    }
    ctx.restore();

    // 星光（光晕）
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (var i = 0; i < sec.stars.length; i++) {
      var s = sec.stars[i];
      var inPath = !hasSel || (this.related && this.related[s.id]);
      var tw = 0.68 + 0.32 * Math.sin(this.timeSec * s.twSpeed + s.twPhase);
      var bright = s.glow * tw * (inPath ? 1 : 0.16) * opacity;
      this._star(ctx, s.sx, s.sy, s.core, s.color, bright, s.glow > 0.56 && inPath);
    }
    ctx.restore();

    // 星核
    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    for (var j = 0; j < sec.stars.length; j++) {
      var n = sec.stars[j];
      var related = !hasSel || (this.related && this.related[n.id]);
      var selected = n.id === this.selectedId;
      var hovered = n.id === this.hoverId;
      ctx.fillStyle = 'rgba(255,255,255,' + ((related ? 0.96 : 0.26) * opacity) + ')';
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
      var a = (hot ? 0.8 : 0.2) * opacity;
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
      ctx.fillStyle = 'rgba(3,7,14,' + (0.55 * a) + ')';
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

  // ===== 前景：校园建筑/草坪/人物剪影照片（贴底，固定不旋转）=====
  CareerNetwork.prototype._drawGround = function (ctx, layout) {
    if (this.fgImg) {
      this._drawCampusPhoto(ctx);
    } else {
      this._drawFallbackGround(ctx, layout);
    }
    this._drawNowMarker(ctx, layout);
  };

  CareerNetwork.prototype._drawCampusPhoto = function (ctx) {
    var img = this.fgImg;
    var iw = img.naturalWidth || img.width;
    var ih = img.naturalHeight || img.height;
    // 宽度覆盖；竖屏时按需放大保证底部铺满
    var s = Math.max(this.W / iw, 1.34 * this.H / ih);
    var dw = iw * s, dh = ih * s;
    var dx = (this.W - dw) / 2;
    var dy = this.H * FG_BUILDING_SCREEN - FG_BUILDING_LINE * ih * s;
    // 屋顶后透出的暖光，让星图与建筑自然衔接
    ctx.save();
    var warm = ctx.createLinearGradient(0, this.H * (FG_BUILDING_SCREEN - 0.06), 0, this.H);
    warm.addColorStop(0, 'rgba(255,196,120,0)');
    warm.addColorStop(0.5, 'rgba(255,190,118,0.08)');
    warm.addColorStop(1, 'rgba(255,184,110,0.16)');
    ctx.fillStyle = warm;
    ctx.fillRect(0, this.H * (FG_BUILDING_SCREEN - 0.06), this.W, this.H * (1.06 - FG_BUILDING_SCREEN));
    ctx.restore();
    ctx.drawImage(img, dx, dy, dw, dh);
  };

  CareerNetwork.prototype._drawFallbackGround = function (ctx, layout) {
    var W = this.W, H = this.H;
    ctx.save();
    var g = ctx.createLinearGradient(0, layout.groundY - 30, 0, H);
    g.addColorStop(0, 'rgba(7,16,12,0.6)');
    g.addColorStop(0.4, '#06120d');
    g.addColorStop(1, '#020604');
    ctx.fillStyle = g;
    ctx.fillRect(0, layout.groundY - 30, W, H - layout.groundY + 30);
    ctx.fillStyle = 'rgba(2,8,15,0.96)';
    ctx.fillRect(W * 0.2, layout.groundY - 28, W * 0.6, 60);
    ctx.restore();
  };

  CareerNetwork.prototype._drawNowMarker = function (ctx, layout) {
    ctx.save();
    var glow = ctx.createRadialGradient(layout.pivotX, layout.groundY, 0, layout.pivotX, layout.groundY, 150);
    glow.addColorStop(0, 'rgba(255,235,170,0.30)');
    glow.addColorStop(0.3, 'rgba(110,231,255,0.12)');
    glow.addColorStop(1, 'rgba(110,231,255,0)');
    ctx.globalCompositeOperation = 'lighter';
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(layout.pivotX, layout.groundY, 150, 0, TAU);
    ctx.fill();
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = 'rgba(255,245,205,0.96)';
    ctx.beginPath();
    ctx.arc(layout.pivotX, layout.groundY, 4.6, 0, TAU);
    ctx.fill();
    ctx.font = '800 12px "PingFang SC","Microsoft YaHei",sans-serif';
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(255,240,180,0.95)';
    ctx.shadowColor = 'rgba(2,5,12,0.9)';
    ctx.shadowBlur = 8;
    ctx.fillText('现在', layout.pivotX, layout.groundY + 20);
    ctx.restore();
  };

  // ===== 四边渐隐：让前景/背景平滑融入页面深色边缘 =====
  CareerNetwork.prototype._drawEdgeFade = function (ctx) {
    var W = this.W, H = this.H;
    var base = '2,4,12';
    var fx = Math.max(46, W * 0.09);
    var ft = Math.max(40, H * 0.085);
    var fb = Math.max(26, H * 0.045);
    var g;
    ctx.save();
    g = ctx.createLinearGradient(0, 0, 0, ft);
    g.addColorStop(0, 'rgba(' + base + ',0.92)'); g.addColorStop(1, 'rgba(' + base + ',0)');
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, ft);
    g = ctx.createLinearGradient(0, H, 0, H - fb);
    g.addColorStop(0, 'rgba(' + base + ',0.8)'); g.addColorStop(1, 'rgba(' + base + ',0)');
    ctx.fillStyle = g; ctx.fillRect(0, H - fb, W, fb);
    g = ctx.createLinearGradient(0, 0, fx, 0);
    g.addColorStop(0, 'rgba(' + base + ',0.95)'); g.addColorStop(1, 'rgba(' + base + ',0)');
    ctx.fillStyle = g; ctx.fillRect(0, 0, fx, H);
    g = ctx.createLinearGradient(W, 0, W - fx, 0);
    g.addColorStop(0, 'rgba(' + base + ',0.95)'); g.addColorStop(1, 'rgba(' + base + ',0)');
    ctx.fillStyle = g; ctx.fillRect(W - fx, 0, fx, H);
    ctx.restore();
  };

  CareerNetwork.prototype._draw = function () {
    var ctx = this.ctx;
    var layout = this._layout();
    var blur = 0;
    var skyAngle = this.skyBaseAngle + Math.sin(this.timeSec * 0.016) * 0.012;

    if (this.transition) {
      var k = ease(this.transition.t);
      skyAngle = lerp(this.transition.skyFrom, this.transition.skyTo, k) + Math.sin(this.timeSec * 0.02) * 0.012;
      blur = Math.sin(k * Math.PI) * 3.6;
    }

    this._drawBackground(ctx, layout, skyAngle, blur);
    this.clickables = [];

    if (this.transition) {
      var tr = this.transition;
      var t = ease(tr.t);
      var fromSec = this.sectors[tr.from];
      var toSec = this.sectors[tr.to];
      this._drawSector(ctx, fromSec, layout, -tr.dir * FAN_ENTER_ANGLE * t, 1 - t * 0.3, Math.sin(t * Math.PI) * 2.2, false);
      this._drawSector(ctx, toSec, layout, tr.dir * FAN_ENTER_ANGLE * (1 - t), 0.4 + t * 0.6, Math.sin(t * Math.PI) * 2.4, false);
    } else {
      this._drawSector(ctx, this.sectors[this.sectorIndex], layout, 0, 1, 0, true);
    }

    this._drawGround(ctx, layout);
    this._drawEdgeFade(ctx);
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
