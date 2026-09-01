"""LessonDoc 2.0 —— 配置驱动的课程学习文档包(设计: docs/course-lessondoc-template-2026-09.md).

子模块:
- spec       常量/块类型注册表/限额
- validate   降级式校验(丢块不丢页)
- render     壳 HTML 生成与反抽取(可逆两端)
- assets     平台引擎资产读取
- pack_service  包骨架落库/清单读写/pack 登记表
"""

from .spec import SPEC_VERSION  # noqa: F401
from .validate import LessonDocValidationError, validate_deck, validate_manifest  # noqa: F401
from .render import (  # noqa: F401
    extract_deck_text,
    extract_embedded_json,
    is_lessondoc_html,
    render_home_html,
    render_lesson_html,
)
