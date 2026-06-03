from fastapi import APIRouter

from .ui_parts import auth as _auth
from .ui_parts import dashboard as _dashboard
from .ui_parts import classroom as _classroom
from .ui_parts import assignment_pages as _assignment_pages
from .ui_parts import manage_pages as _manage_pages
from .ui_parts import exam_pages as _exam_pages

from .ui_parts.common import *
from .ui_parts.auth import *
from .ui_parts.dashboard import *
from .ui_parts.classroom import *
from .ui_parts.assignment_pages import *
from .ui_parts.manage_pages import *
from .ui_parts.exam_pages import *


router = APIRouter()
router.include_router(_auth.router)
router.include_router(_dashboard.router)
router.include_router(_classroom.router)
router.include_router(_assignment_pages.router)
router.include_router(_manage_pages.router)
router.include_router(_exam_pages.router)


__all__ = [name for name in globals() if not name.startswith("__")]
