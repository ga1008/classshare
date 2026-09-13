"""管理中心页面路由聚合器。

各域页面按 docs/manage-center-improvement-plan-2026-09-11.md §5.2 拆到
manage_pages_{teaching,library,academic,me,admin}.py；这里只汇总 router 并
保持 `from .ui_parts.manage_pages import *` 的历史导入不变。
"""
from fastapi import APIRouter

from .manage_pages_shared import (  # noqa: F401 - re-exported for callers and tests
    _academic_event_label,
    _password_reset_login_summary_sql,
    _table_has_column,
)
from .manage_pages_teaching import *  # noqa: F401,F403
from .manage_pages_library import *  # noqa: F401,F403
from .manage_pages_academic import *  # noqa: F401,F403
from .manage_pages_me import *  # noqa: F401,F403
from .manage_pages_admin import *  # noqa: F401,F403
from . import manage_pages_academic, manage_pages_admin, manage_pages_library, manage_pages_me, manage_pages_teaching

router = APIRouter()
for _domain_router in (
    manage_pages_teaching.router,
    manage_pages_library.router,
    manage_pages_academic.router,
    manage_pages_me.router,
    manage_pages_admin.router,
):
    router.include_router(_domain_router)
