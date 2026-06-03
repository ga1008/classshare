from fastapi import APIRouter

from . import classes_courses_onboarding as _classes_courses_onboarding
from . import classes_courses_classes as _classes_courses_classes
from . import classes_courses_courses as _classes_courses_courses
from . import classes_courses_offerings as _classes_courses_offerings

from .common import *
from .classes_courses_onboarding import *
from .classes_courses_classes import *
from .classes_courses_courses import *
from .classes_courses_offerings import *


router = APIRouter()
router.include_router(_classes_courses_onboarding.router)
router.include_router(_classes_courses_classes.router)
router.include_router(_classes_courses_courses.router)
router.include_router(_classes_courses_offerings.router)


__all__ = [name for name in globals() if not name.startswith("__")]
