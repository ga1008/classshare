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
    {"title", "section", "content", "two-col", "center", "grid", "end"}
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
