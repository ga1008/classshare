/* 课程学习文档公共交互脚本 —— 思维导图 / 测验 / 选项卡 / 代码复制 / 步骤动画 */
(function () {
  "use strict";

  /* 思维导图:.mindmap 内嵌套 ul → 可折叠树;有子级的节点点击展开/收起 */
  function initMindmaps() {
    document.querySelectorAll(".mindmap").forEach(function (mm) {
      var rootTitle = mm.getAttribute("data-root");
      if (rootTitle && !mm.querySelector(".mm-root")) {
        var rootEl = document.createElement("div");
        rootEl.className = "mm-root";
        rootEl.textContent = rootTitle;
        mm.insertBefore(rootEl, mm.firstChild);
      }
      mm.querySelectorAll("li").forEach(function (li) {
        var childUl = li.querySelector(":scope > ul");
        var node = document.createElement("span");
        node.className = "mm-node";
        var toMove = [];
        Array.prototype.forEach.call(li.childNodes, function (n) {
          if (n !== childUl) toMove.push(n);
        });
        toMove.forEach(function (n) { node.appendChild(n); });
        li.insertBefore(node, li.firstChild);
        if (childUl) {
          li.classList.add("mm-branch");
          if (li.hasAttribute("data-collapsed")) li.classList.add("collapsed");
          node.addEventListener("click", function (e) {
            if (e.target.closest("a")) return; // 链接照常跳转
            li.classList.toggle("collapsed");
          });
        }
      });
    });
  }

  /* 测验:.quiz[data-answer] 内 .quiz-opts button[data-k] */
  function initQuizzes() {
    document.querySelectorAll(".quiz").forEach(function (q) {
      var answer = q.getAttribute("data-answer");
      var btns = q.querySelectorAll(".quiz-opts button");
      btns.forEach(function (b) {
        b.addEventListener("click", function () {
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
  function initTabs() {
    document.querySelectorAll(".tabs").forEach(function (t) {
      var navBtns = t.querySelectorAll(":scope > .tab-nav > button");
      var panels = t.querySelectorAll(":scope > .tab-panels > .tab-panel");
      navBtns.forEach(function (b, i) {
        b.addEventListener("click", function () {
          navBtns.forEach(function (x) { x.classList.remove("active"); });
          panels.forEach(function (p) { p.classList.remove("active"); });
          b.classList.add("active");
          if (panels[i]) panels[i].classList.add("active");
        });
      });
    });
  }

  /* 代码块:自动注入"复制"按钮 */
  function initCodeCopy() {
    function fallbackCopy(text) {
      var ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (e) { /* 剪贴板不可用时静默 */ }
      document.body.removeChild(ta);
    }
    document.querySelectorAll(".code-block").forEach(function (cb) {
      var pre = cb.querySelector("pre");
      if (!pre || cb.querySelector(".copy-btn")) return;
      var btn = document.createElement("button");
      btn.className = "copy-btn";
      btn.textContent = "复制";
      btn.addEventListener("click", function () {
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
    prev.addEventListener("click", function () { if (i > 0) { i--; render(); } });
    next.addEventListener("click", function () { if (i < steps.length - 1) { i++; render(); } });
    render();
  };

  document.addEventListener("DOMContentLoaded", function () {
    initMindmaps();
    initQuizzes();
    initTabs();
    initCodeCopy();
  });
})();
