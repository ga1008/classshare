/* ============================================================
   幻灯片框架脚本 slides.js —— 与 course.js 并用,零外部依赖
   用法:
     <body class="slides-page">
       <div class="deck" data-course="课程名">
         <section class="slide" data-section="小节名">
           <h2 class="slide-title">页标题</h2>
           <div class="slide-body"> …内容,可含 .fragment… </div>
         </section>
       </div>
   机制:虚拟画布 720 高、宽随比例(16:9→1280 / 4:3→960 /
   16:10→1152 / fit→随窗口),transform:scale 等比缩放居中。
   键盘 →↓空格PgDn / ←↑PgUp / Home End / F 全屏 / Esc·O 总览;
   触屏左右滑;URL hash #/页号;比例选择存 localStorage。
   ============================================================ */
(function () {
  "use strict";

  var RATIOS = { "16:9": 1280, "4:3": 960, "16:10": 1152 };
  var CANVAS_H = 720;
  var STORE_KEY = "slides-ratio";
  var SWIPE_MIN = 46;

  var deck, slides, cur = 0, ratio, canvasW = 1280;
  var progressEl, pageEl, overviewEl;
  var locked = false;          /* 编辑态:禁用键盘/触屏/hash 翻页,不写 hash */

  /* ---------- 布局:等比缩放虚拟画布 ---------- */
  function currentCanvasW() {
    if (locked) return 1280;
    if (ratio === "fit") {
      var w = Math.round(CANVAS_H * window.innerWidth / Math.max(1, window.innerHeight));
      return Math.max(640, Math.min(2400, w));
    }
    return RATIOS[ratio] || 1280;
  }

  function layout() {
    if (!slides || !slides.length) return;
    canvasW = currentCanvasW();
    var s = Math.min(window.innerWidth / canvasW, window.innerHeight / CANVAS_H) * 0.97;
    slides.forEach(function (sl) {
      sl.style.width = canvasW + "px";
      sl.style.transform = "translate(-50%, -50%) scale(" + s + ")";
      /* 比例变化后旧的适配缩放作废 */
      var body = sl.querySelector(".slide-body");
      if (body && body.dataset.fitted) {
        body.style.transform = ""; body.style.transformOrigin = "";
        delete body.dataset.fitted;
      }
    });
    fitSlide(slides[cur]);
  }

  /* ---------- 内容溢出保险:整体缩小 .slide-body ---------- */
  function fitSlide(sl) {
    if (!sl || locked) return;
    var body = sl.querySelector(".slide-body");
    if (!body || body.dataset.fitted) return;
    /* 需要可见才能测量 */
    if (!sl.classList.contains("active")) return;
    var sh = body.scrollHeight, ch = body.clientHeight;
    var sw = body.scrollWidth, cw = body.clientWidth;
    if (sh <= ch + 2 && sw <= cw + 2) { body.dataset.fitted = "1"; return; }
    var k = Math.min(ch / sh, cw / sw);
    k = Math.max(0.5, Math.floor(k * 100) / 100);
    body.style.transformOrigin = "top center";
    body.style.transform = "scale(" + k + ")";
    body.dataset.fitted = "1";
  }

  /* ---------- fragment ----------
     LessonDoc 2.0 扩展:
     1. 按 data-step 数值排序(缺省 0,同值保持 DOM 顺序)——grid 版式
        里登场顺序可与 DOM 顺序无关。
     2. 带 data-exit-target 的隐形 fragment 是"退场标记":它显现时
        给目标元素加 .frag-exited(离场),回退时撤销。 */
  function fragmentsOf(sl) {
    var list = Array.prototype.slice.call(sl.querySelectorAll(".fragment"));
    list.sort(function (a, b) {
      return (parseFloat(a.getAttribute("data-step")) || 0) -
             (parseFloat(b.getAttribute("data-step")) || 0);
    });
    return list;
  }
  function applyExitMarkers(sl) {
    Array.prototype.forEach.call(sl.querySelectorAll(".fragment[data-exit-target]"), function (m) {
      var sel = m.getAttribute("data-exit-target");
      var target = null;
      try { target = sel ? sl.querySelector(sel) : null; } catch (e) { /* 非法选择器忽略 */ }
      if (target) target.classList.toggle("frag-exited", m.classList.contains("visible"));
    });
  }
  function stepFragment(sl, dir) {   /* 返回 true = 本页内消化了这次按键 */
    var frs = fragmentsOf(sl);
    if (!frs.length) return false;
    var consumed = false;
    if (dir > 0) {
      for (var i = 0; i < frs.length; i++) {
        if (!frs[i].classList.contains("visible")) { frs[i].classList.add("visible"); consumed = true; break; }
      }
    } else {
      for (var j = frs.length - 1; j >= 0; j--) {
        if (frs[j].classList.contains("visible")) { frs[j].classList.remove("visible"); consumed = true; break; }
      }
    }
    if (consumed) applyExitMarkers(sl);
    return consumed;
  }

  /* ---------- 翻页 ---------- */
  function goTo(n, showAllFragments) {
    n = Math.max(0, Math.min(slides.length - 1, n));
    if (n !== cur) {
      slides[cur].classList.remove("active");
      cur = n;
    }
    var sl = slides[cur];
    sl.classList.add("active");
    fragmentsOf(sl).forEach(function (f) {
      f.classList.toggle("visible", !!showAllFragments);
    });
    applyExitMarkers(sl);
    if (!locked && location.protocol !== "about:" && location.hash !== "#/" + (cur + 1)) {
      history.replaceState(null, "", "#/" + (cur + 1));
    }
    updateHud();
    document.dispatchEvent(new CustomEvent("lessondoc:slidechange", { detail: { index: cur } }));
    /* setTimeout 而非 rAF:后台标签页 rAF 不触发,溢出保险会失效 */
    setTimeout(function () { fitSlide(sl); }, 0);
  }
  function next() { if (!stepFragment(slides[cur], 1)) { if (cur < slides.length - 1) goTo(cur + 1, false); } }
  function prev() { if (!stepFragment(slides[cur], -1)) { if (cur > 0) goTo(cur - 1, true); } }

  function updateHud() {
    progressEl.style.width = ((cur + 1) / slides.length * 100) + "%";
    pageEl.textContent = (cur + 1) + " / " + slides.length;
  }

  /* ---------- 页眉页脚注入 ---------- */
  function injectChrome() {
    var course = deck.getAttribute("data-course") || document.title;
    slides.forEach(function (sl, i) {
      var bare = sl.classList.contains("slide--title") || sl.classList.contains("slide--section") ||
                 sl.classList.contains("slide--end");
      if (bare) return;
      /* 幂等:重渲/单页替换后再次调用只更新页码,不重复注入 */
      var existingFoot = sl.querySelector(":scope > .slide-chrome-foot .foot-no");
      if (existingFoot) { existingFoot.textContent = (i + 1) + " / " + slides.length; return; }
      var head = document.createElement("div");
      head.className = "slide-chrome-head";
      head.innerHTML = '<span class="sec-name"></span><span class="head-course"></span>';
      head.querySelector(".sec-name").textContent = sl.getAttribute("data-section") || "";
      sl.insertBefore(head, sl.firstChild);
      var foot = document.createElement("div");
      foot.className = "slide-chrome-foot";
      foot.innerHTML = '<span class="foot-course"></span><span class="foot-no"></span>';
      foot.querySelector(".foot-course").textContent = course;
      foot.querySelector(".foot-no").textContent = (i + 1) + " / " + slides.length;
      sl.appendChild(foot);
    });
  }

  /* ---------- HUD / 箭头 / 进度条 ---------- */
  function buildHud() {
    progressEl = document.createElement("div");
    progressEl.className = "slides-progress";
    document.body.appendChild(progressEl);

    var pa = document.createElement("button");
    pa.className = "nav-arrow prev"; pa.innerHTML = "‹"; pa.title = "上一页 (←)";
    pa.addEventListener("click", prev);
    var na = document.createElement("button");
    na.className = "nav-arrow next"; na.innerHTML = "›"; na.title = "下一页 (→)";
    na.addEventListener("click", next);
    document.body.appendChild(pa); document.body.appendChild(na);

    var hud = document.createElement("div");
    hud.className = "slides-hud";
    hud.innerHTML =
      '<span class="page-no"></span>' +
      '<select class="ratio-sel" title="画面比例">' +
        '<option value="16:9">16:9</option><option value="4:3">4:3</option>' +
        '<option value="16:10">16:10</option><option value="fit">适应窗口</option>' +
      '</select>' +
      '<button class="btn-ov" title="总览 (O/Esc)">▦ 总览</button>' +
      '<button class="btn-fs" title="全屏 (F)">⛶ 全屏</button>';
    document.body.appendChild(hud);
    pageEl = hud.querySelector(".page-no");

    var sel = hud.querySelector(".ratio-sel");
    sel.value = ratio;
    sel.addEventListener("change", function () {
      ratio = sel.value;
      try { localStorage.setItem(STORE_KEY, ratio); } catch (e) { /* 隐私模式下忽略 */ }
      layout();
      sel.blur();
    });
    hud.querySelector(".btn-ov").addEventListener("click", toggleOverview);
    hud.querySelector(".btn-fs").addEventListener("click", toggleFullscreen);
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      if (document.exitFullscreen) document.exitFullscreen();
    } else if (document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen();
    }
  }

  /* ---------- 总览网格 ---------- */
  function buildOverview() {
    overviewEl = document.createElement("div");
    overviewEl.className = "slides-overview";
    var grid = document.createElement("div");
    grid.className = "ov-grid";
    overviewEl.appendChild(grid);
    overviewEl.addEventListener("click", function (e) {
      if (e.target === overviewEl) closeOverview();
    });
    document.body.appendChild(overviewEl);
  }
  function openOverview() {
    var grid = overviewEl.querySelector(".ov-grid");
    grid.innerHTML = "";
    slides.forEach(function (sl, i) {
      var th = document.createElement("div");
      th.className = "ov-thumb" + (i === cur ? " current" : "");
      th.innerHTML = '<span class="ov-no">' + (i + 1) + "</span>" +
                     '<div class="ov-canvas"></div>';
      var clone = sl.cloneNode(true);
      clone.classList.add("active");
      /* 去掉克隆体的所有 id,避免与正文重复 */
      if (clone.id) clone.removeAttribute("id");
      Array.prototype.forEach.call(clone.querySelectorAll("[id]"), function (n) { n.removeAttribute("id"); });
      th.querySelector(".ov-canvas").appendChild(clone);
      th.addEventListener("click", function () { closeOverview(); goTo(i, true); });
      grid.appendChild(th);
    });
    overviewEl.classList.add("open");
    /* 缩放缩略图 */
    requestAnimationFrame(function () {
      Array.prototype.forEach.call(grid.querySelectorAll(".ov-thumb"), function (th) {
        var cv = th.querySelector(".ov-canvas");
        var w = cv.clientWidth || 240;
        var k = w / canvasW;
        cv.style.height = (CANVAS_H * k) + "px";
        var clone = cv.querySelector(".slide");
        clone.style.width = canvasW + "px";
        clone.style.transform = "scale(" + k + ")";
      });
      var curTh = grid.querySelector(".ov-thumb.current");
      if (curTh && curTh.scrollIntoView) curTh.scrollIntoView({ block: "center" });
    });
  }
  function closeOverview() { overviewEl.classList.remove("open"); }
  function toggleOverview() {
    if (overviewEl.classList.contains("open")) closeOverview(); else openOverview();
  }

  /* ---------- 键盘 ---------- */
  function isFormTarget(t) {
    if (!t || !t.tagName) return false;
    var tag = t.tagName.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || t.isContentEditable;
  }
  function onKey(e) {
    if (locked) return;
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    var t = e.target;
    if (isFormTarget(t)) return;                       /* 输入焦点在表单内不翻页 */
    var tag = (t && t.tagName || "").toLowerCase();
    var isBtnLike = tag === "button" || tag === "a" || tag === "summary";
    switch (e.key) {
      case " ":
        if (isBtnLike) return;                         /* 空格留给按钮 */
        e.preventDefault(); next(); break;
      case "ArrowRight": case "ArrowDown": case "PageDown":
        e.preventDefault(); next(); break;
      case "ArrowLeft": case "ArrowUp": case "PageUp":
        e.preventDefault(); prev(); break;
      case "Home": e.preventDefault(); goTo(0, false); break;
      case "End": e.preventDefault(); goTo(slides.length - 1, true); break;
      case "f": case "F": toggleFullscreen(); break;
      case "o": case "O": toggleOverview(); break;
      case "Escape":
        if (overviewEl.classList.contains("open")) closeOverview();
        else openOverview();
        break;
    }
  }

  /* ---------- 触屏滑动 ---------- */
  function bindTouch() {
    var x0 = null, y0 = 0;
    document.addEventListener("touchstart", function (e) {
      if (e.touches.length !== 1) { x0 = null; return; }
      x0 = e.touches[0].clientX; y0 = e.touches[0].clientY;
    }, { passive: true });
    document.addEventListener("touchend", function (e) {
      if (locked || x0 === null || overviewEl.classList.contains("open")) return;
      var t = e.changedTouches[0];
      var dx = t.clientX - x0, dy = t.clientY - y0;
      x0 = null;
      if (Math.abs(dx) < SWIPE_MIN || Math.abs(dx) < Math.abs(dy)) return;
      if (isFormTarget(e.target) || (e.target.closest && e.target.closest(".stepper, .quiz"))) return;
      if (dx < 0) next(); else prev();
    }, { passive: true });
  }

  /* ---------- hash ---------- */
  function pageFromHash() {
    var total = slides ? slides.length : document.querySelectorAll(".deck > section.slide").length;
    var m = /^#\/(\d+)/.exec(location.hash);
    return m ? Math.max(0, Math.min(Math.max(0, total - 1), parseInt(m[1], 10) - 1)) : 0;
  }

  /* ---------- 初始化 ---------- */
  /* 绑定(或重新绑定)当前 .deck 下的幻灯片:重渲/单页替换后可重复调用 */
  function bindDeck(start, showAll) {
    deck = document.querySelector(".deck");
    if (!deck) return false;
    slides = Array.prototype.slice.call(deck.querySelectorAll(":scope > section.slide"));
    if (!slides.length) return false;
    slides.forEach(function (sl) { sl.classList.remove("active"); });
    cur = Math.max(0, Math.min(slides.length - 1, start || 0));
    injectChrome();
    goTo(cur, !!showAll);
    layout();
    return true;
  }

  function init() {
    if (!document.querySelector(".deck")) return;
    try { ratio = localStorage.getItem(STORE_KEY) || "16:9"; } catch (e) { ratio = "16:9"; }
    if (ratio !== "fit" && !RATIOS[ratio]) ratio = "16:9";

    buildHud();
    buildOverview();
    bindTouch();

    var start = pageFromHash();
    if (!bindDeck(start, start > 0)) return;   /* 分享/刷新到中间页时,该页 fragment 全部展开 */

    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", layout);
    window.addEventListener("hashchange", function () {
      if (locked) return;
      var p = pageFromHash();
      if (p !== cur) goTo(p, true);
    });
  }

  /* 2.1 对外接口:动作运行时(goto/next/prev)与编辑桥接(reinit/setLocked)使用 */
  window.SLIDES = {
    goTo: function (n) { if (slides && slides.length) goTo(n, true); },
    next: function () { if (slides && slides.length) next(); },
    prev: function () { if (slides && slides.length) prev(); },
    current: function () { return cur; },
    count: function () { return slides ? slides.length : 0; },
    reinit: function (index) { return bindDeck(index || 0, true); },
    refreshSlide: function (index) {
      if (!deck || !slides) return false;
      var node = deck.querySelectorAll(":scope > section.slide")[index];
      if (!node) return false;
      slides[index] = node;
      injectChrome();
      node.style.width = canvasW + "px";
      var scale = Math.min(window.innerWidth / canvasW, window.innerHeight / CANVAS_H) * 0.97;
      node.style.transform = "translate(-50%, -50%) scale(" + scale + ")";
      node.classList.toggle("active", index === cur);
      fitSlide(node);
      return true;
    },
    setLocked: function (v) { locked = !!v; layout(); },
    isLocked: function () { return locked; }
  };

  document.addEventListener("DOMContentLoaded", init);
})();
