/* ============================================================
   LessonDoc 2.1 — interact.js(行为运行时 + 编辑桥接)
   职责(设计: docs/lessondoc-editor-2026-09.md §3.2 / §4.6 / §4.7):
     1. 动作运行时 runActions:show/hide/toggle/move/moveTo/goto/next/prev/run/reset
        —— deck-engine 把块的 actions 序列化在 data-ld-actions 上,这里做事件委托。
     2. CodewalkPlayer:代码逐步执行演示(高亮/箭头/输出/解说/循环/单步)。
     3. 编辑桥接 window.LESSONDOC.edit:仅当父页显式 mount() 时锁定页面;学生态零开销。
   约定:
   - 在 deck-engine.js 之后加载(新壳页显式 <script>;旧壳页由 deck-engine 自动注入)。
   - 零外部依赖、ES5 口径、file:// 可用。
   ============================================================ */
(function () {
  "use strict";
  if (window.__LESSONDOC_INTERACT__) return;
  window.__LESSONDOC_INTERACT__ = true;

  var LD = window.LESSONDOC = window.LESSONDOC || {};
  var REDUCED = false;
  try { REDUCED = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch (e) { /* noop */ }

  function parseJsonAttr(node, attr) {
    var raw = node && node.getAttribute(attr);
    if (!raw) return null;
    try { return JSON.parse(raw); } catch (e) { return null; }
  }
  function easeCss(name) {
    if (name === "in") return "ease-in";
    if (name === "out") return "ease-out";
    if (name === "inout") return "ease-in-out";
    return "linear";
  }
  function num(v, d) { var n = parseFloat(v); return isFinite(n) ? n : d; }
  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  function players(scope, fn) {
    scope = scope || document;
    if (scope.matches && scope.matches(".codewalk") && scope.__ldPlayer) fn(scope.__ldPlayer);
    Array.prototype.forEach.call(scope.querySelectorAll(".codewalk"), function (n) { if (n.__ldPlayer) fn(n.__ldPlayer); });
  }
  function controller(node) {
    var root = node && (node.classList.contains("codewalk") ? node : node.querySelector(".codewalk"));
    return root && root.__ldPlayer;
  }
  function active(root) {
    var sl = root.closest(".deck > section.slide");
    return root.isConnected && (!sl || sl.classList.contains("active"));
  }

  /* ============================================================
     动作运行时
     ============================================================ */
  function currentSlide() {
    return document.querySelector(".deck > section.slide.active") ||
           document.querySelector(".article-flow") || document.body;
  }
  /* 目标解析:先当前页(含全局克隆 data-ld-gid),再整个文档 */
  function resolveTarget(id, scope) {
    if (!id) return null;
    var sel = '[data-ld-id="' + id + '"], [data-ld-gid="' + id + '"]';
    var root = scope || currentSlide();
    var found = null;
    try { found = root.querySelector(sel); } catch (e) { found = null; }
    if (found) return found;
    try { return document.querySelector(sel); } catch (e2) { return null; }
  }
  function setVisible(node, visible, ms) {
    if (!node) return;
    var box = node.closest(".ld-pos") || node;
    if (box.__ldVisibilityTimer) { clearTimeout(box.__ldVisibilityTimer); box.__ldVisibilityTimer = null; }
    var dur = REDUCED ? 0 : num(ms, 400);
    box.style.transition = dur ? "opacity " + dur + "ms ease" : "";
    if (visible) {
      box.classList.remove("ld-hidden");
      box.style.opacity = "0";
      void box.offsetWidth;                       /* 强制回流后淡入 */
      box.style.opacity = "1";
    } else {
      box.style.opacity = "0";
      var done = function () { box.__ldVisibilityTimer = null; box.classList.add("ld-hidden"); box.style.opacity = ""; };
      if (dur) box.__ldVisibilityTimer = setTimeout(done, dur); else done();
    }
  }
  function moveNode(node, dx, dy, absolute, ms, ease) {
    if (!node) return;
    var dur = REDUCED ? 0 : num(ms, 600);
    var pos = node.classList.contains("ld-pos") ? node : node.closest(".ld-pos");
    if (pos) {
      pos.style.transition = dur ? "left " + dur + "ms " + easeCss(ease) + ", top " + dur + "ms " + easeCss(ease) : "";
      var x = absolute ? dx : num(pos.style.left, 0) + dx;
      var y = absolute ? dy : num(pos.style.top, 0) + dy;
      pos.style.left = x + "px"; pos.style.top = y + "px";
      return;
    }
    /* 流式块:用 translate 过渡(moveTo 对流式块也按相对位移处理) */
    var cur = node.__ldOffset || { x: 0, y: 0 };
    var nx = absolute ? dx : cur.x + dx, ny = absolute ? dy : cur.y + dy;
    node.__ldOffset = { x: nx, y: ny };
    node.style.transition = dur ? "transform " + dur + "ms " + easeCss(ease) : "";
    node.style.transform = "translate(" + nx + "px," + ny + "px)";
  }
  function runActions(steps, ctx) {
    if (!actionsEnabled || !steps || !steps.length) return;
    var scope = (ctx && ctx.scope) || currentSlide();
    steps.forEach(function (s) {
      if (!s || !s.do) return;
      var t;
      switch (s.do) {
        case "show": setVisible(resolveTarget(s.target, scope), true, s.ms); break;
        case "hide": setVisible(resolveTarget(s.target, scope), false, s.ms); break;
        case "toggle":
          t = resolveTarget(s.target, scope);
          if (t) setVisible(t, (t.closest(".ld-pos") || t).classList.contains("ld-hidden"), s.ms);
          break;
        case "move": moveNode(resolveTarget(s.target, scope), num(s.dx, 0), num(s.dy, 0), false, s.ms, s.ease); break;
        case "moveTo": moveNode(resolveTarget(s.target, scope), num(s.x, 0), num(s.y, 0), true, s.ms, s.ease); break;
        case "goto":
          if (window.SLIDES) {
            var index = num(s.slide, 1) - 1;
            if (s.slideId && LD.data && LD.data.slides) index = LD.data.slides.findIndex(function (sl) { return sl.id === s.slideId; });
            if (index >= 0) window.SLIDES.goTo(index);
          }
          break;
        case "next": if (window.SLIDES) window.SLIDES.next(); break;
        case "prev": if (window.SLIDES) window.SLIDES.prev(); break;
        case "run":
          t = resolveTarget(s.target, scope);
          if (controller(t)) controller(t).run();
          break;
        case "reset":
          t = resolveTarget(s.target, scope);
          if (controller(t)) controller(t).reset();
          break;
      }
    });
  }
  LD.runActions = runActions;

  var actionsEnabled = true;
  document.addEventListener("click", function (e) {
    if (!actionsEnabled) return;
    var node = e.target && e.target.closest ? e.target.closest("[data-ld-actions]") : null;
    if (!node) return;
    if (node.classList.contains("codewalk")) return;   /* codewalk 的 actions 挂在运行按钮上,由播放器处理 */
    var steps = parseJsonAttr(node, "data-ld-actions");
    if (!steps) return;
    if (node.getAttribute("data-ld-once") === "1" && node.__ldFired) return;
    node.__ldFired = true;
    runActions(steps, { scope: node.closest("section.slide") || currentSlide() });
  });

  /* ============================================================
     CodewalkPlayer
     DOM 契约(deck-engine 渲染):
       .codewalk[data-ld-cw='{"steps":[{"line":i,"out":..,"note":..}],"loop":..,"speedMs":..,"autoStart":..}']
         .cw-code > .cw-line[data-line=i] > .cw-gutter + .cw-src
         .cw-out(可选) .cw-note(可选)
         .cw-bar > button[data-cw="run|pause|step|reset"] + .cw-count
     ============================================================ */
  function CodewalkPlayer(root) {
    this.root = root;
    this.cfg = parseJsonAttr(root, "data-ld-cw") || { steps: [] };
    this.steps = this.cfg.steps || [];
    this.i = -1;
    this.timer = null;
    this.playing = false;
    this.lines = Array.prototype.slice.call(root.querySelectorAll(".cw-line"));
    this.outEl = root.querySelector(".cw-out");
    this.noteEl = root.querySelector(".cw-note");
    this.countEl = root.querySelector(".cw-count");
    this.btnRun = root.querySelector('[data-cw="run"]');
    this.btnPause = root.querySelector('[data-cw="pause"]');
    this.btnStep = root.querySelector('[data-cw="step"]');
    this.btnReset = root.querySelector('[data-cw="reset"]');
    var self = this;
    this.handlers = [];
    function bind(btn, fn) {
      if (!btn) return;
      var guarded = function () { if (actionsEnabled && !self.destroyed) fn(); };
      btn.addEventListener("click", guarded);
      self.handlers.push([btn, guarded]);
    }
    bind(this.btnRun, function () {
      var extra = parseJsonAttr(root, "data-ld-actions");
      if (extra && actionsEnabled) runActions(extra, { scope: root.closest("section.slide") || currentSlide() });
      self.run();
    });
    bind(this.btnPause, function () { self.pause(); });
    bind(this.btnStep, function () { self.pause(); self.step(); });
    bind(this.btnReset, function () { self.reset(); });
    root.__ldPlayer = this;
    this.render();
    if (this.cfg.autoStart && actionsEnabled && active(root)) { this.autoStarted = true; this.run(); }
  }
  CodewalkPlayer.prototype.render = function () {
    var cur = this.i >= 0 ? this.steps[this.i] : null;
    var curLine = cur ? cur.line : -1;
    this.lines.forEach(function (ln) {
      var idx = num(ln.getAttribute("data-line"), -1);
      ln.classList.toggle("active", idx === curLine);
    });
    if (this.noteEl) this.noteEl.textContent = cur && cur.note ? cur.note : "";
    if (this.countEl) this.countEl.textContent = (this.i + 1) + " / " + this.steps.length;
    if (this.btnPause) this.btnPause.style.display = this.playing ? "" : "none";
    if (this.btnRun) this.btnRun.style.display = this.playing ? "none" : "";
    if (this.btnStep) this.btnStep.disabled = this.i >= this.steps.length - 1 && !this.cfg.loop;
    this.root.classList.toggle("is-playing", !!this.playing);
  };
  CodewalkPlayer.prototype.appendOut = function (text) {
    if (!this.outEl || !text) return;
    var line = document.createElement("div");
    line.className = "cw-out-line";
    line.textContent = text;
    this.outEl.appendChild(line);
    this.outEl.scrollTop = this.outEl.scrollHeight;
  };
  CodewalkPlayer.prototype.step = function () {
    if (this.destroyed || !actionsEnabled || !this.steps.length) return false;
    if (this.i >= this.steps.length - 1) {
      if (!this.cfg.loop) { this.render(); return false; }
      this.i = -1;
      if (this.outEl) this.outEl.innerHTML = "";
    }
    this.i += 1;
    var s = this.steps[this.i];
    this.render();
    if (s && s.out) this.appendOut(s.out);
    return true;
  };
  CodewalkPlayer.prototype.run = function () {
    var self = this;
    if (this.destroyed || !actionsEnabled || !active(this.root) || this.playing) return;
    if (this.i >= this.steps.length - 1 && !this.cfg.loop) this.reset();
    this.playing = true;
    this.render();
    var tick = function () {
      if (!self.playing || !active(self.root) || !actionsEnabled) { self.pause(); return; }
      var ok = self.step();
      if (!ok) { self.playing = false; self.render(); return; }
      self.timer = setTimeout(tick, Math.max(200, num(self.cfg.speedMs, 900)));
    };
    tick();
  };
  CodewalkPlayer.prototype.pause = function () {
    this.playing = false;
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    this.render();
  };
  CodewalkPlayer.prototype.reset = function () {
    this.pause();
    this.i = -1;
    if (this.outEl) this.outEl.innerHTML = "";
    this.render();
  };
  CodewalkPlayer.prototype.destroy = function () {
    this.pause();
    this.destroyed = true;
    this.handlers.forEach(function (h) { h[0].removeEventListener("click", h[1]); });
    this.handlers = [];
    delete this.root.__ldPlayer;
  };
  function initCodewalks(scope) {
    Array.prototype.forEach.call((scope || document).querySelectorAll(".codewalk"), function (root) {
      if (!root.__ldPlayer) new CodewalkPlayer(root);
    });
  }
  LD.initCodewalks = initCodewalks;
  LD.CodewalkPlayer = CodewalkPlayer;
  LD.disposeRuntime = function (root) {
    players(root, function (p) { p.destroy(); });
    if (LD.unmountInteractions) LD.unmountInteractions(root);
    Array.prototype.forEach.call((root || document).querySelectorAll("*"), function (n) {
      if (n.__ldVisibilityTimer) { clearTimeout(n.__ldVisibilityTimer); n.__ldVisibilityTimer = null; }
    });
  };
  function mountRuntime(root) {
    if (LD.mountInteractions) LD.mountInteractions(root);
    initCodewalks(root);
  }
  document.addEventListener("lessondoc:slidechange", function () {
    players(document, function (p) {
      if (!active(p.root)) p.pause();
      else if (p.cfg.autoStart && !p.autoStarted && actionsEnabled) { p.autoStarted = true; p.run(); }
    });
    if (edit.mounted && !edit.trial) edit.showAllFragments();
  });

  /* ============================================================
     编辑桥接(仅编辑态;父页同源直接调用)
     ============================================================ */
  function blockNodeSelector(id) {
    return '[data-ld-id="' + id + '"], [data-ld-gid="' + id + '"]';
  }
  var edit = {
    version: "2.1",
    mounted: false,
    trial: false,
    listeners: {},
    on: function (event, fn) { (this.listeners[event] = this.listeners[event] || []).push(fn); },
    off: function (event, fn) { this.listeners[event] = (this.listeners[event] || []).filter(function (handler) { return handler !== fn; }); },
    emit: function (event, payload) {
      (this.listeners[event] || []).forEach(function (fn) { try { fn(payload); } catch (e) { /* noop */ } });
    },
    mount: function (opts) {
      opts = opts || {};
      document.documentElement.classList.add("ld-editing");
      document.documentElement.classList.remove("ld-trial");
      actionsEnabled = false;
      players(document, function (p) { p.pause(); });
      if (window.SLIDES) window.SLIDES.setLocked(true);
      if (!this._clickGuard) {
        this._clickGuard = function (e) {
          if (!document.documentElement.classList.contains("ld-editing") || edit.trial) return;
          var a = e.target.closest && e.target.closest("a, button, summary, input, label");
          if (a && !e.target.closest(".ld-edit-layer")) { e.preventDefault(); e.stopImmediatePropagation(); }
        };
        document.addEventListener("click", this._clickGuard, true);
      }
      this.mounted = true;
      this.trial = false;
      if (opts.slide != null && window.SLIDES) window.SLIDES.goTo(num(opts.slide, 0));
      this.showAllFragments();
      this.emit("ready", { slideCount: this.slideCount() });
      return this;
    },
    unmount: function () {
      document.documentElement.classList.remove("ld-editing");
      document.documentElement.classList.remove("ld-trial");
      actionsEnabled = true;
      if (window.SLIDES) window.SLIDES.setLocked(false);
      this.mounted = false;
      this.trial = false;
    },
    showAllFragments: function () {
      Array.prototype.forEach.call(document.querySelectorAll(".fragment"), function (f) { f.classList.add("visible"); });
      Array.prototype.forEach.call(document.querySelectorAll(".frag-exited"), function (f) { f.classList.remove("frag-exited"); });
    },
    slideCount: function () { return document.querySelectorAll(".deck > section.slide").length; },
    currentIndex: function () { return window.SLIDES ? window.SLIDES.current() : 0; },
    render: function (deck, slideIndex) {
      var eng = LD.__engine;
      if (!eng) throw new Error("engine not ready");
      LD.disposeRuntime(document);
      LD.data = copy(deck);
      eng.rerender(LD.data);
      if (window.SLIDES && deck.kind !== "home") window.SLIDES.reinit(num(slideIndex, 0));
      mountRuntime();
      if (this.mounted && !this.trial) this.showAllFragments();
      this.emit("rendered", { slideCount: this.slideCount() });
      return { slideCount: this.slideCount() };
    },
    patchSlide: function (slideJson, index) {
      var eng = LD.__engine;
      if (!eng) throw new Error("engine not ready");
      var deck = LD.data;
      var prior = this.slideEl(index);
      if (prior) LD.disposeRuntime(prior);
      if (deck && deck.slides && index >= 0 && index < deck.slides.length) deck.slides[index] = copy(slideJson);
      eng.patchSlide(copy(slideJson), index);
      if (window.SLIDES) window.SLIDES.refreshSlide(index);
      mountRuntime(this.slideEl(index) || document);
      if (this.mounted && !this.trial) this.showAllFragments();
      this.emit("rendered", { slideCount: this.slideCount() });
    },
    slideEl: function (index) {
      if (LD.data && LD.data.kind === "home") return document.querySelector(".ld-home");
      var slides = document.querySelectorAll(".deck > section.slide");
      return slides[index == null ? this.currentIndex() : index] || null;
    },
    geometry: function () {
      var sl = this.slideEl();
      if (!sl) return { scale: 1, originX: 0, originY: 0, width: 1280, height: 720 };
      var r = sl.getBoundingClientRect();
      var home = LD.data && LD.data.kind === "home";
      var w = home ? sl.offsetWidth : num(sl.style.width, 1280);
      return { scale: w ? r.width / w : 1, originX: r.left, originY: r.top, width: w, height: home ? sl.offsetHeight : 720 };
    },
    toCanvas: function (clientX, clientY) {
      var g = this.geometry();
      return { x: (clientX - g.originX) / g.scale, y: (clientY - g.originY) / g.scale };
    },
    rects: function (ids) {
      var sl = this.slideEl();
      var out = {};
      if (!sl) return out;
      var g = this.geometry();
      var nodes = ids
        ? ids.map(function (id) { return sl.querySelector(blockNodeSelector(id)); })
        : Array.prototype.slice.call(sl.querySelectorAll("[data-ld-id], [data-ld-gid]"));
      nodes.forEach(function (n) {
        if (!n) return;
        var id = n.getAttribute("data-ld-gid") || n.getAttribute("data-ld-id");
        var box = n.getBoundingClientRect();
        out[id] = {
          x: (box.left - g.originX) / g.scale, y: (box.top - g.originY) / g.scale,
          w: box.width / g.scale, h: box.height / g.scale,
          positioned: n.classList.contains("ld-pos"), global: n.hasAttribute("data-ld-gid")
        };
      });
      return out;
    },
    hitTest: function (x, y) {
      var g = this.geometry();
      var el = document.elementFromPoint(g.originX + x * g.scale, g.originY + y * g.scale);
      if (!el) return null;
      var chain = [];
      var n = el.closest ? el.closest("[data-ld-id], [data-ld-gid]") : null;
      while (n) {
        chain.push(n.getAttribute("data-ld-gid") || n.getAttribute("data-ld-id"));
        var p = n.parentElement;
        n = p && p.closest ? p.closest("[data-ld-id], [data-ld-gid]") : null;
      }
      return chain.length ? { id: chain[0], chain: chain } : null;
    },
    layer: function () {
      var sl = this.slideEl();
      if (!sl) return null;
      var layer = sl.querySelector(":scope > .ld-edit-layer");
      if (!layer) {
        layer = document.createElement("div");
        layer.className = "ld-edit-layer";
        sl.appendChild(layer);
      }
      return layer;
    },
    select: function (ids) {
      Array.prototype.forEach.call(document.querySelectorAll(".ld-selected"), function (n) { n.classList.remove("ld-selected"); });
      var sl = this.slideEl();
      if (!sl) return;
      (ids || []).forEach(function (id) {
        var n = sl.querySelector(blockNodeSelector(id));
        if (n) n.classList.add("ld-selected");
      });
    },
    previewActions: function (enabled) {
      this.trial = !!enabled;
      actionsEnabled = !!enabled;
      document.documentElement.classList.toggle("ld-trial", !!enabled);
      if (!enabled) players(document, function (p) { p.pause(); });
    },
    measureFlowFrames: function (index) {
      var sl = this.slideEl(index);
      if (!sl) return {};
      var body = sl.querySelector(".slide-body");
      if (!body) return {};
      var g = this.geometry();
      var sr = sl.getBoundingClientRect();
      var out = {};
      Array.prototype.forEach.call(body.querySelectorAll("[data-ld-id]"), function (n) {
        if (n.closest(".ld-pos")) return;
        var parentBlock = n.parentElement && n.parentElement.closest("[data-ld-id]");
        if (parentBlock && body.contains(parentBlock)) return;      /* 只取顶层流式块 */
        var b = n.getBoundingClientRect();
        out[n.getAttribute("data-ld-id")] = {
          x: Math.round((b.left - sr.left) / g.scale), y: Math.round((b.top - sr.top) / g.scale),
          w: Math.round(b.width / g.scale), h: Math.round(b.height / g.scale)
        };
      });
      return out;
    }
  };
  LD.edit = edit;
  window.addEventListener("resize", function () { if (edit.mounted) edit.emit("geometry", edit.geometry()); });

  document.addEventListener("keydown", function (e) {
    if (edit.mounted) edit.emit("keydown", e);
  });

  function boot() { mountRuntime(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
