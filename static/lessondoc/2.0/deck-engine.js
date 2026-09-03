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
  function copyModel(value) { return JSON.parse(JSON.stringify(value)); }
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
    return sanitizeMarkup(body, true);
  }
  function brokenCard(msg) { return el("div", "ld-broken", "⚠ " + esc(msg || "此内容块加载失败")); }
  /* html 块二次消毒(服务端已白名单过滤;这里再剥一遍危险标签,双保险) */
  function sanitizeHtml(body) {
    return sanitizeMarkup(body, false);
  }
  function mediaSrcOk(src) {
    src = String(src || "");
    for (var i = 0; i < 4; i++) {
      var decoded;
      try { decoded = decodeURIComponent(src); } catch (e) { return false; }
      if (decoded === src) break;
      src = decoded;
    }
    if (i === 4 || !src || src !== src.trim() || /[\x00-\x1f\x7f\\]/.test(src) || /^[a-z][a-z0-9+.-]*:/i.test(src) || src[0] === "/" || src[0] === "#") return false;
    var path = src.split(/[?#]/)[0];
    if (path.indexOf("../assets/") === 0) path = path.slice(10);
    return !!path && path.split("/").every(function (p) { return !!p && p !== ".."; });
  }
  var HTML_TAGS = "div span p h1 h2 h3 h4 h5 h6 ul ol li table thead tbody tr th td b i em strong u s small sub sup code pre img br hr blockquote a figure figcaption".split(" ");
  var SVG_TAGS = "svg g a path rect circle ellipse line polyline polygon text tspan defs marker lineargradient radialgradient stop title desc clippath mask pattern use".split(" ");
  var SAFE_ATTRS = "class style src href alt title width height colspan rowspan viewbox d x y x1 y1 x2 y2 cx cy r rx ry points fill stroke stroke-width stroke-dasharray stroke-linecap stroke-linejoin opacity fill-opacity stroke-opacity transform text-anchor font-size font-weight font-family id offset stop-color markerwidth markerheight refx refy orient marker-end marker-start visibility dominant-baseline gradientunits gradienttransform preserveaspectratio patternunits clip-path".split(" ");
  // This property list is mirrored from css_policy.py and checked by the runtime tests.
  var CSS_PROPS = "color background background-color background-image background-size background-position background-repeat opacity font font-family font-size font-style font-weight font-variant line-height letter-spacing word-spacing text-align text-decoration text-transform text-shadow text-indent white-space word-break overflow-wrap vertical-align display visibility width height min-width max-width min-height max-height box-sizing margin margin-top margin-right margin-bottom margin-left padding padding-top padding-right padding-bottom padding-left border border-width border-style border-color border-radius border-top border-right border-bottom border-left border-collapse border-spacing box-shadow outline outline-width outline-style outline-color outline-offset overflow overflow-x overflow-y position top right bottom left z-index transform transform-origin flex flex-grow flex-shrink flex-basis flex-direction flex-wrap align-items align-self align-content justify-content justify-items justify-self gap row-gap column-gap order grid grid-template-columns grid-template-rows grid-template-areas grid-area grid-column grid-row grid-auto-flow grid-auto-columns grid-auto-rows list-style-type list-style-position object-fit object-position aspect-ratio fill stroke stroke-width stroke-linecap stroke-linejoin stroke-dasharray stroke-dashoffset fill-opacity stroke-opacity".split(" ");
  function safeCssValue(value) {
    var decoded = value.replace(/\\([0-9a-f]{1,6})\s?|\\([^\r\n])/gi, function (_, hex, ch) { return hex ? String.fromCodePoint(parseInt(hex, 16) || 65533) : ch; });
    return !/[<>\\@]/.test(decoded) && !/(?:url|expression|image|image-set|cross-fade|paint|element)\s*\(|javascript\s*:|behavior\s*:|-moz-binding/i.test(decoded);
  }
  function cleanCssDeclarations(style) {
    var result = [];
    for (var i = 0; i < style.length; i++) {
      var key = style[i], value = style.getPropertyValue(key);
      if (CSS_PROPS.indexOf(key) < 0 || !safeCssValue(value)) continue;
      if (key === "position" && ["relative", "absolute", "static"].indexOf(value.trim()) < 0) continue;
      result.push(key + ":" + value + (style.getPropertyPriority(key) ? " !important" : ""));
    }
    return result.join(";");
  }
  function sanitizeMarkup(body, svgOnly) {
    var template = document.createElement("template");
    template.innerHTML = svgOnly ? "<svg>" + String(body || "") + "</svg>" : String(body || "");
    var root = svgOnly ? template.content.firstElementChild : template.content;
    Array.prototype.slice.call(root.querySelectorAll("*")).forEach(function (node) {
      var tag = node.localName.toLowerCase();
      if (SVG_TAGS.indexOf(tag) < 0 && (svgOnly || HTML_TAGS.indexOf(tag) < 0)) {
        if (/^(script|style|iframe|object|embed|form|input|button|textarea|select|video|audio|source|base|template|noscript|foreignobject|math)$/.test(tag)) node.remove();
        else node.replaceWith.apply(node, Array.prototype.slice.call(node.childNodes));
        return;
      }
      Array.prototype.slice.call(node.attributes).forEach(function (attr) {
        var key = attr.name.toLowerCase(), value = attr.value;
        if (SAFE_ATTRS.indexOf(key) < 0) { node.removeAttribute(attr.name); return; }
        if (key === "src" || key === "href") {
          if (!(key === "href" && /^#[A-Za-z0-9_-]+$/.test(value)) && (svgOnly || !mediaSrcOk(value))) node.removeAttribute(attr.name);
        } else if (key === "style") {
          node.setAttribute("style", cleanCssDeclarations(node.style));
        } else if (["fill", "stroke", "marker-end", "marker-start", "clip-path"].indexOf(key) >= 0 && !/^url\(\s*#[A-Za-z0-9_-]+\s*\)$/.test(value) && !safeCssValue(value)) node.removeAttribute(attr.name);
      });
    });
    return svgOnly ? root.innerHTML : template.innerHTML;
  }
  function scopeCss(css, id) {
    try {
      var sheet = new CSSStyleSheet();
      sheet.replaceSync(String(css));
      var result = [];
      Array.prototype.forEach.call(sheet.cssRules, function (rule) {
        if (rule.type !== 1 || !rule.style) return;
        var selectors = [], part = "", depth = 0, quote = "";
        String(rule.selectorText).split("").forEach(function (c) {
          if (quote) { part += c; if (c === quote) quote = ""; return; }
          if (c === '"' || c === "'") quote = c;
          if (c === "(" || c === "[") depth++;
          if (c === ")" || c === "]") depth--;
          if (c === "," && !depth) { selectors.push(part); part = ""; } else part += c;
        });
        selectors.push(part);
        selectors = selectors.map(function (s) {
          s = s.trim();
          while (/^\.ld-html-[\w-]+\s+/.test(s)) s = s.replace(/^\.ld-html-[\w-]+\s+/, "");
          return s;
        }).filter(function (s) { return s && !/[{}@\\<&]/.test(s) && !/^[>+~]|:host|:root/.test(s); });
        var body = cleanCssDeclarations(rule.style);
        if (selectors.length && body) result.push(selectors.map(function (s) { return ".ld-html-" + id + " " + s; }).join(",") + "{" + body + "}");
      });
      return result.join("\n");
    } catch (e) { return ""; }
  }
  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }
  var domSequence = 0;
  function namespaceDom(root) {
    var prefix = "ld-dom-" + (++domSequence) + "-", ids = {};
    var nodes = Array.prototype.slice.call(root.querySelectorAll("[id]"));
    if (root.id) nodes.unshift(root);
    nodes.forEach(function (node, index) {
      var old = node.id, fresh = prefix + index;
      if (!node.hasAttribute("data-ld-origin-id")) node.setAttribute("data-ld-origin-id", old);
      if (!ids[old]) ids[old] = fresh;
      node.id = fresh;
    });
    root.querySelectorAll("*").forEach(function (node) {
      Array.prototype.slice.call(node.attributes).forEach(function (attr) {
        if (attr.name === "id" || attr.name === "data-ld-origin-id") return;
        var value = attr.value;
        if (["href", "xlink:href", "data-exit-target"].indexOf(attr.name) >= 0 && value[0] === "#" && ids[value.slice(1)]) value = "#" + ids[value.slice(1)];
        if (["fill", "stroke", "marker-end", "marker-start", "clip-path", "style"].indexOf(attr.name) >= 0) value = value.replace(/url\(\s*#([\w-]+)\s*\)/g, function (all, id) { return ids[id] ? "url(#" + ids[id] + ")" : all; });
        if (value !== attr.value) node.setAttribute(attr.name, value);
      });
    });
    root.querySelectorAll("style").forEach(function (style) {
      try {
        var sheet = new CSSStyleSheet();
        sheet.replaceSync(style.textContent);
        style.textContent = Array.prototype.map.call(sheet.cssRules, function (rule) {
          if (rule.type !== 1) return "";
          var selector = rule.selectorText.replace(/#([\w-]+)/g, function (all, id) { return ids[id] ? "#" + ids[id] : all; });
          return selector + "{" + rule.style.cssText + "}";
        }).join("\n");
      } catch (e) { style.textContent = ""; }
    });
    return root;
  }

  /* ============================================================
     2.1 样式层 / 定位层 / 背景(设计: docs/lessondoc-editor-2026-09.md §4)
     ============================================================ */
  var SEMANTIC_COLORS = {
    primary: "var(--primary)", "primary-dark": "var(--primary-dark)", "primary-soft": "var(--primary-soft)",
    ok: "var(--ok)", warn: "var(--warn)", err: "var(--err)", muted: "var(--muted)",
    text: "var(--text)", white: "#ffffff", transparent: "transparent"
  };
  var STYLE_FONTS = {
    sans: "var(--font)",
    serif: '"Songti SC","SimSun","Noto Serif CJK SC",serif',
    kai: '"KaiTi","STKaiti","Kaiti SC",serif',
    mono: "var(--mono)",
    rounded: '"Yuanti SC","YouYuan",system-ui,sans-serif'
  };
  var BOX_SHADOWS = { soft: "0 4px 14px rgba(0,0,0,.12)", hard: "4px 4px 0 rgba(0,0,0,.18)", glow: "0 0 18px var(--primary-soft)" };
  var TEXT_SHADOWS = { soft: "0 2px 6px rgba(0,0,0,.18)", hard: "3px 3px 0 rgba(0,0,0,.2)", glow: "0 0 12px var(--primary)" };
  /* 文本类块:size/gradient/stroke/shadow 按文字语义解释;其余按盒子语义 */
  var TEXT_BLOCKS = {
    text: 1, bigmark: 1, bignum: 1, cards: 1, timeline: 1, callout: 1, quiz: 1, reveal: 1,
    button: 1, tasklist: 1, table: 1, details: 1, tabs: 1
  };
  function cssColor(c) {
    if (!c) return "";
    c = String(c);
    if (SEMANTIC_COLORS[c]) return SEMANTIC_COLORS[c];
    return /^#[0-9a-f]{3,8}$/i.test(c) ? c : "";
  }
  function gradientCss(g) {
    if (!g || typeof g !== "object") return "";
    var a = cssColor(g.from), b = cssColor(g.to);
    if (!a || !b) return "";
    return "linear-gradient(" + clamp(num(g.angle, 135), 0, 360) + "deg," + a + "," + b + ")";
  }
  function applyStyle(node, st, isText) {
    if (!node || !st || typeof st !== "object") return;
    var s = node.style;
    if (st.font && STYLE_FONTS[st.font]) s.fontFamily = STYLE_FONTS[st.font];
    if (st.size != null) { s.fontSize = clamp(num(st.size, 26), 12, 160) + "px"; node.classList.add("ld-fs"); }
    if (st.weight) s.fontWeight = String(clamp(num(st.weight, 400), 100, 900));
    if (st.italic) s.fontStyle = "italic";
    var color = cssColor(st.color);
    if (color) s.color = color;
    var textGrad = isText ? gradientCss(st.gradient) : "";
    if (textGrad) {
      s.backgroundImage = textGrad;
      s.webkitBackgroundClip = "text"; s.backgroundClip = "text";
      s.color = "transparent"; s.webkitTextFillColor = "transparent";
      node.classList.add("ld-grad-text");
    }
    if (st.stroke && typeof st.stroke === "object") {
      s.webkitTextStroke = clamp(num(st.stroke.width, 1), 0, 6) + "px " + (cssColor(st.stroke.color) || "var(--text)");
    }
    if (st.shadow && st.shadow !== "none") {
      if (isText) s.textShadow = TEXT_SHADOWS[st.shadow] || "";
      else s.boxShadow = BOX_SHADOWS[st.shadow] || "";
    }
    if (st.align) s.textAlign = String(st.align);
    if (st.lineHeight != null) s.lineHeight = String(clamp(num(st.lineHeight, 1.5), 0.9, 3));
    if (st.letterSpacing != null) s.letterSpacing = clamp(num(st.letterSpacing, 0), -2, 20) + "px";
    if (st.opacity != null) s.opacity = String(clamp(num(st.opacity, 1), 0, 1));
    var bg = cssColor(st.bg);
    if (bg) s.backgroundColor = bg;
    if (!textGrad) {
      var bgg = gradientCss(st.bgGradient);
      if (bgg) s.backgroundImage = bgg;
    }
    if (st.border && typeof st.border === "object") {
      s.border = clamp(num(st.border.width, 1), 0, 12) + "px " + (st.border.style === "dashed" ? "dashed" : "solid") +
        " " + (cssColor(st.border.color) || "var(--muted)");
      if (st.border.radius != null) s.borderRadius = clamp(num(st.border.radius, 0), 0, 120) + "px";
    }
    if (st.padding != null) s.padding = clamp(num(st.padding, 0), 0, 120) + "px";
  }
  /* 定位块:frame → 绝对定位包裹层;包裹层是可选中/可寻址单元(携带 data-ld-id) */
  function renderPositioned(b, opts) {
    opts = opts || {};
    var f = (b && b.frame) || {};
    var wrap = el("div", "ld-pos");
    wrap.style.left = num(f.x, 0) + "px";
    wrap.style.top = num(f.y, 0) + "px";
    wrap.style.width = num(f.w, 320) + "px";
    wrap.style.height = num(f.h, 120) + "px";
    if (f.r) wrap.style.transform = "rotate(" + num(f.r, 0) + "deg)";
    if (f.z != null) wrap.style.zIndex = String(num(f.z, 0));
    var inner = renderBlock(b, true);
    var id = b && b.id ? String(b.id).replace(/[^\w-]/g, "") : "";
    if (id) {
      wrap.setAttribute(opts.global ? "data-ld-gid" : "data-ld-id", id);
      if (!opts.global) wrap.id = id;
      var carrier = inner.getAttribute("data-ld-id") ? inner : inner.querySelector('[data-ld-id="' + id + '"]');
      if (carrier) { carrier.removeAttribute("data-ld-id"); if (carrier.id === id) carrier.removeAttribute("id"); }
    }
    if (b && b.hidden) { wrap.classList.add("ld-hidden"); inner.classList.remove("ld-hidden"); }
    /* 动作属性上提到包裹层:包裹层才是按 id 寻址/点击的单元(codewalk 例外,动作属于其运行按钮) */
    if (inner.hasAttribute("data-ld-actions") && !inner.classList.contains("codewalk")) {
      wrap.setAttribute("data-ld-actions", inner.getAttribute("data-ld-actions"));
      wrap.classList.add("ld-actionable");
      inner.removeAttribute("data-ld-actions");
      inner.classList.remove("ld-actionable");
      if (inner.getAttribute("data-ld-once") === "1") { wrap.setAttribute("data-ld-once", "1"); inner.removeAttribute("data-ld-once"); }
    }
    if (b.type !== "group" && b.natural && num(b.natural.w, 0) > 0 && num(b.natural.h, 0) > 0) {
      var scaled = el("div", "ld-scaled-inner");
      var nw = clamp(num(b.natural.w, 320), 1, 10000), nh = clamp(num(b.natural.h, 120), 1, 10000);
      scaled.style.width = nw + "px"; scaled.style.height = nh + "px";
      scaled.style.transform = "scale(" + (num(f.w, 320) / nw) + "," + (num(f.h, 120) / nh) + ")";
      scaled.appendChild(inner); wrap.appendChild(scaled);
    } else wrap.appendChild(inner);
    return wrap;
  }
  function positionedToFlow(list, into) {
    (list || []).slice().sort(function (a, b) {
      return num(a && a.frame && a.frame.z, 0) - num(b && b.frame && b.frame.z, 0);
    }).forEach(function (o) { if (o) into.appendChild(renderBlock(o)); });
  }
  /* 页面/首页背景层 */
  function renderBg(bg) {
    if (!bg || typeof bg !== "object") return null;
    var node = el("div", "slide-bg"), any = false;
    var c = cssColor(bg.color);
    if (c) { node.style.backgroundColor = c; any = true; }
    var g = gradientCss(bg.gradient);
    if (g) { node.style.backgroundImage = g; any = true; }
    var im = bg.image;
    if (im && typeof im === "object" && mediaSrcOk(im.src)) {
      var img = el("div", "slide-bg-img");
      img.style.backgroundImage = 'url("' + String(im.src).replace(/["\\()]/g, "") + '")';
      var fit = String(im.fit || "cover");
      if (fit === "cover" || fit === "contain") img.style.backgroundSize = fit;
      else if (fit === "stretch") img.style.backgroundSize = "100% 100%";
      else if (fit === "tile") { img.style.backgroundSize = "auto"; img.style.backgroundRepeat = "repeat"; }
      else img.style.backgroundSize = clamp(num(im.scale, 100), 10, 400) + "%";
      img.style.backgroundPosition = clamp(num(im.x, 50), 0, 100) + "% " + clamp(num(im.y, 50), 0, 100) + "%";
      if (im.rotate) img.style.transform = "rotate(" + clamp(num(im.rotate, 0), -180, 180) + "deg) scale(1.45)";
      if (im.opacity != null) img.style.opacity = String(clamp(num(im.opacity, 1), 0, 1));
      if (im.blur) img.style.filter = "blur(" + clamp(num(im.blur, 0), 0, 40) + "px)";
      node.appendChild(img); any = true;
    }
    if (bg.tint && typeof bg.tint === "object" && cssColor(bg.tint.color)) {
      var tint = el("div", "slide-bg-tint");
      tint.style.backgroundColor = cssColor(bg.tint.color);
      tint.style.opacity = String(clamp(num(bg.tint.opacity, 0.3), 0, 1));
      node.appendChild(tint); any = true;
    }
    return any ? node : null;
  }
  var ARTICLE_MODE = false;

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
    cfg = copyModel(cfg);
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
    cfg = copyModel(cfg);
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
    cfg = copyModel(cfg);
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
      if (node.href && (mediaSrcOk(node.href) || /^#[A-Za-z0-9_-]+$/.test(node.href))) {
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
      if (b.poster && mediaSrcOk(b.poster)) node.poster = String(b.poster);
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
              else if (/^(visibility|opacity|transform|fill|stroke|stroke-width|x|y|cx|cy|r|width|height|d|points)$/.test(String(op.attr)) && safeCssValue(String(op.value || ""))) t.setAttribute(String(op.attr), String(op.value == null ? "" : op.value));
            });
          };
        })(s)
      };
    });
    /* makeStepper 由 course.js 提供;延后一拍确保 SVG 已插入文档。
       用 setTimeout 而非 requestAnimationFrame:后台标签页 rAF 不触发,控件会被吞 */
    if (steps.length && window.makeStepper) {
      setTimeout(function () { if (root.isConnected) window.makeStepper(root.id, steps); }, 0);
    }
    return root;
  };
  function safeQuery(scope, sel) {
    try {
      var original = sel.replace(/#([\w-]+)/g, '[data-ld-origin-id="$1"]');
      return scope.querySelector(original) || scope.querySelector(sel);
    } catch (e) { return null; }
  }

  /* ---- 2.1 新块:button / codewalk / group / html ---- */
  BLOCKS.button = function (b) {
    var variant = ["primary", "outline", "ghost", "link"].indexOf(b.variant) >= 0 ? b.variant : "primary";
    var size = ["sm", "md", "lg"].indexOf(b.size) >= 0 ? b.size : "md";
    var btn = el("button", "ld-btn ld-btn--" + variant + " ld-btn--" + size,
      (b.icon ? esc(b.icon) + " " : "") + md(b.label || "按钮"));
    btn.type = "button";
    return btn;
  };

  BLOCKS.codewalk = function (b) {
    var root = el("div", "codewalk" + (b.arrow === false ? " no-arrow" : ""));
    if (b.title || b.lang) {
      root.appendChild(el("div", "cw-head",
        '<span class="cw-title">' + md(b.title || "") + "</span>" +
        (b.lang ? '<span class="cw-lang">' + esc(b.lang) + "</span>" : "")));
    }
    var code = el("div", "cw-code");
    var steps = [], srcIdx = 0;
    (b.lines || []).forEach(function (ln) {
      if (ln == null) return;
      if (typeof ln === "string") ln = { code: ln };
      if (ln.ref != null && ln.code == null) {           /* 执行轨迹行:只驱动高亮/输出,不显示源码 */
        var ref = num(ln.ref, 0);
        if (ref >= 0 && ref < srcIdx) steps.push({ line: ref, out: ln.out || "", note: ln.note || "" });
        return;
      }
      var row = el("div", "cw-line");
      row.setAttribute("data-line", String(srcIdx));
      row.appendChild(el("span", "cw-gutter", String(srcIdx + 1)));
      var src = el("span", "cw-src");
      src.textContent = String(ln.code == null ? "" : ln.code);
      row.appendChild(src);
      code.appendChild(row);
      steps.push({ line: srcIdx, out: ln.out || "", note: ln.note || "" });
      srcIdx++;
    });
    if (!srcIdx) throw new Error("codewalk 无代码行");
    root.appendChild(code);
    if (b.showOutput !== false) root.appendChild(el("div", "cw-out"));
    if (b.showNotes !== false) root.appendChild(el("div", "cw-note"));
    root.appendChild(el("div", "cw-bar",
      '<button type="button" data-cw="run">' + esc(b.runLabel || "▶ 运行") + "</button>" +
      '<button type="button" data-cw="pause">⏸ 暂停</button>' +
      '<button type="button" data-cw="step">⏭ 单步</button>' +
      '<button type="button" data-cw="reset">↺ 重置</button>' +
      '<span class="cw-count"></span>'));
    root.setAttribute("data-ld-cw", JSON.stringify({
      steps: steps, loop: !!b.loop, speedMs: clamp(num(b.speedMs, 900), 200, 5000), autoStart: !!b.autoStart
    }));
    return root;
  };

  BLOCKS.group = function (b) {
    if (ARTICLE_MODE) {                                  /* 长文模式:组展开为流式 */
      var flat = el("div", "ld-group-flat");
      positionedToFlow(b.children, flat);
      return flat;
    }
    var nat = b.natural && num(b.natural.w, 0) > 0 && num(b.natural.h, 0) > 0 ? b.natural : { w: 1, h: 1 };
    var f = b.frame || {};
    var root = el("div", "ld-group");
    var inner = el("div", "ld-group-inner");
    inner.style.width = nat.w + "px";
    inner.style.height = nat.h + "px";
    inner.style.transform = "scale(" + (num(f.w, nat.w) / nat.w) + "," + (num(f.h, nat.h) / nat.h) + ")";
    (b.children || []).forEach(function (c) { if (c) inner.appendChild(renderPositioned(c)); });
    root.appendChild(inner);
    return root;
  };

  BLOCKS.html = function (b) {
    var id = String(b.id || "x").replace(/[^\w-]/g, "");
    var root = el("div", "ld-html ld-html-" + id);
    if (b.css) {
      var st = document.createElement("style");
      st.textContent = scopeCss(b.css, id);
      root.appendChild(st);
    }
    root.appendChild(el("div", "ld-html-body", sanitizeHtml(b.body)));
    return root;
  };

  function markFragment(node, step) {
    node.classList.add("fragment");
    node.setAttribute("data-step", String(num(step, 0)));
  }

  function renderBlock(b, positioned) {
    if (!b || typeof b !== "object") return brokenCard("空内容块");
    if (!positioned && !ARTICLE_MODE && b.flowFrame && b.natural) {
      // Keep the original flow slot; scale/move the editable content inside it.
      // Identity, media and delegated interactions remain on the original block.
      var slot = el("div", "ld-flow-slot");
      slot.setAttribute("data-ld-flow-slot", String(b.id || ""));
      slot.style.width = b.natural.w + "px"; slot.style.height = b.natural.h + "px";
      var sized = Object.assign({}, b, {frame: b.flowFrame});
      var object = renderPositioned(sized); object.classList.add("ld-flow-object");
      slot.appendChild(object); return slot;
    }
    var fn = BLOCKS[String(b.type)];
    try {
      if (!fn) return brokenCard("未知内容块类型:" + esc(String(b.type)));
      var node = fn(b);
      if (!node) return brokenCard("内容块渲染为空:" + esc(String(b.type)));
      if (b.type === "html" || b.type === "svg") namespaceDom(node);
      if (b.step != null && !node.classList.contains("fragment")) markFragment(node, b.step);
      if (b.id) {
        var cleanId = String(b.id).replace(/[^\w-]/g, "");
        if (cleanId) {
          if (!node.id) node.id = cleanId;
          node.setAttribute("data-ld-id", cleanId);
        }
      }
      /* 2.1:样式层 / 初始隐藏 / 点击动作(interact.js 做事件委托) */
      if (b.style) applyStyle(node, b.style, !!TEXT_BLOCKS[b.type]);
      if (b.hidden) node.classList.add("ld-hidden");
      if (b.actions && b.actions.length) {
        node.setAttribute("data-ld-actions", JSON.stringify(b.actions));
        node.classList.add("ld-actionable");
        if (b.once) node.setAttribute("data-ld-once", "1");
      }
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
        case "canvas":  /* 2.1 自由排版页:全部元素带 frame 绝对定位 */
          sec.className = "slide slide--canvas";
          if (s.section) sec.setAttribute("data-section", s.section);
          if (s.title) sec.appendChild(el("h2", "slide-title", md(s.title)));
          if (s.sub) sec.appendChild(el("p", "slide-sub", md(s.sub)));
          sec.appendChild(el("div", "slide-body ld-canvas-body"));
          (s.objects || []).forEach(function (o) { if (o) sec.appendChild(renderPositioned(o)); });
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
      /* 2.1:页面 id / 背景层 / 浮层 */
      if (s.id) sec.setAttribute("data-ld-slide-id", String(s.id).replace(/[^\w-]/g, ""));
      var bgNode = renderBg(s.bg || data.bg);
      if (bgNode) sec.insertBefore(bgNode, sec.firstChild);
      (s.overlays || []).forEach(function (o) { if (o) sec.appendChild(renderPositioned(o)); });
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

  /* 2.1 全局层:每页追加 globals(封面/章节/结尾默认跳过;excludeSlides 逐页排除) */
  function applyGlobalsTo(sec, s, data) {
    var globals = Array.isArray(data.globals) ? data.globals : [];
    if (!globals.length) return;
    var layout = String((s && s.layout) || "content");
    var bare = layout === "title" || layout === "section" || layout === "end";
    var sid = s && s.id ? String(s.id) : "";
    globals.forEach(function (g) {
      if (!g) return;
      if (bare && g.skipCovers !== false) return;
      if (sid && Array.isArray(g.excludeSlides) && g.excludeSlides.indexOf(sid) >= 0) return;
      sec.appendChild(renderPositioned(g, { global: true }));
    });
  }
  function buildSlide(s, data) {
    var sec = renderSlide(s || {}, data);
    applyGlobalsTo(sec, s, data);
    return namespaceDom(sec);
  }
  function buildDeck(data) {
    var deck = el("div", "deck");
    deck.setAttribute("data-course", data.course || "");
    var slides = Array.isArray(data.slides) ? data.slides : [];
    slides.forEach(function (s) { deck.appendChild(buildSlide(s, data)); });
    if (!slides.length) {
      var empty = el("section", "slide");
      empty.appendChild(el("h2", "slide-title", "本课次暂无内容"));
      deck.appendChild(empty);
    }
    return deck;
  }
  function renderDeck(data) {
    document.body.className = "slides-page";
    var home = el("a", "slides-home", "⌂ 课程首页");
    home.href = withQuery("../main.html");
    document.body.appendChild(home);
    document.body.appendChild(buildDeck(data));
  }
  /* 编辑桥接用:整体重渲 / 单页替换(slides.js 随后 reinit 接管) */
  function rerenderDeck(data) {
    if (data.kind === "home") { renderHome(data); return; }
    var old = document.querySelector(".deck");
    var fresh = buildDeck(data);
    if (old && old.parentNode) old.parentNode.replaceChild(fresh, old);
    else document.body.appendChild(fresh);
  }
  function patchSlide(slideJson, index) {
    var deck = document.querySelector(".deck");
    if (!deck) return;
    var data = (window.LESSONDOC && window.LESSONDOC.data) || {};
    var sections = deck.querySelectorAll(":scope > section.slide");
    var fresh = buildSlide(slideJson || {}, data);
    if (sections[index]) deck.replaceChild(fresh, sections[index]);
    else deck.appendChild(fresh);
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
    ARTICLE_MODE = true;                     /* 定位块/组按流式渲染,globals 不渲染 */
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
      if (layout === "title") {
        if (s.overlays && s.overlays.length) positionedToFlow(s.overlays, flow);
        return;
      }
      if (layout === "section") {
        flow.appendChild(el("div", "a-sec",
          '<span class="sec-no">' + esc(s.no || "") + '</span><span class="sec-title">' + esc(s.title || "") + "</span>" +
          (s.hint ? '<div class="sec-hint">' + md(s.hint) + "</div>" : "")));
        positionedToFlow(s.overlays, flow);
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
      } else if (layout === "canvas") {
        positionedToFlow(s.objects, card);
      } else {
        renderBlocks(s.blocks || [], card);
        if (layout === "end") {
          if (s.summary) card.appendChild(el("p", null, md(s.summary)));
          if (s.nextUp) card.appendChild(el("div", "callout think", md(s.nextUp)));
        }
      }
      positionedToFlow(s.overlays, card);
      flow.appendChild(card);
    });
    if (data.globals && data.globals.length) {
      var globals = el("div", "a-slide ld-article-globals");
      positionedToFlow(data.globals, globals);
      flow.appendChild(globals);
    }
    namespaceDom(flow);
    document.body.appendChild(flow);
    buildHomeAppearance(data);
  }

  /* ============================================================
     kind=home:课程首页渲染
     ============================================================ */
  function lessonHref(n) { return withQuery("lesson_" + n + "/lesson_" + n + ".html"); }

  var HOME_SECTION_ORDER = ["hero", "mindmap", "nav", "blocks", "tabs", "footer"];

  function renderHome(m) {
    document.body.className = "";
    var oldHome = document.querySelector(".ld-home");
    var homeRoot = el("div", "ld-home");
    if (oldHome) oldHome.replaceWith(homeRoot);
    else document.body.appendChild(homeRoot);
    var course = m.course || {};
    var lessons = Array.isArray(m.lessons) ? m.lessons : [];
    var byN = {};
    lessons.forEach(function (L) { if (L && L.n != null) byN[L.n] = L; });
    var stages = Array.isArray(m.stages) ? m.stages : [];
    var home = (m.home && typeof m.home === "object") ? m.home : {};

    /* 2.1:home.sections 决定区块顺序/显隐/标题;缺省与 2.0 逐像素一致 */
    var cfgByKey = {}, order = [];
    (Array.isArray(home.sections) ? home.sections : []).forEach(function (sc) {
      if (!sc || !sc.key || cfgByKey[sc.key] || HOME_SECTION_ORDER.indexOf(sc.key) < 0) return;
      cfgByKey[sc.key] = sc; order.push(sc.key);
    });
    HOME_SECTION_ORDER.forEach(function (k) { if (!cfgByKey[k]) { cfgByKey[k] = { key: k }; order.push(k); } });

    var homeBg = renderBg(home.bg);
    if (homeBg) { homeBg.className = "home-bg"; homeRoot.appendChild(homeBg); }

    /* 内容区块共用一个 .wrap;hero/footer 直挂 body 并「关闭」当前 wrap 以保持顺序 */
    var wrap = null;
    function contentWrap() {
      if (!wrap) { wrap = el("div", "wrap"); homeRoot.appendChild(wrap); }
      return wrap;
    }
    function sectionTitle(cfg, dflt) { return esc(cfg.title || dflt); }

    var builders = {
      hero: function (cfg) {
        wrap = null;
        var hero = el("header", "hero");
        hero.setAttribute("data-ld-section", "hero");
        hero.innerHTML = "<h1>" + esc(cfg.title || course.name || "课程学习文档") + "</h1>" +
          (course.intro ? "<p>" + md(course.intro) + "</p>" : "") +
          (course.teacher || course.department ? "<p>" + [course.teacher, course.department].filter(Boolean).map(esc).join(" · ") + "</p>" : "");
        if (home.style && (home.style.heroGradient || home.style.bgGradient)) {
          var hg = gradientCss(home.style.heroGradient || home.style.bgGradient);
          if (hg) hero.style.backgroundImage = hg;
        }
        var allowed = Array.isArray(cfg.stats) ? cfg.stats : null;
        var statsEl = el("div", "stats");
        [
          { k: "totalHours", v: course.totalHours, l: "总学时" },
          { k: "sessionCount", v: course.sessionCount || lessons.length, l: "课次" },
          { k: "credits", v: course.credits, l: "学分" },
          { k: "assessment", v: course.assessment, l: "考核", small: true }
        ].forEach(function (s) {
          if (s.v == null || s.v === "") return;
          if (allowed && allowed.indexOf(s.k) < 0) return;
          statsEl.appendChild(el("div", "stat",
            "<b" + (s.small ? ' style="font-size:1em"' : "") + ">" + esc(s.v) + "</b><span>" + esc(s.l) + "</span>"));
        });
        hero.appendChild(statsEl);
        homeRoot.appendChild(hero);
      },
      mindmap: function (cfg) {
        var mmSection = el("section", "section");
        mmSection.setAttribute("data-ld-section", "mindmap");
        mmSection.appendChild(el("h2", null, sectionTitle(cfg, "课程知识体系总览")));
        mmSection.appendChild(el("p", "sub", "点击分支展开/收起,点击课次名进入对应课次"));
        var mmCard = el("div", "card");
        var depth = num(cfg.collapsedDepth, 1);
        var collapsedStages = depth === 0;
        var mmChildren = stages.map(function (st) {
          var ns = (st && st.lessons || []).filter(function (n) { return byN[n]; });
          return {
            label: st && st.label || "",
            note: ns.length ? "第" + ns[0] + "—" + ns[ns.length - 1] + "课" : "",
            collapsed: collapsedStages,
            children: ns.map(function (n) {
              var L = byN[n];
              return {
                label: "第" + n + "课 " + (L.title || ""),
                note: (L.topics || []).slice(0, 2).join(" · ") || (L.status !== "ready" ? "待发布" : ""),
                collapsed: depth <= 1,
                children: cfg.collapsedDepth == null ? [] : (L.topics || []).map(function (topic) { return { label: topic }; }),
                href: L.status === "ready" ? lessonHref(n) : null
              };
            })
          };
        });
        mmCard.appendChild(renderMindmap({ root: course.name || "课程", children: mmChildren }));
        mmSection.appendChild(mmCard);
        contentWrap().appendChild(mmSection);
      },
      nav: function (cfg) {
        var navSection = el("section", "section");
        navSection.setAttribute("data-ld-section", "nav");
        navSection.appendChild(el("h2", null, sectionTitle(cfg, "课次导航")));
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
        contentWrap().appendChild(navSection);
      },
      blocks: function (cfg) {                    /* 2.1 首页可编辑区块 */
        var blocks = Array.isArray(cfg.blocks) ? cfg.blocks : [];
        if (!blocks.length) return;
        var sec = el("section", "section home-blocks");
        sec.setAttribute("data-ld-section", "blocks");
        sec.appendChild(el("h2", null, sectionTitle(cfg, "课程说明")));
        var card = el("div", "card");
        renderBlocks(blocks, card);
        sec.appendChild(card);
        contentWrap().appendChild(sec);
      },
      tabs: function (cfg) {
        var tabs = Array.isArray(m.tabs) ? m.tabs : [];
        if (!tabs.length) return;
        var infoSection = el("section", "section");
        infoSection.setAttribute("data-ld-section", "tabs");
        infoSection.appendChild(el("h2", null, sectionTitle(cfg, "课程信息")));
        infoSection.appendChild(renderBlock({ type: "tabs", tabs: tabs }));
        contentWrap().appendChild(infoSection);
      },
      footer: function (cfg) {
        wrap = null;
        var tb = m.textbook || {};
        var foot = el("footer", "site");
        foot.setAttribute("data-ld-section", "footer");
        var tbText = tb.title ? "教材:《" + esc(tb.title) + "》" +
          (tb.author ? " · " + esc(tb.author) : "") + (tb.publisher ? " · " + esc(tb.publisher) : "") : "";
        foot.innerHTML = (cfg.title ? esc(cfg.title) + "<br>" : "") + tbText + (tbText ? "<br>" : "") +
          "LessonDoc " + esc(String(m.spec || "2.0").replace("lessondoc/", "")) + " · 本文档由课程学习文档模板生成";
        homeRoot.appendChild(foot);
      }
    };
    order.forEach(function (key) {
      var cfg = cfgByKey[key];
      if (cfg.hidden) return;
      try { builders[key](cfg); } catch (e) { homeRoot.appendChild(brokenCard("首页区块渲染失败:" + key)); }
    });

    if (home.style && home.style.cardRadius != null) {
      homeRoot.querySelectorAll(".card, .lesson-card").forEach(function (card) { card.style.borderRadius = clamp(num(home.style.cardRadius, 18), 0, 120) + "px"; });
    }
    namespaceDom(homeRoot);
    var appearance = document.querySelector(".ld-appearance");
    if (appearance) appearance.remove();
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
    var box = el("div", "slides-hud ld-appearance");
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

  /* 旧壳页(未显式引入 interact.js)由引擎按自身路径自动注入,保证动作/codewalk 可用 */
  function ensureInteract() {
    var cur = document.currentScript;
    var source = cur && cur.getAttribute("src") || "";
    var base = source.replace(/deck-engine\.js.*$/, "");
    var query = source.indexOf("?") >= 0 ? source.slice(source.indexOf("?")) : "";
    function load() {
      if (window.__LESSONDOC_INTERACT__) return;
      var present = Array.prototype.some.call(document.scripts, function (script) {
        return /interact\.js(\?|$)/.test(script.getAttribute("src") || "");
      });
      if (present) return;
      var script = document.createElement("script");
      script.src = base + "interact.js" + query;
      document.body.appendChild(script);
    }
    // The parser has not encountered the following explicit script yet. Waiting
    // prevents the cached compatibility runtime racing the new explicit runtime.
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
    else load();
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
    /* 与 interact.js 合并挂载(加载顺序无关):__engine 供编辑桥接重渲 */
    var LD = window.LESSONDOC = window.LESSONDOC || {};
    LD.data = data; LD.renderBlock = renderBlock; LD.renderBlocks = renderBlocks;
    LD.__engine = {
      rerender: rerenderDeck, patchSlide: patchSlide, renderSlide: renderSlide,
      renderHome: renderHome, renderPositioned: renderPositioned, applyStyle: applyStyle, applyTheme: applyTheme
    };
    ensureInteract();

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
