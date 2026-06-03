from fastapi import APIRouter

from .materials_parts import library as _library
from .materials_parts import ai_import as _ai_import
from .materials_parts import final_materials as _final_materials
from .materials_parts import exports as _exports
from .materials_parts import learning as _learning

from .materials_parts.common import *
from .materials_parts.generation_helpers import *
from .materials_parts.ai_import_helpers import *
from .materials_parts.final_material_helpers import *
from .materials_parts.rewrite_helpers import *
from .materials_parts.library import *
from .materials_parts.ai_import import *
from .materials_parts.final_materials import *
from .materials_parts.exports import *
from .materials_parts.learning import *


router = APIRouter()
router.include_router(_library.router)
router.include_router(_ai_import.router)
router.include_router(_final_materials.router)
router.include_router(_exports.router)
router.include_router(_learning.router)


__all__ = [name for name in globals() if not name.startswith("__")]
