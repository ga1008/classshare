/* ============================================================
   LessonDoc 2.0 — deck-engine.js(JSON → DOM 渲染引擎)
   读取壳页内嵌的 <script type="application/json" id="lessondoc-data">,
   按 kind 渲染:
     kind=lesson → PPT 式幻灯片(交给 slides.js 驱动)或 article 长文
     kind=home   → 课程首页(hero/思维导图/课次导航/选项卡)
   约定:
   - 本脚本在 course.js / slides.js 之后引入,同步渲染(脚本位于
     body 尾,DOM 已就绪);course.js / slides.js 的 DOMContentLoaded
     初始化随后运行,接管测验/选项卡/思维导图/翻页。
   - 逐块 try/catch:任何单块失败渲染为 .ld-broken 占位卡,绝不整页
     报错;JSON 解析失败显示友好错误页。
   - 主题优先级:URL ?theme= > localStorage > JSON theme > sky。
   - 零外部依赖,file:// 离线可用。
   ============================================================ */
(function () {
  "use strict";

  var SPEC_MAJOR = "lessondoc/2";
  var THEMES = ["sky", "teal", "violet", "amber", "rose", "slate"];
  var THEME_LABELS = { sky: "天蓝", teal: "青绿", violet: "紫", amber: "暖橙", rose: "玫红", slate: "素雅" };

  /* ---------- 小工具 ---------- */
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  /* 行内 mini-markdown:**粗** `代码` *斜*;先转义再替换,天然防注入 */
  function md(s) {
    var t = esc(s);
    t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
    t = t.replace(/\*([^*]+)\*/g, "<i>$1</i>");
    t = t.replace(/\n/g, "<br>");
    return t;
  }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function textWidth(s, fs) {          /* CJK 宽度估算(图示布局用) */
    var w = 0, i, c;
    s = String(s == null ? "" : s);
    for (i = 0; i < s.length; i++) {
      c = s.charCodeAt(i);
      w += (c > 255 ? fs : fs * 0.56);
    }
    return w;
  }
  function num(v, dflt) { var n = parseFloat(v); return isFinite(n) ? n : dflt; }
  function qs(name) {
    var m = new RegExp("[?&]" + name + "=([^&#]*)").exec(location.search);
    return m ? decodeURIComponent(m[1].replace(/\+/g, " ")) : "";
  }
  function withQuery(href) {           /* 站内跳转保持 theme/profile 参数 */
    var keep = [];
    var t = qs("theme"), p = qs("profile");
    if (t) keep.push("theme=" + encodeURIComponent(t));
    if (p) keep.push("profile=" + encodeURIComponent(p));
    if (!keep.length) return href;
    return href + (href.indexOf("?") >= 0 ? "&" : "?") + keep.join("&");
  }
  /* SVG 逃生舱消毒:剥 script/foreignObject/事件属性/js 协议 */
  function sanitizeSvg(body) {
    var t = String(body == null ? "" : body);
    t = t.replace(/<\s*script[\s\S]*?<\s*\/\s*script\s*>/gi, "");
    t = t.replace(/<\s*foreignObject[\s\S]*?<\s*\/\s*foreignObject\s*>/gi, "");
    t = t.replace(/\son[a-z]+\s*=\s*"[^"]*"/gi, "");
    t = t.replace(/\son[a-z]+\s*=\s*'[^']*'/gi, "");
    t = t.replace(/(href|xlink:href)\s*=\s*(["'])\s*javascript:[^"']*\2/gi, "");
    return t;
  }
  function brokenCard(msg) { return el("div", "ld-broken", "⚠ " + esc(msg || "此内容块加载失败")); }

  /* ---------- 主题 ---------- */
  function packKey() {
    /* 包根路径作为主题记忆键:lesson 页去掉 lesson_N/ 一段 */
    var p = location.pathname.replace(/[^/]*$/, "");
    p = p.replace(/lesson[_-]?\d+\/$/i, "");
    return "lessondoc-theme::" + p;
  }
  function storedTheme() { try { return localStorage.getItem(packKey()) || ""; } catch (e) { return ""; } }
  function storeTheme(v) { try { localStorage.setItem(packKey(), v); } catch (e) { /* 隐私模式忽略 */ } }
  function normalizeTheme(v) {
    if (!v) return "";
    var parts = String(v).trim().toLowerCase().split(/[\s+]+/);
    var name = "", dark = false;
    parts.forEach(function (p) {
      if (p === "dark") dark = true;
      else if (THEMES.indexOf(p) >= 0) name = p;
    });
    if (!name && !dark) return "";
    return (name || "sky") + (dark ? " dark" : "");
  }
  function applyTheme(data) {
    var t = normalizeTheme(qs("theme")) || normalizeTheme(storedTheme()) ||
            normalizeTheme(data && data.theme) || "sky";
    document.documentElement.setAttribute("data-theme", t);
    return t;
  }

  /* ============================================================
     图示 DSL(diagram 块):flow / sequence / arch / mindmap
     统一语义色:var(--dg-*),换主题自动换色。
     ============================================================ */
  var TONE_STROKE = {
    primary: "var(--dg-primary)", ok: "var(--dg-ok)", warn: "var(--dg-warn)",
    err: "var(--dg-err)", muted: "var(--dg-muted)"
  };
  function toneStroke(t) { return TONE_STROKE[t] || TONE_STROKE.primary; }

  function svgWrap(inner, w, h, label, maxW) {
    return '<svg viewBox="0 0 ' + w + " " + h + '" xmlns="http://www.w3.org/2000/svg" role="img"' +
      (label ? ' aria-label="' + esc(label) + '"' : "") +
      (maxW ? ' style="max-width:' + maxW + 'px"' : "") + ">" + inner + "</svg>";
  }
  function arrowDefs(idSuffix) {
    return "<defs>" +
      '<marker id="ldarr' + idSuffix + '" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">' +
      '<path d="M0,0 L8,4 L0,8 z" fill="var(--dg-line)"/></marker>' +
      '<marker id="ldarrP' + idSuffix + '" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">' +
      '<path d="M0,0 L8,4 L0,8 z" fill="var(--dg-primary)"/></marker>' +
      "</defs>";
  }
  var _dgSeq = 0;

  /* ---- flow:分层布局(方向 h/v) ---- */
  function renderFlow(cfg) {
    var nodes = (cfg.nodes || []).filter(function (n) { return n && (n.id != null || n.label); });
    if (!nodes.length) throw new Error("flow 无节点");
    var byId = {};
    nodes.forEach(function (n, idx) { n._id = String(n.id != null ? n.id : n.label); n._i = idx; byId[n._id] = n; });
    var edges = (cfg.edges || []).filter(function (e) {
      return e && byId[String(e.from)] && byId[String(e.to)];
    });
    /* 最长路径分层(防环:迭代上限) */
    nodes.forEach(function (n) { n._d = 0; });
    var changed = true, guard = 0;
    while (changed && guard++ < nodes.length + 2) {
      changed = false;
      edges.forEach(function (e) {
        var a = byId[String(e.from)], b = byId[String(e.to)];
        if (b._d < a._d + 1 && guard <= nodes.length) { b._d = a._d + 1; changed = true; }
      });
    }
    var horiz = cfg.direction !== "v";
    var FS = 14, PADX = 18, NH = 44;
    var layers = [];
    nodes.forEach(function (n) { (layers[n._d] = layers[n._d] || []).push(n); });
    var maxW = 0;
    nodes.forEach(function (n) { n._w = Math.max(96, textWidth(n.label || n._id, FS) + PADX * 2); maxW = Math.max(maxW, n._w); });
    var GX = maxW + 74, GY = NH + 34;
    var maxCross = 0;
    layers.forEach(function (L) { maxCross = Math.max(maxCross, L.length); });
    var W, H;
    if (horiz) { W = layers.length * GX + 20; H = maxCross * GY + 30; }
    else { W = maxCross * GX + 20; H = layers.length * GY + 30; }
    /* 横向节点过多时自动折行:12 个节点排成一条线会被画布缩放压到看不清字,
       折成每行 ≤6 个后宽度减半、字号翻倍。只在单链(每层 1 个节点)时折行,
       多分支图折行会让连线含义混乱。 */
    var PER_ROW = 6;
    var wrap = horiz && maxCross === 1 && layers.length > PER_ROW;
    var rowGap = NH + 46;
    if (wrap) {
      var rowCount = Math.ceil(layers.length / PER_ROW);
      W = Math.min(PER_ROW, layers.length) * GX + 20;
      H = rowCount * rowGap + 30;
      layers.forEach(function (L, d) {
        var row = Math.floor(d / PER_ROW), col = d % PER_ROW;
        L.forEach(function (n) { n._x = 20 + col * GX; n._y = 22 + row * rowGap; });
      });
    } else {
      layers.forEach(function (L, d) {
        L.forEach(function (n, k) {
          var off = (maxCross - L.length) / 2;
          if (horiz) { n._x = 20 + d * GX; n._y = 22 + (k + off) * GY; }
          else { n._x = 20 + (k + off) * GX; n._y = 18 + d * GY; }
        });
      });
    }
    var suf = "f" + (++_dgSeq);
    var out = arrowDefs(suf);
    edges.forEach(function (e) {
      var a = byId[String(e.from)], b = byId[String(e.to)];
      var x1, y1, x2, y2;
      if (wrap && b._y > a._y) {
        /* 折行处:从上一行行末底部绕到下一行行首左侧,不横穿整图 */
        var midY = a._y + NH + (rowGap - NH) / 2;
        var sx = a._x + a._w / 2, sy = a._y + NH;
        var ex = b._x - 2, ey = b._y + NH / 2;
        out += '<path d="M ' + sx + ' ' + sy + ' V ' + midY + ' H ' + (ex - 14) +
          ' V ' + ey + ' H ' + ex + '" fill="none" stroke="var(--dg-line)" stroke-width="2"' +
          ' marker-end="url(#ldarr' + suf + ')"/>';
        if (e.label) {
          out += '<text x="' + (sx + 6) + '" y="' + (midY - 5) +
            '" font-size="12" fill="var(--dg-muted)">' + esc(e.label) + "</text>";
        }
        return;
      }
      if (horiz) { x1 = a._x + a._w; y1 = a._y + NH / 2; x2 = b._x - 2; y2 = b._y + NH / 2; }
      else { x1 = a._x + a._w / 2; y1 = a._y + NH; x2 = b._x + b._w / 2; y2 = b._y - 2; }
      out += '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
        '" stroke="var(--dg-line)" stroke-width="2" marker-end="url(#ldarr' + suf + ')"/>';
      if (e.label) {
        out += '<text x="' + ((x1 + x2) / 2) + '" y="' + ((y1 + y2) / 2 - 7) +
          '" text-anchor="middle" font-size="12" fill="var(--dg-muted)">' + esc(e.label) + "</text>";
      }
    });
    nodes.forEach(function (n) {
      out += '<g><rect x="' + n._x + '" y="' + n._y + '" width="' + n._w + '" height="' + NH +
        '" rx="10" fill="var(--dg-fill)" stroke="' + toneStroke(n.tone) + '" stroke-width="2"/>' +
        '<text x="' + (n._x + n._w / 2) + '" y="' + (n._y + NH / 2 + 5) +
        '" text-anchor="middle" font-size="' + FS + '" fill="var(--dg-text)">' + esc(n.label || n._id) + "</text></g>";
    });
    return svgWrap(out, W, H, cfg.caption || "流程图", Math.min(940, W * 1.15));
  }

  /* ---- sequence:泳道时序图 ---- */
  function renderSequence(cfg) {
    var actors = (cfg.actors || []).filter(function (a) { return a && (a.id != null || a.label); });
    if (actors.length < 2) throw new Error("sequence 至少两个参与者");
    var byId = {};
    actors.forEach(function (a, i) { a._id = String(a.id != null ? a.id : a.label); a._i = i; byId[a._id] = a; });
    var msgs = (cfg.messages || []).filter(function (m) {
      return m && byId[String(m.from)] && byId[String(m.to)];
    });
    var FS = 13, AH = 40, MSG_GAP = 52, TOP = 14;
    var maxLabel = 0;
    actors.forEach(function (a) { a._w = Math.max(110, textWidth(a.label || a._id, FS) + 34); maxLabel = Math.max(maxLabel, a._w); });
    var SP = Math.max(maxLabel + 60, 200);
    actors.forEach(function (a) { a._x = 70 + a._i * SP + a._w / 2; });
    var H = TOP + AH + 26 + msgs.length * MSG_GAP + 26;
    var W = 70 + actors.length * SP;
    var suf = "s" + (++_dgSeq);
    var out = arrowDefs(suf);
    actors.forEach(function (a) {
      out += '<line x1="' + a._x + '" y1="' + (TOP + AH) + '" x2="' + a._x + '" y2="' + (H - 8) +
        '" stroke="var(--dg-line)" stroke-width="1.5" stroke-dasharray="5 5"/>' +
        '<rect x="' + (a._x - a._w / 2) + '" y="' + TOP + '" width="' + a._w + '" height="' + AH +
        '" rx="9" fill="var(--dg-primary-soft)" stroke="var(--dg-primary)" stroke-width="2"/>' +
        '<text x="' + a._x + '" y="' + (TOP + AH / 2 + 5) + '" text-anchor="middle" font-size="' + FS +
        '" font-weight="bold" fill="var(--dg-primary-dark)">' + esc(a.label || a._id) + "</text>";
    });
    msgs.forEach(function (m, i) {
      var a = byId[String(m.from)], b = byId[String(m.to)];
      var y = TOP + AH + 40 + i * MSG_GAP;
      var g = "";
      if (a === b) {  /* 自环 */
        g += '<path d="M ' + (a._x + 2) + " " + (y - 8) + ' h 46 v 18 h -44"' +
          ' fill="none" stroke="var(--dg-primary)" stroke-width="2" marker-end="url(#ldarrP' + suf + ')"/>' +
          '<text x="' + (a._x + 56) + '" y="' + (y - 12) + '" font-size="12" fill="var(--dg-text)">' + esc(m.label || "") + "</text>";
      } else {
        var x1 = a._x + (a._x < b._x ? 3 : -3), x2 = b._x + (a._x < b._x ? -4 : 4);
        g += '<line x1="' + x1 + '" y1="' + y + '" x2="' + x2 + '" y2="' + y +
          '" stroke="var(--dg-primary)" stroke-width="2"' +
          (m.dashed ? ' stroke-dasharray="6 5"' : "") +
          ' marker-end="url(#ldarrP' + suf + ')"/>' +
          '<text x="' + ((x1 + x2) / 2) + '" y="' + (y - 9) +
          '" text-anchor="middle" font-size="12.5" fill="var(--dg-text)">' + esc(m.label || "") + "</text>";
      }
      if (m.step != null) {
        out += '<g class="fragment" data-step="' + num(m.step, 0) + '">' + g + "</g>";
      } else out += g;
    });
    return svgWrap(out, W, H, cfg.caption || "时序图", Math.min(940, W * 1.2));
  }

  /* ---- arch:分层架构图 ---- */
  function renderArch(cfg) {
    var layers = (cfg.layers || []).filter(function (L) { return L && (L.nodes || []).length; });
    if (!layers.length) throw new Error("arch 无层");
    var FS = 13, NH = 42, LH = 86, PAD = 16, LABEL_W = 96;
    var maxRowW = 0;
    layers.forEach(function (L) {
      var w = PAD;
      L.nodes.forEach(function (n) {
        n._w = Math.max(96, textWidth(n.label || "", FS) + 30);
        w += n._w + 14;
      });
      L._rowW = w + PAD - 14;
      maxRowW = Math.max(maxRowW, L._rowW);
    });
    var W = LABEL_W + maxRowW + 24, H = layers.length * (LH + 14) + 10;
    var pos = {};  /* label/id → 中心坐标 */
    var suf = "a" + (++_dgSeq);
    var out = arrowDefs(suf);
    layers.forEach(function (L, li) {
      var y = 8 + li * (LH + 14);
      out += '<rect x="10" y="' + y + '" width="' + (W - 20) + '" height="' + LH +
        '" rx="12" fill="var(--dg-primary-soft)" fill-opacity="0.4" stroke="var(--dg-line)" stroke-width="1.5"/>' +
        '<text x="26" y="' + (y + LH / 2 + 5) + '" font-size="' + FS + '" font-weight="bold" fill="var(--dg-primary-dark)">' +
        esc(L.label || "") + "</text>";
      var x = LABEL_W + (maxRowW - L._rowW) / 2 + PAD;
      L.nodes.forEach(function (n) {
        var ny = y + (LH - NH) / 2;
        out += '<rect x="' + x + '" y="' + ny + '" width="' + n._w + '" height="' + NH +
          '" rx="9" fill="var(--dg-fill)" stroke="' + toneStroke(n.tone) + '" stroke-width="2"/>' +
          '<text x="' + (x + n._w / 2) + '" y="' + (ny + NH / 2 + 5) +
          '" text-anchor="middle" font-size="' + FS + '" fill="var(--dg-text)">' + esc(n.label || "") + "</text>";
        var key = String(n.id != null ? n.id : n.label);
        pos[key] = { x: x + n._w / 2, y: ny + NH / 2, top: ny, bottom: ny + NH };
        x += n._w + 14;
      });
    });
    (cfg.links || []).forEach(function (lk) {
      var a = pos[String(lk.from)], b = pos[String(lk.to)];
      if (!a || !b) return;
      var y1 = a.y < b.y ? a.bottom : a.top, y2 = a.y < b.y ? b.top - 2 : b.bottom + 2;
      out += '<line x1="' + a.x + '" y1="' + y1 + '" x2="' + b.x + '" y2="' + y2 +
        '" stroke="var(--dg-line)" stroke-width="2" marker-end="url(#ldarr' + suf + ')"/>';
      if (lk.label) {
        out += '<text x="' + ((a.x + b.x) / 2 + 6) + '" y="' + ((y1 + y2) / 2 + 4) +
          '" font-size="11.5" fill="var(--dg-muted)">' + esc(lk.label) + "</text>";
      }
    });
    return svgWrap(out, W, H, cfg.caption || "架构图", Math.min(940, W * 1.15));
  }

  /* ---- mindmap:嵌套 ul(course.js initMindmaps 接管折叠) ---- */
  function renderMindmapTree(children) {
    var ul = document.createElement("ul");
    (children || []).forEach(function (node) {
      if (!node || !node.label) return;
      var li = document.createElement("li");
      if (node.collapsed) li.setAttribute("data-collapsed", "");
      if (node.href) {
        var a = document.createElement("a");
        a.href = withQuery(String(node.href));
        a.textContent = node.label;
        li.appendChild(a);
      } else {
        li.appendChild(document.createTextNode(node.label));
      }
      if (node.note) li.appendChild(el("span", "mm-leaf-note", esc(node.note)));
      if (node.children && node.children.length) li.appendChild(renderMindmapTree(node.children));
      ul.appendChild(li);
    });
    return ul;
  }
  function renderMindmap(cfg) {
    var mm = el("div", "mindmap");
    if (cfg.root) mm.setAttribute("data-root", cfg.root);
    mm.appendChild(renderMindmapTree(cfg.children));
    return mm;
  }

  /* ============================================================
     内容块渲染器注册表
     ============================================================ */
  var BLOCKS = {};

  BLOCKS.text = function (b) { return el("p", null, md(b.md != null ? b.md : b.text)); };

  BLOCKS.cards = function (b) {
    var cols = [1, 2, 3, 4].indexOf(num(b.cols, 2)) >= 0 ? num(b.cols, 2) : 2;
    var g = el("div", "grid-" + cols);
    (b.items || []).forEach(function (it) {
      if (!it) return;
      var card = el("div", "s-card" + (it.tone && it.tone !== "primary" ? " tone-" + esc(it.tone) : ""));
      card.appendChild(el("h4", null, (it.icon ? esc(it.icon) + " " : "") + md(it.title || "")));
      if (it.text) card.appendChild(el("p", null, md(it.text)));
      if (it.step != null) markFragment(card, it.step);
      g.appendChild(card);
    });
    return g;
  };

  BLOCKS.bignum = function (b) {
    var items = b.items || [];
    var g = el("div", "grid-" + Math.min(4, Math.max(2, items.length)));
    items.forEach(function (it) {
      if (!it) return;
      var card = el("div", "s-card big-num-card");
      card.appendChild(el("b", null, esc(it.value)));
      card.appendChild(el("span", null, esc(it.label || "") + (it.note ? "<br>" + esc(it.note) : "")));
      g.appendChild(card);
    });
    return g;
  };

  BLOCKS.bigmark = function (b) {
    var w = el("div");
    if (b.mark) w.appendChild(el("div", "big-mark", esc(b.mark)));
    if (b.line) w.appendChild(el("div", "big-line", md(b.line)));
    return w;
  };

  BLOCKS.timeline = function (b) {
    var t = el("div", "s-timeline");
    (b.items || []).slice(0, 8).forEach(function (it) {
      if (!it) return;
      var item = el("div", "tl-item");
      item.appendChild(el("b", null, md(it.title || "")));
      item.appendChild(el("span", null, md(it.text || "")));
      if (it.step != null) markFragment(item, it.step);
      t.appendChild(item);
    });
    return t;
  };

  BLOCKS.table = function (b) {
    var tb = el("table", "nice");
    if (b.head && b.head.length) {
      var tr = el("tr");
      b.head.forEach(function (h) { tr.appendChild(el("th", null, md(h))); });
      tb.appendChild(el("thead")).appendChild(tr);
    }
    var body = el("tbody");
    (b.rows || []).forEach(function (row) {
      if (!row || !row.length) return;
      var tr = el("tr");
      row.forEach(function (c) { tr.appendChild(el("td", null, md(c))); });
      if (b.rowStep) tr.className = "fragment";
      body.appendChild(tr);
    });
    tb.appendChild(body);
    return tb;
  };

  BLOCKS.callout = function (b) {
    var cls = "callout";
    if (b.tone === "think" || b.tone === "info") cls += " think";
    else if (b.tone === "ok") cls += " ok";
    else if (b.tone === "err") cls += " err";
    return el("div", cls, md(b.md != null ? b.md : b.text));
  };

  BLOCKS.tabs = function (b) {
    var root = el("div", "tabs");
    var nav = el("div", "tab-nav"), panels = el("div", "tab-panels");
    (b.tabs || []).forEach(function (t, i) {
      if (!t) return;
      var btn = el("button", i === 0 ? "active" : "", esc(t.label || "标签" + (i + 1)));
      btn.type = "button";
      nav.appendChild(btn);
      var p = el("div", "tab-panel" + (i === 0 ? " active" : ""));
      renderBlocks(t.blocks || [], p);
      panels.appendChild(p);
    });
    root.appendChild(nav); root.appendChild(panels);
    return root;
  };

  BLOCKS.details = function (b) {
    var d = el("details", "panel");
    d.appendChild(el("summary", null, md(b.summary || "详情")));
    var body = el("div", "panel-body");
    renderBlocks(b.blocks || [], body);
    d.appendChild(body);
    return d;
  };

  BLOCKS.code = function (b) {
    var w = el("div");
    var cb = el("div", "code-block");
    var pre = el("pre");
    pre.textContent = String(b.code == null ? "" : b.code);
    cb.appendChild(pre);
    w.appendChild(cb);
    if (b.output) {
      var out = el("div", "code-out");
      out.textContent = String(b.output);
      w.appendChild(out);
    }
    return w;
  };

  BLOCKS.media = function (b) {
    var fig = el("figure");
    var src = String(b.src || "");
    /* 只允许包内相对路径,拦网络与绝对路径 */
    var ok = src && !/^(https?:)?\/\//i.test(src) && src.charAt(0) !== "/" &&
      (src.indexOf("..") !== 0 || src.indexOf("../assets/") === 0);
    if (!ok) return brokenCard("媒体路径不合规:" + src);
    var node;
    if (b.kind === "video") {
      node = document.createElement("video");
      node.controls = true; node.src = src;
      if (b.poster) node.poster = String(b.poster);
      node.style.maxWidth = "100%"; node.style.maxHeight = "480px";
    } else if (b.kind === "audio") {
      node = document.createElement("audio");
      node.controls = true; node.src = src;
    } else {
      node = document.createElement("img");
      node.src = src; node.alt = String(b.caption || "");
      node.loading = "lazy";
    }
    node.addEventListener("error", function () {
      var ph = brokenCard("媒体资源缺失:" + src);
      if (node.parentNode) node.parentNode.replaceChild(ph, node);
    });
    fig.appendChild(node);
    if (b.caption) fig.appendChild(el("figcaption", null, md(b.caption)));
    return fig;
  };

  BLOCKS.svg = function (b) {
    var fig = el("figure");
    var vb = String(b.viewBox || "0 0 640 300").replace(/[^\d.\s-]/g, "");
    var maxW = num(b.maxWidth, 940);
    fig.innerHTML = '<svg viewBox="' + vb + '" xmlns="http://www.w3.org/2000/svg" role="img"' +
      (b.caption ? ' aria-label="' + esc(b.caption) + '"' : "") +
      ' style="max-width:' + maxW + 'px">' + sanitizeSvg(b.body) + "</svg>" +
      (b.caption ? "<figcaption>" + md(b.caption) + "</figcaption>" : "");
    return fig;
  };

  BLOCKS.diagram = function (b) {
    if (b.kind === "mindmap") return renderMindmap(b);
    var svg;
    if (b.kind === "sequence") svg = renderSequence(b);
    else if (b.kind === "arch") svg = renderArch(b);
    else svg = renderFlow(b);            /* flow 为默认 */
    var fig = el("figure");
    fig.innerHTML = svg + (b.caption ? "<figcaption>" + md(b.caption) + "</figcaption>" : "");
    return fig;
  };

  BLOCKS.quiz = function (b) {
    var q = el("div", "quiz");
    q.setAttribute("data-answer", String(b.answer || "A"));
    q.appendChild(el("div", "quiz-q", md(b.q || b.question || "")));
    var opts = el("div", "quiz-opts");
    (b.options || []).forEach(function (o) {
      if (!o) return;
      var k = String(o.k || o.key || "");
      var btn = el("button", null, esc(k) + ". " + md(o.text || ""));
      btn.type = "button";
      btn.setAttribute("data-k", k);
      opts.appendChild(btn);
    });
    q.appendChild(opts);
    q.appendChild(el("div", "quiz-exp", "✔ " + md(b.explain || b.exp || "")));
    return q;
  };

  BLOCKS.tasklist = function (b) {
    var ul = el("ul", "tasklist");
    (b.items || []).forEach(function (it) {
      if (it == null) return;
      var li = el("li");
      var cb = document.createElement("input");
      cb.type = "checkbox";
      li.appendChild(cb);
      li.appendChild(el("span", null, md(typeof it === "string" ? it : it.text || "")));
      if (it && it.step != null) markFragment(li, it.step);
      ul.appendChild(li);
    });
    return ul;
  };

  BLOCKS.reveal = function (b) {
    var list = el("div", "reveal-list");
    (b.items || []).forEach(function (it) {
      if (!it) return;
      var item = el("div", "reveal-item");
      var btn = el("button", null, md(it.label || "点击查看"));
      btn.type = "button";
      var body = el("div", "reveal-body", md(it.md != null ? it.md : it.text));
      btn.addEventListener("click", function () { item.classList.toggle("open"); });
      item.appendChild(btn); item.appendChild(body);
      list.appendChild(item);
    });
    return list;
  };

  var _stepperSeq = 0;
  BLOCKS.stepper = function (b) {
    var root = el("div", "stepper");
    root.id = "ld-stepper-" + (++_stepperSeq);
    var stage = el("div", "stage");
    if (b.stage) {
      var st = renderBlock(b.stage);
      if (st) stage.appendChild(st);
    }
    root.appendChild(stage);
    var steps = (b.steps || []).map(function (s) {
      return {
        text: s && s.text || "",
        on: (function (spec) {
          return function () {
            if (!spec) return;
            (spec.show || []).forEach(function (sel) {
              var t = safeQuery(stage, sel);
              if (t) t.setAttribute("visibility", "visible");
            });
            (spec.hide || []).forEach(function (sel) {
              var t = safeQuery(stage, sel);
              if (t) t.setAttribute("visibility", "hidden");
            });
            (spec.set || []).forEach(function (op) {
              if (!op || !op.target) return;
              var t = safeQuery(stage, String(op.target));
              if (!t) return;
              if (op.attr === "textContent") t.textContent = String(op.value == null ? "" : op.value);
              else if (op.attr) t.setAttribute(String(op.attr), String(op.value == null ? "" : op.value));
            });
          };
        })(s)
      };
    });
    /* makeStepper 由 course.js 提供;延后一拍确保 SVG 已插入文档 */
    if (steps.length && window.makeStepper) {
      requestAnimationFrame(function () { window.makeStepper(root.id, steps); });
    }
    return root;
  };
  function safeQuery(scope, sel) {
    try { return scope.querySelector(sel); } catch (e) { return null; }
  }

  function markFragment(node, step) {
    node.classList.add("fragment");
    node.setAttribute("data-step", String(num(step, 0)));
  }

  function renderBlock(b) {
    if (!b || typeof b !== "object") return brokenCard("空内容块");
    var fn = BLOCKS[String(b.type)];
    try {
      if (!fn) return brokenCard("未知内容块类型:" + esc(String(b.type)));
      var node = fn(b);
      if (!node) return brokenCard("内容块渲染为空:" + esc(String(b.type)));
      if (b.step != null && !node.classList.contains("fragment")) markFragment(node, b.step);
      if (b.id && !node.id) node.id = String(b.id).replace(/[^\w-]/g, "");
      if (b.exitStep != null) {
        if (!node.id) node.id = "ld-x" + (++_dgSeq);
        var marker = el("span", "fragment");
        marker.setAttribute("data-step", String(num(b.exitStep, 0)));
        marker.setAttribute("data-exit-target", "#" + node.id);
        marker.style.display = "none";
        var wrap = el("div");
        wrap.appendChild(node); wrap.appendChild(marker);
        return wrap;
      }
      return node;
    } catch (e) {
      return brokenCard("内容块渲染失败(" + esc(String(b.type)) + "):" + esc(e && e.message || ""));
    }
  }
  function renderBlocks(blocks, into) {
    (blocks || []).forEach(function (b) { into.appendChild(renderBlock(b)); });
    return into;
  }

  /* ============================================================
     kind=lesson:幻灯片渲染
     ============================================================ */
  function renderSlide(s, data) {
    var layout = String(s.layout || "content");
    var sec = document.createElement("section");
    var body;
    try {
      switch (layout) {
        case "title":
          sec.className = "slide slide--title";
          sec.innerHTML =
            '<span class="lesson-badge">' + esc(s.badge || data.badge || ("第 " + (data.lesson || "?") + " 课")) + "</span>" +
            "<h1>" + esc(s.title || data.title || "") + "</h1>" +
            '<p class="title-sub">' + esc(s.sub || data.subtitle || "") + "</p>" +
            '<p class="course-name">' + esc(data.course || "") + "</p>";
          break;
        case "section":
          sec.className = "slide slide--section";
          sec.innerHTML =
            '<p class="sec-no">' + esc(s.no || "") + "</p>" +
            '<p class="sec-title">' + esc(s.title || "") + "</p>" +
            (s.hint ? '<p class="sec-hint">' + md(s.hint) + "</p>" : "");
          break;
        case "end":
          sec.className = "slide slide--end";
          var endHtml = "<h2>" + esc(s.title || "本课小结") + "</h2>";
          if (s.summary) endHtml += "<p>" + md(s.summary) + "</p>";
          if (s.nextUp) endHtml += '<div class="next-up">' + md(s.nextUp) + "</div>";
          endHtml += '<p class="mt-lg"><a href="' + esc(withQuery("../main.html")) + '">⌂ 返回课程首页</a></p>';
          sec.innerHTML = endHtml;
          if (s.blocks && s.blocks.length) renderBlocks(s.blocks, sec);
          break;
        case "two-col":
          sec.className = "slide slide--two-col" +
            (s.ratio === "3:2" ? " ratio-3-2" : s.ratio === "2:3" ? " ratio-2-3" : "");
          if (s.section) sec.setAttribute("data-section", s.section);
          if (s.title) sec.appendChild(el("h2", "slide-title", md(s.title)));
          if (s.sub) sec.appendChild(el("p", "slide-sub", md(s.sub)));
          body = el("div", "slide-body");
          body.appendChild(renderBlocks(s.left || [], el("div", "col")));
          body.appendChild(renderBlocks(s.right || [], el("div", "col")));
          sec.appendChild(body);
          break;
        case "center":
          sec.className = "slide slide--center";
          if (s.section) sec.setAttribute("data-section", s.section);
          body = el("div", "slide-body");
          renderBlocks(s.blocks || [], body);
          sec.appendChild(body);
          break;
        case "grid":
          sec.className = "slide slide--grid";
          if (s.section) sec.setAttribute("data-section", s.section);
          if (s.title) sec.appendChild(el("h2", "slide-title", md(s.title)));
          if (s.sub) sec.appendChild(el("p", "slide-sub", md(s.sub)));
          body = el("div", "slide-body");
          (s.areas || []).forEach(function (a) {
            if (!a) return;
            var cell = el("div", "grid-area");
            var area = String(a.area || "").replace(/[^\d/\s]/g, "");
            if (/^\d+\s*\/\s*\d+\s*\/\s*\d+\s*\/\s*\d+$/.test(area)) cell.style.gridArea = area;
            renderBlocks(a.blocks || [], cell);
            body.appendChild(cell);
          });
          sec.appendChild(body);
          break;
        default:  /* content */
          sec.className = "slide";
          if (s.section) sec.setAttribute("data-section", s.section);
          if (s.title) sec.appendChild(el("h2", "slide-title", md(s.title)));
          if (s.sub) sec.appendChild(el("p", "slide-sub", md(s.sub)));
          body = el("div", "slide-body");
          renderBlocks(s.blocks || [], body);
          sec.appendChild(body);
      }
      if (s.notes) sec.appendChild(el("div", "slide-notes", md(s.notes)));
    } catch (e) {
      sec.className = "slide";
      sec.innerHTML = "";
      sec.appendChild(el("h2", "slide-title", "此页渲染失败"));
      var eb = el("div", "slide-body");
      eb.appendChild(brokenCard(e && e.message || "未知错误"));
      sec.appendChild(eb);
    }
    return sec;
  }

  function renderDeck(data) {
    document.body.className = "slides-page";
    var home = el("a", "slides-home", "⌂ 课程首页");
    home.href = withQuery("../main.html");
    document.body.appendChild(home);
    var deck = el("div", "deck");
    deck.setAttribute("data-course", data.course || "");
    var slides = Array.isArray(data.slides) ? data.slides : [];
    slides.forEach(function (s) { deck.appendChild(renderSlide(s || {}, data)); });
    if (!slides.length) {
      var empty = el("section", "slide");
      empty.appendChild(el("h2", "slide-title", "本课次暂无内容"));
      deck.appendChild(empty);
    }
    document.body.appendChild(deck);
  }

  /* ---------- article 版式族(同一份 JSON 的长文渲染) ---------- */
  function buildQuery(overrides) {
    var params = {};
    var t = qs("theme"), p = qs("profile");
    if (t) params.theme = t;
    if (p) params.profile = p;
    for (var k in overrides) {
      if (overrides[k] == null) delete params[k];
      else params[k] = overrides[k];
    }
    var parts = [];
    for (var key in params) parts.push(key + "=" + encodeURIComponent(params[key]));
    return parts.length ? "?" + parts.join("&") : "";
  }
  function renderArticle(data) {
    document.body.className = "article-page";
    var bar = el("div", "article-topbar");
    var back = el("a", null, "⌂ 课程首页"); back.href = withQuery("../main.html");
    var toSlides = el("a", null, "▶ 幻灯模式");
    toSlides.href = location.pathname.split("/").pop() + buildQuery({ profile: null });
    bar.appendChild(back); bar.appendChild(toSlides);
    document.body.appendChild(bar);

    var flow = el("div", "article-flow");
    var head = el("div", "a-head");
    head.innerHTML = '<span class="lesson-badge">' + esc(data.badge || "") + "</span>" +
      "<h1>" + esc(data.title || "") + "</h1>" +
      '<p class="title-sub">' + esc(data.subtitle || "") + "</p>";
    flow.appendChild(head);
    (data.slides || []).forEach(function (s) {
      if (!s) return;
      var layout = String(s.layout || "content");
      if (layout === "title") return;                 /* 封面已并入 a-head */
      if (layout === "section") {
        flow.appendChild(el("div", "a-sec",
          '<span class="sec-no">' + esc(s.no || "") + '</span><span class="sec-title">' + esc(s.title || "") + "</span>" +
          (s.hint ? '<div class="sec-hint">' + md(s.hint) + "</div>" : "")));
        return;
      }
      var card = el("div", "a-slide");
      if (s.title) card.appendChild(el("h2", null, md(s.title)));
      if (s.sub) card.appendChild(el("p", "a-sub", md(s.sub)));
      if (layout === "two-col") {
        renderBlocks(s.left || [], card);
        renderBlocks(s.right || [], card);
      } else if (layout === "grid") {
        (s.areas || []).forEach(function (a) { renderBlocks(a && a.blocks || [], card); });
      } else {
        renderBlocks(s.blocks || [], card);
        if (layout === "end") {
          if (s.summary) card.appendChild(el("p", null, md(s.summary)));
          if (s.nextUp) card.appendChild(el("div", "callout think", md(s.nextUp)));
        }
      }
      flow.appendChild(card);
    });
    document.body.appendChild(flow);
    buildHomeAppearance(data);
  }

  /* ============================================================
     kind=home:课程首页渲染
     ============================================================ */
  function lessonHref(n) { return withQuery("lesson_" + n + "/lesson_" + n + ".html"); }

  function renderHome(m) {
    document.body.className = "";
    var course = m.course || {};
    var lessons = Array.isArray(m.lessons) ? m.lessons : [];
    var byN = {};
    lessons.forEach(function (L) { if (L && L.n != null) byN[L.n] = L; });

    /* --- hero --- */
    var hero = el("header", "hero");
    hero.innerHTML = "<h1>" + esc(course.name || "课程学习文档") + "</h1>" +
      (course.intro ? "<p>" + md(course.intro) + "</p>" : "");
    var statsEl = el("div", "stats");
    [
      { v: course.totalHours, l: "总学时" },
      { v: course.sessionCount || lessons.length, l: "课次" },
      { v: course.credits, l: "学分" },
      { v: course.assessment, l: "考核", small: true }
    ].forEach(function (s) {
      if (s.v == null || s.v === "") return;
      statsEl.appendChild(el("div", "stat",
        "<b" + (s.small ? ' style="font-size:1em"' : "") + ">" + esc(s.v) + "</b><span>" + esc(s.l) + "</span>"));
    });
    hero.appendChild(statsEl);
    document.body.appendChild(hero);

    var wrap = el("div", "wrap");
    document.body.appendChild(wrap);

    /* --- 总览思维导图(stages × lessons 自动生成) --- */
    var stages = Array.isArray(m.stages) ? m.stages : [];
    var mmSection = el("section", "section");
    mmSection.appendChild(el("h2", null, "课程知识体系总览"));
    mmSection.appendChild(el("p", "sub", "点击分支展开/收起,点击课次名进入对应课次"));
    var mmCard = el("div", "card");
    var mmChildren = stages.map(function (st) {
      var ns = (st && st.lessons || []).filter(function (n) { return byN[n]; });
      return {
        label: st && st.label || "",
        note: ns.length ? "第" + ns[0] + "—" + ns[ns.length - 1] + "课" : "",
        children: ns.map(function (n) {
          var L = byN[n];
          return {
            label: "第" + n + "课 " + (L.title || ""),
            note: (L.topics || []).slice(0, 2).join(" · ") || (L.status !== "ready" ? "待发布" : ""),
            href: L.status === "ready" ? lessonHref(n) : null
          };
        })
      };
    });
    mmCard.appendChild(renderMindmap({ root: course.name || "课程", children: mmChildren }));
    mmSection.appendChild(mmCard);
    wrap.appendChild(mmSection);

    /* --- 课次导航(阶段分组卡片墙) --- */
    var navSection = el("section", "section");
    navSection.appendChild(el("h2", null, "课次导航"));
    navSection.appendChild(el("p", "sub", "已发布课次可点击进入"));
    stages.forEach(function (st) {
      if (!st) return;
      navSection.appendChild(el("h3", "mt-md", esc(st.label || "")));
      var grid = el("div", "grid");
      (st.lessons || []).forEach(function (n) {
        var L = byN[n];
        if (!L) return;
        var ready = L.status === "ready";
        var card = document.createElement(ready ? "a" : "div");
        card.className = "lesson-card " + (ready ? "ready" : "pending");
        if (ready) card.href = lessonHref(n);
        var tags = (L.topics || []).slice(0, 3).map(function (t) {
          return '<span class="tag">' + esc(t) + "</span>";
        }).join("");
        if (L.lab) tags = '<span class="tag lab">★ 上机</span>' + tags;
        card.innerHTML = '<span class="no">第' + n + "课</span>" +
          (ready ? "" : '<span class="badge-soon">待发布</span>') +
          "<h4>" + esc(L.title || "") + "</h4>" +
          '<div class="tags">' + tags + "</div>";
        grid.appendChild(card);
      });
      navSection.appendChild(grid);
    });
    wrap.appendChild(navSection);

    /* --- 课程信息选项卡 --- */
    var tabs = Array.isArray(m.tabs) ? m.tabs : [];
    if (tabs.length) {
      var infoSection = el("section", "section");
      infoSection.appendChild(el("h2", null, "课程信息"));
      infoSection.appendChild(renderBlock({ type: "tabs", tabs: tabs }));
      wrap.appendChild(infoSection);
    }

    /* --- footer --- */
    var tb = m.textbook || {};
    var foot = el("footer", "site");
    var tbText = tb.title ? "教材:《" + esc(tb.title) + "》" +
      (tb.author ? " · " + esc(tb.author) : "") + (tb.publisher ? " · " + esc(tb.publisher) : "") : "";
    foot.innerHTML = tbText + (tbText ? "<br>" : "") +
      "LessonDoc " + esc(String(m.spec || "2.0").replace("lessondoc/", "")) + " · 本文档由课程学习文档模板生成";
    document.body.appendChild(foot);

    buildHomeAppearance(m);
  }

  /* ---------- 外观切换 UI ---------- */
  function themeSelectHtml(current) {
    var opts = "";
    THEMES.forEach(function (t) {
      opts += '<option value="' + t + '"' + (current.indexOf(t) === 0 ? " selected" : "") + ">" +
        THEME_LABELS[t] + "</option>";
    });
    return opts;
  }
  function bindThemeControls(sel, darkBtn) {
    function currentName() { return (document.documentElement.getAttribute("data-theme") || "sky"); }
    sel.addEventListener("change", function () {
      var dark = /\bdark\b/.test(currentName());
      var v = sel.value + (dark ? " dark" : "");
      document.documentElement.setAttribute("data-theme", v);
      storeTheme(v);
      sel.blur();
    });
    if (darkBtn) {
      darkBtn.addEventListener("click", function () {
        var name = currentName();
        var dark = !/\bdark\b/.test(name);
        var base = name.replace(/\s*dark\s*/, "").trim() || "sky";
        var v = base + (dark ? " dark" : "");
        document.documentElement.setAttribute("data-theme", v);
        storeTheme(v);
        darkBtn.textContent = dark ? "☀" : "🌙";
      });
    }
  }
  function buildHomeAppearance() {
    var cur = document.documentElement.getAttribute("data-theme") || "sky";
    var box = el("div", "slides-hud");   /* 复用 HUD 样式作为浮动外观条 */
    box.innerHTML = '<span>外观</span><select class="ld-theme-sel">' + themeSelectHtml(cur) +
      '</select><button type="button" class="ld-dark-btn" title="深色切换">' +
      (/\bdark\b/.test(cur) ? "☀" : "🌙") + "</button>";
    document.body.appendChild(box);
    bindThemeControls(box.querySelector(".ld-theme-sel"), box.querySelector(".ld-dark-btn"));
  }
  function extendSlidesHud() {
    /* slides.js 的 HUD 在 DOMContentLoaded 生成;本函数在其后调用 */
    var hud = document.querySelector(".slides-hud");
    if (!hud || hud.querySelector(".ld-theme-sel")) return;
    var cur = document.documentElement.getAttribute("data-theme") || "sky";
    var sel = document.createElement("select");
    sel.className = "ld-theme-sel"; sel.title = "配色主题";
    sel.innerHTML = themeSelectHtml(cur);
    var darkBtn = el("button", "ld-dark-btn", /\bdark\b/.test(cur) ? "☀" : "🌙");
    darkBtn.type = "button"; darkBtn.title = "深色切换";
    var artBtn = el("button", null, "📄 文档");
    artBtn.type = "button"; artBtn.title = "切换为长文阅读模式";
    artBtn.addEventListener("click", function () {
      location.href = location.pathname.split("/").pop() + buildQuery({ profile: "article" });
    });
    hud.appendChild(sel); hud.appendChild(darkBtn); hud.appendChild(artBtn);
    bindThemeControls(sel, darkBtn);
  }

  /* ---------- 友好错误页 ---------- */
  function renderFatal(msg) {
    document.body.className = "";
    var wrap = el("div", "wrap");
    var card = el("div", "card mt-lg");
    card.appendChild(el("h3", null, "学习文档暂时无法显示"));
    card.appendChild(el("p", null, esc(msg)));
    card.appendChild(el("p", null, '若你是学生,请联系老师重新生成本课次;若你是老师,可在平台"材料"页对本课次执行"AI 重写"。'));
    wrap.appendChild(card);
    document.body.appendChild(wrap);
  }

  /* ---------- 入口 ---------- */
  function main() {
    var holder = document.getElementById("lessondoc-data");
    if (!holder) { renderFatal("未找到文档数据(#lessondoc-data)。"); return; }
    var data;
    try { data = JSON.parse(holder.textContent); }
    catch (e) { renderFatal("文档数据不是有效的 JSON:" + (e && e.message || "")); return; }
    if (!data || typeof data !== "object") { renderFatal("文档数据为空。"); return; }
    if (String(data.spec || "").indexOf(SPEC_MAJOR) !== 0) {
      /* 版本不匹配仍尽力渲染,只在控制台提示 */
      try { console.warn("[lessondoc] spec 版本不匹配:", data.spec); } catch (e) { /* noop */ }
    }
    applyTheme(data);
    window.LESSONDOC = { data: data, renderBlock: renderBlock, renderBlocks: renderBlocks };

    var kind = String(data.kind || document.documentElement.getAttribute("data-doc-kind") || "lesson");
    if (kind === "home") {
      renderHome(data);
      return;
    }
    var profile = qs("profile") || String(data.layoutProfile || "slides");
    if (profile === "article") {
      renderArticle(data);
    } else {
      renderDeck(data);
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", extendSlidesHud);
      } else {
        requestAnimationFrame(extendSlidesHud);
      }
    }
  }

  main();   /* 同步执行:脚本位于 body 尾,DOM 已就绪;course.js/slides.js 的
               DOMContentLoaded 初始化随后接管交互 */
})();
