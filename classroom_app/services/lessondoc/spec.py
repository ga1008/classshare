"""LessonDoc 2.0 规范常量(块类型注册表/版式/主题/限额).

真源文档: docs/course-lessondoc-template-2026-09.md 与
docs/lessondoc-authoring-guide.md。改这里必须同步文档。
"""

from __future__ import annotations

SPEC_VERSION = "lessondoc/2.0"
SPEC_MAJOR_PREFIX = "lessondoc/2"

# 壳 HTML 判别标志(<html data-lessondoc="2.0">)与内嵌数据节点 id
HTML_MARKER_ATTR = "data-lessondoc"
DATA_SCRIPT_ID = "lessondoc-data"

DOC_KIND_LESSON = "lesson"
DOC_KIND_HOME = "home"

SLIDE_LAYOUTS = frozenset(
    {"title", "section", "content", "two-col", "center", "grid", "end", "canvas"}
)
DEFAULT_LAYOUT = "content"

BLOCK_TYPES = frozenset(
    {
        "text",
        "cards",
        "bignum",
        "bigmark",
        "timeline",
        "table",
        "callout",
        "tabs",
        "details",
        "code",
        "media",
        "svg",
        "diagram",
        "quiz",
        "tasklist",
        "reveal",
        "stepper",
        # 2.1(编辑器,docs/lessondoc-editor-2026-09.md §4)
        "button",
        "codewalk",
        "group",
        "html",
    }
)
DIAGRAM_KINDS = frozenset({"flow", "sequence", "arch", "mindmap"})
CALLOUT_TONES = frozenset({"info", "think", "warn", "ok", "err"})
CARD_TONES = frozenset({"primary", "ok", "warn", "err"})
MEDIA_KINDS = frozenset({"image", "video", "audio"})

THEMES = ("sky", "teal", "violet", "amber", "rose", "slate")
DEFAULT_THEME = "sky"

# 校验限额(超限降级而非报错,见 validate.py)
MAX_SLIDES = 40
MAX_BLOCKS_PER_SLIDE = 12
MAX_TEXT_CHARS = 240
MAX_TABLE_ROWS = 12          # 硬截断线;>6 行仅记告警建议拆页
WARN_TABLE_ROWS = 6
MAX_SVG_BODY_CHARS = 20000
MAX_STEPPER_STEPS = 12
MAX_QUIZ_OPTIONS = 6

# 引擎资产清单(生成包时从 static/lessondoc/<ver>/ 复制进包 assets/)
ASSET_FILES = (
    "course.css",
    "slides.css",
    "themes.css",
    "course.js",
    "slides.js",
    "deck-engine.js",
    "interact.js",          # 2.1:动作运行时 / codewalk 播放器 / 编辑桥接
)
ASSET_STATIC_SUBDIR = "lessondoc/2.0"

# 图示允许的语义色变量(svg 逃生舱中出现其他颜色会被替换并记告警)
DIAGRAM_COLOR_VARS = (
    "--dg-primary",
    "--dg-primary-dark",
    "--dg-primary-soft",
    "--dg-ok",
    "--dg-warn",
    "--dg-err",
    "--dg-muted",
    "--dg-line",
    "--dg-fill",
    "--dg-text",
)

# ---------------------------------------------------------------- 2.1 编辑器模型(严格加法)

# 定位块 frame 的允许范围(画布 1280×720,允许略出血但不允许飞走)
FRAME_X_RANGE = (-200, 1480)
FRAME_Y_RANGE = (-200, 920)
FRAME_SIZE_RANGE = (8, 1680)
NATURAL_SIZE_RANGE = (1, 10000)   # 内部渲染尺寸;拆组时保留整体缩放后的外观
MAX_POSITIONED_PER_SLIDE = 40     # objects + overlays
MAX_GLOBALS = 12
MAX_GROUP_DEPTH = 2
MAX_ACTIONS_PER_BLOCK = 12

# 样式白名单(键 → 取值口径,见 validate_style.py)
STYLE_FONTS = {
    "sans": "var(--font)",
    "serif": '"Songti SC","SimSun","Noto Serif CJK SC",serif',
    "kai": '"KaiTi","STKaiti","Kaiti SC",serif',
    "mono": "var(--mono)",
    "rounded": '"Yuanti SC","YouYuan",system-ui,sans-serif',
}
STYLE_WEIGHTS = (400, 500, 600, 700, 800)
STYLE_SHADOWS = ("none", "soft", "hard", "glow")
STYLE_ALIGNS = ("left", "center", "right")
STYLE_BORDER_STYLES = ("solid", "dashed")
STYLE_SIZE_RANGE = (12, 160)
STYLE_LINE_HEIGHT_RANGE = (0.9, 3.0)
STYLE_LETTER_SPACING_RANGE = (-2, 20)
STYLE_STROKE_WIDTH_RANGE = (0, 6)
STYLE_BORDER_WIDTH_RANGE = (0, 12)
STYLE_RADIUS_RANGE = (0, 120)
STYLE_PADDING_RANGE = (0, 120)
# 语义色名(引擎映射到 --primary/--ok… 变量;换主题跟随)
STYLE_SEMANTIC_COLORS = (
    "primary", "primary-dark", "primary-soft", "ok", "warn", "err",
    "muted", "text", "white", "transparent",
)

# 页面背景
BG_FITS = ("cover", "contain", "stretch", "tile", "custom")
BG_SCALE_RANGE = (10, 400)
BG_OPACITY_RANGE = (0.0, 1.0)
BG_BLUR_RANGE = (0, 40)

# 动作
ACTION_KINDS = frozenset(
    {"show", "hide", "toggle", "move", "moveTo", "goto", "next", "prev", "run", "reset"}
)
ACTION_TARGET_KINDS = frozenset({"show", "hide", "toggle", "move", "moveTo", "run", "reset"})
ACTION_MS_RANGE = (0, 5000)
ACTION_EASES = ("linear", "in", "out", "inout")

# button
BUTTON_VARIANTS = ("primary", "outline", "ghost", "link")
BUTTON_SIZES = ("sm", "md", "lg")

# codewalk
MAX_CODEWALK_LINES = 60
MAX_CODEWALK_LINE_CHARS = 200
CODEWALK_SPEED_RANGE = (200, 5000)

# html 块消毒(validate_html.py)
MAX_HTML_BODY_CHARS = 20000
MAX_HTML_CSS_CHARS = 4000
HTML_ALLOWED_TAGS = frozenset(
    {
        "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
        "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
        "b", "i", "em", "strong", "u", "s", "small", "sub", "sup", "code", "pre",
        "img", "br", "hr", "blockquote", "a", "figure", "figcaption",
        "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
        "polygon", "text", "tspan", "defs", "marker", "lineargradient", "stop",
    }
)
HTML_ALLOWED_ATTRS = frozenset(
    {
        "class", "style", "src", "href", "alt", "title", "width", "height",
        "colspan", "rowspan",
        # svg 几何/绘制
        "viewbox", "d", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
        "points", "fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap",
        "stroke-linejoin", "opacity", "fill-opacity", "stroke-opacity", "transform",
        "text-anchor", "font-size", "font-weight", "font-family", "id", "offset",
        "stop-color", "markerwidth", "markerheight", "refx", "refy", "orient",
        "marker-end", "marker-start", "visibility", "dominant-baseline",
    }
)

# 首页 home.sections 的区块键
HOME_SECTION_KEYS = ("hero", "mindmap", "nav", "blocks", "tabs", "footer")
HOME_STAT_KEYS = ("totalHours", "sessionCount", "credits", "assessment")
