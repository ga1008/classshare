from fastapi import APIRouter, Depends

from ..dependencies import get_current_teacher

from .manage_parts import classes_courses as _classes_courses
from .manage_parts import semesters_textbooks as _semesters_textbooks
from .manage_parts import system_config as _system_config
from .manage_parts import integrations as _integrations

from .manage_parts.common import *
from .manage_parts.classes_courses import *
from .manage_parts.semesters_textbooks import *
from .manage_parts.system_config import *
from .manage_parts.integrations import *


router = APIRouter(prefix='/api/manage', dependencies=[Depends(get_current_teacher)])
router.include_router(_classes_courses.router)
router.include_router(_semesters_textbooks.router)
router.include_router(_system_config.router)
router.include_router(_integrations.router)


__all__ = [name for name in globals() if not name.startswith("__")]
