from fastapi import APIRouter

from .homework_parts import assignments as _assignments
from .homework_parts import submissions as _submissions
from .homework_parts import grading as _grading
from .homework_parts import exam_papers as _exam_papers
from .homework_parts import drafts as _drafts
from .homework_parts import exports as _exports

from .homework_parts.common import *
from .homework_parts.assignments import *
from .homework_parts.submissions import *
from .homework_parts.grading import *
from .homework_parts.exam_papers import *
from .homework_parts.drafts import *
from .homework_parts.exports import *


router = APIRouter(prefix='/api')
router.include_router(_assignments.router)
router.include_router(_submissions.router)
router.include_router(_grading.router)
router.include_router(_exam_papers.router)
router.include_router(_drafts.router)
router.include_router(_exports.router)


__all__ = [name for name in globals() if not name.startswith("__")]
