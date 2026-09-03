/* 课程学习文档公共交互脚本 —— 思维导图 / 测验 / 选项卡 / 代码复制 / 步骤动画 */
(function () {
  "use strict";
  var LD = window.LESSONDOC = window.LESSONDOC || {};
  function each(root, selector, fn) {
    if (root.matches && root.matches(selector)) fn(root);
    root.querySelectorAll(selector).forEach(fn);
  }
  function listen(owner, node, event, fn) {
    var guarded = function (e) {
      if (LD.edit && LD.edit.mounted && !LD.edit.trial) return;
      fn(e);
    };
    node.addEventListener(event, guarded);
    (owner.__ldCourseHandlers = owner.__ldCourseHandlers || []).push([node, event, guarded]);
  }

  /* 思维导图:.mindmap 内嵌套 ul → 可折叠树;有子级的节点点击展开/收起 */
  function initMindmaps(root) {
    each(root, ".mindmap", function (mm) {
      if (mm.__ldCourseHandlers) return;
      mm.__ldCourseHandlers = [];
      var rootTitle = mm.getAttribute("data-root");
      if (rootTitle && !mm.querySelector(".mm-root")) {
        var rootEl = document.createElement("div");
        rootEl.className = "mm-root";
        rootEl.textContent = rootTitle;
        mm.insertBefore(rootEl, mm.firstChild);
      }
      mm.querySelectorAll("li").forEach(function (li) {
        var childUl = li.querySelector(":scope > ul");
        var node = li.querySelector(":scope > .mm-node") || document.createElement("span");
        node.className = "mm-node";
        var toMove = [];
        Array.prototype.forEach.call(li.childNodes, function (n) {
          if (n !== childUl && n !== node) toMove.push(n);
        });
        toMove.forEach(function (n) { node.appendChild(n); });
        li.insertBefore(node, li.firstChild);
        if (childUl) {
          li.classList.add("mm-branch");
          if (li.hasAttribute("data-collapsed")) li.classList.add("collapsed");
          listen(mm, node, "click", function (e) {
            if (e.target.closest("a")) return; // 链接照常跳转
            li.classList.toggle("collapsed");
          });
        }
      });
    });
  }

  /* 测验:.quiz[data-answer] 内 .quiz-opts button[data-k] */
  function initQuizzes(root) {
    each(root, ".quiz", function (q) {
      if (q.__ldCourseHandlers) return;
      q.__ldCourseHandlers = [];
      var answer = q.getAttribute("data-answer");
      var btns = q.querySelectorAll(".quiz-opts button");
      btns.forEach(function (b) {
        listen(q, b, "click", function () {
          if (q.classList.contains("answered")) return;
          q.classList.add("answered");
          btns.forEach(function (x) {
            if (x.getAttribute("data-k") === answer) x.classList.add("correct");
            x.disabled = true;
          });
          if (b.getAttribute("data-k") !== answer) b.classList.add("wrong");
        });
      });
    });
  }

  /* 选项卡:.tabs > .tab-nav button + .tab-panels > .tab-panel */
  function initTabs(root) {
    each(root, ".tabs", function (t) {
      if (t.__ldCourseHandlers) return;
      t.__ldCourseHandlers = [];
      var navBtns = t.querySelectorAll(":scope > .tab-nav > button");
      var panels = t.querySelectorAll(":scope > .tab-panels > .tab-panel");
      navBtns.forEach(function (b, i) {
        listen(t, b, "click", function () {
          navBtns.forEach(function (x) { x.classList.remove("active"); });
          panels.forEach(function (p) { p.classList.remove("active"); });
          b.classList.add("active");
          if (panels[i]) panels[i].classList.add("active");
        });
      });
    });
  }

  /* 代码块:自动注入"复制"按钮 */
  function initCodeCopy(root) {
    function fallbackCopy(text) {
      var ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (e) { /* 剪贴板不可用时静默 */ }
      document.body.removeChild(ta);
    }
    each(root, ".code-block", function (cb) {
      var pre = cb.querySelector("pre");
      if (!pre || cb.__ldCourseHandlers) return;
      cb.__ldCourseHandlers = [];
      var btn = cb.querySelector(".copy-btn") || document.createElement("button");
      btn.className = "copy-btn";
      btn.textContent = "复制";
      listen(cb, btn, "click", function () {
        var text = pre.innerText;
        function done() { btn.textContent = "已复制✓"; setTimeout(function () { btn.textContent = "复制"; }, 1500); }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
        } else { fallbackCopy(text); done(); }
      });
      cb.appendChild(btn);
    });
  }

  /* 步骤动画:makeStepper(容器id, steps)
     steps = [{ text: "解说文字", on: function(root){ 更新SVG状态 } }, ...] */
  window.makeStepper = function (id, steps) {
    var root = document.getElementById(id);
    if (!root || !steps || !steps.length) return;
    var bar = root.querySelector(".stepper-bar");
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "stepper-bar";
      root.appendChild(bar);
    }
    bar.innerHTML = "<button class=\"prev\">← 上一步</button>" +
      "<span class=\"stepper-count\"></span>" +
      "<button class=\"next\">下一步 →</button>" +
      "<div class=\"stepper-desc\"></div>";
    var prev = bar.querySelector(".prev"), next = bar.querySelector(".next");
    var count = bar.querySelector(".stepper-count"), desc = bar.querySelector(".stepper-desc");
    var i = 0;
    function render() {
      count.textContent = (i + 1) + " / " + steps.length;
      desc.textContent = steps[i].text || "";
      prev.disabled = i === 0;
      next.disabled = i === steps.length - 1;
      if (steps[i].on) steps[i].on(root);
    }
    listen(root, prev, "click", function () { if (i > 0) { i--; render(); } });
    listen(root, next, "click", function () { if (i < steps.length - 1) { i++; render(); } });
    render();
  };

  LD.mountInteractions = function (root) {
    root = root || document;
    initMindmaps(root); initQuizzes(root); initTabs(root); initCodeCopy(root);
  };
  LD.unmountInteractions = function (root) {
    each(root || document, ".mindmap, .quiz, .tabs, .code-block, .stepper", function (node) {
      (node.__ldCourseHandlers || []).forEach(function (h) { h[0].removeEventListener(h[1], h[2]); });
      delete node.__ldCourseHandlers;
    });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { LD.mountInteractions(); });
  else LD.mountInteractions();
})();
